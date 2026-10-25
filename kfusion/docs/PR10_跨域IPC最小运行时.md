# PR10 —— 跨域 IPC 最小运行时（IVSHMEM 发布-订阅）

> 状态：**两端实现 + 板上实测完成**（2026-10-25）。
> 目标：给 ECoS 的跨域边一个**可测的代价**，而不是拿单域融合的 `c`（唤醒税）去糊。
> 结论先说：**通路本身是亚微秒级，但控制回路里跨域依赖的代价是"一个周期"**。
> 这两件事差三个数量级，混用会把跨域优化的收益算到天上去。

---

## 1. 为什么不是"把旧 PoC 扩大一下"

仓库里原有 `xshm` + `ivshmem_probe`：直接拿 rw_section 起始 16 字节当 mailbox，
四个 32 位字，单向写、对端轮询。它能证明"通路通了"，但做不了三件事：

| 缺口 | 后果 |
|---|---|
| 只用一个方向的一段内存 | 两个方向要同时跑就得各写一套偏移，两端一旦不一致就是"都跑得动、数据不对" |
| 无提交语义（读者可能读到半个槽） | 跨域场景下这个窗口比单核大得多 |
| 无"这一轮"概念 | cell 重建**不清零** IVSHMEM，上一次的残留会被当成有效状态 |

PR10 的运行时有四条硬设计：

1. **一份头，两个宿主**：`kfusion/runtime/xshm/ecos_xipc.h` 被 root 侧（Linux 用户态）
   与 inmate 侧（NuttX）同时 `#include`，布局与环操作**只有一处定义**。
   仓库里不允许有第二份（自测里查）。
2. **按 peer 分片**：rw_section 是 3 peer × 12 KiB（0x9000）。peer0 片放 root→inmate 环，
   peer1 片放 inmate→root 环，peer2 片放统计。补上了旧 PoC 明确留下的 TODO。
3. **SPSC 无锁环**，每个环唯一写者、唯一读者，`head`/`tail` 各占一条 cache line；
   提交靠 `slot.seq`：先写负载 → 再写 seq → 最后推进 head，读者只认 `seq == tail+1`。
4. **session 号**：owner 每轮用 `CNTVCT` 生成一个新 session 写进环头，对端必须看到
   session **变化**才认为"新一轮开始了"。这一条是实测踩出来的，见 §5 的坑 3。

```
rw_section @ 0x1faf1000（root: /dev/uio0 map2；inmate: 同一物理地址同虚拟地址）
  0x0000  peer0 片  ┌─ struct xipc_ring  root → inmate  （224 槽 × 16 B）
  0x3000  peer1 片  ├─ struct xipc_ring  inmate → root
  0x6000  peer2 片  └─ struct xipc_stats（各字段归属固定，避免伪共享）
```

环头：`magic 0x00 | version 0x04 | cap 0x08 | slot_bytes 0x0C | session 0x10 |
head 0x40 | tail 0x80 | drops 0xC0 | slot[224] 0x100`，总共 3840 B，装进一页。
这些偏移在头文件里由 `XIPC_ASSERT_EQ` 编译期断言，另有
`kfusion/scripts/ecos/xipc_layout_check.py` 在本机（无编译器）做同一套算术校验。

### 消息与控制面

| kind | 方向 | 语义 |
|---|---|---|
| `HELLO` | inmate→root | 上线，a=版本，b=CNTFRQ |
| `PING` | root→inmate | a=序号，b=发送时刻（counter 低 32 位） |
| `ECHO` | inmate→root | a=序号，b=**读到 PING 那一刻**的 counter |
| `TICK` | inmate→root | 周期数据，b=产生时刻 |
| `MODE` | root→inmate | a=tick_every，b=period_us（运行时切模式） |
| `STOP` | root→inmate | 收工 |

`MODE` 不只是省一次交互：那块板子的串口**只能看不能敲**（只接了 Pi TX → 适配器 RX），
没法在 NSH 里输参数。让 root 侧用一条消息切换实时侧的工作模式，等于把"控制面"
也架在同一条环上——正好是要证明的东西：一条 SPSC 环 + 两类消息就能跑一个跨域控制回路。

---

## 2. 为什么两端都是轮询（门铃的硬约束）

IVSHMEM 门铃 = 往寄存器区 offset `0x0C` 写 `(target<<16 | vector)`，由 hypervisor
陷获后向目标 cell 注入中断。它成立的前提是**寄存器区被注册成可陷获的 MMIO**，而这
要求本 cell 的 PCI 命令寄存器置了 `MEM` 位：

