# stage_extract — 同源 C 周期函数阶段提取（Gate 1）

默认流水线入口是同目录 **`stage_extract.py`**（Clang JSON AST + K-C0 fail-closed）和
`kfusion/scripts/ecos_from_c__从C编译.sh`。本 README 描述的 LLVM-C 抽取器来自
`feat/ecos-temporal-semantics`，两套产物 JSON 都交给 `ast_to_plan.py` / `emit.py`。

K-C0 入口必须是直线直接调用。条件调用、重复调用、循环调用、间接调用、入口写全局
一律 `E_KC0_*` 拒绝转换，**不**按函数名去重来「猜」程序在干什么；失败时不写出
可部署 kmod。阶段函数体内的 `if`（例如 clamp）仍允许。

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
    {"name": "stage_sample", "fn_type": "void ()",
     "reads": ["g_target", "g_output"], "writes": ["g_output"]},
    {"name": "stage_pid",    "fn_type": "void ()",
     "reads": ["g_output", "g_integral"], "writes": ["g_integral", "g_output"]},
    {"name": "stage_clamp",  "fn_type": "void ()",
     "reads": ["g_output"], "writes": ["g_output"]}
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

## K-C0 拒绝策略（fail-closed，已在 Pi4 验证）

`stage_extract` 对除 `main` 外的每个本模块函数，检查其**外部调用**（无本模块定义的函数），
分类并给出**拒绝理由**，输出 `k_c0:{ok, rejections[]}`：

| kind | 触发（示例） | reason |
|------|------|------|
| block | nanosleep/usleep/pthread_*/sem_*/sched_yield | 可能阻塞/调度原语（内核周期体禁止睡眠） |
| mem | malloc/calloc/realloc/free/alloca/mmap | 动态内存（K-C0 要求固定布局状态） |
| io | printf/fprintf/write/read/open/… | I/O/打印（增加抖动、非确定性） |
| unknown | 其余任何外部调用 | 未归档的外部调用（无法证明副作用，fail-closed） |

`ast_to_plan` 见到 `k_c0.ok=false` 时**直接拒绝并打印理由**，不再生成 plan/源码；
`auto_kernelize.sh` 因 `set -e` 随之中止——**不支持的东西不会被硬塞进内核模块**。

