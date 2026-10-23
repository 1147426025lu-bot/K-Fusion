//===- EffectSummaryAnalysis__副作用摘要.cpp --------------------------------===//
//
// ECoS §13 的 `feat(analysis)`：从 IR 推导每个函数的**效果摘要**，输出 JSON，
// 直接喂给 `kfusion/scripts/contract_check__契约校验.py`。
//
// 为什么需要它：
//   上一轮 `feat(contract)` 建立了「契约 + fail-closed 校验器」，但契约里的
//   `effects` 是**人工声明**的。人工声明能证明门禁机制可用，却证不了
//   "编译器真的知道这段代码会睡/会碰 MMIO"。本 Pass 补的是这一环。
//
// === v2（2026-10-23 审查后重做）===
//
// v1 的三个设计错误（代码审查指出，均已修正）：
//   1) `memcpy/memset/memmove` 被当成"纯函数" —— 它们显然读写内存。
//      当时只考虑了"会不会睡/碰 MMIO"，忘了**数据依赖**也要靠这张摘要。
//   2) 指针参数只判了"被 store"，没判"被 load"；`memcpy(out,in,n)` 写 out 读 in，
//      在 v1 里可能整个函数判成 pure。
//   3) 递归靠"迭代到 64 次上限"判断 —— 深非递归链会误标，真递归也可能提前收敛。
//      改为对调用图做 Tarjan SCC。
//
// 现在的模型把效果分成两组：
//   **执行效果**（决定能进哪个上下文）：sleep / blocking_io / mmio
//   **内存效果**（决定有没有数据依赖）：reads / writes，元素是抽象区域
//       @全局名    —— 某个全局变量
//       arg:N      —— 第 N 个指针参数指向的区域（传播到调用者时映射成实参）
//       heap       —— 归不到具体对象的指针（保守）
//
// 原则：**unknown 不等于 pure**。解析不出的间接调用记 `unknown`，下游一律拒绝。
//
// Pass 名：plc-effect-summary
// 输出：   $PLC_EFFECT_SUMMARY_OUT（文件）或 stdout
//
//===----------------------------------------------------------------------===//

#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Analysis/AliasAnalysis.h"
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/GlobalVariable.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cstdlib>
#include <initializer_list>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

using namespace llvm;

namespace {

// 执行效果位图。与 contract_check 的 KNOWN_EFFECTS 词表对应。
enum Effect : unsigned {
  kPure        = 0,
  kStateRead   = 1u << 0,   // 聚合位：reads 非空时为真（供词表输出）
  kStateWrite  = 1u << 1,   // 聚合位：writes 非空时为真
  kMmio        = 1u << 2,
  kSleep       = 1u << 3,
  kBlockingIO  = 1u << 4,
  kExternal    = 1u << 5,   // 名字认识但语义未归档（默认拒绝，可显式放行）
  kUnknown     = 1u << 6,   // 无法证明任何东西（间接调用等）→ 必拒
};

struct FnSummary {
  unsigned Direct = kPure;
  unsigned Total = kPure;
  std::set<std::string> Callees;
  std::set<std::string> UnknownCalls;

  // 抽象内存区域
  std::set<std::string> Reads;
  std::set<std::string> Writes;

  // 本函数内对每个 callee 的调用点（用于把 callee 的 arg:N 映射成本函数的区域）
  std::map<std::string, std::vector<CallBase *>> Sites;

  bool Recursive = false;

