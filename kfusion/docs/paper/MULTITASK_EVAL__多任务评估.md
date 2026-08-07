# W5 多任务实验评估（plc_multitask + STRICT LET）

工作负载 **W5**：`plc_let` STRICT 调度、6 job（监督 + 4 控制 + 看门狗）、Q 定点、mutex。用于验证 PLCFusion 在**非 cyclictest 微基准**、多 TU 控制负载下的 period jitter 与 LET 语义是否可内核化。

---

## 1. 调度语义（与 OS 多任务的区别）

W5 **不是** RTOS 意义上的「多独立执行流 / 多上下文并发」。当前实现为：

| 维度 | W5（STRICT LET） |
|------|------------------|
| 执行模型 | **单线程** `plc_let_run()`，在 CPU3 上时间复用 |
| 并发 | 无 spatial parallelism；同刻多 job 到期按 **prio** 择一执行 |
| Release | 逻辑 release 时刻由 `next_release_ns` 维护，**不随执行时间漂移** |
| Sleep | 相对 **delta** `clock_nanosleep(CLOCK_MONOTONIC, 0, &ts)`（非 fused 桩上的 `TIMER_ABSTIME`） |
| fused 宿主 | 单 kthread + hrtimer；`FUSE_LINK_PTHREAD_HOST=0`（无 worker pthread） |

论文表述建议：**multi-rate LET control workload on a single core**（时间复用），勿称「四线程真多任务」。

### Job 表

| Job | period | LET | prio | flags |
|-----|--------|-----|------|-------|
| supervisor | 1 ms | 0.8 ms | 7 | — |
| sensor_fusion | 10 ms | 3 ms | 6 | SKIP_MISSED |
| pid_control | 20 ms (φ=5 ms) | 5 ms | 5 | SKIP_MISSED |
| watchdog | 5 ms | 2 ms | 4 | — |
| alarm_log | 100 ms | 15 ms | 3 | SKIP_MISSED |
| stats_heap | 500 ms | 30 ms | 1 | SKIP_MISSED |

---

## 2. 测量指标

### MtSummary（主结果，与 cyclictest 同维度）

监督 job（1 ms）release 相对理想时刻的偏差采样；论文 CSV 取 `abs_max_ns`。

```
MtSummary: baseline=fused abs_max_ns=... min_ns=... max_ns=... cycles=... task_runs=... alarm_edges=... jobs=6 let=strict exit=0
```

### LetSummary（LET 构造效度）

全 job 聚合：release 次数、LET overrun、deadline miss、skipped、release jitter。

```
LetSummary: baseline=fused jobs=6 releases=... let_overrun=... deadline_miss=... skipped=... release_jitter_max_ns=... exit=0
```

---

## 3. 实验矩阵

| 维度 | 取值 |
|------|------|
| baseline | `userspace`（gcc 同源码） / `fused`（`.ko`） |
| measure_kind | `soak`（L2 安静） / `stress`（L1 + CPU0–2 hackbench） |
| 重复 | `PAPER_RUNS`（默认 3） |
| 时长 | `DURATION_SEC`（默认 120） |
| CPU | CPU3 隔离；测量前 `PRE_IDLE_SEC`（默认 30，manifest 可覆盖 90） |

---

## 4. 运行

### 前置

```bash
cd ~/k-fusion
export PATH="/usr/local/llvm-17/bin:$PATH"
sudo -v
# 确认产物存在（脚本也会 preflight）
test -f test/plc_multitask_mod.ko
test -x results/paper/multitask/plc_multitask_uspace
```

### 正式矩阵

```bash
PAPER_RUNS=3 DURATION_SEC=120 bash scripts/paper/run_paper_multitask__论文多任务.sh
```

测量进行中会创建 `results/paper/multitask/.multitask.lock`。**勿**在此窗口运行 `cleanup_repo_local`（会归档 uspace 二进制或误删中间 `.ko`）。

### 汇总

