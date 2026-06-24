/*
 * ast_tool__AST工具.cpp — plc-cc 静态分析器（Clang AST）
 *
 * 职责（FIXED 方向）:
 *   - 校验 plc_cycle / plc_main / plc_logic 入口
 *   - 在周期函数内报告阻塞调用与 WCET 风险调用
 *   - 融合可行性门禁（fork/dlopen/C++ 等）
 *   - manifest 字段建议（--suggest-manifest）
 *
 * 可选（兼容旧管线）:
 *   --emit-kernel-c  生成 .kernel.c（annotate + 旧式 remap，不推荐）
 *
 * 推荐融合路径: 源文件 + plc_cc_fuse_shim + PLCFusionPass（无需 .kernel.c）
 */
#include "clang/AST/DeclCXX.h"
#include "clang/AST/DeclTemplate.h"
#include "clang/AST/Expr.h"
#include "clang/AST/ExprCXX.h"
#include "clang/AST/Stmt.h"
#include "clang/AST/StmtCXX.h"
#include "clang/AST/ASTConsumer.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Basic/DiagnosticIDs.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendAction.h"
#include "clang/Rewrite/Core/Rewriter.h"
#include "clang/Tooling/ArgumentsAdjusters.h"
#include "clang/Tooling/CommonOptionsParser.h"
#include "clang/Tooling/Tooling.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/raw_ostream.h"

#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

using namespace clang;
using namespace clang::tooling;
using namespace llvm;

static cl::OptionCategory PLCToolCategory("plc-ast options");

static cl::opt<bool> OptAnalyzeOnly(
    "analyze-only",
    cl::desc("仅静态分析与 JSON，不改写源码"),
    cl::cat(PLCToolCategory));

static cl::opt<bool> OptEmitKernelC(
    "emit-kernel-c",
    cl::desc("生成 .kernel.c（旧式改写，默认关闭）"),
    cl::cat(PLCToolCategory));

static cl::opt<bool> OptStrict(
    "strict",
    cl::desc("周期函数内 malloc/printf 等也视为 error"),
    cl::cat(PLCToolCategory));

static cl::opt<std::string> OptJsonOut(
    "json",
    cl::desc("JSON 报告路径（默认: <source>.plc_ast.json）"),
    cl::value_desc("path"),
    cl::cat(PLCToolCategory));

static cl::opt<bool> OptNoShim(
    "no-shim",
    cl::desc("不注入 plc_cc_fuse_shim（rt-tests 等上游源）"),
    cl::cat(PLCToolCategory));

static cl::opt<bool> OptFusionStrict(
    "fusion-strict",
    cl::desc("融合 critical 问题视为 exit 1"),
    cl::cat(PLCToolCategory));

static cl::opt<std::string> OptSuggestManifest(
    "suggest-manifest",
    cl::desc("将 manifest 建议写入 .env 片段"),
    cl::value_desc("path"),
    cl::cat(PLCToolCategory));

static const char *kFusionCriticalCalls[] = {
    "fork", "vfork", "execve", "execvp", "execl", "execle", "system",
    "dlopen", "dlsym", "dlclose", nullptr,
};

static const char *kFusionWarnCalls[] = {
    "socket", "connect", "bind", "listen", "accept", "send", "recv",
    "sendto", "recvfrom", "sem_open", "mq_open", "inotify_init",
    "pthread_cancel", "popen", nullptr,
};

static const char *kRtThreadEntries[] = {
    "timerthread", "fifothread", "signalthread", "semathread", nullptr,
};

static const char *kEntryNames[] = {"plc_cycle", "plc_main", "plc_logic",
                                      nullptr};

static bool isEntryName(StringRef Name) {
    for (const char **P = kEntryNames; *P; ++P) {
        if (Name == *P)
            return true;
    }
    return false;
}

static const char *kBlockingCalls[] = {
    "nanosleep",       "sleep",           "usleep",
    "clock_nanosleep", "pthread_create",  "pthread_join",
    "pthread_cond_wait", "pthread_cond_timedwait", "select",
    "poll",            "epoll_wait",      "pause",
    "sigwait",         "accept",          "connect",
    "recv",            "read",            nullptr,
};

static const char *kWcetRiskCalls[] = {
    "malloc",  "calloc",  "realloc", "free",   "printf",
    "fprintf", "puts",    "dprintf", "fopen",  "fclose",
    "open",    "write",   nullptr,
};

enum class IssueSeverity { Warn, Error };

static const char *kFloatMathCalls[] = {
    "sin", "cos", "tan", "sqrt", "pow", "exp", "log", "floor", "ceil",
    "fabs", "fmod", nullptr,
};

enum class IssueKind { Blocking, WcetRisk, Float, Sched };

