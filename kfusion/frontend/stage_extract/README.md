# stage_extract — 同源 C 周期函数阶段提取（Gate 1）

**角色**：Gate 1 的第一块——从**既有 C 周期程序**自动提取周期入口、被调用的阶段函数、
全局状态与类型，产出机器可消费的 JSON，供下游「AST/IR → plan/dag → emit」自动生成
用户态/内核态源码，**不再要求人工手写 DAG 或两份 stage 实现**。

## 实现路线（与最终代码一致）

先用 **`clang-16 -O0 -emit-llvm` 把源码编成 LLVM bitcode**，再用 **LLVM C API**
（`llvm-c/Core.h` + `llvm-c/BitReader.h`）遍历 IR：

- 入口函数：`plc_cycle` / `plc_main` / `plc_logic`（或 `--entry=` 指定）；
- 阶段：入口函数体里 `call` 到的、**本模块内有定义**的函数（排除 `llvm.*` 内建、声明、入口自身），按调用出现顺序去重；
- 全局：模块级全局变量 + **值类型**（如 `i32`）；
- 阶段类型：函数值类型（如 `void ()`）。

### 为什么不是 C++ Clang Tooling / libclang

目标板 Pi4（Debian bullseye）的三点现实（均实测）：

1. **C++ Clang 头不打包**：`apt install libclang-cpp16-dev` 后仍无 `clang/Tooling/Tooling.h`，
   C++ Tooling 无法编译；仓库里现有的 `frontend/ast/ast_tool__AST工具.cpp` 同样受此限。
2. **libclang C API 头需 `libclang-16-dev`**，而该包在板子上**下载极慢**（~13 KB/s，
   十几分钟未完成）——不可作为可靠前置。
3. **已装可用**：`clang-16` + `llvm-16-dev`（含 `llvm-c/*`）齐全，`clang-16 -emit-llvm`
   出 bitcode 正常，`llvm-config-16 --libs` 即单库 `-lLLVM-16`。

因此选 **LLVM C API**：立即可编译、且直接是 IR 级（比 AST 更接近后续要做的数据流/别名）。
代价：默认无源码行号（AST 才有）；需要行号时用 `-g` 再读 debug loc（后续补）。

## 当前版本能做什么（v2，已在 Pi4 验证）

```c
int g_integral; int g_output; int g_target;
void stage_sample(void){...} void stage_pid(void){...} void stage_clamp(void){...}
void plc_cycle(void){ stage_sample(); stage_pid(); stage_clamp(); }
```

输出：

```json
{
  "tool": "stage_extract", "version": 2,
  "entry": "plc_cycle",
  "stages": [
    {"name": "stage_sample", "fn_type": "void ()"},
    {"name": "stage_pid",    "fn_type": "void ()"},
    {"name": "stage_clamp",  "fn_type": "void ()"}
  ],
  "globals": [
    {"name": "g_target", "type": "i32"},
    {"name": "g_output", "type": "i32"},
    {"name": "g_integral", "type": "i32"}
  ],
  "ok": true
}
```

无入口时：`"entry": null, "ok": false`，退出码 4。

## 范围与限制（诚实）

1. **只做提取，不做验证/拒绝**：K-C0 子集与拒绝理由在后续 Python 侧实现；本工具如实提取，
   正确性由 Gate 3 独立验证链把关。
2. 阶段识别是**调用图启发式**：入口体里直接/嵌套调用且本模块有定义的函数才算阶段；
   `printf/malloc` 等库调用因无本模块定义被排除。
3. 用 `-O0` 出 bitcode（`-O1` 会把小阶段内联掉、调用图消失）；`run_on_pi.sh` 已固定 `-O0`。
4. 全局按 LLVM 模块顺序（接近声明逆序）；顺序不影响下游（下游按名字/依赖建图）。
5. **不声称功能等价**：本工具只是把源码变成结构事实，不是等价性证明。

## 构建（Pi4）

```sh
bash kfusion/frontend/stage_extract/build_on_pi.sh     # clang-16 + llvm-16-dev
bash kfusion/frontend/stage_extract/run_on_pi.sh <源.c> [入口名]
```

## 与下游的衔接：ast_to_plan（已实现，本机端到端验证）

`ast_to_plan.py` 把上面的 JSON 变成 **dag/contract/plan**，plan 由现有规划器
`fusion_plan__融合规划.py` 生成并由**独立验证器** `plan_verify` 签署（`verification.ok`），
本脚本不伪造凭据：

```sh
python3 kfusion/frontend/stage_extract/ast_to_plan.py extract.json \
    --period-us 1000 --exec-us '{"stage_pid":5}' --default-exec-us 10 \
    --out-dir build/gate1            # 产出 dag.json / contract.json / plan.json

python3 kfusion/backend/synthesis/emit.py \
    --plan build/gate1/plan.json --dag build/gate1/dag.json \
    --contract build/gate1/contract.json --source src.c \
    --out-dir build/gate1/gen --target linux-kernel --name demo
```

已在本机（Python 3.13，无 clang）端到端验证（`test_ast_to_plan.py` 3/3）：
提取 JSON → ast_to_plan（`verification.ok=true`，1 组）→ emit.py → 生成
`ecos_fused_body.c`（阶段按 sample→pid→clamp 顺序）、`ecos_fused_linux.c`、`Kbuild`。

诚实边界（v1，写在 dag.meta 里）：
- 执行时间未给 `--exec-us` 时按 `--default-exec-us` 占位并标记，**需实测替换**；
- 数据依赖边未恢复（按调用序建 topo_order），Gate 2（TEIR，IR use-def）再补；
- 效果按 state_read/state_write 保守假设。

## 已完成的链路与验证状态

| 环节 | 状态 | 证据 |
|------|------|------|
| stage_extract（C→入口/阶段/全局） | ✅ Pi4 编译+运行通过 | `results/gate1_stage_extract/PI_VALIDATION.md` |
| ast_to_plan（JSON→dag/contract/plan，经验证器签署） | ✅ 本机端到端 | `test_ast_to_plan.py` |
| emit（plan→用户态/内核态源码） | ✅ 本机生成通过 | 同上 |
| 目标构建（生成的 .ko 在 Pi4 构建） | ⏳ 下一步 | —— |
| 「新 C 不手写 DAG 即出双域源码」整链 | ⏳ 待把 extract→plan→emit→build 串起来 | —— |
