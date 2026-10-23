# examples/ — 示例程序（SWAPPABLE）

**角色：SWAPPABLE**

## `plc-cc__低抖动示例/`（plc-cc 路线）

手写 `plc_cycle()` 的 PLC 风格示例，走 **PLCLowJitterPass** + `plc-cc` 编译器（与 PLCFusion 主流程独立，但共享 ABI）。

| 文件 | 对应 manifest |
|------|---------------|
| `hello_plc__入门示例.c` | `manifest_plc_cc_hello__入门.env` |
| `pure_logic__纯逻辑.c` | `manifest_plc_cc_pure_logic__纯逻辑.env` |
| `gpio_blink__GPIO闪烁.c` | `manifest_plc_cc_gpio__PLC示例.env` |
| `dither_test__抖动测试.c` | `manifest_plc_cc_dither__抖动测试.env` |
| `isolation_test__隔离测试.c` | `manifest_plc_cc_isolation__隔离测试.env` |
| `temp_control__温控.c` | `manifest_plc_cc_temp_control__温控.env` |

**替换/新增**：复制任一 `.c` 改逻辑 → 新建 manifest → `plc_fuse` + `ignite_fused`。

## `ecos/`（ECoS 任务图与融合实验负载）

纯计算、无 MMIO、无 sleep，用来把"释放次数"这一个变量隔离出来的周期任务。
状态一律**外部链接**——`static` 会让 `-O2` 把无人读的尾链当死代码删掉（实测 `stage_clamp`
被优化成 `ret void`），于是"性能实验"变成测空函数。

| 文件 | 图 | 用途 |
|---|---|---|
| `four_stage_chain__四阶段示例.c` | DAG：sample → fir → pid → clamp | 融合边界（拍内）实验，plan 1 次 vs flat 4 次释放 |
| `five_stage_loop__闭环五阶段示例.c` | **有环**：… → clamp → plant ─(z⁻¹)→ sample | 跨周期依赖实验，plan 1 次 vs flat 5 次释放 |

骨架分别在 `contracts/examples/{four_stage_chain,five_stage_loop}.stages.json`；
闭环那份显式给了 `offset_us`（0/200/400/600/800），**相位是判定"环跨不跨拍"的唯一依据**，
不给就只能 fail-closed 判成代数环（见 `docs/DAG__任务依赖图生成.md` §6）。

## `plc_let_demo__LET演示/`（单 job STRICT LET）

项目级 LET 运行时最小示例：`include/plc_let__LET.h` + `src/runtime/plc_let__LET.c`。

## `plc_multitask__多任务示例/`（PLCFusion + STRICT LET，W5）

6 job 单线程 LET 调度 + 多 TU + Q 定点。Manifest：`manifest_plc_multitask__多任务优先级.env`；论文测量：`manifest_plc_multitask_paper__论文多任务测量.env`。详见 [plc_multitask__多任务示例/README.md](plc_multitask__多任务示例/README.md) 与 [docs/paper/MULTITASK_EVAL__多任务评估.md](../docs/paper/MULTITASK_EVAL__多任务评估.md)。