static const char *kSchedCalls[] = {
    "pthread_mutex_lock", "pthread_mutex_timedlock",
    "pthread_rwlock_rdlock", "pthread_rwlock_wrlock", "sem_wait",
    nullptr,
};

struct FusionIssue {
    IssueSeverity Sev;
    std::string Symbol;
    std::string Context;
    unsigned Line = 0;
    std::string Message;
};

struct CycleIssue {
    IssueSeverity Sev;
    IssueKind Kind = IssueKind::WcetRisk;
    std::string Function;
    std::string Call;
    unsigned Line = 0;
    std::string Message;
};

struct GlobalInfo {
    std::string Name;
    unsigned Line = 0;
};

struct PLCAnalysis {
    std::string SourcePath;
    std::set<std::string> EntriesFound;
    std::set<std::string> DefinedFunctions;
    std::string PrimaryEntry;
    std::vector<GlobalInfo> Globals;
    std::vector<CycleIssue> Issues;
    std::vector<FusionIssue> FusionIssues;
    std::map<std::string, std::set<std::string>> CallGraph;
    unsigned ErrorCount = 0;
    unsigned WarnCount = 0;
    unsigned FusionCriticalCount = 0;
    unsigned FusionWarnCount = 0;
    unsigned SchedCriticalCount = 0;
    unsigned SchedWarnCount = 0;
    bool FloatInCycle = false;
    bool FloatAnywhere = false;
    bool HasMain = false;
    bool HasPthreadCreate = false;
    bool IsCPlusPlus = false;
    unsigned IndirectResolvedCount = 0;
    unsigned IndirectUnresolvedCount = 0;
    std::set<std::string> FloatWarnedFuncs;
    std::set<std::string> FusionReported;

    void addFusionIssue(IssueSeverity Sev, StringRef Symbol, StringRef Ctx,
                        unsigned Line, StringRef Msg) {
        std::string Key = (Symbol + "@" + std::to_string(Line)).str();
        if (!FusionReported.insert(Key).second)
            return;
        FusionIssue F;
        F.Sev = Sev;
        F.Symbol = Symbol.str();
        F.Context = Ctx.str();
        F.Line = Line;
        F.Message = Msg.str();
        FusionIssues.push_back(std::move(F));
        if (Sev == IssueSeverity::Error)
            ++FusionCriticalCount;
        else
            ++FusionWarnCount;
    }

    bool fusionEligible() const { return FusionCriticalCount == 0; }

    static std::string joinNames(const std::set<std::string> &S) {
        std::string R;
        for (const auto &N : S) {
            if (!R.empty())
                R += ',';
            R += N;
        }
        return R;
    }

    static std::string joinRoots(const std::string &Root,
                                 const std::set<std::string> &Callees) {
        std::set<std::string> All;
        if (!Root.empty())
            All.insert(Root);
        All.insert(Callees.begin(), Callees.end());
        return joinNames(All);
    }

    json::Object buildManifestSuggestions() const {
        json::Object S;
        if (!PrimaryEntry.empty()) {
            S["FUSE_KTHREAD_ENTRY"] = PrimaryEntry;
            auto It = CallGraph.find(PrimaryEntry);
            std::string Roots = PrimaryEntry;
            if (It != CallGraph.end())
                Roots = joinRoots(PrimaryEntry, It->second);
            S["FUSE_DCE_ROOTS"] = Roots;
            S["FUSE_HOT_PATH_FUNCTIONS"] = Roots;
            S["FUSE_HOST"] = "hrtimer";
            S["FUSE_WCET_MODE"] = "1";
            S["FUSE_PLC_CC_AST"] = "1";
            S["FUSE_CLANG_FLAGS"] =
                "-O2 -fno-builtin -D_GNU_SOURCE -include "
                "plc_cc_fuse_shim__融合头.h";
        } else {
            for (const char **P = kRtThreadEntries; *P; ++P) {
                if (DefinedFunctions.count(*P)) {
                    S["FUSE_KTHREAD_ENTRY"] = *P;
                    auto It = CallGraph.find(*P);
                    if (It != CallGraph.end())
                        S["FUSE_DCE_ROOTS"] = joinRoots(*P, It->second);
                    else
                        S["FUSE_DCE_ROOTS"] = *P;
                    S["FUSE_HOST"] = "hrtimer";
                    break;
                }
            }
            if (HasMain && HasPthreadCreate) {
                S["FUSE_RUN_MAIN"] = "1";
                S["FUSE_LINK_PTHREAD_HOST"] = "1";
                if (!S.get("FUSE_HOST"))
                    S["FUSE_HOST"] = "hrtimer";
            }
        }
        if (HasPthreadCreate)
            S["FUSE_LINK_PTHREAD_HOST"] = "1";
        if (FloatAnywhere || FloatInCycle)
            S["FUSE_LINK_COMPILER_RT"] = "auto";
        if (!Globals.empty()) {
            std::set<std::string> Gset;
            for (const auto &G : Globals)
                Gset.insert(G.Name);
            S["FUSE_DETECT_GLOBALS"] = joinNames(Gset);
        }
        S["FUSE_LINK_RUNTIME_STUBS"] = "1";
        S["FUSE_AUTO_DETECT"] = "1";
        S["FUSE_AUTO_STUBS"] = "1";
        S["FUSE_PREFLIGHT"] = "1";
        S["FUSE_AST_PREFLIGHT"] = "1";
        return S;
    }

