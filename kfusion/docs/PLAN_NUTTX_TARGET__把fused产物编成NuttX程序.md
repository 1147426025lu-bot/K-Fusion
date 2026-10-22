# fused 产物编译为 NuttX 程序 —— 零移植成本的同域对比

> 2026-10-19 提出并**两次实测验证**。
> 最终结论：**用 NuttX 自带的 cyclictest 源码作内核化输入，移植成本为零**（27 个依赖全部满足）。
> 这比原"裸机 inmate 桩"（3–5 天）和"编 rt-tests 原版"（~1 天补 stub）都更优。

---

## 1. 核心设计

cRTOS 的 60 µs 就是「**在 NuttX 里跑 cyclictest**」的结果。我们改成：

| | cRTOS（Access'25） | K-Fusion |
|---|---|---|
| 源码 | NuttX 移植版 cyclictest | **同一份源码** |
| 变换 | 无 | **过 LLVM 内核化 pass** |
| 运行域 | NuttX cell（CPU1） | **同一个 NuttX cell** |
| 负载/口径 | cpu/cache/vmem/stream、abs_max | 完全相同 |
| **唯一变量** | — | **是否经过编译器内核化** |

---

## 2. 两次实测（Pi 上真实编译，非推断）

### 2.1 路线甲：编 rt-tests **原版** → 成本高，放弃

从 kernel.org clone `rt-tests`，用 aarch64 gcc 编 `src/cyclictest/cyclictest.c`：

| 项 | 结果 |
|---|---|
| 编译 | ❌ 先卡在 `numa.h`（`rt_numa.h` **无条件** `#include`，没有 `NUMA` 宏开关） |
| 装 `libnuma-dev` 后 | ✅ 成功，57 776 字节 |
| **外部依赖** | **116 个** |
| **NuttX 缺失** | **60 个** |

60 个缺失分四类：

| 类别 | 数量 | 处理 |
|---|---|---|
| rt-tests 自己的 `src/lib/*.c` 里已定义（`check_privs`/`hist_*`/`err_msg`/`get_available_cpus`…） | ~35 | **一起编即可，零成本** |
| NUMA（`numa_*`） | 9 | 需 no-op stub |
| POSIX 边角（`mlockall`/`mmap`/`shm_open`/`setitimer`/`sigwait`…） | ~12 | 需 stub |
| 变量与 libc 边角（`optarg`/`stderr`/`sysconf`/`__xstat`） | ~4 | 需适配 |

→ **约 25 个 stub + 一个假 `numa.h` 头**，约 1 天。

### 2.2 路线乙：编 **NuttX 版** cyclictest → **零成本** ✅

用 `apps/benchmarks/cyclictest/cyclictest.c`（1115 行，NuttX 移植版）作输入：

```
aarch64-none-elf-gcc -c cyclictest.c -o nx_cyc.o \
  -I nuttx/include -I apps/include -D__NuttX__ -mcpu=cortex-a72 -Os -fno-builtin
```

| 项 | 结果 |
|---|---|
| 编译 | ✅ **成功**，14 688 字节 |
| **外部依赖** | **27 个** |
| **NuttX 缺失** | **0 个** |

27 个依赖全部是 NuttX 原生符号：

```
calloc clock_gettime clock_nanosleep close fprintf free getoptargp getoptindp
getopt_long getpid gettid ioctl lib_get_stream malloc memset open perror
poll printf pthread_create pthread_join pthread_self pthread_setschedparam
puts strcmp strtol usleep
```

> `gettid` / `lib_get_stream` / `getoptargp` 是 NuttX 特有符号，证明编译环境与目标 ABI 正确对接。

**这是决定性结果**：不需要写 stub、不需要假 NUMA 头、不需要裸机桩。

---

## 3. 实施步骤（约 0.5–1 天，前提是有 LLVM 环境）

### 步骤 1：产出 fused `.o`（2–3 小时）

用现有管线，**只把输入换成 NuttX 版源码**：

