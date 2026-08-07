/*
 * PLCFusionFixedPoint__定点Pass.cpp — IR 浮点 → Q 定点转换
 */
#include "PLCFusionFixedPoint__定点Pass.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/DataLayout.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/DerivedTypes.h"
#include <cmath>
#include <cstdlib>

using namespace llvm;

namespace {

static bool envEnabled(const char *Name, bool Default) {
    const char *Val = std::getenv(Name);
    if (!Val || !Val[0])
        return Default;
    return Val[0] != '0';
}

static unsigned fracBits(Type *FTy) {
    if (FTy->isFloatTy())
        return 16;
    if (FTy->isDoubleTy())
        return 32;
    return 16;
}

static IntegerType *fixType(Type *FTy, LLVMContext &Ctx) {
    if (FTy->isFloatingPointTy())
        return Type::getInt64Ty(Ctx);
    return nullptr;
}

static ConstantInt *floatToFixedConst(ConstantFP *CF, unsigned FB,
                                      IntegerType *IT) {
    double D = CF->getValueAPF().convertToDouble();
    int64_t Raw = (int64_t)llround(D * (long double)(1ULL << FB));
    return ConstantInt::get(IT, Raw);
}

static Function *getOrInsert(Module &M, StringRef Name, FunctionType *Ty) {
    if (Function *F = M.getFunction(Name))
        return F;
    return Function::Create(Ty, Function::ExternalLinkage, Name, M);
}

static Function *getFixToDoubleFn(Module &M) {
    LLVMContext &Ctx = M.getContext();
    FunctionType *Ty = FunctionType::get(
        Type::getDoubleTy(Ctx),
        {Type::getInt64Ty(Ctx), Type::getInt32Ty(Ctx)}, false);
    return getOrInsert(M, "plc_fix_to_double", Ty);
}

static Function *getFixMulI64Fn(Module &M) {
    LLVMContext &Ctx = M.getContext();
    Type *I64 = Type::getInt64Ty(Ctx);
    Type *I32 = Type::getInt32Ty(Ctx);
    FunctionType *Ty = FunctionType::get(I64, {I64, I64, I32}, false);
    return getOrInsert(M, "plc_fix_mul_i64", Ty);
}

static Function *getFixDivI64Fn(Module &M) {
    LLVMContext &Ctx = M.getContext();
    Type *I64 = Type::getInt64Ty(Ctx);
    Type *I32 = Type::getInt32Ty(Ctx);
    FunctionType *Ty = FunctionType::get(I64, {I64, I64, I32}, false);
    return getOrInsert(M, "plc_fix_div_i64", Ty);
}

static bool isCompilerRtFloat(StringRef Name) {
    if (!Name.starts_with("__"))
        return false;
    return Name.contains("df") || Name.contains("sf") ||
           Name.ends_with("tf") || Name.ends_with("hf") ||
           Name.ends_with("bf");
}

static bool isFixToDoubleFn(StringRef Name) {
    return Name == "plc_fix_to_double";
}

static bool isPrintfFamily(StringRef Name) {
    return Name == "printf" || Name == "fprintf" || Name == "dprintf" ||
           Name == "plc_printk" || Name == "plc_fprintf" ||
           Name == "plc_warn" || Name == "plc_info" || Name == "plc_fatal";
}

static bool typeHasFloat(Type *Ty) {
    if (!Ty)
        return false;
    if (Ty->isFloatingPointTy())
        return true;
    if (auto *AT = dyn_cast<ArrayType>(Ty))
        return typeHasFloat(AT->getElementType());
    if (auto *ST = dyn_cast<StructType>(Ty)) {
        for (unsigned i = 0; i < ST->getNumElements(); ++i)
            if (typeHasFloat(ST->getElementType(i)))
                return true;
    }
    return false;
}

static Type *mapFixedType(Type *Ty, LLVMContext &Ctx) {
    if (Ty->isFloatingPointTy())
        return fixType(Ty, Ctx);
    if (auto *AT = dyn_cast<ArrayType>(Ty)) {
        Type *El = mapFixedType(AT->getElementType(), Ctx);
        if (El && El != AT->getElementType())
            return ArrayType::get(El, AT->getNumElements());
    }
    if (auto *ST = dyn_cast<StructType>(Ty)) {
        (void)ST;
        return Ty;
    }
    return Ty;
}

static void convertStructTypes(Module &M) {
    LLVMContext &Ctx = M.getContext();
    SmallVector<StructType *, 8> Todo;
    for (StructType *ST : M.getIdentifiedStructTypes()) {
        if (ST->isOpaque())
            continue;
        for (Type *ET : ST->elements()) {
            if (typeHasFloat(ET)) {
                Todo.push_back(ST);
                break;
            }
        }
    }
    for (StructType *ST : Todo) {
        SmallVector<Type *, 16> Elts;
        for (Type *ET : ST->elements()) {
            Type *N = mapFixedType(ET, Ctx);
            Elts.push_back(N ? N : ET);
        }
        if (ST->elements().size() == Elts.size())
            ST->setBody(Elts, ST->isPacked());
    }
}

static Constant *mapFixedConstant(Constant *C, Type *OldTy, Type *NewTy) {
    if (!C)
        return nullptr;
    if (OldTy->isFloatingPointTy()) {
        if (auto *CF = dyn_cast<ConstantFP>(C))
            return floatToFixedConst(CF, fracBits(OldTy),
                                     cast<IntegerType>(NewTy));
        return ConstantInt::get(cast<IntegerType>(NewTy), 0);
    }
    if (auto *OldAT = dyn_cast<ArrayType>(OldTy)) {
        auto *NewAT = cast<ArrayType>(NewTy);
        SmallVector<Constant *, 8> Elts;
        auto appendMapped = [&](Constant *El, Type *OldElTy) {
            Elts.push_back(mapFixedConstant(El, OldElTy, NewAT->getElementType()));
        };
        if (auto *CA = dyn_cast<ConstantArray>(C)) {
            for (unsigned i = 0; i < CA->getNumOperands(); ++i)
                appendMapped(CA->getOperand(i), OldAT->getElementType());
            return ConstantArray::get(NewAT, Elts);
        }
        if (auto *CDS = dyn_cast<ConstantDataSequential>(C)) {
            Type *OldElTy = OldAT->getElementType();
            for (unsigned i = 0; i < CDS->getNumElements(); ++i) {
                if (OldElTy->isFloatingPointTy()) {
                    double D = CDS->getElementAsDouble(i);
                    int64_t Raw = (int64_t)llround(
                        D * (long double)(1ULL << fracBits(OldElTy)));
                    Elts.push_back(ConstantInt::get(NewAT->getElementType(), Raw));
                } else {
                    appendMapped(CDS->getElementAsConstant(i), OldElTy);
                }
            }
            return ConstantArray::get(NewAT, Elts);
        }
    }
    return ConstantAggregateZero::get(NewTy);
}

class FixedPointConverter {
    Module &M;
    DenseMap<Value *, Value *> Map;
    bool Changed = false;

