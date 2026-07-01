# plc_multitask — 多任务优先级调度演示

**角色：SWAPPABLE 示例** — 展示 PLCFusion 多项能力在同一应用中的组合使用。

## 架构

```
main (1ms hrtimer 周期)
  ├─ mt_scheduler_tick()     定长任务表 + 优先级位图（协作式）
  │    ├─ task_sensor_fusion  prio 6 / 10ms   float 加权融合
  │    ├─ task_pid_control    prio 5 / 20ms   double PID
  │    ├─ task_alarm_log      prio 3 / 100ms  printf + mutex
  │    └─ task_stats_heap     prio 1 / 500ms  malloc/free
  └─ pthread watchdog_thread  5ms 轮询 alarm（plc_pthread_host）
```

## 用到的项目能力

| 能力 | 体现 |
|------|------|
| 多 TU llvm-link | 主程序 + 调度器 + 任务集 |
| `FUSE_RUN_MAIN=1` | `main()` 入口 |
| hrtimer 宿主 | `nanosleep` 1ms 周期 |
| pthread 宿主 | `watchdog_thread` |
| mutex 桩 | `g_state_lock` 保护共享浮点状态 |
| Q 定点 Pass | 源码内 `double`/`float` PID 与传感融合 |
| DCE / hotpath | 仅保留调度热路径函数 |
| AST 预检 | `FUSE_AST_PREFLIGHT=1` |

## 快速运行

```bash
export PRJ=/home/pi/plc_compiler
cd "$PRJ"

bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_plc_multitask__多任务优先级.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_multitask__多任务优先级.env

sudo insmod test/plc_multitask_mod.ko
sudo dmesg | tail -20

echo 1 | sudo tee /sys/module/plc_multitask_mod/parameters/shutdown_request
bash scripts/safe_rmmod_fused__安全卸载.sh plc_multitask_mod
```

用户态先编译验证（可选）：

```bash
gcc -O2 -I examples/plc_multitask__多任务示例 -I include \
  examples/plc_multitask__多任务示例/plc_multitask_demo__主程序.c \
  examples/plc_multitask__多任务示例/plc_multitask_scheduler__调度器.c \
  examples/plc_multitask__多任务示例/plc_multitask_tasks__任务集.c \
  examples/plc_multitask__多任务示例/plc_multitask_uspace_stubs__用户态桩.c \
  -lpthread -lm -o /tmp/plc_multitask_demo && /tmp/plc_multitask_demo
```

## 文件

| 文件 | 说明 |
|------|------|
| `plc_multitask_demo__主程序.c` | `main`、信号、`pthread_create` |
| `plc_multitask_scheduler__调度器.c` | 优先级查表调度 |
| `plc_multitask_tasks__任务集.c` | 浮点任务、PID、堆统计 |
| `plc_multitask__多任务.h` | 共享声明与全局导出符号 |
