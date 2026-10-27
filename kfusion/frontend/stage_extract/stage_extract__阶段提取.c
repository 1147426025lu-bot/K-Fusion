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

typedef struct {
	const char *entry_want;
	char entry[CAP];
	int found;
	Item stages[MAX_ITEMS];
	int nstages;
	Item globals[MAX_ITEMS];
	int nglobals;
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
	printf("  \"ok\": %s\n", ctx.found ? "true" : "false");
	printf("}\n");

	LLVMDisposeModule(mod);
	LLVMContextDispose(cctx);
	return ctx.found ? 0 : 4;
}