    static IRBuilder<> builderAfter(Value *V, IRBuilder<> &Fallback) {
        if (auto *I = dyn_cast<Instruction>(V)) {
            if (Instruction *Next = I->getNextNode())
                return IRBuilder<>(Next);
            return IRBuilder<>(I->getParent()->getTerminator());
        }
        return builderAtDef(V, Fallback);
    }

    static IRBuilder<> builderAtDef(Value *V, IRBuilder<> &Fallback) {
        if (auto *I = dyn_cast<Instruction>(V))
            return IRBuilder<>(I);
        if (auto *A = dyn_cast<Argument>(V)) {
            Function *Fn = A->getParent();
            BasicBlock &EB = Fn->getEntryBlock();
            return IRBuilder<>(&EB, EB.getFirstInsertionPt());
        }
        return IRBuilder<>(Fallback.GetInsertBlock(), Fallback.GetInsertPoint());
    }

    Value *materialize(Value *V, IRBuilder<> &B) {
        if (!V)
            return nullptr;
        if (Map.count(V))
            return Map[V];
        if (!V->getType()->isFloatingPointTy())
            return V;

        IRBuilder<> DefB = builderAtDef(V, B);

        if (auto *CF = dyn_cast<ConstantFP>(V)) {
            unsigned FB = fracBits(CF->getType());
            IntegerType *IT = fixType(CF->getType(), M.getContext());
            Value *C = floatToFixedConst(CF, FB, IT);
            Map[V] = C;
            return C;
        }

        if (auto *Cast = dyn_cast<CastInst>(V)) {
            if (Cast->getSrcTy()->isIntegerTy() &&
                Cast->getDestTy()->isFloatingPointTy()) {
                unsigned FB = fracBits(Cast->getDestTy());
                Value *N = intToFixed(Cast->getOperand(0), FB, DefB);
                Map[V] = N;
                Cast->replaceAllUsesWith(N);
                Cast->eraseFromParent();
                Changed = true;
                return N;
            }
        }

        if (auto *I = dyn_cast<Instruction>(V)) {
            convertInst(I);
            if (Map.count(V))
                return Map[V];
        }

        IRBuilder<> InsB = builderAfter(V, B);
        unsigned FB = fracBits(V->getType());
        IntegerType *IT = fixType(V->getType(), M.getContext());
        Value *Ext = V;
        if (V->getType()->isFloatTy())
            Ext = InsB.CreateFPExt(V, Type::getDoubleTy(M.getContext()));
        Value *Scaled = InsB.CreateFMul(
            Ext, ConstantFP::get(Ext->getType(), (double)(1ULL << FB)));
        Value *Fix = InsB.CreateFPToSI(Scaled, IT);
        Map[V] = Fix;
        Changed = true;
        return Fix;
    }