    void writeSuggestManifest(StringRef Path) const {
        if (Path.empty())
            return;
        std::error_code EC;
        raw_fd_ostream Out(Path, EC);
        if (EC)
            return;
        json::Object S = buildManifestSuggestions();
        Out << "# plc_ast manifest suggestions — review before use\n";
        for (auto &KV : S) {
            if (auto Sv = KV.second.getAsString())
                Out << KV.first << "='" << Sv->str() << "'\n";
        }
    }

    void addIssue(IssueSeverity Sev, StringRef Func, StringRef Detail,
                  unsigned Line, StringRef Msg,
                  IssueKind Kind = IssueKind::WcetRisk) {
        CycleIssue I;
        I.Sev = Sev;
        I.Kind = Kind;
        I.Function = Func.str();
        I.Call = Detail.str();
        I.Line = Line;
        I.Message = Msg.str();
        Issues.push_back(std::move(I));
        if (Kind == IssueKind::Sched) {
            if (Sev == IssueSeverity::Error)
                ++SchedCriticalCount;
            else
                ++SchedWarnCount;
        }
        if (Sev == IssueSeverity::Error)
            ++ErrorCount;
        else
            ++WarnCount;
    }

    void finalizeEntry() {
        for (const char **P = kEntryNames; *P; ++P) {
            if (EntriesFound.count(*P)) {
                PrimaryEntry = *P;
                return;
            }
        }
        if (EntriesFound.empty() && !OptNoShim)
            addIssue(IssueSeverity::Error, "", "",
                     0, "未找到 plc_cycle / plc_main / plc_logic 定义");
        else if (!EntriesFound.empty())
            PrimaryEntry = *EntriesFound.begin();
    }

    json::Object toJson() const {
        json::Object Root;
        Root["tool"] = "plc_ast";
        Root["version"] = 4;
        Root["source"] = SourcePath;
        Root["entry"] = PrimaryEntry;
        Root["ok"] = ErrorCount == 0 && fusionEligible();
        Root["fusion_eligible"] = fusionEligible();
        Root["fusion_critical_count"] = static_cast<int64_t>(FusionCriticalCount);
        Root["fusion_warn_count"] = static_cast<int64_t>(FusionWarnCount);
        Root["has_main"] = HasMain;
        Root["has_pthread_create"] = HasPthreadCreate;
        Root["float_anywhere"] = FloatAnywhere;

        json::Array Entries;
        for (const auto &E : EntriesFound)
            Entries.push_back(E);
        Root["entries_found"] = std::move(Entries);

        json::Array GArr;
        for (const auto &G : Globals) {
            json::Object Go;
            Go["name"] = G.Name;
            Go["line"] = G.Line;
            GArr.push_back(std::move(Go));
        }
        Root["globals"] = std::move(GArr);

        json::Array IArr;
        for (const auto &I : Issues) {
            json::Object Io;
            Io["severity"] = I.Sev == IssueSeverity::Error ? "error" : "warn";
            Io["kind"] = I.Kind == IssueKind::Float ? "float"
                       : I.Kind == IssueKind::Blocking ? "blocking"
                       : I.Kind == IssueKind::Sched ? "sched"
                                                       : "wcet_risk";
            Io["function"] = I.Function;
            Io["call"] = I.Call;
            Io["line"] = I.Line;
            Io["message"] = I.Message;
            IArr.push_back(std::move(Io));
        }
        Root["cycle_issues"] = std::move(IArr);

        json::Array SArr;
        for (const auto &I : Issues) {
            if (I.Kind != IssueKind::Sched)
                continue;
            json::Object So;
            So["severity"] = I.Sev == IssueSeverity::Error ? "error" : "warn";
            So["function"] = I.Function;
            So["symbol"] = I.Call;
            So["line"] = I.Line;
            So["message"] = I.Message;
            SArr.push_back(std::move(So));
        }
        for (const auto &F : FusionIssues) {
            if (F.Message.find("递归") == std::string::npos)
                continue;
            json::Object So;
            So["severity"] = F.Sev == IssueSeverity::Error ? "error" : "warn";
            So["function"] = F.Context;
            So["symbol"] = F.Symbol;
            So["line"] = F.Line;
            So["message"] = F.Message;
            SArr.push_back(std::move(So));
        }
        Root["sched_issues"] = std::move(SArr);
        Root["sched_critical_count"] =
            static_cast<int64_t>(SchedCriticalCount);
        Root["sched_warn_count"] = static_cast<int64_t>(SchedWarnCount);
        Root["sched_ok"] = SchedCriticalCount == 0;
        Root["is_cplusplus"] = IsCPlusPlus;
        Root["cpp_subset_eligible"] = fusionEligible();
        Root["indirect_resolved_count"] =
            static_cast<int64_t>(IndirectResolvedCount);
        Root["indirect_unresolved_count"] =
            static_cast<int64_t>(IndirectUnresolvedCount);

        json::Array FArr;
        for (const auto &F : FusionIssues) {
            json::Object Fo;
            Fo["severity"] =
                F.Sev == IssueSeverity::Error ? "critical" : "warn";
            Fo["symbol"] = F.Symbol;
            Fo["context"] = F.Context;
            Fo["line"] = F.Line;
            Fo["message"] = F.Message;
            FArr.push_back(std::move(Fo));
        }
        Root["fusion_issues"] = std::move(FArr);

        json::Object CG;
        for (const auto &KV : CallGraph) {
            json::Array Callees;
            for (const auto &C : KV.second)
                Callees.push_back(C);
            CG[KV.first] = std::move(Callees);
        }
        Root["call_graph"] = std::move(CG);
        Root["manifest_suggestions"] = buildManifestSuggestions();

        Root["error_count"] = static_cast<int64_t>(ErrorCount);
        Root["warn_count"] = static_cast<int64_t>(WarnCount);
        Root["float_in_cycle"] = FloatInCycle;
        return Root;
    }
};

