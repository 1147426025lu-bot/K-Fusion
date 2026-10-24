# ECoS PR5 结果：五阶段闭环融合对照（Linux root cell）

> 对应研究设计 `RESEARCH_DESIGN_ECoS_v1.0` §10 / §13 `feat(fusion)`。
> 测量日：2026-10-23～10-24；板卡 Pi4B root cell；核隔离 `taskset -c 3` + `SCHED_FIFO`。

## 1. 做了什么

闭环负载 `five_stage_loop`：`sample → fir → pid → clamp → plant`，`plant→sample` 为跨拍延迟边（z⁻¹）。

| 模式 | 含义 | 每周期释放次数 |
|---|---|---|
| **plan** | 规划器最优：五阶段并成一组 | 1 |
| **flat** | 不融合对照：每阶段一次释放 | 5 |
| **multi** | 真多任务：每阶段一个 `pthread`，按相位释放 | 5（真线程） |

三者**工作量同源**（同一阶段函数、同一调用表达式）；唯一自变量是释放次数 / 是否线程化。  
判据：`# digest:`（对 `g_out`/`g_plant` 的 FNV-1a）必须逐位相同。

## 2. Linux 60 s @ 1 kHz（n=60000）

数据：`/root/pr5/mt_{plan,flat,multi}.log`（与 `run_{plan,flat}.log` 同口径）。

| 模式 | response p50 | response max | start_lat p50 | digest | miss |
|---|---|---|---|---|---|
| plan | **22 µs** | 62.9 µs | 20 µs | `b6eb20b8` | 0 |
| flat | **820 µs** | 880.5 µs | 20 µs | `b6eb20b8` | 0 |
| multi | **821 µs** | 845.1 µs | ~20 µs/阶段 | `b6eb20b8` | 0 |

结论（可写进实验段，不可外推成硬实时保证）：

1. **融合省下的就是释放税**：flat/multi 的 R ≈ 4×200 µs 相位跨度 + 一次末相完成，与 plan 的 ~22 µs 差约 800 µs；不是统计噪声。
2. **真多任务并不额外「吃掉」融合收益的叙事空间**：multi 与 flat 的 R 同量级——在同核 FIFO 下，瓶颈仍是「每阶段一次绝对睡眠」，不是线程创建本身。
3. **语义未改**：三模式 digest 相同 → 采样序列逐位不变；融合只改了环闭合边的死区时间（200 µs → ~整拍），见 `plan.json` 的 `recurrences`。

## 3. 规划器修正（同日）

闭环图若把 `plant→sample` 延迟边留在不可分性可达闭包里，环变强连通，枚举只剩「全合 / 全不合」两种。

**修复**：`fusion_plan` 计算可达性时跳过 `recurrences[].closing_edges`（与 `topo_order_delayed` 一致）。  
修复后五阶段闭环可枚举 **16** 种合法分段（`2^4`）；DP 仍选 G=1（R̂ 最优）。

```text
python3 fusion_plan__融合规划.py dag.json --platforms platform_caps.json \
  --platform linux_kernel --enumerate
```

开链四阶段仍为 8 种（含 `sample+fir|pid+clamp` 等 AB|C 形）。  
带 `mmio` 的 `plc_chain` 在 `linux_kernel`（`allow_mmio=false`）上枚举为 0——fail-closed；在 `nuttx` 上 4 种全合法。

## 4. NuttX 侧状态

- App 已入库：`crtos/jailhouse_overlay_pi4/nuttx/apps/benchmarks/fiveloop/`（自包含：`fiveloop_main.c` + `five_stage_loop.c` + 探针头）。
- `build_nuttx_inmate` 已 `inject_app benchmarks/fiveloop`；defconfig 开 `CONFIG_BENCHMARK_FIVELOOP=y`。
- 本机 `nuttx.bin`（2026-10-24）已链入 `fiveloop`。
- 跑测脚本：`crtos/scripts/pi4/fiveloop_nuttx_run__NuttX域跑五阶段.sh`（需主机串口 USB-TTL；Pi 本机无适配器时无法在板上自抓）。

## 5. 还不是什么

- ❌ 尚未在 NuttX inmate 上落盘 plan/flat 对照数（缺串口会话）。
- ❌ 尚未做跨域 `A@Linux | (B+C)@NuttX`（`feat(ivshmem)` + 多域规划器）。
- ❌ `feat(fusion)` 的 Linux **kthread** 宿主差分验证仍缺（当前是用户态驱动）。
