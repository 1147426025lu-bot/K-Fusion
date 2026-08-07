/*
 * PLCFusionPass.cpp — LLVM NewPM 内核化 Pass（v3.6）
 *
 * 功能: 将用户态 C 的 LLVM IR 转为可链接的 freestanding 内核 .o
 *   - normalize: DSOLocal 全局/函数；thread_local → 普通全局（避免 TLS reloc）
 *   - fixed:     浮点 IR → Q 定点（Q16.16 / Q32.32，默认开启）
 *   - remap:     POSIX → plc_* 映射；pthread_create → 提升线程入口
 *   - dce:       从 PLC_FUSION_ROOTS / PLC_FUSION_HOT_PATH_FUNCTIONS 可达性裁剪
 *   - export:    保留 FUSE_GLOBALIZE_SYMBOLS / llvm.used
 *   - wcet-mark: 热函数 optnone（RTSS 2025 函数级 WCET 调优前置）
 *   - cleanup:   死 declare 清理；blackhole 未映射 external
 *
 * 可组合子 Pass: plc-fusion-{normalize,fixed,remap,dce,export,wcet-mark,cleanup,wcet-schedule}
 * 预设 pipeline: plc-kernelize-{mainline,generic,minimal,debug,size,hotpath,wcet}
 *
 * 环境: PLC_FUSION_FIXED_POINT, PLC_FUSION_DCE, PLC_FUSION_BLACKHOLE,
 *       PLC_FUSION_ROOTS,        PLC_FUSION_HOT_PATH_FUNCTIONS,
 *       PLC_FUSION_WCET_HOT_FUNCTIONS,
 *       PLC_FUSION_KEEP_GLOBALS, PLC_FUSION_UNMAPPED_LOG
 */
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Attributes.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "PLCFusionFixedPoint__定点Pass.h"
#include "KFusionWCETSchedulePass__函数级WCET.h"
#include <cstdio>
#include <cstdlib>
#include <set>
#include <string>
#include <vector>

using namespace llvm;