static PLCAnalysis *gAnalysis = nullptr;

static bool callListed(const char *const *List, StringRef Name) {
    for (const char *const *P = List; *P; ++P) {
        if (Name == *P)
            return true;
    }
    return false;
}

static unsigned lineOf(ASTContext &Ctx, SourceLocation Loc) {
    if (Loc.isInvalid())
        return 0;
    return Ctx.getSourceManager().getSpellingLineNumber(Loc);
}

static const FunctionDecl *resolveCalleeOneLayer(const Expr *CalleeE) {
    if (!CalleeE)
        return nullptr;
    CalleeE = CalleeE->IgnoreImpCasts();

    if (auto *DRE = dyn_cast<DeclRefExpr>(CalleeE)) {
        if (auto *FD = dyn_cast<FunctionDecl>(DRE->getDecl()))
            return FD;
        if (auto *VD = dyn_cast<VarDecl>(DRE->getDecl())) {
            if (const Expr *Init = VD->getInit())
                return resolveCalleeOneLayer(Init);
        }
    }

    if (auto *UO = dyn_cast<UnaryOperator>(CalleeE)) {
        if (UO->getOpcode() == UO_AddrOf)
            return resolveCalleeOneLayer(UO->getSubExpr());
    }
    return nullptr;
}

class PLCAstVisitor : public RecursiveASTVisitor<PLCAstVisitor> {
public:
    PLCAstVisitor(ASTContext *Ctx, Rewriter *RW, bool DoRewrite)
        : Context(Ctx), TheRewriter(RW), DoRewrite(DoRewrite) {}

    bool TraverseFunctionDecl(FunctionDecl *FD) {
        if (FD->doesThisDeclarationHaveABody()) {
            CurrentFunction = FD;
            bool Ret = RecursiveASTVisitor::TraverseFunctionDecl(FD);
            CurrentFunction = nullptr;
            return Ret;
        }
        return RecursiveASTVisitor::TraverseFunctionDecl(FD);
    }

    bool VisitFunctionDecl(FunctionDecl *FD) {
        if (!FD->isThisDeclarationADefinition())
            return true;
        StringRef Name = FD->getName();
        gAnalysis->DefinedFunctions.insert(Name.str());
        if (Name == "main")
            gAnalysis->HasMain = true;
        if (isEntryName(Name)) {
            gAnalysis->EntriesFound.insert(Name.str());
            if (DoRewrite && TheRewriter && Name == "plc_cycle") {
                TheRewriter->InsertText(
                    FD->getBeginLoc(),
                    "__attribute__((noinline, annotate(\"plc_rt_task\"))) ",
                    false, true);
            }
        }
        return true;
    }

