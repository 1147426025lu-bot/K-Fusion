# PLCFusion Compiler

将用户态 C 程序编译为可在 Linux 内核模块中链接运行的 freestanding `.o`。

> 完整文件索引见 [FILES.md](FILES.md)（`原名__功能说明` 命名规则）。  
> **仓库结构（固定代码 / 可替换测试 / 生成物）** 见 [REPO_LAYOUT.md](REPO_LAYOUT.md)。

---

## 能内核化什么

**可以。** 较复杂的程序（多线程 rt-tests、多 TU 工程、带浮点/定时器/互斥的周期逻辑）已纳入 CI 与批量验证，不必为每类应用单独写宿主。

当前 **13 个 manifest** 覆盖下列 **6 大类**（均走统一 `ignite_fused__通用ko构建.sh`）：

| 大类 | 典型应用 | manifest 要点 | 宿主组件 |
|------|----------|---------------|----------|
| **1. rt-tests 多线程 main** | signaltest、ptsematest | `FUSE_RUN_MAIN=1`，自动链 pthread 宿主 | `plc_fused_host` + `plc_pthread_host` |
| **2. rt-tests 周期定时** | cyclictest（含多 TU + histogram） | `FUSE_RUN_MAIN=1`，`FUSE_HOST=hrtimer` | 上者 + `plc_fused_timer_host` |
| **3. 第三方 RT demo** | github rt_periodic | `FUSE_KTHREAD_ENTRY=plc_main` 或 main 路径 + hrtimer | hrtimer 宿主 |
| **4. plc-cc 周期控制** | hello / gpio / 温控 / 隔离 / 抖动等 6 例 | `FUSE_KTHREAD_ENTRY=plc_main`，`FUSE_HOST=hrtimer` | hrtimer 宿主 |
| **5. 单 TU 库/算法** | stb sprintf demo | `FUSE_RUN_MAIN=1`，POSIX 面小 | 通用宿主 + runtime 桩 |
| **6. 多 TU C 工程** | cyclictest+histogram、rt_periodic multitu | `FUSE_EXTRA_SOURCES='...'` | 按 IR 自动推断 hrtimer/pthread |

**自研复杂 C 代码** 走同一流程：写 manifest → `plc_fuse` → `ignite_fused`。Pass 会自动探测入口、合并缺失桩、按 IR 选 hrtimer/pthread 宿主。

**尚不适合或需额外工作：**

- 重度依赖未映射 libc（socket、复杂 I/O、动态加载）— 需补 `kRemap` 或 runtime 桩
- 大量浮点且 `FUSE_FLOAT_KILL=0` 时，`ignite_fused` 会自动链 `plc_compiler_rt__软浮点桩.c`（`FUSE_LINK_COMPILER_RT=auto`）
- 不是把任意用户态进程「整体搬进内核」，而是 **源码算法保留、POSIX 换 ABI、链接进 .ko**

cyclictest **极致抖动** 仍可选用专用宿主 `scripts/deploy/ignite_official_cycletest__cyclictest主线.sh`；与通用路径 **注入方式相同**（都是调用 `_kernel.o` 里的函数），差别在 timer 实现是否更贴 benchmark。

---

## 内核化原理

### 流水线

```
C 源码 ──Clang──► LLVM IR ──PLCFusionPass──► 内核化 IR ──llc──► ${FUSE_NAME}_kernel.o
                                                      │
              plc_runtime_stubs.o + plc_fused_host.o (+ timer/pthread 宿主) ──► ${FUSE_NAME}_mod.ko
```

1. **IR 变换（Pass）**：`malloc`→`plc_kmalloc`，`pthread_create`→`plc_pthread_create`，`timer_create`→`plc_timer_create` 等；未映射的外部调用可黑洞或留给桩；从入口函数做 DCE，只保留可达代码。
2. **产物**：`${FUSE_NAME}_kernel.o` 含你的 **原始算法**（timerthread、main、plc_main 等），不含 glibc。
3. **宿主 `.ko`**：只做 **启动、POSIX 替身、卸载**；**不向宿主注入应用源码**。

### 运行时模型（函数调用，非「掏空线程」）

```
insmod → module_init → kthread_create("plc_fused_worker")
                    → fused_worker() 里调用 main() 或 timerthread() / plc_main()
```

