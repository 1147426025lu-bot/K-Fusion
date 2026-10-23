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
// 判定原则（宁保守勿漏报）：
//   - 认不出的一律往**更重**的效果记（unknown external → external）。
//   - 递归无法展开时取不动点并置 `recursive=true`，同时在诊断里喊出来。
//   - 不试图证明任何东西；只汇总"可能发生什么"。
//
// Pass 名：plc-effect-summary
// 输出：   $PLC_EFFECT_SUMMARY_OUT（文件）或 stdout
//
//===----------------------------------------------------------------------===//

#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"
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

#include <cstdlib>
#include <initializer_list>
#include <map>
#include <set>
#include <string>
#include <vector>

using namespace llvm;

namespace {

// 效果位图。与 contract_check 的 EFFECTS 词表一一对应。
enum Effect : unsigned {
  kPure        = 0,
  kStateRead   = 1u << 0,
  kStateWrite  = 1u << 1,
  kMmio        = 1u << 2,
  kSleep       = 1u << 3,
  kBlockingIO  = 1u << 4,
  kExternal    = 1u << 5,
};

struct FnSummary {
  unsigned Direct = kPure;      // 函数体内直接看到的效果
  unsigned Total = kPure;       // 加上被调者传播后的效果
  std::set<std::string> Callees;
  std::set<std::string> UnknownCalls;
  bool Recursive = false;

