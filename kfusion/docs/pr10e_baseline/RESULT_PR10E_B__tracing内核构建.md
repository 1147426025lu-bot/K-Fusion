# PR-10E 阶段 B：tracing 内核的准备与构建

日期：2026-10-28
对应评审要求：阶段 B「准备可回退的 tracing 内核」、不覆盖当前稳定内核、先核实内核来源。

---

## 一、核实当前运行内核的来源（不凭路径名）

评审明确要求「先确认当前运行内核确实由这棵源码及配置构建，而不是仅凭路径名判断」。查了五项：

| 检查项 | 结果 |
|---|---|
| `uname -r` | `5.15.86`（`#1 SMP PREEMPT`，**非 PREEMPT_RT**） |
| 构建者/时间 | `builder@e3665d6e1a6c`（容器主机名），`Wed Oct 14 10:16:11 UTC 2026` |
| 编译器 | `aarch64-linux-gnu-gcc (Debian 10.2.1-6) 10.2.1`，ld 2.35.2 → **交叉编译** |
| 包来源 | `dpkg -l` → `linux-image-jailhouse 5.15.86+r0`（Debian 包） |
| 树内产物 | `/root/linux-5.15.86` 下**没有** `arch/arm64/boot/Image`、`vmlinux`、`System.map`，仅有 in-tree 的 `.config`/`autoconf.h` |

**结论**：运行内核来自打包构建（容器内交叉编译），不是在这棵树上原生编出来的。

### 修正上一轮的说法

上一轮我说「`/boot/config-5.15.86` 与树 `.config` 不一致」，这个描述过重，现按实测更正：

```
CONFIG_ 行数：/boot/config-5.15.86 = 2080，树 .config = 2077
实质差异仅 3 项（全部 jailhouse 相关）：
  CONFIG_IVSHMEM_NET=y      （boot 有，树无）
  CONFIG_JAILHOUSE_DBGCON=y （boot 有，树无）
  CONFIG_UIO_IVSHMEM=y      （boot 有，树无）
另有 1 项 CONFIG_CC_VERSION_TEXT 差异（交叉 gcc vs 原生 gcc 的自述字符串）
```

所以正确表述是：**树与运行内核的配置几乎相同（差 3 项），差的那 3 项正是树里缺失的 jailhouse patch**——`ls drivers/ | grep jail` 为空，`grep JAILHOUSE_DBGCON --include=Kconfig` 无命中，证实源码树不含 jailhouse。

这条修正对实验没有影响，但如果不改，论文里会写成「使用了不同源的内核」，那是错的。

---

## 二、构建策略

| 决策 | 做法 | 理由 |
|---|---|---|
| 配置基线 | 用 `/boot/config-5.15.86`（运行内核真实配置） | 尽量贴近现网；不用树里那份（虽近乎相同，但无 jailhouse） |
| 源码树 | `cp -a` 成 `/root/linux-5.15.86-trace`，`mrproper` 后重配 | **stable 树保持原样**，我们的 kmod 仍按旧 vermagic 可编 |
| 版本号 | `CONFIG_LOCALVERSION="-trace"` → `5.15.86-trace` | 模块落 `/lib/modules/5.15.86-trace`，**不覆盖**现有模块目录 |
| 开启项 | `FTRACE` `TRACING` `FUNCTION_TRACER` `SCHED_TRACER` `OSNOISE_TRACER` `TIMERLAT_TRACER` `IRQSOFF_TRACER` | `olddefconfig` 后逐项 grep 确认生效（不是"以为开了"） |

`olddefconfig` 后实测生效：

```
CONFIG_LOCALVERSION="-trace"    CONFIG_PREEMPT=y        CONFIG_HZ=250
CONFIG_TRACING=y                CONFIG_FTRACE=y
CONFIG_FUNCTION_TRACER=y        CONFIG_IRQSOFF_TRACER=y
CONFIG_SCHED_TRACER=y           CONFIG_OSNOISE_TRACER=y
CONFIG_TIMERLAT_TRACER=y
```

---

## 三、三个必然差异，及其对实验的影响

| 差异 | 后果 | 是否影响 tracing 实验 |
|---|---|---|
| 丢了 `JAILHOUSE_DBGCON` / `IVSHMEM_NET` / `UIO_IVSHMEM` | 新内核**没有 jailhouse 驱动**；`config.txt` 里的 `dtoverlay=jailhouse` 只是加载 dtbo，不会有驱动匹配，设备不出现（不会导致启动失败） | **不影响**。本阶段只做单域 tracing 归因，不做跨域 |
| 交叉编译 → 原生编译 | 编译器都是 Debian gcc 10.2.1，**vermagic 不含编译器**，但含版本/`SMP`/`PREEMPT`/`MODULE_UNLOAD` 等 | 不影响内核本身；但**我们的 kmod 必须用新树重编**（`-trace` 后缀已使 vermagic 不同） |
| `LOCALVERSION=-trace` | `uname -r` = `5.15.86-trace`，模块目录隔离 | 这正是我们要的隔离效果 |

**因此**：切到 tracing 内核后，跑实验前必须用 `/root/linux-5.15.86-trace` 作 `KDIR` 重编 `kmod_uku` 与探针模块，否则 `insmod` 会因 vermagic 不匹配失败。

---

## 四、回退路径

- 备份目录：`/root/kernel-backup-20261028-1217`（65 MB）
  - `/boot/config-5.15.86`、`System.map-5.15.86`、`vmlinux-5.15.86`、`initrd.img-5.15.86`
  - 树 `.config`（`tree.config`）
  - 启动分区 `config.txt`、`cmdline.txt`
  - **`p1-full.img`：`/dev/mmcblk0p1` 整分区 dd 镜像（32 MB）**，可原样写回
- 串口已确认可用：`cmdline.txt` 含 `console=ttyS1,115200`，`config.txt` 含 `enable_uart=1`
- 启动切换方案：**`tryboot` 一次性启动**
  - `kernel8.img`（25 MB 稳定内核）**保持不动**
  - 新放 `kernel8-trace.img` + `tryboot.txt`（`tryboot.txt` = 完整 `config.txt` 内容 + `kernel=kernel8-trace.img`）
  - `reboot "0 tryboot"` → 固件本次用 `tryboot.txt`，成功则正常跑，失败/掉电则**自动回落 `config.txt`**
  - 固件支持性已实测：`strings start4.elf | grep -c tryboot` = 1（非 0）

若 `tryboot` 因固件版本被忽略，退化为「照常启动原内核」，不会变砖。

---

## 五、对照评审的阶段 B 验收项

| 验收项 | 状态 |
|---|---|
| tracing 内核独立构建（不覆盖 stable） | ✅ 独立树 + 独立 LOCALVERSION |
| 可启动 | ⬜ 待编译完成 + tryboot 验证 |
| 有回退路径 | ✅ 备份含 p1 整分区镜像；tryboot 自动回落 |
| 记录本次运行内核的完整标识 | ✅ 见第一节 |
| 修正"内核对不上"的错误表述 | ✅ 见第一节 |

---

## 六、明确未做的事

- 未编译完成，未安装、未重启。本文只记录核查结论与构建准备。
- 未对任何 ms 级尖峰做归因（PR-10D 遗留问题仍未解释）。
- 未声称 tracing 内核会给出比 stable 更好的数字——它的唯一用途是**解释异常**，正式性能数字仍回 stable 采集（评审阶段 D 要求）。