- 应用逻辑通过 **普通 C 函数调用** 进入 `_kernel.o`，不是预留空内核线程再填代码。
- `main()` 里若调 `pthread_create`，Pass 映射为 `plc_pthread_create`，pthread 宿主 **按需再建 kthread**，入口仍是你的 `timerthread` / `fifothread` 等函数。
- 专用 cyclictest 宿主同样：`kthread` → `timerthread(fused_par)`。

### 两条编译路径（同一仓库）

| | **PLCFusion** | **plc-cc** |
|--|---------------|------------|
| 输入 | 任意 C（rt-tests、demo、自研） | 手写 `plc_cycle()` 的 PLC 程序 |
| Pass | `PLCFusionPass`（POSIX→plc_*） | `PLCLowJitterPass`（周期抖动优化） |
| 验证 | cyclictest / verify_fused 13 类 | `examples/plc-cc__低抖动示例/` |

二者共享 `plc_abi__运行时ABI.h` 与 `plc_*` 运行时 ABI。

---

## 快速开始

```bash
export PRJ=/home/pi/plc_compiler
cd $PRJ

# 依赖 + 编译 Pass（首次）
# LLVM：apt 安装 clang-19，或确保 /usr/local/llvm-{17,18,19}/bin 在 PATH 中
sudo apt install -y clang-19 llvm-19 cmake python3-matplotlib \
    raspberrypi-kernel-headers   # 或 linux-headers-$(uname -r)
cd build && cmake .. && make PLCFusionPass -j$(nproc) && cd ..

# 融合 + 通用 .ko + 加载（以 signaltest 为例）
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_signaltest__信号测试.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_signaltest__信号测试.env
sudo insmod test/signaltest_mod.ko
echo 1 | sudo tee /sys/module/signaltest_mod/parameters/shutdown_request
bash scripts/safe_rmmod_fused__安全卸载.sh signaltest_mod
```

cyclictest 测量主线（融合 + 加载）：

```bash
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_cyclictest__主线压测.env
cd scripts/deploy && bash ignite_official_cycletest__cyclictest主线.sh
cat /sys/kernel/debug/fused_stats
```

**测量类型**（勿与「融合清单」混淆）：

| 类型 | 含义 | 入口 |
|------|------|------|
| **浸泡 soak** | CPU3 安静隔离，仅 1kHz 周期自跑，测 best-case 抖动 | `run_soak_cycletest__浸泡长测.sh` |
| **加压 stress** | CPU0-2 跑 hackbench 背景负载，CPU3 测最坏延迟 | `run_stress_cycletest__加压长测.sh` |

正式测量 **最短 15 分钟**；结果写入 `results/soak/` 或 `results/stress/`。详见 [scripts/deploy/README.md](scripts/deploy/README.md)。

### 多平台（`PLC_PLATFORM`）

| 平台 | 架构 | 状态 |
|------|------|------|
| `rpi5`（aarch64 默认） | AArch64 + `-fp-armv8,-neon` | **已验证**（浸泡/加压） |
| `x86_64` | x86-64 + `+sse4.2` | **构建已支持**；insmod/抖动需 x86 实机 |
| `generic` | 按 `uname -m` 回退 | 隔离参数需自配 |

```bash
# Raspberry Pi（默认，可省略 PLC_PLATFORM）
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_signaltest__信号测试.env

# x86_64：在 Pi 上交叉验证 kernel.o 架构（无需 x86 机器）
export PLC_PLATFORM=x86_64
bash scripts/platform/validate_build__平台构建验证.sh

# x86 实机：同上 fuse + ignite，再 insmod
export PLC_PLATFORM=x86_64
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_signaltest__信号测试.env
```

配置：`manifests/platform/{rpi5,x86_64,generic}.env`。详见 [docs/platform/README.md](docs/platform/README.md)。

**注意**：不要用 `sudo bash ignite_official_cycletest__...` 跑整脚本（`sudo` 会重置 `PATH`，找不到 `/usr/local/llvm-*` 里的 clang）；`insmod` 在脚本 `[5/5]` 内自动 `sudo`。跑前建议先 **`sudo -v`**（刷新 sudo 凭证，避免 `[5/5]` 等密码）。若上一步 `plc_fuse` 已成功，可跳过重建：`FORCE_REBUILD_KERNEL_O=0 bash ignite_official_cycletest__cyclictest主线.sh`

