# RESULT｜四组对照（A/B/C/D）· 计算链统一 + 调频对照 · 2026-11-21

> **本文件取代 `results/let_jitter_20261119/RESULT__四组对照__ABCD各5轮.md`。**
> 那份数据的四组不是同一份计算（生产者算术、消费相位、变量宽度都不同），已被判为"原型跑通"证据。
> 本轮把计算收敛到 `kfusion/src/let_pc_compute__产消计算体.h` 单一来源，相位统一
> `prod@0 / commit@400 / fetch@500 / act@700`，消费者统一吃同拍快照；C 与 D 改为**共用同一份宿主**，
> 只替换读写实现。另外本轮发现并量化了一个此前**完全没记录**的平台变量：cpufreq governor。

实验日期 2026-11-21。平台 Pi4B Rev1.5 / `5.15.49-rt47-v8+` PREEMPT_RT（`realtime=1`，`HZ=1000`），
`isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3 irqaffinity=0-2`，测量核 CPU3，周期 1000 µs，
实时优先级 80，每臂 5 轮 × 60 s，统一丢弃开跑后前 10 s。

- 主口径（1.8 GHz）：Pi `~/kfusion_results/four_arms_perf/`；本地 `results/let_jitter_20261121/perf/`
- 对照口径（ondemand 常态约 700 MHz）：Pi `~/kfusion_results/four_arms_v2/`；本地 `results/let_jitter_20261121/ondemand/`

---

## 0. 三条结论（先给能站住的）

1. **偏差几乎全部落在"定时器到期 → 走到计时点"这段入口上，读写函数体只占 2%–7%。**
   1.8 GHz 下 C 组读：入口 1204 ns、函数体 56 ns、合计 1266 ns。所以"A 组慢 / 硬中断快"讲的
   始终是**唤醒与投递路径**，不是 `memcpy` 或赋值。
2. **把读写钉到硬中断，把读 p50 从 8.31 µs 压到 1.25 µs（6.6×）、写从 8.42 µs 压到 1.61 µs（5.2×）**。
   A（用户态 FIFO 线程）与 B（内核软中断）都在 8 µs 量级，**二者之差在本轮不显著**——
   旧结论"A 比 B 准时（p=0.031）"在统一计算链后不再成立。
3. **C 与 D（同宿主、只换读写实现）在 p50 上差 2% 量级、方向稳定但未达校正后显著**：
   读 1.279 vs 1.250 µs（D 高 2.3%）、写 1.638 vs 1.610 µs（D 高 1.7%），两项均 5/5 轮同向，
   单侧符号检验各 p=0.031，**两项一起做 Bonferroni 校正后 p=0.0625，未过 0.05**。
   差值可定位到函数体多出的 18 ns（生成版每条 `WRITE_ONCE` 各自取一次快照）。
   尾部方向相反：C 的最坏一次比 D 差（写 max 5.09 vs 3.77 µs）。**不合并成"谁更好"。**

---

## 1. 这批数据为什么能回答"硬中断读写减少了多少"

评审此前的否决理由是"四组计算链不同，无法归因"。本轮的单变量证据链是**文件哈希**，不是描述：

| 项 | C 组（手写实现） | D 组（生成实现） | 判定 |
|---|---|---|---|
| 宿主 `ecos_fused_linux.c` | `ff281b17cd94f13a…e2eea` | `ff281b17cd94f13a…e2eea` | **同一份** |
| 宿主目标码 `ecos_fused_linux.o` | `0c5a5265…d128d` | `0c5a5265…d128d` | **逐字节相同** |
| `stage_impl.o` / `ecos_fused_body.o` | `cda42c1f…` / `93b7cc0b…` | 同 | **逐字节相同** |
| 读写实现 `let_impl.c` | `a32faca0…`（手写，114 行 diff） | `813b6e1f…`（契约生成） | **唯一变量** |
| 实现目标码 `let_impl.o` | `fdc9f920…` | 见 build_d | 67 条 AArch64 指令，两者相同 |

两条补充证据：

- **指令数相同**：`objdump -d let_impl.o | wc -l` 两版都是 **67 条**。差异只在两处——
  分派键（生成版按事件 `id`、手写版按 `op`，本契约下一一对应）与访存顺序
  （生成版 `ldr/str` 成对，手写版先批量 `ldr` 再批量 `str`）。