    static Value *adjustIntWidth(IRBuilder<> &B, Value *In, Type *DstTy) {
        if (In->getType() == DstTy)
            return In;
        unsigned InBits = In->getType()->getScalarSizeInBits();
        unsigned DstBits = DstTy->getScalarSizeInBits();
        if (InBits < DstBits)
            return B.CreateZExt(In, DstTy);
        if (InBits > DstBits)
            return B.CreateTrunc(In, DstTy);
        return In;
    }

    Value *fixMul(Value *A, Value *Rhs, unsigned FB, IRBuilder<> &Builder) {
        IntegerType *IT = Type::getInt64Ty(M.getContext());
        Type *I32 = Type::getInt32Ty(M.getContext());
        A = adjustIntWidth(Builder, A, IT);
        Rhs = adjustIntWidth(Builder, Rhs, IT);
        return Builder.CreateCall(
            getFixMulI64Fn(M),
            {A, Rhs, ConstantInt::get(I32, FB)});
    }

    Value *fixDiv(Value *A, Value *Rhs, unsigned FB, IRBuilder<> &Builder) {
        IntegerType *IT = Type::getInt64Ty(M.getContext());
        Type *I32 = Type::getInt32Ty(M.getContext());
        A = adjustIntWidth(Builder, A, IT);
        Rhs = adjustIntWidth(Builder, Rhs, IT);
        return Builder.CreateCall(
            getFixDivI64Fn(M),
            {A, Rhs, ConstantInt::get(I32, FB)});
    }

    static int floatInstPriority(Instruction *I) {
        if (isa<FCmpInst>(I))
            return 0;
        if (auto *BO = dyn_cast<BinaryOperator>(I)) {
            if (BO->getType()->isFloatingPointTy())
                return 0;
        }
        if (auto *C = dyn_cast<CastInst>(I)) {
            if (C->getSrcTy()->isFloatingPointTy() &&
                C->getDestTy()->isIntegerTy())
                return 2;
            if (C->getSrcTy()->isIntegerTy() &&
                C->getDestTy()->isFloatingPointTy())
                return 3;
            if (C->getSrcTy()->isFloatingPointTy())
                return 1;
        }
        if (I->getType()->isFloatingPointTy())
            return 4;
        return 5;
    }

