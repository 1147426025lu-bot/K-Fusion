/*
 * KFusionWCETSchedulePass — Lavinium 风格函数级冷路径 LLVM 优化
 */
#include "KFusionWCETSchedulePass__函数级WCET.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/Module.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Support/Error.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"
#include <cstdlib>
#include <string>
#include <unordered_map>
#include <vector>

using namespace llvm;

static std::string wcetSchedulePath() {
    const char *Env = std::getenv("PLC_FUSION_WCET_SCHEDULE_FILE");
    return Env ? std::string(Env) : std::string();
}

static bool loadWcetSchedule(
    StringRef Path,
    std::unordered_map<std::string, std::vector<std::string>> &Cold,
    std::vector<std::string> &ModulePasses) {
    auto Buf = MemoryBuffer::getFile(Path);
    if (!Buf)
        return false;
    Expected<json::Value> Parsed = json::parse(Buf.get()->getBuffer());
    if (!Parsed)
        return false;
    json::Object *Root = Parsed->getAsObject();
    if (!Root)
        return false;
    if (json::Object *ColdObj = Root->getObject("cold_sequences")) {
        for (auto &KV : *ColdObj) {
            if (json::Array *Arr = KV.second.getAsArray()) {
                std::vector<std::string> Seq;
                for (json::Value &V : *Arr)
                    if (auto S = V.getAsString())
                        Seq.emplace_back(S->str());
                if (!Seq.empty())
                    Cold[KV.first.str()] = std::move(Seq);
            }
        }
    }
    if (json::Array *Mod = Root->getArray("module_passes")) {
        for (json::Value &V : *Mod)
            if (auto S = V.getAsString())
                ModulePasses.emplace_back(S->str());
    }
    return true;
}

static std::string formatFunctionPipeline(ArrayRef<std::string> Atoms) {
    if (Atoms.empty())
        return "";
    std::string Out = "function(";
    for (size_t I = 0; I < Atoms.size(); ++I) {
        if (I)
            Out += ",";
        Out += Atoms[I];
    }
    Out += ")";
    return Out;
}

static bool runFunctionPipeline(Function &F, StringRef Pipeline,
                                FunctionAnalysisManager &FAM) {
    if (Pipeline.empty() || F.isDeclaration())
        return false;
    PassBuilder PB;
    FunctionPassManager FPM;
    if (Error Err = PB.parsePassPipeline(FPM, Pipeline)) {
        consumeError(std::move(Err));
        return false;
    }
    FPM.run(F, FAM);
    return true;
}

PreservedAnalyses KFusionWCETSchedulePass::run(Module &M, ModuleAnalysisManager &MAM) {
    std::string Path = wcetSchedulePath();
    if (Path.empty())
        return PreservedAnalyses::all();

    std::unordered_map<std::string, std::vector<std::string>> Cold;
    std::vector<std::string> ModulePasses;
    if (!loadWcetSchedule(Path, Cold, ModulePasses))
        return PreservedAnalyses::all();

    PassBuilder PB;
    LoopAnalysisManager LAM;
    FunctionAnalysisManager FAM;
    CGSCCAnalysisManager CGAM;
    PB.registerModuleAnalyses(MAM);
    PB.registerCGSCCAnalyses(CGAM);
    PB.registerFunctionAnalyses(FAM);
    PB.registerLoopAnalyses(LAM);
    PB.crossRegisterProxies(LAM, FAM, CGAM, MAM);

    bool Changed = false;
    for (Function &F : M) {
        if (F.isDeclaration())
            continue;
        auto It = Cold.find(F.getName().str());
        if (It == Cold.end())
            continue;
        std::string Pipe = formatFunctionPipeline(It->second);
        if (runFunctionPipeline(F, Pipe, FAM))
            Changed = true;
    }

    if (!ModulePasses.empty()) {
        std::string ModPipe;
        for (size_t I = 0; I < ModulePasses.size(); ++I) {
            if (I)
                ModPipe += ",";
            ModPipe += ModulePasses[I];
        }
        ModulePassManager MPM;
        if (Error Err = PB.parsePassPipeline(MPM, ModPipe)) {
            consumeError(std::move(Err));
        } else {
            MPM.run(M, MAM);
            Changed = true;
        }
    }

    return Changed ? PreservedAnalyses::none() : PreservedAnalyses::all();
}

void registerKFusionWCETSchedulePipeline(PassBuilder &PB) {
    PB.registerPipelineParsingCallback(
        [](StringRef Name, ModulePassManager &MPM,
           ArrayRef<PassBuilder::PipelineElement>) {
            if (Name == "plc-fusion-wcet-schedule") {
                MPM.addPass(KFusionWCETSchedulePass());
                return true;
            }
            return false;
        });
}
