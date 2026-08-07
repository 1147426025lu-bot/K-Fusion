# src/runtime/ — 应用侧运行时库（FIXED）

与 **内核宿主**（`src/plc_*_host__*.c`）分离：此处为 **userspace / fused 应用均可链接** 的调度与工具库。

| 文件 | 说明 |
|------|------|
| `plc_let__LET.c` | STRICT LET 调度器（单线程；逻辑 release + 相对 delta sleep） |

融合时在 manifest 的 `FUSE_EXTRA_SOURCES` 中加入本文件；用户态 gcc 直接 `-I include` 编译。

- P0 示例：`examples/plc_let_demo__LET演示/`
- W5 负载：`examples/plc_multitask__多任务示例/` + `docs/paper/MULTITASK_EVAL__多任务评估.md`