namespace {

struct RemapRule {
    StringRef From;
    StringRef To;
};

static bool envEnabled(const char *Name, bool Default) {
    const char *Val = std::getenv(Name);
    if (!Val || !Val[0])
        return Default;
    return Val[0] != '0';
}

static bool isPreservedExternal(StringRef Name) {
    if (Name.starts_with("llvm."))
        return true;
    static const char *kKeep[] = {
        "memset", "memcpy", "memmove",
        "strlen", "strcmp", "strncmp", "strncasecmp", "strncpy", "strnlen",
        "strerror", "__errno_location",
        "snprintf",
        "bsearch", "qsort", "lsearch",
        "rt_init", "check_privs", "sysconf", "__sysconf",
        "enable_trace_mark", "disable_trace_mark", "tracing_stop", "tracemark",
        "numa_initialize", "numa_sched_setaffinity", "numa_node_of_cpu",
        "numa_alloc_onnode", "numa_free", "numa_run_on_node",
        "numa_bitmask_weight", "numa_bitmask_isbitset", "numa_bitmask_free",
        "parse_time_string", "parse_cpumask", "get_available_cpus",
        "cpu_for_thread_sp", "cpu_for_thread_ua",
        "sched_get_priority_max", "getrlimit", "setrlimit",
        "usleep", "getopt_long", "optarg", "optind", "stdout", "stderr",
        "alarm", "pause", "sleep", "sched_yield",
        "atexit", "getenv",
        "strcpy", "strcat",
        "hist_init", "hist_init_oflow", "hist_destroy", "hist_sample",
        "hist_print_json", "hist_print_oflows",
        "hset_init", "hset_destroy", "hset_print_bucket",
        "rt_write_json", "get_tracefs_prefix",
        "__isoc23_strtol", "strtol",
        "setitimer", "setvbuf", "syscall",
        "pthread_attr_init", "pthread_attr_getstack", "pthread_attr_setstack",
        "plc_gpio_set", "plc_cycle", "plc_main", "plc_logic",
        "plc_fix_to_double",
        "stbsp_sprintf", "stbsp_snprintf", "stbsp_vsprintf",
        "stbsp_vsprintfcb", "stbsp_set_separators",
        "rt_periodic_record_worst",
    };
    for (const char *Sym : kKeep)
        if (Name == Sym)
            return true;
    return false;
}

static Function *getOrInsert(Module &M, StringRef Name, FunctionType *Ty) {
    return cast<Function>(M.getOrInsertFunction(Name, Ty).getCallee());
}

static Function *calleeFromValue(Value *V) {
    if (!V)
        return nullptr;
    V = V->stripPointerCasts();
    if (auto *F = dyn_cast<Function>(V))
        return F;
    if (auto *CE = dyn_cast<ConstantExpr>(V)) {
        if (CE->getOpcode() == Instruction::BitCast ||
            CE->getOpcode() == Instruction::AddrSpaceCast ||
            CE->getOpcode() == Instruction::GetElementPtr)
            return calleeFromValue(CE->getOperand(0));
    }
    return nullptr;
}

static Function *resolveIndirectCalleeImpl(Value *Op, unsigned Depth);

static Function *resolveFromConstant(Constant *C) {
    if (!C)
        return nullptr;
    if (Function *F = calleeFromValue(C))
        return F;
    if (auto *CE = dyn_cast<ConstantExpr>(C)) {
        if (CE->getOpcode() == Instruction::GetElementPtr)
            return resolveFromConstant(CE->getOperand(0));
        if (CE->getOpcode() == Instruction::BitCast ||
            CE->getOpcode() == Instruction::AddrSpaceCast)
            return resolveFromConstant(CE->getOperand(0));
    }
    if (auto *CA = dyn_cast<ConstantArray>(C)) {
        for (unsigned i = 0; i < CA->getNumOperands(); ++i) {
            if (Function *F = resolveFromConstant(CA->getOperand(i)))
                return F;
        }
    }
    if (auto *CS = dyn_cast<ConstantStruct>(C)) {
        for (unsigned i = 0; i < CS->getNumOperands(); ++i) {
            if (Function *F = resolveFromConstant(CS->getOperand(i)))
                return F;
        }
    }
    if (auto *CV = dyn_cast<ConstantVector>(C)) {
        for (unsigned i = 0; i < CV->getNumOperands(); ++i) {
            if (Function *F = resolveFromConstant(CV->getOperand(i)))
                return F;
        }
    }
    return nullptr;
}

// 解析 load @global → 常量函数指针（signal handler / 函数表等间接调用）
static Function *resolveIndirectCalleeImpl(Value *Op, unsigned Depth) {
    if (!Op || Depth > 8)
        return nullptr;
    if (Function *F = calleeFromValue(Op))
        return F;

    if (auto *Load = dyn_cast<LoadInst>(Op)) {
        auto *Ptr = Load->getPointerOperand();
        if (auto *GEP = dyn_cast<GetElementPtrInst>(Ptr))
            return resolveIndirectCalleeImpl(GEP, Depth + 1);
        if (auto *GV = dyn_cast<GlobalVariable>(Ptr))
            return resolveFromConstant(GV->getInitializer());
    }
    if (auto *GEP = dyn_cast<GetElementPtrInst>(Op))
        return resolveIndirectCalleeImpl(GEP->getPointerOperand(), Depth + 1);
    if (auto *Sel = dyn_cast<SelectInst>(Op)) {
        if (Function *T = resolveIndirectCalleeImpl(Sel->getTrueValue(), Depth + 1))
            return T;
        return resolveIndirectCalleeImpl(Sel->getFalseValue(), Depth + 1);
    }
    if (auto *Phi = dyn_cast<PHINode>(Op)) {
        for (Value *Incoming : Phi->incoming_values()) {
            if (Function *F = resolveIndirectCalleeImpl(Incoming, Depth + 1))
                return F;
        }
    }
    if (auto *Arg = dyn_cast<Argument>(Op)) {
        Function *Parent = Arg->getParent();
        if (Parent && Depth < 8) {
            unsigned ArgNo = Arg->getArgNo();
            for (User *U : Parent->users()) {
                if (auto *CB = dyn_cast<CallBase>(U)) {
                    if (CB->getCalledFunction() != Parent)
                        continue;
                    if (ArgNo >= CB->arg_size())
                        continue;
                    if (Function *F = resolveIndirectCalleeImpl(
                            CB->getArgOperand(ArgNo), Depth + 1))
                        return F;
                }
            }
        }
    }
    if (auto *BC = dyn_cast<BitCastInst>(Op))
        return resolveIndirectCalleeImpl(BC->getOperand(0), Depth + 1);
    return nullptr;
}

static Function *resolveIndirectCallee(Value *Op) {
    return resolveIndirectCalleeImpl(Op, 0);
}

static void collectGlobalFunctionPointerRoots(Module &M,
                                              SmallPtrSet<Function *, 32> &Live,
                                              SmallVector<Function *, 32> &Worklist) {
    for (GlobalVariable &GV : M.globals()) {
        if (!GV.hasInitializer())
            continue;
        if (Function *F = resolveFromConstant(GV.getInitializer())) {
            if (!F->isDeclaration() && Live.insert(F).second)
                Worklist.push_back(F);
        }
    }
}

static bool remapDirect(Module &M, CallBase *CB, Function *Callee, StringRef ToName)
{
    FunctionType *Ty = Callee->getFunctionType();
    CB->setCalledFunction(getOrInsert(M, ToName, Ty));
    return true;
}

static void promoteThreadEntry(CallBase *CB) {
    if (CB->arg_size() < 3)
        return;
    Value *FnVal = CB->getArgOperand(2)->stripPointerCasts();
    if (Function *TF = dyn_cast<Function>(FnVal))
        TF->setLinkage(GlobalValue::ExternalLinkage);
}

static void blackholeCall(CallBase *CB, bool &Changed) {
    if (!CB->getType()->isVoidTy()) {
        if (CB->getType()->isPointerTy())
            CB->replaceAllUsesWith(
                ConstantPointerNull::get(cast<PointerType>(CB->getType())));
        else if (CB->getType()->isIntegerTy())
            CB->replaceAllUsesWith(ConstantInt::get(CB->getType(), 0));
    }
    if (auto *Invoke = dyn_cast<InvokeInst>(CB)) {
        BranchInst::Create(Invoke->getNormalDest(), Invoke);
        Invoke->eraseFromParent();
    } else {
        CB->eraseFromParent();
    }
    Changed = true;
}

static std::set<std::string> gUnmappedLog;

static void resetUnmappedLog() {
    gUnmappedLog.clear();
    const char *Path = std::getenv("PLC_FUSION_UNMAPPED_LOG");
    if (Path && Path[0])
        std::remove(Path);
}

static void logUnmapped(StringRef Name) {
    if (gUnmappedLog.insert(Name.str()).second) {
        const char *Path = std::getenv("PLC_FUSION_UNMAPPED_LOG");
        if (!Path || !Path[0])
            return;
        FILE *F = std::fopen(Path, "a");
        if (F) {
            std::fprintf(F, "%s\n", Name.str().c_str());
            std::fclose(F);
        }
    }
}

static bool applyRemapRules(Module &M, CallBase *CB, Function *Callee,
                            StringRef Name, bool &Changed) {
        LLVMContext &Ctx = M.getContext();
        
    static const RemapRule kRemap[] = {
        {"printf", "plc_printk"},
        {"puts", "plc_puts"},
        {"fprintf", "plc_fprintf"},
        {"dprintf", "plc_dprintf"},
        {"warn", "plc_warn"},
        {"info", "plc_info"},
        {"fatal", "plc_fatal"},
        {"err_msg", "plc_warn"},
        {"err_msg_n", "plc_err_msg_n"},
        {"perror", "plc_perror"},
        {"clock_gettime", "plc_ktime_get_ts"},
        {"clock_getres", "plc_ktime_get_ts"},
        {"nanosleep", "plc_nanosleep"},
        {"clock_nanosleep", "plc_clock_nanosleep"},
        {"malloc", "plc_kmalloc"},
        {"calloc", "plc_kcalloc"},
        {"realloc", "plc_krealloc"},
        {"strdup", "plc_kstrdup"},
        {"free", "plc_kfree"},
        {"timer_create", "plc_timer_create"},
        {"timer_settime", "plc_timer_settime"},
        {"timer_getoverrun", "plc_timer_getoverrun"},
        {"timer_delete", "plc_timer_delete"},
        {"sigemptyset", "plc_sigemptyset"},
        {"sigaddset", "plc_sigaddset"},
        {"sigprocmask", "plc_sigprocmask"},
        {"sigwait", "plc_sigwait"},
        {"signal", "plc_signal"},
        {"sigaction", "plc_sigaction"},
        {"__assert_fail", "plc_assert_fail"},
        {"pthread_self", "plc_pthread_self"},
        {"pthread_setaffinity_np", "plc_pthread_setaffinity_np"},
        {"pthread_create", "plc_pthread_create"},
        {"pthread_join", "plc_pthread_join"},
        {"pthread_kill", "plc_pthread_kill"},
        {"pthread_mutex_init", "plc_mutex_init"},
        {"pthread_mutex_destroy", "plc_mutex_destroy"},
        {"pthread_sigmask", "plc_sigprocmask"},
        {"gettid", "plc_gettid"},
        {"sched_setscheduler", "plc_setscheduler"},
        {"sched_setaffinity", "plc_sched_setaffinity"},
        {"gettimeofday", "plc_gettimeofday"},
        {"pthread_mutex_lock", "plc_mutex_lock"},
        {"pthread_mutex_unlock", "plc_mutex_unlock"},
        {"pthread_cond_wait", "plc_cond_wait"},
        {"pthread_cond_signal", "plc_cond_signal"},
        {"pthread_cond_broadcast", "plc_cond_broadcast"},
        {"pthread_cond_timedwait", "plc_cond_timedwait"},
        {"pthread_barrier_init", "plc_barrier_init"},
        {"pthread_barrier_wait", "plc_barrier_wait"},
        {"mlockall", "plc_mlockall"},
        {"munlockall", "plc_munlockall"},
        {"mlock", "plc_mlock"},
        {"getpid", "plc_getpid"},
        {"open", "plc_open"},
        {"read", "plc_read"},
        {"write", "plc_write"},
        {"close", "plc_close"},
        {"mmap", "plc_mmap"},
        {"munmap", "plc_munmap"},
        {"shm_open", "plc_shm_open"},
        {"shm_unlink", "plc_shm_unlink"},
        {"lseek", "plc_lseek"},
        {"ftruncate", "plc_ftruncate"},
        {"stat", "plc_stat"},
        {"unlink", "plc_unlink"},
        {"mkfifo", "plc_mkfifo"},
        {"fopen", "plc_fopen"},
        {"fclose", "plc_fclose"},
        {"fdopen", "plc_fdopen"},
        {"vfprintf", "plc_fprintf"},
        {"vprintf", "plc_printk"},
        {"vsnprintf", "plc_snprintf"},
        {"fflush", "plc_fflush"},
        {"fsync", "plc_fsync"},
        {"pthread_detach", "plc_pthread_detach"},
        {"pthread_attr_setdetachstate", "plc_pthread_attr_setdetachstate"},
        {"pthread_attr_setinheritsched", "plc_pthread_attr_setinheritsched"},
        {"pthread_attr_setschedpolicy", "plc_pthread_attr_setschedpolicy"},
        {"sem_init", "plc_sem_init"},
        {"sem_destroy", "plc_sem_destroy"},
        {"sem_wait", "plc_sem_wait"},
        {"sem_post", "plc_sem_post"},
        {"sem_timedwait", "plc_sem_timedwait"},
        {"sem_getvalue", "plc_sem_getvalue"},
        {"sched_getparam", "plc_sched_getparam"},
        {"sched_getscheduler", "plc_sched_getscheduler"},
    };

    for (const RemapRule &Rule : kRemap) {
        if (Name != Rule.From)
            continue;
        if (Name == "malloc") {
            FunctionType *KmallocTy = FunctionType::get(
                Type::getInt8PtrTy(Ctx), {Type::getInt64Ty(Ctx)}, false);
            IRBuilder<> Builder(CB);
            CallInst *NewCall = Builder.CreateCall(
                getOrInsert(M, Rule.To, KmallocTy),
                {Builder.CreateZExtOrTrunc(CB->getArgOperand(0),
                                           Type::getInt64Ty(Ctx))});
            CB->replaceAllUsesWith(NewCall);
            if (auto *Invoke = dyn_cast<InvokeInst>(CB))
                BranchInst::Create(Invoke->getNormalDest(), Invoke);
            CB->eraseFromParent();
            Changed = true;
            return true;
        }
        if (Name == "realloc") {
            FunctionType *KreallocTy = FunctionType::get(
                Type::getInt8PtrTy(Ctx),
                {Type::getInt8PtrTy(Ctx), Type::getInt64Ty(Ctx)}, false);
            IRBuilder<> Builder(CB);
            CallInst *NewCall = Builder.CreateCall(
                getOrInsert(M, Rule.To, KreallocTy),
                {CB->getArgOperand(0),
                 Builder.CreateZExtOrTrunc(CB->getArgOperand(1),
                                            Type::getInt64Ty(Ctx))});
            CB->replaceAllUsesWith(NewCall);
            if (auto *Invoke = dyn_cast<InvokeInst>(CB))
                BranchInst::Create(Invoke->getNormalDest(), Invoke);
            CB->eraseFromParent();
            Changed = true;
            return true;
        }
        if (Name == "pthread_create")
            promoteThreadEntry(CB);
        remapDirect(M, CB, Callee, Rule.To);
        Changed = true;
        return true;
    }

    if (Name == "exit" || Name == "abort" || Name == "_exit" || Name == "raise") {
        FunctionType *Ty = FunctionType::get(Type::getVoidTy(Ctx),
                                             {Type::getInt32Ty(Ctx)}, false);
        if (Name == "exit" || Name == "_exit") {
            CB->setCalledFunction(getOrInsert(M, "plc_exit", Ty));
        } else {
            IRBuilder<> Builder(CB);
            int Code = (Name == "raise") ? 1 : 134;
            Builder.CreateCall(getOrInsert(M, "plc_exit", Ty),
                               {ConstantInt::get(Type::getInt32Ty(Ctx), Code)});
            if (auto *Invoke = dyn_cast<InvokeInst>(CB))
                BranchInst::Create(Invoke->getNormalDest(), Invoke);
            CB->eraseFromParent();
        }
        Changed = true;
        return true;
    }
    return false;
}

static bool handleCallBase(Module &M, CallBase *CB, bool &Changed, bool Blackhole) {
    Function *Callee = CB->getCalledFunction();
    if (!Callee)
        Callee = resolveIndirectCallee(CB->getCalledOperand());

    if (!Callee) {
        if (Blackhole) {
            logUnmapped("indirect:unresolved");
            blackholeCall(CB, Changed);
            return true;
        }
        return false;
    }

    if (!Callee->isDeclaration())
        return false;

    StringRef Name = Callee->getName();
    if (isPreservedExternal(Name))
        return false;
    if (Name.starts_with("plc_"))
        return false;

    if (applyRemapRules(M, CB, Callee, Name, Changed))
        return true;

    logUnmapped(Name);
    if (!Blackhole)
        return false;

    blackholeCall(CB, Changed);
    return true;
}

static void preserveExportedGlobals(Module &M, bool &Changed) {
    const char *Env = std::getenv("PLC_FUSION_KEEP_GLOBALS");
    if (!Env || !Env[0])
        return;

    SmallVector<Constant *, 8> Used;
    SmallVector<StringRef, 8> Names;
    StringRef(Env).split(Names, ',', -1, false);

    for (StringRef N : Names) {
        N = N.trim();
        if (N.empty())
            continue;
        GlobalVariable *G = M.getGlobalVariable(N);
        if (!G)
            continue;
        G->setLinkage(GlobalValue::ExternalLinkage);
        Used.push_back(G);
        Changed = true;
    }

    if (Used.empty())
        return;

    LLVMContext &Ctx = M.getContext();
    ArrayType *ArrTy = ArrayType::get(Type::getInt8PtrTy(Ctx), Used.size());
    Constant *Arr = ConstantArray::get(ArrTy, Used);
    if (!M.getGlobalVariable("llvm.used")) {
        GlobalVariable *LLVMUsed = new GlobalVariable(
            M, ArrTy, false, GlobalValue::AppendingLinkage, Arr, "llvm.used");
        LLVMUsed->setSection("llvm.metadata");
    }
}

static void removeDeadDeclarations(Module &M, bool &Changed) {
    std::vector<Function *> Dead;
    for (Function &F : M)
        if (F.isDeclaration() && F.use_empty())
            Dead.push_back(&F);
    for (Function *F : Dead) {
        F->eraseFromParent();
        Changed = true;
    }
}

static void collectRoots(Module &M, SmallPtrSet<Function *, 32> &Live,
                         SmallVector<Function *, 32> &Worklist) {
    auto addRoot = [&](StringRef Name) {
        Name = Name.trim();
        if (Name.empty())
            return;
        Function *F = M.getFunction(Name);
        if (!F || F->isDeclaration())
            return;
        if (Live.insert(F).second)
            Worklist.push_back(F);
    };

    auto addRootsFromEnv = [&](const char *Var) {
        if (!Var)
            return;
        const char *Env = std::getenv(Var);
        if (!Env)
            return;
        SmallVector<StringRef, 8> Parts;
        StringRef(Env).split(Parts, ',', -1, false);
        for (StringRef P : Parts)
            addRoot(P);
    };
    addRootsFromEnv("PLC_FUSION_ROOTS");
    addRootsFromEnv("PLC_FUSION_HOT_PATH_FUNCTIONS");
    collectGlobalFunctionPointerRoots(M, Live, Worklist);

    auto promoteCallee = [&](CallBase *CB, unsigned ArgIdx) {
        if (CB->arg_size() <= ArgIdx)
            return;
        if (Function *TF = resolveIndirectCallee(CB->getArgOperand(ArgIdx))) {
            TF->setLinkage(GlobalValue::ExternalLinkage);
            if (Live.insert(TF).second)
                Worklist.push_back(TF);
        }
    };

    for (Function &F : M) {
        for (BasicBlock &BB : F) {
            for (Instruction &I : BB) {
                auto *CB = dyn_cast<CallBase>(&I);
                if (!CB)
                    continue;
                Function *Target = CB->getCalledFunction();
                if (!Target)
                    Target = resolveIndirectCallee(CB->getCalledOperand());
                if (!Target)
                    continue;
                StringRef TName = Target->getName();
                if (TName == "plc_pthread_create")
                    promoteCallee(CB, 2);
                else if (TName == "plc_signal")
                    promoteCallee(CB, 1);
                else if (TName == "plc_sigaction" && CB->arg_size() >= 2)
                    promoteCallee(CB, 1);
            }
        }
    }
}

static void markFunctionsReferencedBy(Function *F, SmallPtrSet<Function *, 32> &Live,
                                      SmallVector<Function *, 32> &Worklist) {
    for (BasicBlock &BB : *F) {
        for (Instruction &I : BB) {
            for (Use &U : I.operands()) {
                if (Function *RF = calleeFromValue(U.get())) {
                    if (!RF->isDeclaration() && Live.insert(RF).second)
                        Worklist.push_back(RF);
                }
            }
        }
    }
}

static void markReachable(Module &M, bool &Changed) {
    if (!envEnabled("PLC_FUSION_DCE", true))
        return;

    SmallPtrSet<Function *, 32> Live;
    SmallVector<Function *, 32> Worklist;
    collectRoots(M, Live, Worklist);

    if (Live.empty())
        return;

    while (!Worklist.empty()) {
        Function *F = Worklist.pop_back_val();
        for (BasicBlock &BB : *F) {
            for (Instruction &I : BB) {
                if (auto *CB = dyn_cast<CallBase>(&I)) {
                    if (Function *CF =
                            resolveIndirectCallee(CB->getCalledOperand())) {
                        if (!CF->isDeclaration() && Live.insert(CF).second)
                            Worklist.push_back(CF);
                    }
                }
            }
        }
        markFunctionsReferencedBy(F, Live, Worklist);
    }

    std::vector<Function *> Dead;
    for (Function &F : M) {
        if (F.isDeclaration() || Live.count(&F))
            continue;
        Dead.push_back(&F);
    }
    for (Function *F : Dead) {
        F->eraseFromParent();
        Changed = true;
    }
}

static bool runNormalize(Module &M) {
    bool Changed = false;
    for (GlobalVariable &G : M.globals()) {
        if (!G.isDSOLocal()) {
            G.setDSOLocal(true);
            Changed = true;
        }
        if (G.isThreadLocal()) {
            G.setThreadLocal(false);
            Changed = true;
        }
    }
    for (Function &F : M) {
        if (!F.isDSOLocal()) {
            F.setDSOLocal(true);
            Changed = true;
        }
        for (BasicBlock &BB : F) {
            for (auto I = BB.begin(), E = BB.end(); I != E;) {
                Instruction *Inst = &*I++;
                auto *CB = dyn_cast<CallBase>(Inst);
                if (!CB)
                    continue;
                Function *Callee = CB->getCalledFunction();
                if (!Callee)
                    Callee = resolveIndirectCallee(CB->getCalledOperand());
                if (!Callee)
                    continue;
                if (!Callee->getName().starts_with("llvm.threadlocal.address"))
                    continue;
                Value *TlsBase = CB->getArgOperand(0);
                CB->replaceAllUsesWith(TlsBase);
                CB->eraseFromParent();
                Changed = true;
            }
        }
    }
    return Changed;
}

static bool runFixedPoint(Module &M) { return runFixedPointConvert(M); }

static bool runRemap(Module &M) {
    bool Changed = false;
    const bool Blackhole = envEnabled("PLC_FUSION_BLACKHOLE", true);
    std::vector<CallBase *> Calls;

    for (Function &F : M)
        for (BasicBlock &BB : F)
            for (Instruction &I : BB)
                if (auto *CB = dyn_cast<CallBase>(&I))
                    Calls.push_back(CB);

    for (CallBase *CB : Calls) {
        if (!CB->getParent())
            continue;
        handleCallBase(M, CB, Changed, Blackhole);
    }
    return Changed;
}

static bool runDCE(Module &M) {
    bool Changed = false;
    markReachable(M, Changed);
    return Changed;
}

static bool runExport(Module &M) {
    bool Changed = false;
    preserveExportedGlobals(M, Changed);
    return Changed;
}

static bool runCleanup(Module &M) {
    bool Changed = false;
    removeDeadDeclarations(M, Changed);
    return Changed;
}

// RTSS 2025 风格：热路径函数 optnone，冷路径留给后续 LLVM loop/instcombine
static bool runWCETMark(Module &M) {
    const char *Env = std::getenv("PLC_FUSION_WCET_HOT_FUNCTIONS");
    if (!Env || !Env[0])
        Env = std::getenv("PLC_FUSION_HOT_PATH_FUNCTIONS");
    if (!Env || !Env[0])
        return false;

    bool Changed = false;
    SmallVector<StringRef, 8> Names;
    StringRef(Env).split(Names, ',', -1, false);
    for (StringRef N : Names) {
        N = N.trim();
        if (N.empty())
            continue;
        Function *F = M.getFunction(N);
        if (!F || F->isDeclaration())
            continue;
        F->addFnAttr(Attribute::NoInline);
        F->addFnAttr(Attribute::OptimizeNone);
        F->addFnAttr("plc-wcet-hot", "true");
        Changed = true;
    }
    return Changed;
}

struct PLCFusionNormalizePass : public PassInfoMixin<PLCFusionNormalizePass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        resetUnmappedLog();
        return runNormalize(M) ? PreservedAnalyses::none()
                             : PreservedAnalyses::all();
    }
};

