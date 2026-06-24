/*
 * cpp_extern_c__extern-C示例子.cpp — extern-C 子集 smoke（plc_ast + 函数指针 1 层）
 */
extern "C" {

typedef void (*step_fn)(void);

static void fp_target(void) {}

static step_fn g_step = fp_target;

void plc_cycle(void) {
    g_step();
}

}