    static bool isFloatProducer(Instruction *I) {
        if (auto *CB = dyn_cast<CallBase>(I)) {
            if (Function *Callee = CB->getCalledFunction()) {
                if (isFixToDoubleFn(Callee->getName()))
                    return false;
            }
        }
        if (isa<FCmpInst>(I))
            return true;
        if (auto *BO = dyn_cast<BinaryOperator>(I)) {
            if (BO->getType()->isFloatingPointTy())
                return true;
        }
        if (auto *C = dyn_cast<CastInst>(I)) {
            if (C->getSrcTy()->isFloatingPointTy() ||
                C->getDestTy()->isFloatingPointTy())
                return true;
        }
        if (I->getType()->isFloatingPointTy())
            return true;
        for (Value *Op : I->operands()) {
            if (Op->getType()->isFloatingPointTy())
                return true;
        }
        return false;
    }

    Value *intToFixed(Value *Op, unsigned FB, IRBuilder<> &B) {
        IntegerType *IT = Type::getInt64Ty(M.getContext());
        Value *Ext = adjustIntWidth(B, Op, IT);
        return B.CreateShl(Ext, FB);
    }

    bool castOnlyUsedByFloatBinOp(CastInst *Cast) {
        if (!Cast->hasOneUse())
            return false;
        User *U = *Cast->user_begin();
        auto *BO = dyn_cast<BinaryOperator>(U);
        if (!BO || !BO->getType()->isFloatingPointTy())
            return false;
        unsigned Opc = BO->getOpcode();
        return Opc == Instruction::FDiv || Opc == Instruction::FMul ||
               Opc == Instruction::FAdd || Opc == Instruction::FSub;
    }

