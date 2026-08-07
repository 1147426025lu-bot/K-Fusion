#pragma once

#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"

struct KFusionWCETSchedulePass : public llvm::PassInfoMixin<KFusionWCETSchedulePass> {
    llvm::PreservedAnalyses run(llvm::Module &M, llvm::ModuleAnalysisManager &MAM);
};

void registerKFusionWCETSchedulePipeline(llvm::PassBuilder &PB);
