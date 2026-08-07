# plc_multitask — STRICT LET 多 job 演示（W5）

**角色：SWAPPABLE 示例** — PLCFusion + 项目级 `plc_let` 运行时。

## 架构

```
main → plc_let_run()  （单线程 STRICT LET 调度器）
  ├─ supervisor     1 ms   监督 tick / MtSummary 采样
  ├─ sensor_fusion  10 ms  float 加权融合（Q 定点）
  ├─ pid_control    20 ms  PID（phase 5 ms）
  ├─ watchdog       5 ms   alarm 边沿
  ├─ alarm_log      100 ms printf + mutex
  └─ stats_heap     500 ms malloc/free
```

- 逻辑 release 不随 job 执行时间漂移；调度器在两次 release 之间用 **相对 delta** `clock_nanosleep` 等待下一时刻。
- 同一逻辑时刻多个 job 到期时，按 **prio** 选最高者优先执行（其余留待下一 tick）。
- **不是** OS/RTOS 多上下文并发：全程一条用户态（或 fused kthread）执行流。

## 能力

| 能力 | 体现 |
|------|------|
| `plc_let` | `src/runtime/plc_let__LET.c` 全项目 LET 运行时 |
| 多 TU | 主程序 + 调度表 + 任务集 + LET |
| Q 定点 | PID / 传感融合（`FUSE_FIXED_POINT=1`） |
| fused | hrtimer 宿主 + `LetSummary` / `MtSummary` |
| 论文对照 | gcc userspace 与 `.ko` 同源码（`MT_RUN_LOOPS=0` 按时长结束） |

## Manifest

| 用途 | 文件 |
|------|------|
| 日常 fused 构建 | `manifests/manifest_plc_multitask__多任务优先级.env` |
| 论文 W5 测量 | `manifests/manifest_plc_multitask_paper__论文多任务测量.env` |

论文 manifest 要点：`FUSE_LINK_PTHREAD_HOST=0`、`FUSE_PIPELINE=debug`、`FUSE_LOW_JITTER=0`。

## 运行

### 最小 userspace 冒烟

```bash
export PRJ=/home/pi/K-Fusion
gcc -O2 -I"$PRJ/examples/plc_multitask__多任务示例" -I"$PRJ/include" \
  -D_GNU_SOURCE -DMT_RUN_LOOPS=200 -DMT_PAPER_BASELINE=\"smoke\" \
  "$PRJ/examples/plc_multitask__多任务示例/plc_multitask_demo__主程序.c" \
  "$PRJ/examples/plc_multitask__多任务示例/plc_multitask_scheduler__调度器.c" \
  "$PRJ/examples/plc_multitask__多任务示例/plc_multitask_tasks__任务集.c" \
  "$PRJ/src/runtime/plc_let__LET.c" \
  "$PRJ/examples/plc_multitask__多任务示例/plc_multitask_uspace_stubs__用户态桩.c" \
  -lpthread -lm -o /tmp/plc_multitask_smoke && /tmp/plc_multitask_smoke
```

退出应看到 `MtSummary:` 与 `LetSummary:`。

### fused 手动 insmod

```bash
export PRJ=/home/pi/K-Fusion
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_plc_multitask__多任务优先级.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_multitask__多任务优先级.env
sudo insmod test/plc_multitask_mod.ko fused_cpu=3
echo 1 | sudo tee /sys/module/plc_multitask_mod/parameters/shutdown_request
sudo rmmod plc_multitask_mod
```

### 论文矩阵

```bash
PAPER_RUNS=3 DURATION_SEC=120 bash scripts/paper/run_paper_multitask__论文多任务.sh
```

详见 [docs/paper/MULTITASK_EVAL__多任务评估.md](../../docs/paper/MULTITASK_EVAL__多任务评估.md)。

单 job LET 最小示例见 [plc_let_demo__LET演示](../plc_let_demo__LET演示/README.md)。