---

## 故障排除（终端 / insmod / reboot 卡住）

开发 fused 模块时，若 **`rmmod` / `insmod` 卡在内核**，会出现：`Ctrl+C` 无效、新开的 `sudo`/`lsmod`/`reboot` 也长时间无响应。这不是终端坏了，而是进程处于 **D 状态（不可中断睡眠）**，在等内核模块子系统。

### 典型原因

| 现象 | 常见原因 |
|------|----------|
| 卡在 `[5/5] 加载模块...` | 旧模块未卸干净；`sudo -n` 未先 `sudo -v`；`insmod` 在 `module_init` 里阻塞 |
| `Ctrl+C` 停不下来 | 前台 bash 在 `wait` 一个 **D 状态** 的子进程（`sudo`、`rmmod`、`insmod`） |
| `sudo reboot` 也卡住 | 内核模块路径已死锁；软重启需等内核清理，可能永远等不到 |
| 之前有 `rmmod xxx_mod` 一直不返回 | 例如残留的 `rmmod signaltest_mod`，会拖住后续所有模块操作 |

### 立刻恢复（按顺序试）

**1. 硬件重启（最可靠）**

- 树莓派：**长按电源键约 3 秒**强制断电，或 **拔电源** 后再插上（仅在软重启无效时用）。
- 远程 SSH 全卡死时，只能走 **带外**（物理电源 / 智能插座 / IPMI），无法在 shell 里解决。

**2. 若还能敲键、但 `reboot` 无响应，可试一次魔法键重启**（不保证成功，内核已死锁时同样会卡）：

```bash
# 需 root；会立即重启、不 sync，仅作软 reboot 失败时的备选
echo 1 | sudo tee /proc/sys/kernel/sysrq
echo b | sudo tee /proc/sys/sysrq-trigger
```

若这条也卡住 → **直接硬件断电**。

**3. 重启后**（确认无残留模块再跑）：

```bash
lsmod | grep -E 'official_cycletest|signaltest|ptsematest'   # 应为空
sudo -v
cd /home/pi/plc_compiler/scripts/deploy
bash ignite_official_cycletest__cyclictest主线.sh
```

### 预防（避免再次卡死）

1. **加载前**：`sudo -v`；`lsmod` 确认没有旧的 `*_mod`。
2. **卸载**：优先用脚本，不要强杀终端：
   - cyclictest 主线：`bash scripts/deploy/safe_rmmod_official__cyclictest卸载.sh`
   - 通用模块：`bash scripts/safe_rmmod_fused__安全卸载.sh <模块名>`
3. **切换应用前**先卸旧模块（例如先卸 `signaltest_mod` 再跑 cyclictest）。
4. **`rmmod` 超过 ~60s 仍无返回**：不要反复 `insmod` / 不要开新终端狂试 `sudo` → **直接 reboot**，否则容易 D 状态死锁。
5. **不要用 `sudo bash ignite_...` 跑整脚本**；不要用 `kill -9` 杀正在跑 cyclictest 的会话。

### 诊断（系统还能响应时）

```bash
ps -eo pid,stat,cmd | grep -E 'rmmod|insmod|ignite_official'
# STAT 含 D → 不可中断，kill/Ctrl+C 通常无效
sudo dmesg | tail -40
```

`ignite_official_cycletest__cyclictest主线.sh` 已在 `[5/5]` 增加分步日志与 `insmod` 超时（默认 30s，可调 `INSMOD_TIMEOUT_SEC=60`）。

---

## 验证

| 命令 | 说明 |
|------|------|
| `bash scripts/run_ci__CI门禁.sh` | 无 insmod：Pass + 13 manifest 融合 + 覆盖率 + JSON + WCET sweep |
| `bash scripts/run_smoke_tests__冒烟测试.sh --insmod` | CI + cyclictest insmod 短测 |
| `bash scripts/run_smoke_tests__冒烟测试.sh --full` | 上者 + `verify_fused_apps` 全 13 类 insmod |
| `bash scripts/verify_fused_apps__批量验证.sh` | 仅批量 insmod/rmmod（需 sudo） |