struct PLCFusionFixedPass : public PassInfoMixin<PLCFusionFixedPass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        return runFixedPoint(M) ? PreservedAnalyses::none()
                                : PreservedAnalyses::all();
    }
};

struct PLCFusionRemapPass : public PassInfoMixin<PLCFusionRemapPass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        return runRemap(M) ? PreservedAnalyses::none()
                           : PreservedAnalyses::all();
    }
};

struct PLCFusionDCEPass : public PassInfoMixin<PLCFusionDCEPass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        return runDCE(M) ? PreservedAnalyses::none() : PreservedAnalyses::all();
    }
};

struct PLCFusionExportPass : public PassInfoMixin<PLCFusionExportPass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        return runExport(M) ? PreservedAnalyses::none() : PreservedAnalyses::all();
    }
};

struct PLCFusionCleanupPass : public PassInfoMixin<PLCFusionCleanupPass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        return runCleanup(M) ? PreservedAnalyses::none()
                             : PreservedAnalyses::all();
    }
};

struct PLCFusionWCETMarkPass : public PassInfoMixin<PLCFusionWCETMarkPass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        return runWCETMark(M) ? PreservedAnalyses::none()
                              : PreservedAnalyses::all();
    }
};

static void addKernelizeMainline(ModulePassManager &MPM) {
    MPM.addPass(PLCFusionNormalizePass());
    MPM.addPass(PLCFusionFixedPass());
    MPM.addPass(PLCFusionRemapPass());
    MPM.addPass(PLCFusionDCEPass());
    MPM.addPass(PLCFusionFixedPass());
    MPM.addPass(PLCFusionExportPass());
    MPM.addPass(PLCFusionCleanupPass());
}