    void checkFusionCall(StringRef FuncName, unsigned Ln, StringRef InFunc) {
        if (callListed(kFusionCriticalCalls, FuncName)) {
            gAnalysis->addFusionIssue(
                IssueSeverity::Error, FuncName, InFunc, Ln,
                "该 API 通常无法在内核模块融合路径直接使用");
            return;
        }
        if (callListed(kFusionWarnCalls, FuncName)) {
            gAnalysis->addFusionIssue(
                IssueSeverity::Warn, FuncName, InFunc, Ln,
                "网络/IPC 类 API 需映射或桩，融合前请确认");
        }
    }

    bool inEntryFunction() const {
        return CurrentFunction && isEntryName(CurrentFunction->getName());
    }

    void noteFloatInCycle(StringRef InFunc, StringRef Detail, unsigned Ln) {
        gAnalysis->FloatInCycle = true;
        gAnalysis->FloatAnywhere = true;
        std::string Key = InFunc.str();
        if (gAnalysis->FloatWarnedFuncs.count(Key))
            return;
        gAnalysis->FloatWarnedFuncs.insert(Key);
        IssueSeverity Sev =
            OptStrict ? IssueSeverity::Error : IssueSeverity::Warn;
        gAnalysis->addIssue(
            Sev, InFunc, Detail, Ln,
            "周期函数内存在浮点运算（WCET/软浮点风险，strict 下为 error）",
            IssueKind::Float);
    }

    bool checkFloatExpr(Expr *E) {
        if (!E || !inEntryFunction())
            return true;
        if (!E->getType()->isFloatingType())
            return false;
        noteFloatInCycle(CurrentFunction->getName(), "float_expr",
                       lineOf(*Context, E->getBeginLoc()));
        return true;
    }

    bool VisitFloatingLiteral(FloatingLiteral *FL) {
        checkFloatExpr(FL);
        return true;
    }

    bool VisitBinaryOperator(BinaryOperator *BO) {
        if (inEntryFunction() && BO->getType()->isFloatingType())
            noteFloatInCycle(CurrentFunction->getName(), "float_arith",
                           lineOf(*Context, BO->getBeginLoc()));
        return true;
    }

    bool VisitUnaryOperator(UnaryOperator *UO) {
        checkFloatExpr(UO);
        return true;
    }

    bool VisitNamespaceDecl(NamespaceDecl *ND) {
        if (!ND || !ND->isFileContext())
            return true;
        if (ND->isAnonymousNamespace())
            return true;
        gAnalysis->addFusionIssue(
            IssueSeverity::Error, "namespace", ND->getName(), 0,
            "C++ namespace 不在 extern-C 可融合子集内");
        return true;
    }

    bool VisitCXXRecordDecl(CXXRecordDecl *D) {
        if (!D || !D->isCompleteDefinition())
            return true;
        if (D->isClass()) {
            gAnalysis->addFusionIssue(
                IssueSeverity::Error, "class", D->getName(),
                lineOf(*Context, D->getBeginLoc()),
                "C++ class 不在 extern-C 可融合子集内");
            return true;
        }
        if (D->isStruct()) {
            for (auto *M : D->methods()) {
                if (M->isUserProvided()) {
                    gAnalysis->addFusionIssue(
                        IssueSeverity::Error, "struct_method", M->getName(),
                        lineOf(*Context, M->getBeginLoc()),
                        "带成员函数的 C++ struct 不在 extern-C 子集内");
                    break;
                }
            }
        }
        return true;
    }

    bool VisitFunctionTemplateDecl(FunctionTemplateDecl *FTD) {
        if (!FTD)
            return true;
        gAnalysis->addFusionIssue(
            IssueSeverity::Error, "template", FTD->getName(),
            lineOf(*Context, FTD->getBeginLoc()),
            "C++ 模板不在 extern-C 可融合子集内");
        return true;
    }

    bool VisitCXXNewExpr(CXXNewExpr *E) {
        gAnalysis->addFusionIssue(
            IssueSeverity::Error, "new", "", lineOf(*Context, E->getBeginLoc()),
            "C++ new 不在 extern-C 可融合子集内");
        return true;
    }

    bool VisitCXXDeleteExpr(CXXDeleteExpr *E) {
        gAnalysis->addFusionIssue(
            IssueSeverity::Error, "delete", "",
            lineOf(*Context, E->getBeginLoc()),
            "C++ delete 不在 extern-C 可融合子集内");
        return true;
    }

    bool VisitCXXThrowExpr(CXXThrowExpr *E) {
        gAnalysis->addFusionIssue(
            IssueSeverity::Error, "throw", "", lineOf(*Context, E->getBeginLoc()),
            "C++ 异常不在 extern-C 可融合子集内");
        return true;
    }

    bool VisitCXXTryStmt(CXXTryStmt *S) {
        gAnalysis->addFusionIssue(
            IssueSeverity::Error, "try", "", lineOf(*Context, S->getBeginLoc()),
            "C++ try/catch 不在 extern-C 可融合子集内");
        return true;
    }

