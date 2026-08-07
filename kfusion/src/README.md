# src/ — 宿主与运行时（FIXED）

**权威源码目录**。Kbuild 宿主从 `src/plc_*_host__*.c` **复制**到 `test/plc_*.c`；应用运行时库在 `src/runtime/`。

## 内核宿主（→ `test/plc_*.c`）

| 文件 | 用途 |
|------|------|
| `plc_runtime_stubs__POSIX桩.c` | 通用 POSIX / rt-tests 桩 |
| `plc_fused_host__通用宿主.c` | kthread + `main()` 入口 |
| `plc_fused_timer_host__hrtimer宿主.c` | hrtimer 周期睡眠 |
| `plc_pthread_host__pthread宿主.c` | pthread → kthread |
| `plc_fused_host__通用宿主.c` | 通用 kthread 壳（main 或 KTHREAD_ENTRY） |
| `plc_fused_timer_host__hrtimer宿主.c` | hrtimer 定时 / sleep 后端 |
| `plc_pthread_host__pthread宿主.c` | pthread → kthread |
| `plc_runner_official__cyclictest宿主.c` | cyclictest **L2 测量 profile**（非默认 CI 路径） |

宿主组合见 [`docs/HOST__宿主架构.md`](../docs/HOST__宿主架构.md)。

## 应用运行时（→ manifest `FUSE_EXTRA_SOURCES`）

见 [runtime/README.md](runtime/README.md)（如 `plc_let__LET.c`）。

## 其它

| 文件 | 用途 |
|------|------|
| `plc_baseline_cyclic__手写基线.c` | 论文手写基线 |
| `plot_frequency_polygon__抖动绘图.py` | 浸泡 histogram 绘图 |

详见 [REPO_LAYOUT.md](../REPO_LAYOUT.md)。