    void convertInst(Instruction *Inst) {
        if (!Inst || !Inst->getParent() || Map.count(Inst))
            return;

        IRBuilder<> B(Inst);
        Type *Ty = Inst->getType();

        if (auto *BO = dyn_cast<BinaryOperator>(Inst)) {
            if (!Ty->isFloatingPointTy())
                return;
            unsigned FB = fracBits(Ty);
            Value *L = materialize(BO->getOperand(0), B);
            Value *R = materialize(BO->getOperand(1), B);
            Value *N = nullptr;
            switch (BO->getOpcode()) {
            case Instruction::FAdd:
                N = B.CreateAdd(L, R);
                break;
            case Instruction::FSub:
                N = B.CreateSub(L, R);
                break;
            case Instruction::FMul:
                N = fixMul(L, R, FB, B);
                break;
            case Instruction::FDiv:
                N = fixDiv(L, R, FB, B);
                break;
            default:
                return;
            }
            Map[Inst] = N;
            Inst->replaceAllUsesWith(N);
            Inst->eraseFromParent();
            Changed = true;
            return;
        }

        if (auto *FCmp = dyn_cast<FCmpInst>(Inst)) {
            Value *L = materialize(FCmp->getOperand(0), B);
            Value *R = materialize(FCmp->getOperand(1), B);
            CmpInst::Predicate P = CmpInst::BAD_ICMP_PREDICATE;
            switch (FCmp->getPredicate()) {
            case FCmpInst::FCMP_OEQ:
            case FCmpInst::FCMP_UEQ:
                P = CmpInst::ICMP_EQ;
                break;
            case FCmpInst::FCMP_ONE:
            case FCmpInst::FCMP_UNE:
                P = CmpInst::ICMP_NE;
                break;
            case FCmpInst::FCMP_OGT:
            case FCmpInst::FCMP_UGT:
                P = CmpInst::ICMP_SGT;
                break;
            case FCmpInst::FCMP_OGE:
            case FCmpInst::FCMP_UGE:
                P = CmpInst::ICMP_SGE;
                break;
            case FCmpInst::FCMP_OLT:
            case FCmpInst::FCMP_ULT:
                P = CmpInst::ICMP_SLT;
                break;
            case FCmpInst::FCMP_OLE:
            case FCmpInst::FCMP_ULE:
                P = CmpInst::ICMP_SLE;
                break;
            default:
                P = CmpInst::ICMP_EQ;
                break;
            }
            Value *N = B.CreateICmp(P, L, R);
            Map[Inst] = N;
            Inst->replaceAllUsesWith(N);
            Inst->eraseFromParent();
            Changed = true;
            return;
        }

        if (auto *Cast = dyn_cast<CastInst>(Inst)) {
            Type *Src = Cast->getSrcTy();
            Type *Dst = Cast->getDestTy();
            if (!Src->isFloatingPointTy() && Dst->isIntegerTy()) {
                Value *In = Cast->getOperand(0);
                Value *N = adjustIntWidth(B, In, Dst);
                Map[Inst] = N;
                Inst->replaceAllUsesWith(N);
                Inst->eraseFromParent();
                Changed = true;
                return;
            }
            if (Src->isFloatingPointTy() && Dst->isIntegerTy()) {
                Value *In = materialize(Cast->getOperand(0), B);
                unsigned FB = fracBits(Src);
                Value *N = nullptr;
                if (In->getType()->isIntegerTy()) {
                    N = adjustIntWidth(B, B.CreateAShr(In, FB), Dst);
                } else {
                    Value *Scaled = B.CreateFMul(
                        In, ConstantFP::get(In->getType(), (double)(1ULL << FB)));
                    N = B.CreateFPToSI(Scaled, Dst);
                }
                Map[Inst] = N;
                Inst->replaceAllUsesWith(N);
                Inst->eraseFromParent();
                Changed = true;
                return;
            }
            if (Src->isFloatingPointTy() && Dst->isFloatingPointTy()) {
                Value *In = materialize(Cast->getOperand(0), B);
                unsigned FBIn = fracBits(Src);
                unsigned FBOut = fracBits(Dst);
                Value *N = In;
                if (FBOut > FBIn)
                    N = B.CreateShl(B.CreateSExt(In, fixType(Dst, M.getContext())),
                                    FBOut - FBIn);
                else if (FBOut < FBIn)
                    N = B.CreateTrunc(B.CreateAShr(In, FBIn - FBOut),
                                      fixType(Dst, M.getContext()));
                Map[Inst] = N;
                Inst->replaceAllUsesWith(N);
                Inst->eraseFromParent();
                Changed = true;
                return;
            }
            if (Src->isIntegerTy() && Dst->isFloatingPointTy()) {
                if (castOnlyUsedByFloatBinOp(Cast))
                    return;
                unsigned FB = fracBits(Dst);
                Value *N = intToFixed(Cast->getOperand(0), FB, B);
                Map[Inst] = N;
                Inst->replaceAllUsesWith(N);
                Inst->eraseFromParent();
                Changed = true;
                return;
            }
        }

        if (auto *Sel = dyn_cast<SelectInst>(Inst)) {
            if (!Ty->isFloatingPointTy())
                return;
            Value *C = Sel->getCondition();
            Value *T = materialize(Sel->getTrueValue(), B);
            Value *F = materialize(Sel->getFalseValue(), B);
            Value *N = B.CreateSelect(C, T, F);
            Map[Inst] = N;
            Inst->replaceAllUsesWith(N);
            Inst->eraseFromParent();
            Changed = true;
            return;
        }

        if (auto *PN = dyn_cast<PHINode>(Inst)) {
            if (!Ty->isFloatingPointTy())
                return;
            IntegerType *IT = fixType(Ty, M.getContext());
            PHINode *NP =
                PHINode::Create(IT, PN->getNumIncomingValues(),
                                PN->getName() + ".fix", PN);
            for (unsigned i = 0; i < PN->getNumIncomingValues(); ++i) {
                BasicBlock *IB = PN->getIncomingBlock(i);
                IRBuilder<> IBld(IB->getTerminator());
                NP->addIncoming(materialize(PN->getIncomingValue(i), IBld), IB);
            }
            Map[Inst] = NP;
            Inst->replaceAllUsesWith(NP);
            Inst->eraseFromParent();
            Changed = true;
            return;
        }

        if (auto *CB = dyn_cast<CallBase>(Inst)) {
            Function *Callee = CB->getCalledFunction();
            if (Callee && isCompilerRtFloat(Callee->getName()) &&
                Ty->isFloatingPointTy()) {
                unsigned FB = fracBits(Ty);
                Value *L = materialize(CB->getArgOperand(0), B);
                Value *R = materialize(CB->getArgOperand(1), B);
                Value *N = nullptr;
                if (Callee->getName().contains("mul"))
                    N = fixMul(L, R, FB, B);
                else if (Callee->getName().contains("add"))
                    N = B.CreateAdd(L, R);
                else if (Callee->getName().contains("sub"))
                    N = B.CreateSub(L, R);
                else if (Callee->getName().contains("div"))
                    N = fixDiv(L, R, FB, B);
                else
                    N = ConstantInt::get(fixType(Ty, M.getContext()), 0);
                Map[Inst] = N;
                Inst->replaceAllUsesWith(N);
                Inst->eraseFromParent();
                Changed = true;
                return;
            }

            if (Callee && isPrintfFamily(Callee->getName())) {
                bool touched = false;
                for (unsigned i = 0; i < CB->arg_size(); ++i) {
                    Value *Arg = CB->getArgOperand(i);
                    if (!Arg->getType()->isFloatingPointTy())
                        continue;
                    if (auto *ArgCall = dyn_cast<CallInst>(Arg)) {
                        Function *AC = ArgCall->getCalledFunction();
                        if (AC && isFixToDoubleFn(AC->getName()))
                            continue;
                    }
                    Value *Fix = materialize(Arg, B);
                    Value *Wide =
                        B.CreateSExt(Fix, Type::getInt64Ty(M.getContext()));
                    Value *D = B.CreateCall(
                        getFixToDoubleFn(M),
                        {Wide, ConstantInt::get(Type::getInt32Ty(M.getContext()),
                                                fracBits(Arg->getType()))});
                    CB->setArgOperand(i, D);
                    touched = true;
                }
                if (touched)
                    Changed = true;
                return;
            }
        }

        if (auto *SI = dyn_cast<StoreInst>(Inst)) {
            Value *Val = SI->getValueOperand();
            if (!Val->getType()->isFloatingPointTy())
                return;
            IntegerType *IT = fixType(Val->getType(), M.getContext());
            Value *Fix = materialize(Val, B);
            if (Fix->getType() != IT)
                Fix = B.CreateTrunc(Fix, IT);
            Type *I64Ty = Type::getInt64Ty(M.getContext());
            Value *Ptr = SI->getPointerOperand();
            Value *I64Ptr = B.CreateBitCast(Ptr, PointerType::get(I64Ty, 0));
            Value *Wide = Fix;
            if (IT->getBitWidth() < 64)
                Wide = B.CreateZExt(Fix, I64Ty);
            else if (IT->getBitWidth() > 64)
                Wide = B.CreateTrunc(Fix, I64Ty);
            B.CreateStore(Wide, I64Ptr);
            SI->eraseFromParent();
            Changed = true;
            return;
        }

        if (auto *LI = dyn_cast<LoadInst>(Inst)) {
            if (!Ty->isFloatingPointTy())
                return;
            IntegerType *IT = fixType(Ty, M.getContext());
            Type *I64Ty = Type::getInt64Ty(M.getContext());
            Value *I64Ptr = B.CreateBitCast(LI->getPointerOperand(),
                                            PointerType::get(I64Ty, 0));
            Value *Wide = B.CreateLoad(I64Ty, I64Ptr, LI->getName() + ".fix");
            Value *Fix = Wide;
            if (IT->getBitWidth() < 64)
                Fix = B.CreateTrunc(Wide, IT);
            else if (IT->getBitWidth() > 64)
                Fix = B.CreateSExt(Wide, IT);
            Map[Inst] = Fix;
            Inst->replaceAllUsesWith(Fix);
            Inst->eraseFromParent();
            Changed = true;
            return;
        }

        if (auto *AI = dyn_cast<AllocaInst>(Inst)) {
            if (!AI->getAllocatedType()->isFloatingPointTy())
                return;
            IntegerType *IT = fixType(AI->getAllocatedType(), M.getContext());
            AllocaInst *NA =
                new AllocaInst(IT, 0, AI->getArraySize(), AI->getAlign(),
                               AI->getName(), AI->getParent());
            NA->insertBefore(AI);
            AI->replaceAllUsesWith(NA);
            AI->eraseFromParent();
            Changed = true;
            return;
        }

        if (Ty->isFloatingPointTy()) {
            if (auto *U = dyn_cast<UnaryOperator>(Inst)) {
                if (U->getOpcode() == Instruction::FNeg) {
                    Value *In = materialize(U->getOperand(0), B);
                    Value *N = B.CreateNeg(In);
                    Map[Inst] = N;
                    Inst->replaceAllUsesWith(N);
                    Inst->eraseFromParent();
                    Changed = true;
                }
            }
        }
    }

