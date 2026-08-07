# 宿主（Host）架构 — 统一组合 vs 专用测量

## 结论（推荐）

**不要为每个应用写独立宿主 .c。** 当前正确方向是 **一层通用壳 + 可组合后端 + 可选测量 profile**：

```
${FUSE_NAME}_mod.ko :=
    plc_fused_host.o                 # 始终：kthread / shutdown / debugfs 壳
  + [plc_hrtimer_core.o]             # hrtimer / L2 runner 共用
  + [plc_fused_timer_host.o]         # FUSE_HOST=hrtimer
  + [plc_pthread_host.o]             # FUSE_LINK_PTHREAD_HOST=1
  + [${FUSE_NAME}_runtime_stubs.o]   # Pass unmapped → 自动桩
  + ${FUSE_NAME}_kernel.o            # Pass 融合产物
```

**cyclictest 额外保留 `FUSE_RUNNER_PROFILE=l2`**：链 `plc_runner_official.o` 代替上述组合，专用于 L2 浸泡 / `fused_stats_reset` / ring 导出。这是 **profile 切换**，不是第二套长期并行的产品架构。

| Profile | 构建入口 | 用途 |
|---------|----------|------|
| `generic` | `ignite_fused__通用ko构建.sh` | CI、功能验证、跑完整 `main()` |
| `l2` | `ignite_official_cycletest__cyclictest主线.sh` | 论文/浸泡、直接 `timerthread()` + 测量 debugfs |

## 现有模块（`src/` 为权威源）

| 文件 | 职责 |
|------|------|
| `plc_fused_host__通用宿主.c` | 模块生命周期；`FUSE_RUN_MAIN` → `main()`，或 `FUSE_KTHREAD_ENTRY` |
| `plc_hrtimer_core__定时核心.c` | 共享 sleep / ktime / EWMA 补偿 |
| `plc_fused_timer_host__hrtimer宿主.c` | 强符号 `plc_timer_*` / `plc_nanosleep` |
| `plc_pthread_host__pthread宿主.c` | `plc_pthread_*` → kthread |
| `plc_runtime_stubs__POSIX桩.c` | weak POSIX / rt-tests 桩 |
| `plc_runner_official__cyclictest宿主.c` | L2 测量宿主（~1k LOC，与 timer 宿主有重叠） |

## Manifest 宿主字段

| 变量 | 含义 |
|------|------|
| `FUSE_HOST=generic\|hrtimer` | 是否链 timer 宿主 |
| `FUSE_LINK_PTHREAD_HOST=1` | 是否链 pthread 宿主 |
| `FUSE_RUN_MAIN=1` | kthread 调 fused `main(argc,argv)` |
| `FUSE_KTHREAD_ENTRY=fn` | kthread 调指定入口（plc-cc） |
| `FUSE_RUNNER_PROFILE=generic\|l2` | 仅 cyclictest：切换 ignite 路径 |

## 后续演进（不必一次做完）

1. ~~**提取 `plc_hrtimer_core`**~~ — runner 与 `plc_fused_timer_host` 共用 sleep/ktime/EWMA
2. ~~**generic 宿主补 `fused_stats_reset`**~~ — 已实现（清零 `loops`）
3. **`test/plc_*.c` 仅构建时复制** — `run_host_stubs_sync_check__宿主桩同步门禁.sh` 已接入 CI

### 宿主桩增强（2026-08）

- **Phase 1:** `plc_sem_*` 计数信号量；libc `fread/fputs/fscanf/sprintf/strtok/…` 桩；`run_host_stubs_sync_check`
- **Phase 2:** per-object `plc_barrier_*` / `plc_cond_*`；host_profile 扩 sem/fileio/barrier；signal IR → 自动 `hrtimer`

## 常用命令

```bash
# CI / 功能（generic，跑 main）
FUSE_RUNNER_PROFILE=generic bash scripts/ignite_fused__通用ko构建.sh \
  manifests/manifest_cyclictest__主线压测.env

# 浸泡 / L2 测量（默认 l2）
bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh

# 仅构建 L2 .ko
IGNITE_BUILD_ONLY=1 bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh
```