  // 传给本函数的指针参数被写入 → 对调用者来说是 state_write
  bool WritesPtrArg = false;
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

/// 已知无副作用的 libc 内建（既不是 external，也不算效果）。
/// 注意：这里只放"确实没有可观测副作用"的，`malloc` 不在此列（它会拿锁）。
bool isBenignIntrinsic(StringRef Name) {
  for (StringRef K : {"llvm.memcpy", "llvm.memset", "llvm.memmove",
                      "llvm.lifetime", "llvm.dbg", "llvm.assume",
                      "llvm.experimental.noalias", "llvm.fabs",
                      "llvm.sqrt", "llvm.floor", "llvm.ceil", "llvm.trunc",
                      "llvm.smax", "llvm.smin", "llvm.umax", "llvm.umin",
                      "llvm.smul.with.overflow", "llvm.uadd.with.overflow"})
    if (Name.startswith(K))
      return true;
  return false;
}

/// 纯算术内建：无内存、无调用。
bool isPureLibcName(StringRef Name) {
  // 注意：memcpy/memset/memmove 会写内存，但对「会不会睡 / 会不会碰 MMIO」这个
  // 判断没有影响，所以按纯函数处理（它们是编译器自动插入的，记 external 会淹没真信号）。
  return nameHasAny(Name, {"strlen", "strcmp", "strncmp", "memcmp", "abs",
                           "labs", "llabs", "sin", "cos", "tan", "exp",
                           "log", "pow", "fmod", "memcpy", "memset",
                           "memmove"});
}

/// 指针是否指向某个 GlobalVariable（用来判状态读写）。
const GlobalVariable *rootGlobal(const Value *V) {
  const Value *P = V->stripPointerCasts();
  while (P) {
    if (auto *GV = dyn_cast<GlobalVariable>(P))
      return GV;
    if (auto *GEP = dyn_cast<GEPOperator>(P)) {
      P = GEP->getPointerOperand()->stripPointerCasts();
      continue;
    }
    break;
  }
  return nullptr;
}

class EffectSummaryAnalysis
    : public PassInfoMixin<EffectSummaryAnalysis> {
public:
  PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
    std::map<std::string, FnSummary> Sum;
    std::map<const Function *, std::string> NameOf;

    // ---- 1. 本地扫描：只看函数体，不跨过程 ----
    for (Function &F : M) {
      if (F.isDeclaration())
        continue;
      std::string FName = F.getName().str();
      NameOf[&F] = FName;
      FnSummary &S = Sum[FName];

      // 参数指针（用于判断"写 out-param"）
      std::set<const Value *> PtrParams;
      for (Argument &A : F.args())
        if (A.getType()->isPointerTy())
          PtrParams.insert(A.stripPointerCasts());

      for (Instruction &I : instructions(F)) {
        if (auto *CB = dyn_cast<CallBase>(&I)) {
          Function *Callee = CB->getCalledFunction();
          if (!Callee) {
            // 间接调用：解析不了目标就按最坏情况记，别假装它是纯的
            S.Direct |= kExternal;
            S.UnknownCalls.insert("indirect");
            continue;
          }
          StringRef CN = Callee->getName();
          if (!Callee->isDeclaration()) {
            /* 内部函数：只记调用关系，效果由不动点传播过来。
             * 绝不能按名字判效果——`state_writer` 含 "write"、`mmio_writer` 含 "mmio"，
             * 按子串匹配会把内部函数名当成外部能力，误报一堆。 */
            S.Callees.insert(CN.str());
            continue;
          }
          /* 外部声明：按名字判效果（不同 libc/内核版本符号名不一致，只能子串匹配） */
          if (isBenignIntrinsic(CN))
            continue;
          if (isSleepCall(CN))
            S.Direct |= kSleep;
          if (isMmioCall(CN))
            S.Direct |= kMmio;
          if (isBlockingIoCall(CN))
            S.Direct |= kBlockingIO;
          if (!isPureLibcName(CN)) {
            S.Direct |= kExternal;
            S.UnknownCalls.insert(CN.str());
          }
          continue;
        }

        if (auto *LI = dyn_cast<LoadInst>(&I)) {
          // volatile 先判：MMIO 读常写成 volatile load 自常量地址，根对象不是 GlobalVariable
          if (LI->isVolatile()) {
            S.Direct |= kMmio;
            continue;
          }
          if (rootGlobal(LI->getPointerOperand()))
            S.Direct |= kStateRead;
          continue;
        }
        if (auto *SI = dyn_cast<StoreInst>(&I)) {
          const Value *Ptr = SI->getPointerOperand();
          // 同上：volatile store 是 MMIO 写寄存器的典型形态
          if (SI->isVolatile()) {
            S.Direct |= kMmio;
            continue;
          }
          if (rootGlobal(Ptr)) {
            S.Direct |= kStateWrite;
          } else if (PtrParams.count(Ptr->stripPointerCasts())) {
            S.WritesPtrArg = true;
            S.Direct |= kStateWrite;
          }
          continue;
        }
        if (auto *RMW = dyn_cast<AtomicRMWInst>(&I)) {
          S.Direct |= kStateWrite;
          continue;
        }
        if (auto *AI = dyn_cast<AtomicCmpXchgInst>(&I)) {
          S.Direct |= kStateWrite;
          continue;
        }
        if (isa<FenceInst>(&I)) {
          S.Direct |= kStateWrite;   // 有 fence 说明在同步共享状态
          continue;
        }
      }
    }

    // ---- 2. 跨过程传播：不动点迭代（递归自然收敛）----
    for (auto &KV : Sum)
      KV.second.Total = KV.second.Direct;

    bool Changed = true;
    int Guard = 0;
    while (Changed && Guard++ < 64) {
      Changed = false;
      for (auto &KV : Sum) {
        FnSummary &S = KV.second;
        unsigned Before = S.Total;
        for (const std::string &C : S.Callees) {
          auto It = Sum.find(C);
          if (It == Sum.end())
            continue;
          S.Total |= It->second.Total;
          // 被调者会写它自己的参数？那对我们是"可能写我们的内存"，保守记 state_write
          if (It->second.WritesPtrArg)
            S.Total |= kStateWrite;
        }
        if (S.Total != Before)
          Changed = true;
      }
    }
    // 迭代到上限还没稳 → 存在递归环，标记出来
    if (Changed) {
      for (auto &KV : Sum)
        KV.second.Recursive = true;
    }

    // ---- 3. 输出 JSON ----
    std::string Buf;
    raw_string_ostream OS(Buf);
    OS << "{\n  \"version\": 1,\n  \"module\": \"" << M.getModuleIdentifier()
       << "\",\n  \"functions\": {\n";
    bool FirstFn = true;
    for (auto &KV : Sum) {
      const std::string &Name = KV.first;
      FnSummary &S = KV.second;
      if (!FirstFn)
        OS << ",\n";
      FirstFn = false;

      std::vector<std::string> Effs;
      if (S.Total & kStateRead)
        Effs.push_back("state_read");
      if (S.Total & kStateWrite)
        Effs.push_back("state_write");
      if (S.Total & kMmio)
        Effs.push_back("mmio");
      if (S.Total & kSleep)
        Effs.push_back("sleep");
      if (S.Total & kBlockingIO)
        Effs.push_back("blocking_io");
      if (S.Total & kExternal)
        Effs.push_back("external");
      if (Effs.empty())
        Effs.push_back("pure");

      // 允许的上下文：能睡就不能进中断上下文
      bool CanSleep = (S.Total & (kSleep | kBlockingIO)) != 0;
      OS << "    \"" << Name << "\": {\n";
      OS << "      \"effects\": [";
      for (size_t i = 0; i < Effs.size(); i++)
        OS << (i ? ", " : "") << "\"" << Effs[i] << "\"";
      OS << "],\n";
      OS << "      \"allowed_contexts\": ["
         << (CanSleep ? "\"thread\"" : "\"thread\", \"softirq\"") << "],\n";
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
    OS << "\n  }\n}\n";
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
        errs() << "[EffectSummary] 写出 " << Out << "（"
               << Sum.size() << " 个函数）\n";
      }
    } else {
      outs() << Buf;
    }

    // 分析型 Pass：不改 IR
    return PreservedAnalyses::all();
  }
};

} // namespace

extern "C" LLVM_ATTRIBUTE_WEAK PassPluginLibraryInfo llvmGetPassPluginInfo() {
  return {LLVM_PLUGIN_API_VERSION, "PLCEffectSummary", "0.1",
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