    void convertGlobals() {
        SmallVector<GlobalVariable *, 8> Todo;
        for (GlobalVariable &G : M.globals()) {
            if (typeHasFloat(G.getValueType()))
                Todo.push_back(&G);
        }
        for (GlobalVariable *G : Todo) {
            Type *OldTy = G->getValueType();
            Type *NewTy = mapFixedType(OldTy, M.getContext());
            if (!NewTy || NewTy == OldTy)
                continue;
            Constant *Init = mapFixedConstant(G->getInitializer(), OldTy, NewTy);
            if (!Init)
                Init = ConstantAggregateZero::get(NewTy);
            GlobalVariable *NG = new GlobalVariable(
                M, NewTy, G->isConstant(), G->getLinkage(), Init, G->getName(),
                G, G->getThreadLocalMode(), G->getAddressSpace());
            NG->copyAttributesFrom(G);
            G->replaceAllUsesWith(NG);
            G->eraseFromParent();
            Changed = true;
        }
    }

    void convertFunctions() {
        for (Function &F : M) {
            if (F.isDeclaration())
                continue;
            for (unsigned Round = 0; Round < 64; ++Round) {
                bool Progress = false;
                SmallVector<Instruction *, 64> Work;
                for (BasicBlock &BB : F)
                    for (Instruction &I : BB)
                        if (isFloatProducer(&I))
                            Work.push_back(&I);
                llvm::sort(Work, [](Instruction *A, Instruction *B) {
                    return floatInstPriority(A) < floatInstPriority(B);
                });
                for (Instruction *I : Work) {
                    if (!I->getParent())
                        continue;
                    if (Map.count(I))
                        continue;
                    size_t Before = Map.size();
                    convertInst(I);
                    if (Map.size() != Before || !I->getParent())
                        Progress = true;
                }
                if (!Progress)
                    break;
            }
        }
    }