- **类型声明对齐后目标码不变**：手写版曾把 `g_cmd_ver` 声明成 `uint32_t`（宿主是 `int32_t`），
  对齐后重建，`let_impl.o` 的 sha256 **完全相同**（`fdc9f920…`），`kf_task.ko` 的 `.text` 段
  逐字节相同（5324 B）。整文件哈希不同只因打包元数据，**加载执行的代码没变**——
  所以本表数字对应的就是仓库里现在这份源码。

B 与 D 之间则是**同一个 `.ko`、只改一个运行时参数**：`hrtimer_hard=1`（→ `HRTIMER_MODE_*_HARD`，
PREEMPT_RT 上是真硬中断）对 `0`（→ 默认模式，回调落在 softirq，即 ksoftirqd 内核线程）。
样本自带的上下文计数可证：B 臂 `let_ctx_softirq=120146 / let_ctx_hardirq=0`，
D 臂 `let_ctx_hardirq≈120000 / let_ctx_softirq=0`。

> 注意 `meta.json` 里的 `arm_path` 是由 `hrtimer_hard` 反推的（0/1），B 臂会显示 `thread`。
> **以样本里的 `ctx_name` 为准**，不要用 `arm_path` 反推执行上下文。

---

## 2. 平台口径必须补一条：cpufreq governor（本轮新发现）

`/sys/devices/system/cpu/cpu3/cpufreq/scaling_governor` = **`ondemand`**（本轮之前从未记录）。
隔离核 CPU3 在 1000 µs 周期里大部分时间空闲，ondemand 会把它降到 **600–700 MHz**；
期间任何"周期数不变、按时间计"的开销都会按频率比例放大。

本轮因此把两组条件都跑了（同一批 `.ko`，`REUSE_BUILD` 只换平台条件）：

| 条件 | governor | CPU3 频率 | 采集时间 |
|---|---|---|---|
| 主口径 | `performance` | 1 800 000 kHz 恒定 | 11:49–12:10 |
| 对照口径 | `ondemand` | 常态约 700 MHz，偶发跳 1.8 GHz | 11:24–11:45 |

**同一份代码，频率差 2.6× ⇒ 时延差 2.6×**。这不是理论推算，是四组各自实测的比值：

| 臂 | 读 p50（ondemand） | 读 p50（performance） | 比值 |
|---|---:|---:|---:|
| A 用户态 | 21.65 µs | 8.31 µs | **2.60** |
| B 软中断 | 15.81 µs | 7.95 µs | **1.99** |
| C 手写硬中断 | 3.22 µs | 1.25 µs | **2.58** |
| D 生成硬中断 | 3.29 µs | 1.28 µs | **2.57** |

`1800/700 = 2.57`。A/C/D 三条几乎是同一个数；B 只有 1.99，因为它的延迟里混了调度等待（不随频率走）。

> **口径结论（写进 §3.3）**：时延数字必须同时报 governor 与实测频率，否则不可比。
> 2026-11-21 之前的所有时延数据都在 `ondemand` 下采的（当时没记录），
> 只能在同一批内部比较，**不得与新口径混表或算倍数**。

### 2.1 顺带解掉一个悬了两周的"预热瞬态"

11-19 那批留下一条未归因现象：A 组开跑后 4.5 s 内读 p50 稳定在 **8.72 µs**，之后跳到 **22.4 µs** 并长期稳定；
当时只能靠"统一丢前 10 s"绕开，并注明"物理成因未归因"。

本轮在 `performance` 下把 A 组 rep5 从第 0 秒起按 5 s 分桶（不截断）：

| 窗口 | 读 p50 | 读 p99 |
|---|---:|---:|
| 0–5 s | 8.294 | 8.441 |
| 5–10 s | 8.300 | 8.445 |
| 10–15 s | 8.301 | 8.439 |
| … | … | … |
| 55–60 s | 8.306 | 8.445 |