- root 侧：`uio_ivshmem` 驱动置了 → 门铃路径存在（`/proc/interrupts` 里能看到
  `uio_ivshmem[0001:00:00.0]`，GICv2 182）。
- NuttX 侧：**没有 PCI 枚举**，这个位不会置。此时 cell 配置里 0x1faf0000 只给了
  `JAILHOUSE_MEM_READ`，对该地址的写不是"没效果"，而是**故障**。

所以本轮两端都是轮询。对周期性控制任务这不是妥协：实时侧本来就要在周期边界采样，
边界上的轮询是零额外代价的。门铃要等 NuttX 侧补一个最小 PCI 配置访问（置 `CMD_MEM`
+ 启 INTx）才谈得上，记在 §6。

---

## 3. 实测（Pi4，root cell ↔ NuttX inmate，1 kHz 参考周期）

条件：Pi4B / jailhouse v0.12-342 / 内核 5.15.86 / NuttX cell 独占 CPU1 /
root cell 0,2-3 / 未绑核 / `CNTFRQ = 54 MHz`。
分位数为原始样本排序后的精确值（不用直方图近似，原因见 §5 坑 6）。

### 3.1 通路下限（inmate 忙等，3000 次一问一答）

| 量 | p50 | p90 | p99 | p99.9 | min | max |
|---|---|---|---|---|---|---|
| 单程 root→inmate | **0.65 µs** | 0.65 | 0.70 | 71.61 | 0.59 | 71.85 |
| RTT | **1.24 µs** | 1.26 | 1.31 | 72.28 | 1.19 | 76.85 |

- 单程/RTT ≈ 1.9 —— 这是**时间基准自洽的经验证据**：两侧都读 `CNTVCT_EL0`，
  Jailhouse 不设 `CNTVOFF`，所以是同一个计数器。异常样本 0 / 3000。
- 尾部 72 µs 不是通路本身，是偶发的调度/中断介入（未绑核，未做归因）。

### 3.2 控制回路里的真实代价（inmate 按 1 ms 周期采样，500 次）

| 量 | p50 | p90 | p99 | min | max |
|---|---|---|---|---|---|
| 单程 | **1005.26 µs** | 1005.33 | 1005.37 | 285.70 | 1006.18 |
| RTT | **1005.87 µs** | 1005.94 | 1006.00 | 286.30 | 1017.44 |

**p50 ≈ 1.005 个周期**，不是半个周期。原因不是"运气差"，而是调用方被相位锁死：
root 收到 ECHO 后立刻发下一个 PING，而此刻 inmate 刚好做完本轮 drain 去睡，
于是下一个请求必然等满一整拍。min 285.7 µs 对应"恰好在 drain 前到达"的少数样本。

对 ECoS 的含义很直接：**跨域边是一条 ≥1 个周期的延迟边**。评估"该不该把这段搬到
另一个域"时，必须用 `≈ period` 而不是 §3.1 的微秒数。

### 3.3 消费侧轮询粒度（inmate 每 1 ms 推一个 TICK）

| root 的 drain-sleep | p50 | p90 | p99 | max |
|---|---|---|---|---|
| 0（忙等） | **0.74 µs** | 0.78 | 0.80 | 15.78 |
| 250 µs | 166.56 µs | 296.04 | 323.07 | 363.22 |
| 500 µs | 286.20 µs | 509.83 | 563.33 | 698.91 |

p50 ≈ `sleep/2 + 40 µs`。那 40 µs 是 `usleep()` 的固有超调（Linux 的 sleep 粒度），
不是通路开销——把它算进"IPC 延迟"会高估一个数量级以上。

---

## 4. 三个必须分开说的判断

1. **通路延迟 ≠ 依赖代价。** 0.65 µs 是"两个 cell 都空转时"的下限；1.005 ms 才是
   "实时侧按周期采样"时的真实值。论文里只能引用后者作为跨域边的代价。
2. **门铃不是"还没做"，是"这侧没有 MMIO 陷获"。** 写门铃会撞只读映射。不写清楚
   这一点，后来的人会以为加个 `write()` 就行。
3. **时间可比是有前提的。** 两侧共基准靠"Jailhouse 不设 CNTVOFF"这个事实，
   不是协议保证的。所以运行时自带经验校验（单程 > RTT 就单列计数），
   一旦换 hypervisor / 换核，异常计数会立刻暴露。