Pi4 实测：含 `malloc/printf/free` 的阶段 → `k_c0.ok=false`，三条理由齐全，
整链中止且**不产出 gen/**；纯整数阶段的闭环 → `k_c0.ok=true` 正常生成。

诚实：v1 只查**直接外部调用**（不递归进本模块内的其它 helper；那种 helper 若也被入口调用
则会作为阶段一并检查）；循环边界、指针别名等 K-C0 细项尚未覆盖（Gate 2/3 补）。

## Gate 2：IR 级数据依赖（reads/writes）与 TEIR（已实现并验证）

**解决的问题**：Gate 1 的 `ast_to_plan` 只能按**调用序**近似建图，没有真实数据依赖。
Gate 2 让提取器给出每个阶段的读/写全局（IR 级 use-def），`ast_to_plan` 据此建**真实
数据依赖边**，并产出 **TEIR**（时序效应中间表示）——把"功能等价 ≠ 时间正确"要钉死的
那条约束（生产者 A 在周期 k 写 `w_g(k)`，消费者 B 读 `r_g(k)`，A 必须在 B 之前）落到数据上。

**提取器新增 `collect_rw`**：对每个阶段函数遍历 IR 指令，Load 的操作数 0 / Store 的操作数 1
（支持解一层 GEP）若指向全局变量，记入该阶段的 `reads`/`writes`（去重）。输出进 stage 的
`reads`/`writes` 数组（见上节 JSON）。

**`ast_to_plan` 据此建边**：对阶段 B 的每个读 `g`，在调用序中 B 之前找**最后一个**写 `g`
的阶段 A → 边 `A→B`（`kind:"data"`, `proven:g`, `version:"same_period"`）。B 读但本周期无人
写的 `g` → 记为 `cross_period_inputs`（外部输入 / 上一周期状态），不算内部边。节点
`declared_deps` 反映真实数据入边。

**新增 TEIR 模块** `kfusion/ir/temporal_effect_ir.py`，表达三类事实：
- 计算事实：每个节点的 `reads`/`writes`（如实，来自 IR use-def）；
- 时间事实：周期/相位，以及 `data_edges` 的 `same_period` 版本依赖（r_i(k) 依赖 w_i(k)）；
- 执行事实：`kernelizable`（来自提取器 K-C0.ok）。
另附 `state_versions`（每个全局的 written_by/read_by 顺序）与 `cross_period_inputs`。

**验证**：
- 本机 `test_ast_to_plan.py` 5/5：含断言校验 `dag.edges` 恰为 `sample→pid(g_output)`、
  `pid→clamp(g_output)`，TEIR 的 `kernelizable/reads/writes/cross_period_inputs` 正确。
- Pi4 实测（clang/llvm 16.0.6）：`ctl.c` 提取出 `sample[reads g_target,g_output; writes g_output]`
  等，整链 `verification.ok=true`，`teir.json` 如上；生成的 `.ko` 构建+运行回归
  `periods≈2010/missed=0`。证据 `results/gate1_stage_extract/GATE2_TEIR.md`。

诚实边界（v1）：单域同周期骨架；跨域/多速率版本依赖（r_i(k) 与
r_j(k-1)）属 Gate 2 后期 + Gate 3，TEIR 暂只给单域同周期。
**执行时间「占位」已由实测机制消除**：见下节「执行时间实测（exec_probe + measure_exec）」。

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

已在本机（Python 3.13，无 clang）端到端验证（`test_ast_to_plan.py` 5/5）：
提取 JSON → ast_to_plan（**按 IR use-def 建真实数据边**，plan 经 `plan_verify` 签署
`verification.ok=true`，1 组）→ emit.py → 生成 `ecos_fused_body.c`（阶段按 sample→pid→clamp
顺序）、`ecos_fused_linux.c`、`Kbuild`，并产出 `teir.json`（TEIR）。

诚实边界（写在 dag.meta 里）：
- 执行时间：未给 `--exec-us` 时按 `--default-exec-us` 占位并记 `placeholder_exec`，
  **但已提供目标板实测机制消除占位**（见下节）；给出后即清空 `placeholder_exec`，不再标记；
- 数据依赖边已来自 IR use-def（同周期生产者→消费者，`kind:"data"`），不再是调用序近似；
  读但本周期无人写的全局记为 `cross_period_inputs`（外部/前周期状态）；
- 效果按 state_read/state_write 保守假设。

## 执行时间实测（exec_probe + measure_exec）

占位边界的**消除机制**：共用周期体 `ecos_fused_body.c` 里每个阶段调用被弱符号钩子
`ecos_stage_enter/exit(idx)` 包裹，Linux 宿主提供强定义用 `ktime_get()` 量每阶段真实
执行时间（max/avg + 采样数），经 `exec_probe` 模块参数暴露（读即打印）。

```sh
# 1) 占位生成并加载，读实测
bash auto_kernelize.sh ctl.c loop1 1000 linux-kernel
insmod loop1/gen/plc_cycle.ko period_us=1000; sleep 3
cat /sys/module/plc_cycle/parameters/exec_probe     # max/avg/n
rmmod plc_cycle
# 2) 实测 → exec-us JSON（默认取 max=WCET，支持 --scale 裕度 / --avg）
cat /sys/module/plc_cycle/parameters/exec_probe | python3 measure_exec.py > exec_us.json
# 3) 用实测值重生（placeholder_exec 清空，phase 用真实值累加）
bash auto_kernelize.sh ctl.c loop2 1000 linux-kernel exec_us.json
```

`measure_exec.py` 解析 `exec_probe` 文本，输出 `--exec-us` 用的 JSON；**默认取 max（最坏情况
WCET）**，因为实时调度不能靠 avg 证明不超时。正式进论文前仍需多工况/长采样并叠裕度。
证据与完整闭环 `results/gate1_stage_extract/EXEC_MEASURE.md`。

## 已完成的链路与验证状态

| 环节 | 状态 | 证据 |
|------|------|------|
| stage_extract（C→入口/阶段/全局/读写） | ✅ Pi4 编译+运行通过 | `results/gate1_stage_extract/PI_VALIDATION.md` |
| IR use-def 读/写（Gate 2） | ✅ Pi4 实测 | `results/gate1_stage_extract/GATE2_TEIR.md` |
| ast_to_plan（JSON→dag/contract/plan，真实数据边，经验证器签署） | ✅ 本机端到端 | `test_ast_to_plan.py` |
| TEIR 产出（计算/时间/执行三类事实） | ✅ 本机+Pi4 | 同上 |
| emit（plan→用户态/内核态源码） | ✅ 本机生成通过 | `test_ast_to_plan.py` |
| 一条命令整链 + 目标构建（生成的 .ko 在 Pi4 构建运行） | ✅ Pi4 端到端 | `results/gate1_stage_extract/END2END.md` |
| K-C0 拒绝（fail-closed） | ✅ Pi4 实测 | `results/gate1_stage_extract/KC0_REJECT.md` |
| 执行时间实测（exec_probe → measure_exec → 回填） | ✅ Pi4 闭环验证 | `results/gate1_stage_extract/EXEC_MEASURE.md` |