    void cleanupStaleFloatCasts() {
        for (Function &F : M) {
            if (F.isDeclaration())
                continue;
            SmallVector<Instruction *, 32> Kill;
            for (BasicBlock &BB : F)
                for (Instruction &I : BB) {
                    auto *Cast = dyn_cast<CastInst>(&I);
                    if (!Cast)
                        continue;
                    if (Cast->getOperand(0)->getType()->isFloatingPointTy())
                        continue;
                    if (Cast->getSrcTy()->isFloatingPointTy() ||
                        Cast->getDestTy()->isFloatingPointTy())
                        Kill.push_back(Cast);
                }
            for (Instruction *I : Kill) {
                IRBuilder<> B(I);
                Value *In = I->getOperand(0);
                Value *N = In;
                if (In->getType() != I->getType() && !I->getType()->isVoidTy())
                    N = adjustIntWidth(B, In, I->getType());
                I->replaceAllUsesWith(N);
                I->eraseFromParent();
                Changed = true;
            }
        }
    }

    bool scrubInvalidCastsOnce() {
        bool Any = false;
        for (Function &F : M) {
            if (F.isDeclaration())
                continue;
            SmallVector<CastInst *, 32> Casts;
            for (BasicBlock &BB : F)
                for (Instruction &I : BB)
                    if (auto *C = dyn_cast<CastInst>(&I))
                        Casts.push_back(C);
            for (CastInst *C : Casts) {
                if (!C->getParent())
                    continue;
                if (!C->getSrcTy()->isIntegerTy() ||
                    !C->getDestTy()->isIntegerTy())
                    continue;
                IRBuilder<> B(C);
                Value *Op = C->getOperand(0);
                Type *Dst = C->getType();
                if (Op->getType() == Dst) {
                    C->replaceAllUsesWith(Op);
                    C->eraseFromParent();
                    Any = true;
                    Changed = true;
                    continue;
                }
                if (C->getSrcTy() != Op->getType()) {
                    Value *N = adjustIntWidth(B, Op, Dst);
                    C->replaceAllUsesWith(N);
                    C->eraseFromParent();
                    Any = true;
                    Changed = true;
                }
            }
        }
        return Any;
    }