**全程没有瞬态，12 个桶的 p50 极差 0.018 µs。** 而 8.29 µs 正是旧数据"瞬态期"那个 8.72 µs。
也就是说：所谓预热瞬态，是 **ondemand 从 1.8 GHz 降到 ~700 MHz 的调频台阶**，
降频动作发生在开跑后数秒，于是"前 4.5 s 快、之后慢"。`22.4 / 8.72 = 2.57`，与频率比一致。

> 处置：**`--warmup-sec 10` 继续保留**（它确实能挡掉启动期的调度/缓存噪声），
> 但"预热瞬态"这条现象从"未归因"改为"已归因：DVFS 降频"。
> 在 `performance` 下它根本不会发生，所以主口径的截断与否不影响结论。

---

## 3. 主表（`performance` 1.8 GHz，5 轮 × 60 s）

报**跨轮中位数 [极小–极大]**，不报跨轮均值。单位 µs。

| 臂 | 操作 | p50 | p99 | max（最坏一次） |
|---|---|---|---|---|
| **A** 用户态 FIFO 线程 | 读 | 8.31 [8.30–8.37] | 8.46 [8.44–8.51] | 10.00 [8.79–10.89] |
| **A** 用户态 FIFO 线程 | 写 | 8.42 [8.39–8.46] | 8.59 [8.54–8.62] | **16.19** [10.80–20.58] |
| **B** 内核软中断（同 .ko，`hrtimer_hard=0`） | 读 | 7.95 [7.88–7.98] | 8.16 [8.09–8.18] | 10.25 [9.00–11.22] |
| **B** 内核软中断 | 写 | 9.10 [8.96–9.14] | 9.32 [9.21–9.34] | **17.89** [12.92–20.57] |
| **C** 硬中断 + 手写实现 | 读 | **1.25** [1.23–1.27] | **1.31** [1.29–1.33] | 1.62 [1.44–1.75] |
| **C** 硬中断 + 手写实现 | 写 | **1.61** [1.60–1.64] | **1.70** [1.69–1.72] | 5.09 [2.65–5.97] |
| **D** 硬中断 + 生成实现 | 读 | 1.28 [1.27–1.30] | 1.34 [1.33–1.36] | **1.47** [1.43–1.88] |
| **D** 硬中断 + 生成实现 | 写 | 1.64 [1.62–1.68] | 1.73 [1.70–1.75] | **3.77** [3.16–4.93] |

正确性：**40 轮（2 批 × 4 臂 × 5 轮）全部 `skip = err = drops = 版本错 = 版本非单调 = 0`**，
就绪门禁五个计数（`stale / not_ready / consumed / too_old / group_mismatch`）全 0。

图（`results/let_jitter_20261121/perf/figs/`）：
`fig1_max_per_rep.png`（各轮最大抖动 + 同条件最高观测值）、`fig2_signed_vs_time.png`（带符号偏差随时间）、
`fig3_p99_tail.png`（p99 / p99.9 尾部）。

---

## 4. 机制分解：偏差在"入口"，不在读写函数体

`kf_let_exec()` 在 `let_run()` 前后各取一次 `ktime_get()`，所以可以把每个样本拆成
`入口 = t_before − nominal` 与 `函数体 = t_after − t_before`。1.8 GHz、rep3、p50：

| 臂 | 操作 | 入口 | 函数体 | 合计 |
|---|---|---:|---:|---:|
| A | 读 | 8261 | 55 | 8308 |
| A | 写 | 8348 | 56 | 8404 |
| B | 读 | 7906 | 74 | 7978 |
| B | 写 | 8280 | **852** | 9137 |
| C | 读 | 1204 | 56 | 1266 |
| C | 写 | 1365 | **241** | 1603 |
| D | 读 | 1228 | 74 | 1297 |
| D | 写 | 1378 | **259** | 1634 |

四条读法：

1. **入口占 A/C/D 的 99%，占 B 写的 91%。** 读写函数体常年 55–260 ns。
   所以"硬中断读写"的收益不是"算得快"，是**删掉了从定时器到期到代码开始执行之间的整条路径**。
2. **C 比 D 快的 31 ns，18 ns 在函数体里**（读 56 vs 74、写 241 vs 259）——就是生成版
   `WRITE_ONCE(g_cmd_snap_ver, READ_ONCE(g_cmd_ver))` 之后又 `READ_ONCE` 回读一次的那条依赖加载。
   剩下 13 ns 在入口，属噪声量级，不作解释。