static void addKernelizeGeneric(ModulePassManager &MPM) {
    addKernelizeMainline(MPM);
}

static void addKernelizeMinimal(ModulePassManager &MPM) {
    MPM.addPass(PLCFusionNormalizePass());
    MPM.addPass(PLCFusionRemapPass());
    MPM.addPass(PLCFusionCleanupPass());
}

static void addKernelizeDebug(ModulePassManager &MPM) {
    MPM.addPass(PLCFusionNormalizePass());
    if (envEnabled("PLC_FUSION_FIXED_POINT", true))
        MPM.addPass(PLCFusionFixedPass());
    MPM.addPass(PLCFusionRemapPass());
    MPM.addPass(PLCFusionExportPass());
    MPM.addPass(PLCFusionCleanupPass());
    if (envEnabled("PLC_FUSION_FIXED_POINT", true))
        MPM.addPass(PLCFusionFixedPass());
}

static void addKernelizeHotpath(ModulePassManager &MPM) {
    addKernelizeMainline(MPM);
}

static void addKernelizeWCET(ModulePassManager &MPM) {
    addKernelizeMainline(MPM);
    MPM.addPass(PLCFusionWCETMarkPass());
}

struct PLCFusionPass : public PassInfoMixin<PLCFusionPass> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
        resetUnmappedLog();
        bool Changed = false;
        Changed |= runNormalize(M);
        Changed |= runFixedPoint(M);
        Changed |= runRemap(M);
        Changed |= runDCE(M);
        Changed |= runExport(M);
        Changed |= runNormalize(M);
        Changed |= runCleanup(M);
        return Changed ? PreservedAnalyses::none() : PreservedAnalyses::all();
    }
};

} // namespace