单 manifest CI：`MANIFESTS="$PRJ/manifests/manifest_xxx.env" bash scripts/run_ci__CI门禁.sh`

---

## 新应用三步

```bash
cp manifests/manifest_template__清单模板.env manifests/my_app.env
# 编辑 FUSE_SOURCE、FUSE_RUN_MAIN=1 或 FUSE_KTHREAD_ENTRY=...
bash scripts/plc_fuse__内核化主流程.sh manifests/my_app.env
bash scripts/plc_fuse_report__覆盖率报告.sh manifests/my_app.env   # 查缺桩
bash scripts/ignite_fused__通用ko构建.sh manifests/my_app.env
sudo insmod test/my_app_mod.ko
```

| 变量 | 说明 |
|------|------|
| `FUSE_NAME` | 输出前缀 → `test/${FUSE_NAME}_kernel.o` |
| `FUSE_SOURCE` / `FUSE_EXTRA_SOURCES` | 主源文件 / 多 TU |
| `FUSE_RUN_MAIN=1` | 宿主 kthread 调 `main()`（rt-tests 类） |
| `FUSE_KTHREAD_ENTRY` | 宿主直接调线程入口（如 `timerthread`、`plc_main`） |
| `FUSE_HOST` | `generic` / `hrtimer`（可省略，ignite 从 IR 推断） |
| `FUSE_PIPELINE` | `auto` / `hotpath` / `debug` 等 |
| `FUSE_AUTO_DETECT=1` | 从 pre.ll 推断入口与 DCE roots（默认开） |

融合产物：`test/${FUSE_NAME}_kernel.o`、`.fusion_report`、`.unmapped`（缺符号列表）。

---

## 常用命令

```bash
# 覆盖率 / 一页报告
bash scripts/plc_fuse_report__覆盖率报告.sh manifests/manifest_cyclictest__主线压测.env
bash scripts/plc_fuse_fusion_report__一页报告.sh manifests/manifest_cyclictest__主线压测.env

# WCET 对照 / 自动调优
bash scripts/plc_fusion_wcet_sweep__tail对照.sh manifests/manifest_cyclictest__主线压测.env
WCET_AUTOTUNE_SKIP_INSMOD=1 bash scripts/plc_fusion_wcet_autotune__WCET自动调优.sh manifests/manifest_cyclictest__主线压测.env

# 用户态 vs fused 对比（需 sudo）
DURATION_SEC=120 bash scripts/demo_compare__用户态vs融合.sh

# 浸泡 / 加压（≥15min，deploy 目录）
cd scripts/deploy
DURATION_MIN=15 bash run_soak_cycletest__浸泡长测.sh
DURATION_MIN=15 bash run_stress_cycletest__加压长测.sh

# 论文实验
PAPER_RUNS=5 DURATION_MIN=15 bash scripts/paper/run_paper_baseline_matrix__论文基线矩阵.sh

# 清理本地 <15min 历史数据
bash scripts/maintenance/cleanup_results__清理结果.sh
```

---

## 成功判据（摘要）

| 阶段 | PASS |
|------|------|
| 融合 | `test/${FUSE_NAME}_kernel.o` 非空，退出码 0 |
| 覆盖率门禁 | `plc_fuse_check`：`unmapped ≤ 25`（可调 `MAX_UNMAPPED`） |
| insmod | `dmesg` 无 unresolved symbol；debugfs `fused_stats` 可读 |
| verify_fused | 13 类均 `✅ *_mod OK` |

---

## 扩展与细节

- **完整项目报告（六大类加强 + cyclictest 逐步详解）**：[`docs/PLCFUSION_完整报告__项目总览.md`](docs/PLCFUSION_完整报告__项目总览.md)
- **加 POSIX 映射**：`backend/pass/PLCFusionPass__内核化Pass.cpp` 的 `kRemap[]`
- **加运行时桩**：`src/plc_runtime_stubs__POSIX桩.c` 或 per-app 自动合并桩
- **Pass 组合 / WCET 搜索 / 脚本索引 / 压测 profile**：见 [FILES.md](FILES.md)

示例 manifest：`manifests/manifest_cyclictest__主线压测.env`、`manifest_signaltest__信号测试.env`、`manifest_plc_cc_gpio__PLC示例.env`