3. **写比读贵 160–180 ns**：写要过就绪门禁 `kf_let_gate_take()`（一次 `raw_spinlock` 配对比对），
   读不过门禁。
4. **B 的写函数体 852 ns 异常高**，是同一个 `let_run` 在软中断上下文里的开销（读只要 74 ns）。
   本轮未做进一步归因，只如实记录；它不影响 B 的读结论。

> 一处未解释的现象（如实留档）：`performance` 批的 rep3 里，C 与 D 都在"开跑后约 41 s"处各有
> 一次孤立尖峰（约 4 µs，随后立刻回到 1.3–1.7 µs 的常态带）。两臂的这一轮相隔约 3.5 分钟运行，
> 尖峰却落在相同的相对时刻。单轮单点、不影响 p50/p99，**本轮不归因**；
> 若后续长测复现，应查是否有周期约 210 s 的内核后台活动（`irqaffinity=0-2` 已把中断赶出 CPU3，
> 但 `rcu_nocb_poll` 与 `nohz_full` 的交互未查）。

---

## 5. 三条被推翻的旧结论（必须写进论文的"修正"）

| 旧结论（11-19） | 复核后的事实 | 为什么错 |
|---|---|---|
| "A 22.24 µs 比 B 46.50 µs 准时，5/5 同向，p=0.031" | performance 下 A 8.31 / B 7.95（读），**A 并不优于 B**；写 A 8.42 < B 9.10 但尾部 B 更差 | 旧 A/C 与 B/D 不是同一份计算（A/C 在 0 µs 一把算完、消费者吃上一拍）；且 A 与 B 对频率敏感度不同（2.60 vs 1.99） |
| "预热瞬态：4.5 s 处 8.72 → 22.4 µs，成因未归因" | **DVFS 降频**：`ondemand` 把 CPU3 从 1.8 GHz 降到 ~700 MHz；performance 下 0–60 s 全程 8.29–8.31 µs | 从未记录 governor/频率，把调频台阶当成启动瞬态 |
| "C 与 D 的差别在尾部：读 max 20.85 vs 3.72（5.6×）" | 同宿主后读 max 1.62 vs 1.47，写 max 5.09 vs 3.77；**p50 反而是 D 略慢 2%** | 旧 C 是独立手写模块、4 个相位全走 HARD timer（D 只有读写两相位），timer 编程次数约 2× 是混杂因素；现已撤回该归因 |

旧文档里"C 组软中断臂 p50 21.02 / max 2405 µs / 5 轮全跳拍"的**定性结论仍然成立**
（软中断上下文会被推迟到毫秒级并导致跳拍），但它也是 ondemand 条件下测的，
**绝对数字不可与新表混比**；本轮的 B 臂（同 .ko、软中断、8 µs 量级、0 跳拍）是它的干净版本——
差别在于 B 臂的读写桩不依赖 worker 唤醒。

---

## 6. 评审第 1、2 条（就绪门禁 + RT 锁）的落地验证

代码侧（`linux_kernel_single_worker.py`）：

- `kf_let_ready` 单个布尔已删除，改为三元组 `struct kf_stage_mark {cycle_id, done_ns, group_id, valid, consumed}`，
  由 `kf_stage_take()` 原子取用，状态 `KF_STAGE_OK / NOT_READY / STALE / CONSUMED / TOO_OLD / GROUP_MISMATCH`。
- 硬中断路径的锁改为 `raw_spinlock_t`（`DEFINE_RAW_SPINLOCK` + `raw_spin_lock_irqsave`），
  队列锁与标记锁各一把，避免 PREEMPT_RT 上在硬中断里取可睡眠的 `spinlock_t`。
- 回归用例 `kfusion/backend/synthesis/test_let_ready_gate.py`：11 项纯文本断言全过（无需 clang/kbuild）。

板上用例（`kfusion/scripts/test_let_ready_gate__就绪门禁用例.sh`，3 组共 16 项断言全过）：

