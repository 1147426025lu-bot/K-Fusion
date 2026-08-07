# include/ — 公共头文件（FIXED）

| 文件 | 说明 |
|------|------|
| `plc_abi__运行时ABI.h` | 内核 fused 运行时 ABI（`plc_kmalloc`、`plc_pthread_*`、`plc_timer_*`） |
| `plc_fixed__定点Q.h` | Q16.16 / Q32.32 定点 API（`FUSE_FIXED_POINT=1`） |
| `plc_let__LET.h` | 项目级 STRICT LET 调度器 API |
| `plc_shm__共享内存.h` | `/dev/kfusion` 共享内存布局 |

Pass 映射与用户态编译均依赖上述头文件；变更 ABI 需同步 Pass 与 `src/plc_runtime_stubs__POSIX桩.c`。