---

## 5. 踩到的坑（每条都花了不止一轮）

| # | 症状 | 根因 | 处理 |
|---|---|---|---|
| 1 | root 侧 `SIGILL`，且**一行输出都没有** | `mrs cntpct_el0` 在 EL0 是未定义指令（内核没置 `CNTKCTL_EL1.EL0PCTEN`）；stdout 走管道是块缓冲，崩了什么都不留 | 改用 `CNTVCT_EL0`（VDSO 用的就是它）；root 侧 `setvbuf(_IOLBF)` |
| 2 | 单程**恒为负**、可见值溢出到量程顶 | 环槽只有 32 位，我把截断值和 64 位值直接比较 | 两端统一用"32 位回绕差"：`(uint32_t)(b - a)`，@54 MHz 可表示 79 s |
| 3 | root 等 30 s 收不到 HELLO，inmate 日志说"已发过" | **IVSHMEM 内存不随 cell 重建清零**，inmate 看到上一轮的 magic 就上线，HELLO 被 root 随后的 init 抹掉 | 加 session：owner 每轮写新的 `CNTVCT` 值，对端必须看到 session **变化** |
| 4 | 改了代码、构建报成功、跑起来还是老行为 | `cp -a` 保留 PC 侧 mtime（还差 8 小时时区），make 认为源码没变；而"镜像里有 xipc"的校验串从第一版就存在 | 构建脚本强制 `touch` + 删 `*.o`；校验串换成**本版专有**标记 `xipc: session 0x` |
| 5 | 开机只打一行 `usage: xipc ...` 就结束 | `task_spawn` 会把 `CONFIG_INIT_ARGS` 塞成 `argv[1]`（argc=2），参数解析遇到不认识的直接 `return 1` | 未知参数只告警并继续；所有参数有缺省值 |
| 6 | "周期"档 p50 报出 `819.10 µs` | 100 ns × 8192 = 819 µs 的直方图，样本全撞桶顶，报出来的是**桶顶假值** | 改存原始样本再排序求分位数（几千个样本，不值得用近似） |
| 7 | 忙等档"2000 万次迭代"0.4 秒就跑完，实验还没开始就退出 | 退出条件写成迭代数，而忙等一圈只有几十纳秒 | 退出条件改成**时间**（deadline），迭代数只作上限 |

另外两条不是坑但值得记：
- inmate 侧日志必须**纯 ASCII**（中文经 mini-UART 出去是乱码）；
- 开机入口从 `fiveloop_main` 换到 `xipc_main` 而不是"关掉 CONFIG_BENCHMARK_FIVELOOP"
  ——关配置项后旧 `.o` 还会赖在 `libapps.a` 里（上一轮踩过）。

---

## 6. 没做 / 下一步

- **门铃（中断）**：需要 NuttX 侧最小 PCI 配置访问（置 `CMD_MEM`、启 INTx、
  接 IRQ 184）。届时可对比"门铃唤醒"与"轮询"在**空闲**场景下的差。
- **按 peer 分片的形式化**：现在用了 peer0/1/2 三片，但没有实现 Jailhouse 内置
  协议的 peer 状态表语义（`State Register` / `State Table`）。
- **未绑核**：尾部 72 µs 未归因；把 inmate 任务与 root 线程钉到固定核后应能压低。
- **接进 ECoS 规划器**（下一轮 PR10-B）：跨域边进入 DAG 后，规划器应
  ① 禁止跨域融合（除非显式允许），② 用 `≈period` 而不是 `c` 计代价，
  ③ 由验证器独立复核该代价。

---

## 7. 复现

```bash
# PC 侧：同步 + 两端构建（会顺带把开机入口换成 xipc）
bash kfusion/scripts/ecos/xipc_sync__同步跨域IPC到Pi.sh RUN

# Pi 侧：部署 cell，然后跑四个阶段
bash /root/kfusion-pi4/scripts/deploy_nuttx_cell.sh
cd /root/ecos_xipc && ./root_side --iters 3000 --slow-n 500

# 实验完把开机入口还给 NSH
bash /root/xipc_restore__恢复开机入口.sh && \
  bash /root/kfusion-pi4/scripts/deploy_nuttx_cell.sh
```

inmate 侧日志只能从物理 mini-UART（GPIO14/15，115200 8N1）看；`jailhouse console`
看不到 inmate 输出。
