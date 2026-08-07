/*
 * cpp_extern_c_bad__extern-C负例.cpp — 应被 AST 拒收（含 class）
 */
extern "C" {

class Bad {
public:
    void run();
};

void plc_cycle(void) {}

}