```bash
# manifest 改动（相对现有 manifest_cyclictest__主线压测.env）
FUSE_SOURCE=apps/benchmarks/cyclictest/cyclictest.c   # 而不是 rt-tests 的 src/cyclictest
FUSE_INCLUDE_DIRS=nuttx/include;apps/include
FUSE_CLANG_FLAGS='-Os -fno-builtin -D__NuttX__ -D_GNU_SOURCE'
FUSE_HOST=none            # 不再用 hrtimer 宿主（NuttX 有 pthread）
```

**验收（唯一标准）**：

```bash
aarch64-linux-gnu-nm -u fused_kernel.o
# 只允许出现上面那 27 个 POSIX/NuttX 符号（或经 pass 重映射后的 plc_*）
# 不得出现任何 Linux 内核符号（ktime_get / printk / mod_* ...）
```

### 步骤 2：编成 NuttX app（2–3 小时）

最小改动：**直接替换 `apps/benchmarks/cyclictest/cyclictest.c` 为 fused 产物**
（保持 PROGNAME=cyclictest、同样的 argv 语义），这样：
- 命令行参数完全相同（`-n 0 -m 0 -q -p 200 -h 200 -l 100000 -i 1000`）
- 测量口径完全相同
- 唯一差别就是内核化

或在 `apps/examples/kfused/` 另建一个程序，与 `cyclictest` 并存，便于 A/B 切换。

### 步骤 3：同条件测量（2 小时）

- 同 cell（CPU1）、同 1 kHz、同 `-l 100000 -q -h 200`
- 五档负载用现有 `xlat_load_matrix__负载档矩阵.sh` 的 stress 配置
- 输出：`abs_max` + 直方图，与 cRTOS 的 60 µs 正面比

---

## 4. ⚠️ 一个必须提前接受的预期

**跑在 NuttX 里，两者抖动大概率几乎一样。**

5 µs 量级由 OS 调度 + 硬件中断 + tickless 补偿决定；编译器 pass 的收益是几十~几百 ns，会被完全淹没。

所以这个实验的价值是**四个维度**，不是"数字更小"：

| 能得到什么 | 说明 |
|---|---|
| **① 不比它差** | 同域同 OS 同负载下，内核化变换不会让实时性变差——这是必须证明的基线 |
| **② 同一产物跨域可部署** | 同一份源码/产物能跑 Linux 内核态、NuttX 用户态（将来还有裸机）——**cRTOS 结构上做不到** |
| **③ 编译期融合测量** | cRTOS 用外部工具测；我们把定时+测量编进产物本身 |
| **④ 编译器贡献可分离** | NuttX 域测不出，但裸机域能测出（那时"显微镜"才有用） |

**论述用维度优势，不要用数字优势。** 数字上"打平"就已经赢了一个论证：
cRTOS 靠架构换来的实时性，我们靠"架构 + 编译器"两条腿达到，且产物可移植。

---

## 5. 当前唯一缺口

**这张卡（jailhouse/cRTOS 卡）没有 LLVM/CMake**（只有 gcc/make）。
K-Fusion 的 pass 需要 `find_package(LLVM)` + `Clang`，按新卡（Debian trixie, LLVM 18/19）的 API 写的。

→ 步骤 1 需要在**新卡（K-Fusion 卡）**上执行，或给这张卡装 LLVM（版本可能不兼容）。

**但本结论不依赖 LLVM**：输入可用（已编译验证）、依赖可满足（27/27 全中）——只差跑 pass。

---

## 6. 与原方案的关系

| 原方案 | 现方案 |
|---|---|
| 裸机 inmate 桩（3–5 天，自己实现时间源/中断/内存） | **不需要** |
| 编 rt-tests 原版（116 依赖 / 缺 60，~1 天补 stub） | **不需要** |
| **编 NuttX 版（27 依赖 / 缺 0）** | ✅ **采用，0.5–1 天** |
| 对比需换算口径 | 同域同口径，直接可比 |
