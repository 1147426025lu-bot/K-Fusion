#include "llvm/IR/PassManager.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/Attributes.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

namespace {

static void markLowJitterFunction(Function *F) {
    if (!F || F->isDeclaration())
        return;
    F->addFnAttr(Attribute::NoInline);
    F->addFnAttr(Attribute::OptimizeNone);
    F->addFnAttr("plc-low-jitter", "true");
    errs() << "[PLCLowJitter] 锁定实时任务函数: " << F->getName() << "\n";
}

static void markFromEnv(Module &M, const char *Var) {
    const char *Env = std::getenv(Var);
    if (!Env || !Env[0])
        return;

    SmallVector<StringRef, 8> Names;
    StringRef(Env).split(Names, ',', -1, false);
    for (StringRef N : Names) {
        N = N.trim();
        if (N.empty())
            continue;
        markLowJitterFunction(M.getFunction(N));
    }
}

class PLCLowJitterPass : public PassInfoMixin<PLCLowJitterPass> {
public:
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &AM) {
        bool Changed = false;
        auto *GlobalAnnots = M.getNamedGlobal("llvm.global.annotations");

        if (GlobalAnnots) {
            auto *Annots = cast<ConstantArray>(GlobalAnnots->getInitializer());
            for (unsigned i = 0; i < Annots->getNumOperands(); ++i) {
                auto *Struct = cast<ConstantStruct>(Annots->getOperand(i));
                Value *Entity = Struct->getOperand(0)->stripPointerCasts();

                if (auto *F = dyn_cast<Function>(Entity)) {
                    auto *AnnotGV =
                        cast<GlobalVariable>(Struct->getOperand(1)->stripPointerCasts());
                    auto *AnnotData =
                        cast<ConstantDataArray>(AnnotGV->getInitializer());
                    StringRef AnnotStr = AnnotData->getAsCString();

                    if (AnnotStr == "plc_rt_task") {
                        markLowJitterFunction(F);
                        Changed = true;
                    }
                }
            }
        }

        static const char *kPlcEntryNames[] = {"plc_cycle", "plc_main",
                                               "plc_logic", nullptr};
        for (const char **P = kPlcEntryNames; *P; ++P) {
            if (Function *F = M.getFunction(*P)) {
                if (!F->isDeclaration()) {
                    markLowJitterFunction(F);
                    Changed = true;
                }
            }
        }

        markFromEnv(M, "PLC_FUSION_LOW_JITTER_FUNCTIONS");
        markFromEnv(M, "PLC_FUSION_HOT_PATH_FUNCTIONS");
        markFromEnv(M, "PLC_FUSION_WCET_HOT_FUNCTIONS");

        return Changed ? PreservedAnalyses::none() : PreservedAnalyses::all();
    }
};
}

extern "C" [[gnu::visibility("default")]] LLVM_ATTRIBUTE_WEAK ::llvm::PassPluginLibraryInfo
llvmGetPassPluginInfo() {
    return {LLVM_PLUGIN_API_VERSION, "PLCLowJitter", LLVM_VERSION_STRING,
            [](PassBuilder &PB) {
                PB.registerPipelineParsingCallback(
                    [](StringRef Name, ModulePassManager &MPM,
                       ArrayRef<PassBuilder::PipelineElement>) {
                        if (Name == "plc-low-jitter") {
                            MPM.addPass(PLCLowJitterPass());
                            return true;
                        }
                        return false;
                    });
            }};
}