| 用例 | 注入 | 结果 |
|---|---|---|
| 正常 3 s | 无 | `ver` 增 1004、`skip/stale/consumed/too_old/group_mismatch` 全 0、`age_max=393 µs` |
| 生产者超时 | `test_stall_prod_us=600` | `ver` 增 **0**、`skip=3027`、`stale=3030`、`age_max=694 µs` ⇒ 能区分"旧值"而不只是"未就绪" |
| 连续漏做 50 拍 | `test_skip_prod_left=50` | `test_prod_skipped=50`、`let_skip_commit=50`、`stale=50`、`unpaired_finish=50`、`missing_groups=0` |

> 第二条特别重要：**旧实现下生产者超时会让发布照常放行（读到旧值但版本号递增）**，
> 现在被门禁拦住并计入 `stale`。这是本轮唯一一处"行为改变"，也是长测前必须修的那条。

---

## 7. 不能主张什么

1. **不主张"A 用户态方案"代表开题目标形态的最优解**——A 8.31 µs 里 99% 是 `clock_nanosleep`
   到期到线程真正跑起来的路径，换忙等会掉到 0.15 µs 量级（11-19 已测），但那不是可用的等待方式。
2. **不主张 A 与 B 谁更好**——读 B 略优（7.95 vs 8.31）、写 A 略优（8.42 vs 9.10），
   且尾部互有胜负，5 轮不够下结论。
3. **不主张 D 比 C 差**——2% 量级、Bonferroni 校正后 p=0.0625 未过 0.05，
   且尾部方向与中位方向相反。
4. **不主张"编译器内核化让抖动更小"**——本轮只测到"生成实现与手写实现等价（差 18 ns）"，
   这支持"Pass 保住了语义与开销"，不支持任何"更优"的说法。
5. **不主张 GPIO 外部计时已验证**——仍是软件时间戳，`ktime_get()` 本身的开销没有独立标定。
6. **GPIO / 负载梯度 / 多时长**：本轮全部是空闲条件、60 s 窗口。B/D 在内存负载下的历史差异
   （11-18 那批）**也是 ondemand 条件下测的**，需要按新口径重做才能引用。

---

## 8. 复现

```bash
# 平台：Pi4B Rev1.5 / 5.15.49-rt47 PREEMPT_RT，隔离核 CPU3
# 1) 把 governor 钉到 performance（否则一切时延数字随频率漂 2.6×）
echo performance | sudo tee /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

# 2) 一次跑齐四组（D 组 seam 构建 → C 组复用宿主只换实现 → A 组 → B/C/D 按轮轮换）
OUT=/home/pi/kfusion_results/four_arms_perf REPS=5 DURATION=60 \
  bash kfusion/scripts/run_four_arms_v2__四组重跑.sh

# 3) 换平台条件重跑（不重编，复用同一批 .ko）
OUT=/home/pi/kfusion_results/four_arms_xxx REUSE_BUILD=/home/pi/kfusion_results/four_arms_perf \
  REPS=5 DURATION=60 bash kfusion/scripts/run_four_arms_v2__四组重跑.sh

# 4) 出三张主图 + 跨轮中位表
python3 kfusion/scripts/plot_four_arms_v2__四组重跑出图.py \
  --run /home/pi/kfusion_results/four_arms_perf -o /home/pi/kfusion_results/four_arms_perf/figs
```

`platform_start.txt` 会记录 `governor / scaling_cur_freq / 可用频率表 / cmdline`，
**没有这个文件的批次不要拿来比时延**。

关键构建产物哈希（两批同一套）：

```
宿主      ecos_fused_linux.c   ff281b17cd94f13a63f1ec4bbb1201394319079f652c39f0b6cf6171b75e2eea
宿主 .o   ecos_fused_linux.o   0c5a5265a2eb9904e4f45cc9c46512018936a9aa5df36c106315bd7e551d128d
生成实现  let_impl.c（D）      813b6e1f5b26feae025915ea0f9219d1b1bfdbcd992eb33b8778478c43167345
手写实现  let_impl.c（C）      a32faca0b9ddba18a83978974d4a6e95ccb9d2995fcb81ccd0a0ef31aa377623
C 实现 .o let_impl.o (C)       fdc9f9200f7e584629b295beb53575aef76c4cd8c6ac3b983c21af127d03b41c
D .ko     kf_task.ko           8400b06f2052d512…
C .ko     kf_task.ko           3017d600e9595aa8…
```