extern "C" LLVM_ATTRIBUTE_WEAK ::llvm::PassPluginLibraryInfo
llvmGetPassPluginInfo() {
    return {LLVM_PLUGIN_API_VERSION, "PLCFusion", "v3.5",
            [](PassBuilder &PB) {
                registerKFusionWCETSchedulePipeline(PB);
                PB.registerPipelineParsingCallback(
                    [](StringRef Name, ModulePassManager &MPM,
                       ArrayRef<PassBuilder::PipelineElement>) {
                        if (Name == "plc-fusion") {
                            MPM.addPass(PLCFusionPass());
                            return true;
                        }
                        if (Name == "plc-fusion-normalize") {
                            MPM.addPass(PLCFusionNormalizePass());
                            return true;
                        }
                        if (Name == "plc-fusion-fixed" || Name == "plc-fusion-float") {
                            MPM.addPass(PLCFusionFixedPass());
                            return true;
                        }
                        if (Name == "plc-fusion-remap") {
                            MPM.addPass(PLCFusionRemapPass());
                            return true;
                        }
                        if (Name == "plc-fusion-dce") {
                            MPM.addPass(PLCFusionDCEPass());
                            return true;
                        }
                        if (Name == "plc-fusion-export") {
                            MPM.addPass(PLCFusionExportPass());
                            return true;
                        }
                        if (Name == "plc-fusion-cleanup") {
                            MPM.addPass(PLCFusionCleanupPass());
                            return true;
                        }
                        if (Name == "plc-fusion-wcet-mark") {
                            MPM.addPass(PLCFusionWCETMarkPass());
                            return true;
                        }
                        if (Name == "plc-kernelize-mainline") {
                            addKernelizeMainline(MPM);
                            return true;
                        }
                        if (Name == "plc-kernelize-generic") {
                            addKernelizeGeneric(MPM);
                            return true;
                        }
                        if (Name == "plc-kernelize-minimal") {
                            addKernelizeMinimal(MPM);
                            return true;
                        }
                        if (Name == "plc-kernelize-debug") {
                            addKernelizeDebug(MPM);
                            return true;
                        }
                        if (Name == "plc-kernelize-size") {
                            addKernelizeMainline(MPM);
                            return true;
                        }
                        if (Name == "plc-kernelize-hotpath") {
                            addKernelizeHotpath(MPM);
                            return true;
                        }
                        if (Name == "plc-kernelize-wcet") {
                            addKernelizeWCET(MPM);
                            return true;
                        }
                    return false;
                });
            }};
}
