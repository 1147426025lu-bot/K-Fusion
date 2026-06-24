# include/ — 运行时 ABI（FIXED）

**角色：FIXED**

| 文件 | 说明 |
|------|------|
| `plc_abi__运行时ABI.h` | `plc_kmalloc`、`plc_pthread_create`、`plc_timer_*` 等内核侧 ABI |
| `plc_shm__共享内存.h` | 用户态与模块共享内存布局（`/dev/plcfusion`） |

Pass 映射与用户态 plc-cc 均依赖此 ABI，变更需同步 Pass 与宿主桩。