```bash
python3 scripts/paper/paper_summarize_multitask__多任务汇总.py \
  --csv results/paper/multitask/paper_multitask_merged.csv \
  --out results/paper/multitask/paper_multitask_summary.md
```

---

## 5. Manifest 与 Pass 选项

论文测量使用 `manifests/manifest_plc_multitask_paper__论文多任务测量.env`：

| 选项 | 值 | 说明 |
|------|-----|------|
| `FUSE_PIPELINE` | `debug` | LET 代码路径上 `plc-low-jitter` Pass 曾 segfault，暂用 debug |
| `FUSE_LOW_JITTER` | `0` | 同上 |
| `FUSE_LINK_PTHREAD_HOST` | `0` | STRICT LET 单线程，不链 pthread 宿主 |
| `FUSE_EXTRA_SOURCES` | 调度器 + 任务集 + `src/runtime/plc_let__LET.c` | 多 TU + LET 运行时 |
| `MT_RUN_LOOPS=0` | duration 驱动结束 | 与 cyclictest 矩阵时长对齐 |

日常 fused 演示可用 `manifest_plc_multitask__多任务优先级.env`（选项相同思路）。

---

## 6. 参考数据（LET 版，`mt_20260715_233337`）

Pi 5 · `6.12.62+rpt-rpi-v8-rt` · 120 s · soak L2 · **有效 cell：soak run 1–2**

| baseline | run | abs_max_ns | cycles | let_overrun | skipped |
|----------|-----|------------|--------|-------------|---------|
| userspace | 1 | 242372 | 119990 | 0 | 0 |
| fused | 1 | 102216 | 122020 | 0 | 0 |
| userspace | 2 | 188608 | 119991 | 0 | 0 |
| fused | 2 | 208150 | 122035 | 0 | 0 |

同 run fused soak `abs_max` 与 userspace 同量级（~100–250 µs），**非**旧版 pthread 路径下 fused 显著劣化的情况。

**未完成 cell**（soak 3 + stress 1–3）：测量中途本地清理删除了 `test/plc_multitask_mod.ko` 并归档 uspace，导致 insmod / 二进制缺失。产物已重建；补跑同一 stamp 或新 stamp 即可。

与旧版 **pthread worker** 路径对比（`mt_20260715_215512`，非 LET）：soak fused ~611 µs vs userspace ~553 µs；stress fused ~832 µs vs userspace ~207 µs — LET  redesign 主要消除「伪多线程 + shim 开销」带来的 fused 劣势。

---

## 7. 相关文件

| 文件 | 说明 |
|------|------|
| `include/plc_let__LET.h` | LET 公共 API |
| `src/runtime/plc_let__LET.c` | STRICT 调度实现 |
| `examples/plc_multitask__多任务示例/` | W5 应用（调度表 + 任务集） |
| `examples/plc_let_demo__LET演示/` | 单 job P0 示例 |
| `manifests/manifest_plc_multitask_paper__论文多任务测量.env` | 论文 W5 manifest |
| `scripts/paper/run_paper_multitask__论文多任务.sh` | 测量矩阵 |
| `scripts/paper/paper_summarize_multitask__多任务汇总.py` | CSV → Markdown |
| `results/paper/multitask/` | CSV、日志、uspace 二进制 |

---

## 8. 已知限制

1. **非 LET 论文对比**：旧 pthread 版数据与当前 LET 版不可直接混表。
2. **Low-jitter Pass**：LET 多 TU IR 上启用 `FUSE_LOW_JITTER=1` 可能崩溃，论文 manifest 已关闭。
3. **fused vs userspace**：重负载 stress 下 fused 仍可能因 kthread/insmod 路径开销劣于原生 gcc；soak 上应接近。
4. **测量与清理互斥**：见 §4；清理脚本见 `scripts/maintenance/cleanup_repo_local__本地仓库清理.sh`（`.multitask.lock` 存在时跳过 multitask 归档）。