    bool VisitWhileStmt(WhileStmt *WS) {
        if (!inEntryFunction() || !CurrentFunction)
            return true;
        Expr *Cond = WS->getCond();
        if (!Cond)
            return true;
        Cond = Cond->IgnoreImpCasts();
        bool TrueLoop = false;
        if (auto *IL = dyn_cast<IntegerLiteral>(Cond))
            TrueLoop = IL->getValue() != 0;
        else if (auto *BL = dyn_cast<CXXBoolLiteralExpr>(Cond))
            TrueLoop = BL->getValue();
        if (TrueLoop) {
            IssueSeverity Sev =
                OptStrict ? IssueSeverity::Error : IssueSeverity::Warn;
            gAnalysis->addIssue(
                Sev, CurrentFunction->getName(), "while(true)",
                lineOf(*Context, WS->getBeginLoc()),
                "周期函数内疑似无限循环（可调度性风险）", IssueKind::Sched);
        }
        return true;
    }

    bool VisitCallExpr(CallExpr *Call) {
        unsigned Ln = lineOf(*Context, Call->getBeginLoc());
        StringRef InFunc =
            CurrentFunction ? CurrentFunction->getName() : StringRef();

        const FunctionDecl *Callee = Call->getDirectCallee();
        bool ResolvedIndirect = false;
        if (!Callee) {
            Callee = resolveCalleeOneLayer(Call->getCallee());
            if (Callee)
                ResolvedIndirect = true;
        }

        if (CurrentFunction) {
            if (Callee) {
                if (ResolvedIndirect)
                    gAnalysis->IndirectResolvedCount++;
                StringRef FuncName = Callee->getName();
                gAnalysis->CallGraph[InFunc.str()].insert(FuncName.str());
                if (FuncName == InFunc) {
                    gAnalysis->addFusionIssue(
                        IssueSeverity::Warn, FuncName, InFunc, Ln,
                        "递归调用使 WCET 不可界");
                }
                if (FuncName == "pthread_create")
                    gAnalysis->HasPthreadCreate = true;
                checkFusionCall(FuncName, Ln, InFunc);
            } else {
                gAnalysis->IndirectUnresolvedCount++;
                gAnalysis->addFusionIssue(
                    IssueSeverity::Warn, "indirect_call", InFunc, Ln,
                    "间接函数调用，Pass 可能无法映射目标符号");
            }
        }

        if (!Callee || !inEntryFunction())
            return true;

        StringRef FuncName = Callee->getName();

        if (callListed(kFloatMathCalls, FuncName)) {
            noteFloatInCycle(InFunc, FuncName, Ln);
            return true;
        }

        if (callListed(kBlockingCalls, FuncName)) {
            gAnalysis->addIssue(
                IssueSeverity::Error, InFunc, FuncName, Ln,
                "周期函数内不应调用阻塞/睡眠 API", IssueKind::Blocking);
            return true;
        }

        if (callListed(kSchedCalls, FuncName)) {
            IssueSeverity Sev =
                OptStrict ? IssueSeverity::Error : IssueSeverity::Warn;
            gAnalysis->addIssue(
                Sev, InFunc, FuncName, Ln,
                "周期函数内锁/信号量等待可能影响可调度性（strict 下为 error）",
                IssueKind::Sched);
            return true;
        }

        if (callListed(kWcetRiskCalls, FuncName)) {
            IssueSeverity Sev =
                OptStrict ? IssueSeverity::Error : IssueSeverity::Warn;
            gAnalysis->addIssue(
                Sev, InFunc, FuncName, Ln,
                "周期函数内动态分配或 I/O 会增加抖动（strict 模式下为 error）",
                IssueKind::WcetRisk);
        }

        if (Call->getType()->isFloatingType())
            noteFloatInCycle(InFunc, FuncName, Ln);

        if (DoRewrite && TheRewriter) {
            static const std::pair<const char *, const char *> kRemap[] = {
    {"malloc", "plc_kmalloc"},
    {"calloc", "plc_kcalloc"},
    {"free", "plc_kfree"},
    {"printf", "plc_printk"},
    {"fprintf", "plc_fprintf"},
    {"puts", "plc_printk"},
    {"clock_gettime", "plc_ktime_get_ts"},
    {"nanosleep", "plc_nanosleep"},
    {"clock_nanosleep", "plc_clock_nanosleep"},
    {"timer_create", "plc_timer_create"},
    {"timer_settime", "plc_timer_settime"},
    {"timer_delete", "plc_timer_delete"},
    {"sigwait", "plc_sigwait"},
    {"pthread_mutex_lock", "plc_mutex_lock"},
    {"pthread_mutex_unlock", "plc_mutex_unlock"},
};
            for (const auto &R : kRemap) {
                if (FuncName == R.first) {
                    TheRewriter->ReplaceText(Call->getBeginLoc(),
                                             StringRef(R.first).size(), R.second);
                    break;
                }
            }
        }
        return true;
    }

