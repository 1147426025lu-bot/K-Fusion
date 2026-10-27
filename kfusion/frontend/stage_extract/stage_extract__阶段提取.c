/*
 * stage_extract__阶段提取.c — 从 C 周期程序提取「入口 / 阶段 / 全局状态」。
 *
 * 实现路线：clang-16 先把源码编成 LLVM bitcode（-O0 -emit-llvm），本工具用
 * **LLVM C API**（llvm-c/Core.h + BitReader.h）遍历 IR，得到：
 *   · 入口函数（plc_cycle/plc_main/plc_logic，或命令行指定）
 *   · 入口里调用到的、**在本模块内有定义**的函数 = 阶段（按调用出现顺序，去重）
 *   · 模块级全局变量（名字 + IR 类型）
 *
 * 为什么走 LLVM C API 而不是 libclang：目标板 Pi4 的 Debian 只提供 libclang-16
 * 运行库、C++ Clang 头不打包，apt 装 libclang-16-dev 又极慢；而 llvm-16-dev 已装、
 * clang-16 可出 bitcode，这条路立刻可编译、且直接是 IR 级（比 AST 更接近数据流）。
 *
 * 输出 JSON 到 stdout。退出码：0=找到入口；4=没找到；2=参数错；3=读/解析失败。
 * 本工具只如实提取结构，不做验证/拒绝；正确性由 Gate 3 独立验证链把关。
 */
#include <llvm-c/Core.h>
#include <llvm-c/BitReader.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ITEMS 256
#define CAP 200

typedef struct {
	char name[CAP];
	char type[CAP];
} Item;

/* K-C0 拒绝项：某函数里出现了不被支持的外部调用 */
typedef struct {
	char function[CAP];
	char call[CAP];
	char kind[32];
	char reason[200];
} Rej;

typedef struct {
	const char *entry_want;
	char entry[CAP];
	int found;
	Item stages[MAX_ITEMS];
	int nstages;
	Item globals[MAX_ITEMS];
	int nglobals;
	Rej rej[MAX_ITEMS];
	int nrej;
} Ctx;

static int is_auto_entry(const char *n)
{
	return strcmp(n, "plc_cycle") == 0 ||
	       strcmp(n, "plc_main") == 0 ||
	       strcmp(n, "plc_logic") == 0;
}

static int is_intrinsic(const char *n)
{
	return n && strncmp(n, "llvm.", 5) == 0;
}

static int stage_exists(const Ctx *ctx, const char *n)
{
	int i;

	for (i = 0; i < ctx->nstages; i++)
		if (strcmp(ctx->stages[i].name, n) == 0)
			return 1;
	return 0;
}

/* ---- K-C0：对单个函数里的外部调用分类并记录拒绝理由（fail-closed）---- */
static int in_list(const char *n, const char *const *list)
{
	int i;

	for (i = 0; list[i]; i++)
		if (strcmp(n, list[i]) == 0)
			return 1;
	return 0;
}

static void classify_ext(const char *n, char *kind, size_t kc,
			 char *reason, size_t rc)
{
	static const char *const blk[] = {
		"nanosleep", "clock_nanosleep", "usleep", "sleep", "sched_yield",
		"pthread_join", "pthread_mutex_lock", "pthread_mutex_unlock",
		"sem_wait", "sem_post", "sem_timedwait", "pause", "waitpid", NULL
	};
	static const char *const mem[] = {
		"malloc", "calloc", "realloc", "free", "alloca", "mmap",
		"munmap", "brk", "sbrk", NULL
	};
	static const char *const io[] = {
		"printf", "fprintf", "sprintf", "snprintf", "puts", "fputs",
		"fwrite", "write", "read", "open", "close", "fopen", "fclose",
		"scanf", "fscanf", "putchar", "getchar", "ioctl", NULL
	};

	if (in_list(n, blk)) {
		snprintf(kind, kc, "block");
		snprintf(reason, rc, "可能阻塞/调度原语（内核周期体禁止睡眠/阻塞）");
	} else if (in_list(n, mem)) {
		snprintf(kind, kc, "mem");
		snprintf(reason, rc, "动态内存（K-C0 要求固定布局状态）");
	} else if (in_list(n, io)) {
		snprintf(kind, kc, "io");
		snprintf(reason, rc, "I/O/打印（增加抖动、非确定性）");
	} else {
		snprintf(kind, kc, "unknown");
		snprintf(reason, rc, "未归档的外部调用（无法证明副作用，fail-closed）");
	}
}

