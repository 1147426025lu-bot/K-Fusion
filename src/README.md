# src/ — 宿主与运行时桩（FIXED）

**角色：FIXED（权威源码）**

本目录是内核模块宿主、POSIX 桩、基线的**唯一维护位置**。构建脚本会将文件复制到 `test/plc_*.c`（Kbuild 固定名）；请只改这里，勿改 `test/` 下副本。

| 文件 | 用途 | 何时选用 |
|------|------|----------|
| `plc_runtime_stubs__POSIX桩.c` | 通用 POSIX/rt-tests 桩 | 几乎所有 fused 应用 |
| `plc_fused_host__通用宿主.c` | kthread + main 入口 | `FUSE_HOST=generic` |
| `plc_fused_timer_host__hrtimer宿主.c` | hrtimer 周期睡眠 | cyclictest、周期 demo |
| `plc_pthread_host__pthread宿主.c` | pthread → kthread | signaltest、多线程 rt-tests |
| `plc_runner_official__cyclictest宿主.c` | cyclictest 专用低抖动宿主 | `ignite_official_cycletest__*` 专用 |
| `plc_compiler_rt__软浮点桩.c` | 软浮点 | `FUSE_LINK_COMPILER_RT=auto` 时链入 |
| `plc_baseline_cyclic__手写基线.c` | 论文手写基线 | `scripts/paper/ignite_baseline_cyclic__*` |
| `plot_frequency_polygon__抖动绘图.py` | 浸泡 histogram 绘图 | deploy 长测后可选 |

详见 [REPO_LAYOUT.md](../REPO_LAYOUT.md)。