    bool VisitVarDecl(VarDecl *VD) {
        if (VD->hasGlobalStorage() && !VD->isStaticLocal() &&
            !isa<ParmVarDecl>(VD)) {
            GlobalInfo G;
            G.Name = VD->getNameAsString();
            G.Line = lineOf(*Context, VD->getBeginLoc());
            gAnalysis->Globals.push_back(std::move(G));
            if (VD->getType()->isFloatingType())
                gAnalysis->FloatAnywhere = true;
            if (DoRewrite && TheRewriter) {
                TheRewriter->InsertText(
                    VD->getBeginLoc(),
                    "__attribute__((section(\".plc_isolation_data\"))) ", false,
                    true);
            }
        }
        if (inEntryFunction() && VD->getType()->isFloatingType()) {
            gAnalysis->FloatAnywhere = true;
            noteFloatInCycle(CurrentFunction->getName(), VD->getNameAsString(),
                           lineOf(*Context, VD->getBeginLoc()));
        }
        return true;
    }

private:
    ASTContext *Context;
    Rewriter *TheRewriter;
    bool DoRewrite;
    const FunctionDecl *CurrentFunction = nullptr;
};

class PLCASTConsumer : public ASTConsumer {
public:
    PLCASTConsumer(ASTContext *Ctx, Rewriter *RW, bool DoRewrite)
        : Visitor(Ctx, RW, DoRewrite) {}

    void HandleTranslationUnit(ASTContext &Ctx) override {
        gAnalysis->IsCPlusPlus = Ctx.getLangOpts().CPlusPlus;
        Visitor.TraverseDecl(Ctx.getTranslationUnitDecl());
        gAnalysis->finalizeEntry();
        if (gAnalysis->IsCPlusPlus && gAnalysis->fusionEligible()) {
            gAnalysis->addFusionIssue(
                IssueSeverity::Warn, "C++", "", 0,
                "C++ extern-C 子集：已通过子集检查（无 class/template/异常）");
        }
    }

private:
    PLCAstVisitor Visitor;
};

class PLCFrontendAction : public ASTFrontendAction {
public:
    PLCFrontendAction(bool AnalyzeOnly, bool EmitKernelC)
        : AnalyzeOnlyMode(AnalyzeOnly), EmitKernelCMode(EmitKernelC) {}

    std::unique_ptr<ASTConsumer>
    CreateASTConsumer(CompilerInstance &CI, StringRef File) override {
        DoRewrite = EmitKernelCMode && !AnalyzeOnlyMode;
        if (DoRewrite) {
            TheRewriter.setSourceMgr(CI.getSourceManager(), CI.getLangOpts());
            return std::make_unique<PLCASTConsumer>(&CI.getASTContext(),
                                                    &TheRewriter, true);
        }
        return std::make_unique<PLCASTConsumer>(&CI.getASTContext(), nullptr,
                                                false);
    }