static void check_kc0(LLVMValueRef fn, const char *fname, Ctx *ctx)
{
	LLVMBasicBlockRef bb;

	for (bb = LLVMGetFirstBasicBlock(fn); bb; bb = LLVMGetNextBasicBlock(bb)) {
		LLVMValueRef ins;

		for (ins = LLVMGetFirstInstruction(bb); ins;
		     ins = LLVMGetNextInstruction(ins)) {
			LLVMValueRef called, cf;
			const char *cn;

			if (LLVMGetInstructionOpcode(ins) != LLVMCall)
				continue;
			called = LLVMGetCalledValue(ins);
			if (!called)
				continue;
			cf = LLVMIsAFunction(called);
			if (!cf)
				continue;
			cn = LLVMGetValueName(cf);
			if (!cn || is_intrinsic(cn))
				continue;
			/* 只判"外部"调用：本模块内定义的函数另有定义（不在本函数里查） */
			if (LLVMCountBasicBlocks(cf) != 0)
				continue;
			if (ctx->nrej >= MAX_ITEMS)
				continue;
			snprintf(ctx->rej[ctx->nrej].function, CAP, "%s", fname);
			snprintf(ctx->rej[ctx->nrej].call, CAP, "%s", cn);
			classify_ext(cn, ctx->rej[ctx->nrej].kind,
				     sizeof ctx->rej[ctx->nrej].kind,
				     ctx->rej[ctx->nrej].reason,
				     sizeof ctx->rej[ctx->nrej].reason);
			ctx->nrej++;
		}
	}
}

static void type_str(LLVMTypeRef t, char *dst, size_t cap)
{
	char *s = LLVMPrintTypeToString(t);

	snprintf(dst, cap, "%s", s ? s : "?");
	if (s)
		LLVMDisposeMessage(s);
}

static void collect_globals(LLVMModuleRef mod, Ctx *ctx)
{
	LLVMValueRef g;

	for (g = LLVMGetFirstGlobal(mod); g; g = LLVMGetNextGlobal(g)) {
		const char *nm = LLVMGetValueName(g);

		if (!nm || is_intrinsic(nm) || ctx->nglobals >= MAX_ITEMS)
			continue;
		if (nm[0] == '.')	/* 编译器内部全局（.str/.L... 字面量），非用户状态 */
			continue;
		snprintf(ctx->globals[ctx->nglobals].name, CAP, "%s", nm);
		/* 取值类型（i32），而不是全局指针类型（ptr） */
		type_str(LLVMGlobalGetValueType(g),
			 ctx->globals[ctx->nglobals].type, CAP);
		ctx->nglobals++;
	}
}

static void collect_stages(LLVMValueRef fn, Ctx *ctx)
{
	LLVMBasicBlockRef bb;

	for (bb = LLVMGetFirstBasicBlock(fn); bb; bb = LLVMGetNextBasicBlock(bb)) {
		LLVMValueRef ins;

		for (ins = LLVMGetFirstInstruction(bb); ins;
		     ins = LLVMGetNextInstruction(ins)) {
			LLVMValueRef called, called_fn;
			const char *nm;

			if (LLVMGetInstructionOpcode(ins) != LLVMCall)
				continue;
			called = LLVMGetCalledValue(ins);
			if (!called)
				continue;
			called_fn = LLVMIsAFunction(called);
			if (!called_fn)
				continue;
			nm = LLVMGetValueName(called_fn);
			if (!nm || is_intrinsic(nm))
				continue;
			/* 只算本模块内有定义的函数（有基本块），排除入口自身 */
			if (LLVMCountBasicBlocks(called_fn) == 0)
				continue;
			if (strcmp(nm, ctx->entry) == 0)
				continue;
			if (stage_exists(ctx, nm) || ctx->nstages >= MAX_ITEMS)
				continue;
			snprintf(ctx->stages[ctx->nstages].name, CAP, "%s", nm);
			/* 取函数值类型（如 void ()），而不是 ptr */
			type_str(LLVMGlobalGetValueType(called_fn),
				 ctx->stages[ctx->nstages].type, CAP);
			ctx->nstages++;
		}
	}
}

static void json_str(const char *s, FILE *o)
{
	const unsigned char *p;

	for (p = (const unsigned char *)s; p && *p; p++) {
		if (*p == '"' || *p == '\\') {
			fputc('\\', o);
			fputc(*p, o);
		} else if (*p == '\n') {
			fputs("\\n", o);
		} else if (*p == '\t') {
			fputs("\\t", o);
		} else {
			fputc(*p, o);
		}
	}
}