  const Function *Fn = nullptr;   // 传播时用来重建 arg:N 表
};

/// 名字匹配表。用子串匹配而不是精确名，因为不同 libc/内核版本的符号名不一致
/// （例如 `__isoc99_fscanf`、`nanosleep64`）。宁可多判。
bool nameHasAny(StringRef Name, std::initializer_list<StringRef> Keys) {
  for (StringRef K : Keys)
    if (Name.contains_insensitive(K))
      return true;
  return false;
}

bool isSleepCall(StringRef N) {
  return nameHasAny(N, {"sleep", "usleep", "msleep", "ssleep", "delay",
                        "schedule_timeout", "wait_event",
                        "wait_for_completion", "mutex_lock", "down_",
                        "down_read", "down_write", "cond_resched",
                        "pthread_cond_wait", "sigwait", "sem_wait"});
}

bool isMmioCall(StringRef N) {
  return nameHasAny(N, {"ioremap", "iounmap", "readl", "writel", "readw",
                        "writew", "readb", "writeb", "ioread", "iowrite",
                        "inb", "outb", "inl", "outl", "memcpy_fromio",
                        "memcpy_toio", "memset_io", "mmio", "regmap"});
}

bool isBlockingIoCall(StringRef N) {
  return nameHasAny(N, {"fwrite", "fread", "fopen", "fclose", "fprintf",
                        "fflush", "fscanf", "fgets", "fputs", "open",
                        "read", "write", "ioctl", "mmap", "munmap",
                        "printf", "puts", "perror"});
}

/// 内存写入型内建：memcpy / memmove / memset / bzero（含 llvm.* intrinsic）
/// 它们**必须**产生 reads/writes，不能再当纯函数（v1 的错误）。
bool isMemWriteCall(StringRef N) {
  return nameHasAny(N, {"memcpy", "memmove", "memset", "bzero", "mempcpy",
                        "wmemset", "strncpy", "strcpy", "strcat"});
}

/// 这类调用里，第 1 个参数是**源**（读），第 0 个是**目的**（写）。
/// memset/bzero 没有源。
bool memCallHasSrc(StringRef N) {
  return nameHasAny(N, {"memcpy", "memmove", "mempcpy", "strncpy",
                        "strcpy", "strcat", "wmemset"});
}

/// 确实没有可观测副作用的（不算 external，也不产生读写）。
/// 注意：v1 曾把 memcpy 放进来，是错的；现在它们由 isMemWriteCall 单独处理。
bool isBenignIntrinsic(StringRef Name) {
  for (StringRef K : {"llvm.lifetime", "llvm.dbg", "llvm.assume",
                      "llvm.experimental.noalias", "llvm.fabs",
                      "llvm.sqrt", "llvm.floor", "llvm.ceil", "llvm.trunc",
                      "llvm.smax", "llvm.smin", "llvm.umax", "llvm.umin",
                      "llvm.smul.with.overflow", "llvm.uadd.with.overflow",
                      "llvm.readnone", "llvm.sideeffect"})
    if (Name.startswith(K))
      return true;
  return false;
}

/// 纯算术：不碰内存、不调用、不阻塞，可以安全地"没有副作用"。
bool isPureArithName(StringRef Name) {
  return nameHasAny(Name, {"strlen", "strcmp", "strncmp", "memcmp", "abs",
                           "labs", "llabs", "sin", "cos", "tan", "exp",
                           "log", "pow", "fmod", "sqrt", "floor", "ceil"});
}

/// 分配型调用：返回的是新内存，不同调用点彼此不别名。
bool isAllocLike(const CallBase *CB) {
  const Function *F = CB->getCalledFunction();
  if (!F)
    return false;
  return nameHasAny(F->getName(), {"malloc", "calloc", "realloc", "strdup",
                                   "strndup", "operator new", "operator_new",
                                   "alloc", "kmalloc", "kzalloc", "vmalloc"});
}

/// 抽象区域命名器。
///
/// v2 的旧实现只分三类（@global / arg:N / heap），于是**所有**堆指针共用一个
/// "heap" 名字——后续数据依赖图会把它们全判成可能别名，连出一堆假边。
///
/// 现在用 `getUnderlyingObject`（能穿透 GEP / bitcast / PHI / select）找到根对象，
/// 再按根对象的种类给**稳定且唯一**的名字：
///   @global            全局变量
///   alloca:<fn>:<id>   某个栈分配
///   heap:<callee>#<id> 某个分配调用点
///   arg:N              第 N 个指针参数
///   heap:?             归不到根（保守兜底）
///
/// 注意 `inttoptr 常量` 不在这里处理——那是 MMIO，由 volatile 判据单独负责。
class RegionNamer {
  std::map<const Value *, std::string> Names;
  unsigned Seq = 0;

public:
  std::string of(const Value *V,
                 const std::map<const Argument *, std::string> &ArgMap) {
    if (!V)
      return "heap:?";

    const Value *Root = getUnderlyingObject(V, 0);
    auto It = Names.find(Root);
    if (It != Names.end())
      return It->second;

    std::string N;
    if (auto *GV = dyn_cast<GlobalVariable>(Root)) {
      N = "@" + GV->getName().str();
    } else if (auto *AI = dyn_cast<AllocaInst>(Root)) {
      std::string Fn = AI->getFunction() ? AI->getFunction()->getName().str() : "?";
      N = "alloca:" + Fn + ":" +
          (AI->hasName() ? AI->getName().str() : ("t" + std::to_string(Seq++)));
    } else if (auto *CB = dyn_cast<CallBase>(Root)) {
      if (isAllocLike(CB)) {
        const Function *F = CB->getCalledFunction();
        N = "heap:" + (F ? F->getName().str() : "?") + "#" +
            std::to_string(Seq++);
      } else {
        N = "heap:?";
      }
    } else if (auto *A = dyn_cast<Argument>(Root)) {
      auto Ai = ArgMap.find(A);
      N = (Ai != ArgMap.end()) ? Ai->second : "arg:?";
    } else {
      N = "heap:?";
    }

    // PLC_EFFECT_SUMMARY_WHY=1 时交代"这个区域名是怎么来的"。
    // 目的：闭接口之前先弄清 heap:? 到底是哪类指针，别对着想象做优化。
    if (N == "heap:?" || N == "arg:?") {
      static const bool Why = std::getenv("PLC_EFFECT_SUMMARY_WHY") != nullptr;
      if (Why) {
        std::string Dump;
        raw_string_ostream DS(Dump);
        DS << "???";
        if (Root) {
          if (auto *I = dyn_cast<Instruction>(Root)) {
            const Function *PF = I->getFunction();
            DS << (PF ? PF->getName() : "?") << ": ";
            I->print(DS);
          } else {
            DS << "non-inst root: ";
            Root->print(DS);
          }
        }
        errs() << "[RegionWhy] " << N << " <- " << DS.str() << "\n";
      }
    }

    Names[Root] = N;
    return N;
  }
};

/// 建立"指针参数 → arg:N"的映射。
std::map<const Argument *, std::string> buildArgMap(const Function *F) {
  std::map<const Argument *, std::string> M;
  if (!F)
    return M;
  unsigned i = 0;
  for (const Argument &A : F->args()) {
    if (A.getType()->isPointerTy())
      M[&A] = "arg:" + std::to_string(i);
    ++i;
  }
  return M;
}

/// Tarjan：求调用图强连通分量，标出递归函数。
/// 替代 v1 的"迭代 64 次上限"——那个判据既会误标深非递归链，也会漏标提前收敛的递归。
void markRecursion(std::map<std::string, FnSummary> &Sum) {
  std::map<std::string, int> Index, Low;
  std::set<std::string> OnStack;
  std::vector<std::string> Stack;
  int Counter = 0;

  std::function<void(const std::string &)> StrongConnect =
      [&](const std::string &V) {
        Index[V] = Low[V] = Counter++;
        Stack.push_back(V);
        OnStack.insert(V);

        auto It = Sum.find(V);
        if (It != Sum.end()) {
          for (const std::string &W : It->second.Callees) {
            if (!Sum.count(W))
              continue;
            if (!Index.count(W)) {
              StrongConnect(W);
              Low[V] = std::min(Low[V], Low[W]);
            } else if (OnStack.count(W)) {
              Low[V] = std::min(Low[V], Index[W]);
            }
          }
        }

        if (Low[V] == Index[V]) {
          // 弹出整个 SCC
          std::vector<std::string> Comp;
          std::string W;
          do {
            W = Stack.back();
            Stack.pop_back();
            OnStack.erase(W);
            Comp.push_back(W);
          } while (W != V);

          bool SelfLoop = false;
          if (Comp.size() == 1) {
            auto SIt = Sum.find(Comp[0]);
            if (SIt != Sum.end() && SIt->second.Callees.count(Comp[0]))
              SelfLoop = true;
          }
          if (Comp.size() > 1 || SelfLoop)
            for (const std::string &M : Comp)
              Sum[M].Recursive = true;
        }
      };

  for (auto &KV : Sum)
    if (!Index.count(KV.first))
      StrongConnect(KV.first);
}

/// 别名分析的量化指标。为什么要它：
/// 「区域名相同」是我们自己对"可能别名"的保守近似。若 AA 判定 NoAlias 却同名，
/// 说明命名太粗（会产生假依赖边）；若 AA 判定 MustAlias 却不同名，说明命名太细
/// （会漏掉真依赖，更危险）。两个计数都输出，任何人都能判断这份摘要可信到什么程度。
struct AliasStat {
  bool Available = false;
  unsigned long long Pairs = 0;
  unsigned long long Coarse = 0;   // 同名但 NoAlias：命名太粗
  unsigned long long Split = 0;    // 不同名但 MustAlias：命名太细（危险）
};

class EffectSummaryAnalysis
    : public PassInfoMixin<EffectSummaryAnalysis> {
public:
  PreservedAnalyses run(Module &M, ModuleAnalysisManager &MAM) {
    std::map<std::string, FnSummary> Sum;
    std::map<const Function *, std::string> NameOf;
    RegionNamer Namer;
    AliasStat AStat;

    // 接口绑定：被调函数 -> 形参序号 -> 所有调用点传进来的区域（聚合视图）。
    // 用途：DAG 生成时把 `arg:0` 这种**函数局部**的名字具体化。
    std::map<std::string, std::map<unsigned, std::set<std::string>>> Interface;

    // 调用点明细：保留"哪个函数、在哪个调用点、传了什么"。
    // 聚合视图丢掉了调用者身份，遇到 `arg:1`（相对名）就没法继续解析；
    // 有了明细才能递归到上层调用者，这是接口闭合的关键。
    struct SiteRec {
      std::string Caller;
      std::string Callee;
      std::vector<std::string> Args;   // 非指针实参记 "-"，保持下标对齐
    };
    std::vector<SiteRec> Sites;

    // 线程入口：pthread_create(thread, attr, entry, arg) 把 entry 交给内核/库去起线程，
    // 所以 entry **不在调用图里**（main 的 calls 里没有 testthread）。
    // 这是本项目的核心场景（跨域任务就是线程），必须单独建模，不能塞进 calls
    // ——"创建即执行"和"直接调用"的时序语义不同。
    std::vector<SiteRec> ThreadEntries;

    // 拿到函数级 AA（opt 的标准 pipeline 会注册 AAManager / BasicAA）
    FunctionAnalysisManager *FAM = nullptr;
    {
      auto &Proxy = MAM.getResult<FunctionAnalysisManagerModuleProxy>(M);
      FAM = &Proxy.getManager();
    }

    // ---- 1. 本地扫描 ----
    for (Function &F : M) {
      if (F.isDeclaration())
        continue;
      std::string FName = F.getName().str();
      NameOf[&F] = FName;
      FnSummary &S = Sum[FName];
      S.Fn = &F;

      AAResults *AA = FAM ? &FAM->getResult<AAManager>(F) : nullptr;
      if (AA)
        AStat.Available = true;
      // 本函数里出现过的内存指针（用于算假别名率）
      SmallVector<std::pair<const Value *, std::string>, 16> MemOps;

      // 指针参数 → 抽象区域名
      std::map<const Argument *, std::string> ArgMap = buildArgMap(&F);

      for (Instruction &I : instructions(F)) {
        if (auto *CB = dyn_cast<CallBase>(&I)) {
          Function *Callee = CB->getCalledFunction();
          if (!Callee) {
            // 间接调用：解析不出目标 → **unknown**，绝不假装它纯
            S.Direct |= kUnknown;
            S.UnknownCalls.insert("indirect");
            continue;
          }
          StringRef CN = Callee->getName();

          // 内存写入型内建：无论有没有函数体都要算读写
          if (isMemWriteCall(CN)) {
            if (CB->arg_size() >= 1) {
              std::string Dst = Namer.of(CB->getArgOperand(0), ArgMap);
              S.Writes.insert(Dst);
              S.Direct |= kStateWrite;
            }
            if (memCallHasSrc(CN) && CB->arg_size() >= 2) {
              std::string Src = Namer.of(CB->getArgOperand(1), ArgMap);
              S.Reads.insert(Src);
              S.Direct |= kStateRead;
            }
            // memset 的其它参数不是指针，忽略
            continue;
          }

          if (!Callee->isDeclaration()) {
            // 内部函数：只记调用关系与调用点，效果靠传播
            S.Callees.insert(CN.str());
            S.Sites[CN.str()].push_back(CB);

            // 登记这次调用把哪个区域绑到了形参上（供 DAG 生成具体化 arg:N）
            SiteRec R;
            R.Caller = FName;
            R.Callee = CN.str();
            R.Args.reserve(CB->arg_size());
            for (unsigned i = 0; i < CB->arg_size(); i++) {
              Value *A = CB->getArgOperand(i);
              if (!A->getType()->isPointerTy()) {
                R.Args.push_back("-");
                continue;
              }
              std::string Reg = Namer.of(A, ArgMap);
              Interface[CN.str()][i].insert(Reg);
              R.Args.push_back(Reg);
            }
            if (Sites.size() < 4096)      // 上限：避免巨型模块把 JSON 撑爆
              Sites.push_back(std::move(R));
            continue;
          }

          /* 线程入口：pthread_create(tid, attr, entry, arg)
           * entry 是交给库去"起"的函数，调用图看不见它；
           * arg 就是 entry 的第 0 个形参实际指向的对象。
           * 单独记 `thread_entries`，不并进 calls（语义不同）。 */
          if (CN == "pthread_create" && CB->arg_size() >= 4) {
            Value *EntryV = CB->getArgOperand(2)->stripPointerCasts();
            if (auto *Entry = dyn_cast<Function>(EntryV)) {
              SiteRec R;
              R.Caller = FName;
              R.Callee = Entry->getName().str();
              Value *ThreadArg = CB->getArgOperand(3);
              R.Args.push_back(ThreadArg->getType()->isPointerTy()
                                   ? Namer.of(ThreadArg, ArgMap)
                                   : "-");
              if (ThreadEntries.size() < 256)
                ThreadEntries.push_back(std::move(R));
            }
          }

          /* 外部声明：按名字判效果 */
          if (isBenignIntrinsic(CN))
            continue;
          if (isPureArithName(CN))
            continue;
          bool Known = false;
          if (isSleepCall(CN)) {
            S.Direct |= kSleep;
            Known = true;
          }
          if (isMmioCall(CN)) {
            S.Direct |= kMmio;
            Known = true;
          }
          if (isBlockingIoCall(CN)) {
            S.Direct |= kBlockingIO;
            Known = true;
          }
          if (!Known) {
            // 名字认识但语义未归档 → external（默认拒绝，可 --allow-external 放行）
            S.Direct |= kExternal;
            S.UnknownCalls.insert(CN.str());
          }
          continue;
        }

        if (auto *LI = dyn_cast<LoadInst>(&I)) {
          if (LI->isVolatile()) {
            S.Direct |= kMmio;
            continue;
          }
          std::string R = Namer.of(LI->getPointerOperand(), ArgMap);
          S.Reads.insert(R);
          S.Direct |= kStateRead;
          MemOps.push_back({LI->getPointerOperand(), R});
          continue;
        }
        if (auto *SI = dyn_cast<StoreInst>(&I)) {
          if (SI->isVolatile()) {
            S.Direct |= kMmio;
            continue;
          }
          std::string W = Namer.of(SI->getPointerOperand(), ArgMap);
          S.Writes.insert(W);
          S.Direct |= kStateWrite;
          MemOps.push_back({SI->getPointerOperand(), W});
          continue;
        }
        if (isa<AtomicRMWInst>(&I) || isa<AtomicCmpXchgInst>(&I)) {
          S.Direct |= kStateWrite;
          continue;
        }
        if (isa<FenceInst>(&I)) {
          S.Direct |= kStateWrite;
          continue;
        }
      }

      // ---- 本函数的别名一致性检查 ----
      // 只看前 48 个指针：O(n²) 在这里不是瓶颈（单函数内存操作通常 < 20），
      // 但病态函数必须有上限，否则一个 10k 条 load 的函数会把分析拖死。
      if (AA) {
        size_t Lim = std::min<size_t>(MemOps.size(), 48);
        for (size_t i = 0; i < Lim; ++i) {
          for (size_t j = i + 1; j < Lim; ++j) {
            if (MemOps[i].first == MemOps[j].first)
              continue;
            AStat.Pairs++;
            AliasResult R = AA->alias(MemOps[i].first, MemOps[j].first);
            if (R == AliasResult::NoAlias &&
                MemOps[i].second == MemOps[j].second)
              AStat.Coarse++;    // 同名却证明不别名 → 命名太粗
            else if (R == AliasResult::MustAlias &&
                     MemOps[i].second != MemOps[j].second)
              AStat.Split++;     // 不同名却必然别名 → 命名太细（会漏依赖）
          }
        }
      }
    }

    // ---- 2. 跨过程传播（不动点 + 参数映射）----
    for (auto &KV : Sum)
      KV.second.Total = KV.second.Direct;

    bool Changed = true;
    int Guard = 0;
    while (Changed && Guard++ < 256) {
      Changed = false;
      for (auto &KV : Sum) {
        FnSummary &S = KV.second;
        unsigned Before = S.Total;
        size_t BeforeR = S.Reads.size(), BeforeW = S.Writes.size();

        for (auto &Sit : S.Sites) {
          auto It = Sum.find(Sit.first);
          if (It == Sum.end())
            continue;
          const FnSummary &C = It->second;

          S.Total |= C.Total;

          // callee 的 arg:N 映射成它在本函数调用点上的实参区域
          const std::map<const Argument *, std::string> CallerArgs =
              buildArgMap(S.Fn);
          auto Import = [&](const std::set<std::string> &Src,
                            std::set<std::string> &Dst) {
            for (const std::string &R : Src) {
              if (R.rfind("arg:", 0) == 0) {
                int n = std::atoi(R.c_str() + 4);
                for (CallBase *CB : Sit.second)
                  if (n >= 0 && (unsigned)n < CB->arg_size())
                    Dst.insert(Namer.of(CB->getArgOperand(n), CallerArgs));
              } else {
                Dst.insert(R);
              }
            }
          };
          Import(C.Reads, S.Reads);
          Import(C.Writes, S.Writes);
        }

        if (S.Total != Before || S.Reads.size() != BeforeR ||
            S.Writes.size() != BeforeW)
          Changed = true;
      }
    }

    markRecursion(Sum);

    // ---- 3. 输出 JSON ----
    std::string Buf;
    raw_string_ostream OS(Buf);
    OS << "{\n  \"version\": 23,\n  \"module\": \"" << M.getModuleIdentifier()
       << "\",\n  \"functions\": {\n";
    bool FirstFn = true;
    for (auto &KV : Sum) {
      const std::string &Name = KV.first;
      FnSummary &S = KV.second;
      if (!FirstFn)
        OS << ",\n";
      FirstFn = false;

      std::vector<std::string> Effs;
      if (S.Total & kMmio)
        Effs.push_back("mmio");
      if (S.Total & kSleep)
        Effs.push_back("sleep");
      if (S.Total & kBlockingIO)
        Effs.push_back("blocking_io");
      if (S.Total & kStateRead)
        Effs.push_back("state_read");
      if (S.Total & kStateWrite)
        Effs.push_back("state_write");
      if (S.Total & kExternal)
        Effs.push_back("external");
      if (S.Total & kUnknown)
        Effs.push_back("unknown");
      if (Effs.empty())
        Effs.push_back("pure");

      bool CanSleep = (S.Total & (kSleep | kBlockingIO)) != 0;
      OS << "    \"" << Name << "\": {\n";
      OS << "      \"effects\": [";
      for (size_t i = 0; i < Effs.size(); i++)
        OS << (i ? ", " : "") << "\"" << Effs[i] << "\"";
      OS << "],\n";
      OS << "      \"allowed_contexts\": ["
         << (CanSleep ? "\"thread\"" : "\"thread\", \"softirq\"") << "],\n";

      auto DumpSet = [&](const char *Key, const std::set<std::string> &Set) {
        OS << "      \"" << Key << "\": [";
        bool F2 = true;
        for (const std::string &E : Set) {
          OS << (F2 ? "" : ", ") << "\"" << E << "\"";
          F2 = false;
        }
        OS << "],\n";
      };
      DumpSet("reads", S.Reads);
      DumpSet("writes", S.Writes);

      OS << "      \"recursive\": " << (S.Recursive ? "true" : "false") << ",\n";
      OS << "      \"calls\": [";
      bool F2 = true;
      for (const std::string &C : S.Callees) {
        OS << (F2 ? "" : ", ") << "\"" << C << "\"";
        F2 = false;
      }
      OS << "],\n";
      OS << "      \"unknown_calls\": [";
      F2 = true;
      for (const std::string &C : S.UnknownCalls) {
        OS << (F2 ? "" : ", ") << "\"" << C << "\"";
        F2 = false;
      }
      OS << "]\n    }";
    }
    OS << "\n  },\n";

    // 调用点明细（供 DAG 侧递归解析相对名 arg:N）
    OS << "  \"callsites\": [";
    for (size_t i = 0; i < Sites.size(); i++) {
      const SiteRec &R = Sites[i];
      OS << (i ? ",\n" : "\n") << "    {\"caller\": \"" << R.Caller
         << "\", \"callee\": \"" << R.Callee << "\", \"args\": [";
      for (size_t k = 0; k < R.Args.size(); k++)
        OS << (k ? ", " : "") << "\"" << R.Args[k] << "\"";
      OS << "]}";
    }
    OS << "\n  ],\n";

    // 线程入口（pthread_create 的 entry 与它的 arg）
    OS << "  \"thread_entries\": [";
    for (size_t i = 0; i < ThreadEntries.size(); i++) {
      const SiteRec &R = ThreadEntries[i];
      OS << (i ? ",\n" : "\n") << "    {\"caller\": \"" << R.Caller
         << "\", \"entry\": \"" << R.Callee << "\", \"arg\": \""
         << (R.Args.empty() ? "-" : R.Args[0]) << "\"}";
    }
    OS << "\n  ],\n";

    // 接口绑定：形参 -> 调用点区域集合（长度 > 1 表示多态，不能具体化）
    OS << "  \"interface_bindings\": {";
    bool FirstCallee = true;
    for (auto &CV : Interface) {
      if (CV.second.empty())
        continue;
      OS << (FirstCallee ? "\n" : ",\n") << "    \"" << CV.first << "\": {";
      FirstCallee = false;
      bool FirstArg = true;
      for (auto &AV : CV.second) {
        OS << (FirstArg ? "" : ", ") << "\"" << AV.first << "\": [";
        FirstArg = false;
        bool F3 = true;
        for (const std::string &R : AV.second) {
          OS << (F3 ? "" : ", ") << "\"" << R << "\"";
          F3 = false;
        }
        OS << "]";
      }
      OS << "}";
    }
    OS << "\n  },\n";

    OS << "  \"alias_stats\": {"
       << "\"aa_available\": " << (AStat.Available ? "true" : "false")
       << ", \"pairs_checked\": " << AStat.Pairs
       << ", \"coarse_pairs\": " << AStat.Coarse
       << ", \"split_pairs\": " << AStat.Split
       << "}\n}\n";
    OS.flush();

    const char *Out = std::getenv("PLC_EFFECT_SUMMARY_OUT");
    if (Out && Out[0]) {
      std::error_code EC;
      raw_fd_ostream FO(Out, EC, sys::fs::OF_Text);
      if (EC) {
        errs() << "[EffectSummary] ERROR: 无法写 " << Out << ": "
               << EC.message() << "\n";
      } else {
        FO << Buf;
        errs() << "[EffectSummary] 写出 " << Out << "（" << Sum.size()
               << " 个函数）\n";
      }
    } else {
      outs() << Buf;
    }

    return PreservedAnalyses::all();
  }
};

} // namespace

extern "C" LLVM_ATTRIBUTE_WEAK PassPluginLibraryInfo llvmGetPassPluginInfo() {
  return {LLVM_PLUGIN_API_VERSION, "PLCEffectSummary", "0.2",
          [](PassBuilder &PB) {
            PB.registerPipelineParsingCallback(
                [](StringRef Name, ModulePassManager &MPM,
                   ArrayRef<PassBuilder::PipelineElement>) {
                  if (Name == "plc-effect-summary") {
                    MPM.addPass(EffectSummaryAnalysis());
                    return true;
                  }
                  return false;
                });
          }};
}