    void fixupGetElementPtrTypes() {
        SmallVector<GetElementPtrInst *, 32> GEPs;
        for (Function &F : M) {
            if (F.isDeclaration())
                continue;
            for (BasicBlock &BB : F)
                for (Instruction &I : BB)
                    if (auto *G = dyn_cast<GetElementPtrInst>(&I))
                        GEPs.push_back(G);
        }
        for (GetElementPtrInst *Old : GEPs) {
            if (!Old->getParent())
                continue;
            auto *GV = dyn_cast<GlobalVariable>(Old->getPointerOperand());
            if (!GV)
                continue;
            Type *PointeeTy = GV->getValueType();
            if (Old->getSourceElementType() == PointeeTy)
                continue;
            SmallVector<Value *, 8> Idx(Old->idx_begin(), Old->idx_end());
            IRBuilder<> B(Old);
            GetElementPtrInst *N =
                cast<GetElementPtrInst>(B.CreateGEP(PointeeTy, GV, Idx));
            N->setIsInBounds(Old->isInBounds());
            Old->replaceAllUsesWith(N);
            Old->eraseFromParent();
            Changed = true;
        }
    }

    void fixupIntegerCasts() {
        for (Function &F : M) {
            if (F.isDeclaration())
                continue;
            SmallVector<CastInst *, 32> Casts;
            for (BasicBlock &BB : F)
                for (Instruction &I : BB)
                    if (auto *C = dyn_cast<CastInst>(&I))
                        Casts.push_back(C);
            for (CastInst *C : Casts) {
                if (!C->getParent())
                    continue;
                IRBuilder<> B(C);
                Value *Op = C->getOperand(0);
                Type *Dst = C->getType();
                if (C->getSrcTy() != Op->getType()) {
                    Value *N = adjustIntWidth(B, Op, Dst);
                    C->replaceAllUsesWith(N);
                    C->eraseFromParent();
                    Changed = true;
                    continue;
                }
                if (Op->getType() == Dst) {
                    C->replaceAllUsesWith(Op);
                    C->eraseFromParent();
                    Changed = true;
                    continue;
                }
                if (C->getOpcode() == Instruction::SExt ||
                    C->getOpcode() == Instruction::ZExt ||
                    C->getOpcode() == Instruction::Trunc) {
                    Value *N = adjustIntWidth(B, Op, Dst);
                    C->replaceAllUsesWith(N);
                    C->eraseFromParent();
                    Changed = true;
                }
            }
        }
    }

public:
    explicit FixedPointConverter(Module &Mod) : M(Mod) {}

    bool run() {
        if (!envEnabled("PLC_FUSION_FIXED_POINT", true))
            return false;
        convertGlobals();
        fixupGetElementPtrTypes();
        for (unsigned Round = 0; Round < 6; ++Round) {
            Map.clear();
            convertFunctions();
            fixupGetElementPtrTypes();
            if (!typeHasFloatInModule(M))
                break;
        }
        for (unsigned Round = 0; Round < 8; ++Round) {
            cleanupStaleFloatCasts();
            fixupIntegerCasts();
            if (!scrubInvalidCastsOnce())
                break;
        }
        while (scrubInvalidCastsOnce()) {
        }
        return Changed;
    }

    static bool typeHasFloatInModule(Module &Mod) {
        for (Function &F : Mod) {
            for (BasicBlock &BB : F)
                for (Instruction &I : BB) {
                    if (typeHasFloat(I.getType()))
                        return true;
                    for (Value *Op : I.operands())
                        if (typeHasFloat(Op->getType()))
                            return true;
                }
        }
        return false;
    }
};

} // namespace

bool llvm::runFixedPointConvert(Module &M) {
    FixedPointConverter C(M);
    return C.run();
}
