# Pass 能力矩阵（K-Fusion v3.6）

权威实现：`backend/pass/PLCFusionPass__内核化Pass.cpp`

## 子 Pass 流水线

| 子 Pass | 作用 |
|---------|------|
| `plc-fusion-normalize` | DSOLocal、去 TLS |
| `plc-fusion-fixed` | 浮点 → Q 定点（`FUSE_FIXED_POINT=1`） |
| `plc-fusion-remap` | POSIX → `plc_*`，间接调用解析 |
| `plc-fusion-dce` | 根函数可达性裁剪 |
| `plc-fusion-export` | `FUSE_GLOBALIZE_SYMBOLS` / llvm.used |
| `plc-fusion-wcet-mark` | 热路径 `optnone` |
| `plc-fusion-wcet-schedule` | 函数级 LLVM tail（JSON） |
| `plc-fusion-cleanup` | 死 declare、blackhole 未映射 |

预设：`plc-kernelize-{mainline,generic,minimal,debug,size,hotpath,wcet}`

## Remap 覆盖（摘要）

- **内存/IO**：malloc/free、open/mmap/shm、stdio 薄层 → `plc_*`
- **时间/定时**：clock_gettime、nanosleep、timer_*
- **线程/同步**：pthread_*、sem_*、barrier、sched_affinity
- **信号**：signal/sigaction/sigwait
- **rt-tests**：hist/hset、getopt、assert → `plc_assert_fail`

**kKeep（不 remap）**：`bsearch/qsort/lsearch`、numa 系列、部分 libc（fread/fputs/fscanf/…）→ 宿主桩。

## 间接调用解析

支持：全局 fn-ptr、GEP 表、Store/Load/Alloca、PHI/Select、**实参回溯**（如 bsearch compar）。

未解析 → `PLC_FUSION_UNMAPPED_LOG` 记 `indirect:unresolved`；`PLC_FUSION_BLACKHOLE=1` 时替换为 null 调用。

## CI 门禁

| 层级 | 入口 | 内容 |
|------|------|------|
| Pass 单测 | `backend/test/run_pass_unit_tests__Pass单元测试.sh` | 15×`.ll` + WCET 负例 + **pre.ll unmapped=0** |
| 融合 CI | `scripts/run_ci__CI门禁.sh` | 12 manifest fuse + validate + wcet sweep |
| ko 链接 | `scripts/run_ko_build__全类ko编译.sh` | 全 manifest `.ko`（无 insmod） |

### pre.ll unmapped 探针（Pass 单测内）

| 标签 | 文件 |
|------|------|
| cyclictest | `official_cycletest_pre.ll`, `official_cycletest_multitu_pre.ll` |
| signaltest | `signaltest_pre.ll` |
| ptsematest | `ptsematest_pre.ll` |
| github_rt_periodic | `github_rt_periodic_pre.ll`, `github_rt_periodic_multitu_pre.ll` |

需先 fuse 生成 pre.ll；`run_ci` 在 Pass 单测前已融合。

## rt-tests manifest 推荐字段

```env
FUSE_STRICT=1
FUSE_STRICT_VALIDATE=1
FUSE_MAX_UNMAPPED=0
FUSE_FIXED_POINT=1
FUSE_AST_INDIRECT_ALLOW=bsearch
FUSE_HOST=hrtimer          # timer/signal 类
FUSE_LINK_PTHREAD_HOST=1
```

## 已知边界

- 复杂跨 TU / 运行时填表间接调用仍可能 unresolved
- FILE 真 I/O 依赖桩，非完整 VFS
- WCET fitness 以板级 probe 为主，无 LLVMTA 静态证明

## 相关文档

- [WCET_LAVINIUM__函数级WCET对标.md](WCET_LAVINIUM__函数级WCET对标.md)
- [KERNELIZE__内核化流程.md](KERNELIZE__内核化流程.md)
- [manifests/README.md](../manifests/README.md)