    void EndSourceFileAction() override {
        gAnalysis->SourcePath = getCurrentFile().str();
        gAnalysis->finalizeEntry();

        std::string JsonPath = OptJsonOut;
        if (JsonPath.empty())
            JsonPath = gAnalysis->SourcePath + ".plc_ast.json";

        json::Object Root = gAnalysis->toJson();
        std::string JsonText;
        raw_string_ostream JOS(JsonText);
        JOS << formatv("{0:2}", json::Value(std::move(Root)));
        JOS.flush();

        std::error_code EC;
        raw_fd_ostream JsonFile(JsonPath, EC);
        if (EC) {
            errs() << "plc_ast: 无法写入 JSON: " << JsonPath << "\n";
        } else {
            JsonFile << JsonText << "\n";
        }

        outs() << ">>> plc_ast: entry=" << gAnalysis->PrimaryEntry
               << " fusion_eligible=" << (gAnalysis->fusionEligible() ? "yes" : "no")
               << " errors=" << gAnalysis->ErrorCount
               << " fusion_crit=" << gAnalysis->FusionCriticalCount
               << " warns=" << gAnalysis->WarnCount
               << " json=" << JsonPath << "\n";

        for (const auto &F : gAnalysis->FusionIssues) {
            if (F.Sev == IssueSeverity::Error)
                errs() << "  F-CRIT ";
            else
                errs() << "  F-WARN ";
            if (F.Line)
                errs() << "L" << F.Line << " ";
            if (!F.Context.empty())
                errs() << F.Context << ": ";
            if (!F.Symbol.empty())
                errs() << F.Symbol << " — ";
            errs() << F.Message << "\n";
        }

        for (const auto &I : gAnalysis->Issues) {
            if (I.Sev == IssueSeverity::Error)
                errs() << "  ERROR ";
            else
                errs() << "  WARN  ";
            if (I.Line)
                errs() << "L" << I.Line << " ";
            if (!I.Function.empty())
                errs() << I.Function << ": ";
            if (!I.Call.empty())
                errs() << I.Call << " — ";
            errs() << I.Message << "\n";
        }

        gAnalysis->writeSuggestManifest(OptSuggestManifest);

        if (AnalyzeOnlyMode || !DoRewrite)
            return;

        SourceLocation StartOfFile = TheRewriter.getSourceMgr().getLocForStartOfFile(
            TheRewriter.getSourceMgr().getMainFileID());
        std::string Decls =
            "// PLC Kernel stubs (legacy --emit-kernel-c; prefer shim+Pass)\n"
            "extern void *plc_kmalloc(unsigned long size);\n"
            "extern void *plc_kcalloc(unsigned long n, unsigned long size);\n"
            "extern void plc_kfree(void *ptr);\n"
            "extern int plc_printk(const char *fmt, ...);\n\n";
        TheRewriter.InsertText(StartOfFile, Decls, false, true);

        std::string DestFile = gAnalysis->SourcePath + ".kernel.c";
        std::error_code WEC;
        raw_fd_ostream Out(DestFile, WEC);
        if (!WEC) {
            TheRewriter.getEditBuffer(
                TheRewriter.getSourceMgr().getMainFileID())
                .write(Out);
            outs() << ">>> 已生成 .kernel.c（legacy）: " << DestFile << "\n";
        }
    }

private:
    bool AnalyzeOnlyMode;
    bool EmitKernelCMode;
    bool DoRewrite = false;
    Rewriter TheRewriter;
};

class PLCActionFactory : public FrontendActionFactory {
public:
    PLCActionFactory(bool AnalyzeOnly, bool EmitKernelC)
        : AnalyzeOnlyMode(AnalyzeOnly), EmitKernelCMode(EmitKernelC) {}

    std::unique_ptr<FrontendAction> create() override {
        return std::make_unique<PLCFrontendAction>(AnalyzeOnlyMode,
                                                   EmitKernelCMode);
    }

private:
    bool AnalyzeOnlyMode;
    bool EmitKernelCMode;
};

static std::string resolveShimInclude() {
    if (const char *Env = std::getenv("PLC_CC_SHIM")) {
        if (Env[0])
            return Env;
    }
    static const char *kCandidates[] = {
        "examples/plc-cc__低抖动示例/plc_cc_fuse_shim__融合头.h",
        "../examples/plc-cc__低抖动示例/plc_cc_fuse_shim__融合头.h",
        nullptr,
    };
    for (const char **P = kCandidates; *P; ++P) {
        if (std::ifstream(*P).good())
            return *P;
    }
    return "examples/plc-cc__低抖动示例/plc_cc_fuse_shim__融合头.h";
}

int main(int argc, const char **argv) {
    auto ExpectedParser = CommonOptionsParser::create(argc, argv, PLCToolCategory);
    if (!ExpectedParser) {
        errs() << ExpectedParser.takeError();
        return 1;
    }
    CommonOptionsParser &OptionsParser = ExpectedParser.get();

    const bool analyzeOnly = OptAnalyzeOnly || !OptEmitKernelC;
    PLCAnalysis Analysis;
    gAnalysis = &Analysis;

    ClangTool Tool(OptionsParser.getCompilations(),
                   OptionsParser.getSourcePathList());
    Tool.appendArgumentsAdjuster(getClangStripOutputAdjuster());
    Tool.appendArgumentsAdjuster(getClangStripDependencyFileAdjuster());

    const std::string Shim = resolveShimInclude();
    if (!OptNoShim) {
        Tool.appendArgumentsAdjuster([Shim](const CommandLineArguments &Args,
                                            StringRef /*File*/) {
            CommandLineArguments Out = Args;
            Out.push_back("-Wno-implicit-function-declaration");
            Out.push_back("-Wno-builtin-requires-header");
            Out.push_back("-include");
            Out.push_back(Shim);
            return Out;
        });
    } else {
        Tool.appendArgumentsAdjuster([](const CommandLineArguments &Args,
                                        StringRef /*File*/) {
            CommandLineArguments Out = Args;
            Out.push_back("-Wno-implicit-function-declaration");
            return Out;
        });
    }

    PLCActionFactory Factory(analyzeOnly, OptEmitKernelC);
    int ToolRc = Tool.run(&Factory);

    if (ToolRc != 0)
        return ToolRc;
    if (Analysis.FusionCriticalCount > 0 && OptFusionStrict)
        return 1;
    if (Analysis.ErrorCount > 0)
        return 1;
    return 0;
}