int main(int argc, char **argv)
{
	const char *bc, *entry = NULL;
	Ctx ctx;
	LLVMContextRef cctx;
	LLVMMemoryBufferRef mb;
	LLVMModuleRef mod;
	LLVMValueRef fn;
	LLVMBool err;
	int i;

	if (argc < 2) {
		fprintf(stderr, "用法: %s <file.bc> [--entry=名字|名字]\n", argv[0]);
		return 2;
	}
	bc = argv[1];
	for (i = 2; i < argc; i++) {
		if (strncmp(argv[i], "--entry=", 8) == 0)
			entry = argv[i] + 8;
		else if (argv[i][0] != '-')
			entry = argv[i];
	}

	memset(&ctx, 0, sizeof ctx);
	ctx.entry_want = entry;

	{
		char *msg = NULL;

		if (LLVMCreateMemoryBufferWithContentsOfFile(bc, &mb, &msg)) {
			fprintf(stderr, "stage_extract: 读取 %s 失败: %s\n",
				bc, msg ? msg : "?");
			if (msg)
				LLVMDisposeMessage(msg);
			return 3;
		}
	}
	cctx = LLVMContextCreate();
	err = LLVMParseBitcodeInContext2(cctx, mb, &mod);
	if (err) {
		/* 失败时 buffer 归我们所有 */
		fprintf(stderr, "stage_extract: 解析 bitcode 失败（%s）\n", bc);
		LLVMDisposeMemoryBuffer(mb);
		LLVMContextDispose(cctx);
		return 3;
	}

	collect_globals(mod, &ctx);

	for (fn = LLVMGetFirstFunction(mod); fn && !ctx.found;
	     fn = LLVMGetNextFunction(fn)) {
		const char *nm = LLVMGetValueName(fn);
		int want;

		if (!nm || LLVMCountBasicBlocks(fn) == 0)
			continue;
		want = ctx.entry_want ? (strcmp(nm, ctx.entry_want) == 0)
				      : is_auto_entry(nm);
		if (want) {
			snprintf(ctx.entry, CAP, "%s", nm);
			ctx.found = 1;
			collect_stages(fn, &ctx);
		}
	}

	/* K-C0 检查：对除 main 外的每个本模块函数，检查其外部调用是否可进内核。
	 * fail-closed：任何未归档外部调用都记一条拒绝理由（不静默放行）。 */
	for (fn = LLVMGetFirstFunction(mod); fn; fn = LLVMGetNextFunction(fn)) {
		const char *nm = LLVMGetValueName(fn);

		if (!nm || is_intrinsic(nm) || LLVMCountBasicBlocks(fn) == 0)
			continue;
		if (strcmp(nm, "main") == 0)
			continue;
		check_kc0(fn, nm, &ctx);
	}

	printf("{\n");
	printf("  \"tool\": \"stage_extract\", \"version\": 2,\n");
	printf("  \"source\": \"");
	json_str(bc, stdout);
	printf("\",\n");
	if (ctx.found) {
		printf("  \"entry\": \"");
		json_str(ctx.entry, stdout);
		printf("\",\n");
	} else {
		printf("  \"entry\": null,\n");
	}
	printf("  \"stages\": [");
	for (i = 0; i < ctx.nstages; i++) {
		printf("%s\n    {\"name\": \"", i ? "," : "");
		json_str(ctx.stages[i].name, stdout);
		printf("\", \"fn_type\": \"");
		json_str(ctx.stages[i].type, stdout);
		printf("\"}");
	}
	printf("%s],\n", ctx.nstages ? "\n  " : "");
	printf("  \"globals\": [");
	for (i = 0; i < ctx.nglobals; i++) {
		printf("%s\n    {\"name\": \"", i ? "," : "");
		json_str(ctx.globals[i].name, stdout);
		printf("\", \"type\": \"");
		json_str(ctx.globals[i].type, stdout);
		printf("\"}");
	}
	printf("%s],\n", ctx.nglobals ? "\n  " : "");
	/* K-C0 结论：ok 当且仅当没有任何拒绝项 */
	printf("  \"k_c0\": {\n    \"ok\": %s,\n    \"rejections\": [",
	       ctx.nrej == 0 ? "true" : "false");
	for (i = 0; i < ctx.nrej; i++) {
		printf("%s\n      {\"function\": \"", i ? "," : "");
		json_str(ctx.rej[i].function, stdout);
		printf("\", \"call\": \"");
		json_str(ctx.rej[i].call, stdout);
		printf("\", \"kind\": \"");
		json_str(ctx.rej[i].kind, stdout);
		printf("\", \"reason\": \"");
		json_str(ctx.rej[i].reason, stdout);
		printf("\"}");
	}
	printf("%s]\n  },\n", ctx.nrej ? "\n    " : "");
	printf("  \"ok\": %s\n", ctx.found ? "true" : "false");
	printf("}\n");

	LLVMDisposeModule(mod);
	LLVMContextDispose(cctx);
	return ctx.found ? 0 : 4;
}
