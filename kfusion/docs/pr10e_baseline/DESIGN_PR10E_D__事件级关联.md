# 阶段 D 设计：怎么把 tracing 事件与应用级延迟关联起来

日期：2026-10-28
对应评审要求（原话）：

> timerlat 自身运行的是探针线程，其采样不等于 K-Fusion 应用级扫描延迟。
> 必须使用同一时间轴上的应用事件或独立测量建立关联，不能直接将 timerlat 的
> IRQ 与线程延迟差值解释为你的应用的调度开销。

本文先把这个「关联」问题解决掉，再谈采集。

---

## 一、问题真正的难点

天真的做法是：跑 timerlat 得到「IRQ 延迟 8 µs、线程延迟 20 µs」，再跑应用得到
「response 1000 µs 峰值」，然后说「尖峰是线程调度造成的」。这个推理**不成立**，因为：

1. **时间轴不同**。timerlat 有自己的周期，与应用的那一拍不对应；某次 1000 µs 尖峰发生时，
   timerlat 可能根本没在采那一拍。
2. **测的不是同一个对象**。timerlat 测的是它自己的探针线程从 hrtimer 到期到上 CPU 的时间；
   我们的应用是另一个线程，被唤醒的原因（nanosleep vs hrtimer）和优先级都不同。
3. **观测本身会扰动**。timerlat 探针线程是 RT 优先级，与应用抢同一个核，会把应用推后。

## 二、三个方案与取舍

| 方案 | 做法 | 能回答什么 | 致命问题 |
|---|---|---|---|
| A 分时 | 先 timerlat（无应用）、再应用（无 timerlat），比分布 | 系统整体能力 | 两次不同时刻，无法归因到具体那一拍 |
| B 同核并存 | timerlat 与应用同 CPU 同时跑 | 应用尖峰时刻附近 timerlat 是否也高 | 互相抢 CPU，测的是干扰后的系统；只能给相关不给因果 |
| **C 直接跟踪应用线程** | 用 ftrace 的 `sched_wakeup` / `sched_switch` 直接跟我们的驱动进程 | **应用自己在每一拍等了多久、被谁换下** | 需要正确的过滤与足够的缓冲；不覆盖「IRQ 被屏蔽」这类无调度事件的路径 |

**选 C 作为主证据，B 作为辅助基线，A 只在需要「系统本底」时用。**

理由：C 的事件和应用的 response 在**同一时间轴、同一进程**上，可以直接回答
「这一拍为什么晚」。B 的 timerlat 数据用来判断「是全局性问题还是这个进程特有的」。

这样也回应了评审的警告：我们**不**用 timerlat 的 IRQ/Thread 差值去解释应用延迟，
只用它说明系统本底；应用的归因靠它自己的调度事件。

## 三、方案 C 的具体做法（先用 tracefs 原生接口，不依赖 rtla）

在 tracing 内核上：

```bash
T=/sys/kernel/tracing

# 只跟踪目标进程，避免全系统噪声
PID=$(pgrep -f 'gen/driver.*gold|gen/driver.*mixed' | head -1)
echo $PID > $T/set_event_pid          # 只记录该 pid 的事件

# 打开调度事件（这是归因的核心证据）
echo 1 > $T/events/sched/sched_wakeup/enable
echo 1 > $T/events/sched/sched_switch/enable
echo 1 > $T/events/sched/sched_wakeup_new/enable

# 缓冲开大（4 核 × 每核 MB）：默认太小会丢事件，丢事件会导致「没看到尖峰」的假结论
echo 16384 > $T/buffer_size_kb        # 每 CPU 16 MB

# 只留时间戳与必要字段，减少开销
echo 1 > $T/options/print-timestamp   # 如可用
echo 0 > $T/tracing_on
echo > $T/trace
echo 1 > $T/tracing_on
# —— 此处跑应用 N 拍 ——
echo 0 > $T/tracing_on
cp $T/trace /root/pr10e_stageD/sched_trace.txt

# 记得复位
echo 0 > $T/set_event_pid
```

**关键提醒**：`buffer_size_kb` 不放大而丢事件，会得出「没有异常」的假结论。
所以采集后必须检查 `<...>[LOST EVENTS]` 计数，有丢失就**不能**说这一段没异常。

## 四、判据：什么样的证据算「归因成立」

对每一个超过阈值的拍（如 response > 400 µs 或 > 1000 µs），在 sched_trace 里查：

| 观察到的模式 | 归因结论 | 允许的说法 |
|---|---|---|
| 应用线程被 `sched_switch` 换下，很久后才被换回，且换下期间跑着别的任务 | **调度延迟** | 「该拍延迟来自进程被抢占，抢占者为 X」 |
| 有 `sched_wakeup` 但 `sched_switch`（切入）晚很多 | **唤醒到运行延迟** | 「醒了但排不上 CPU」 |
| 应用线程一直在跑（无 switch），但那一拍照样长 | **不是调度**（可能是 IRQ 屏蔽、cache、或计时代码本身） | 「该拍未归因」 |
| sched_trace 缺该拍事件（丢失或被过滤） | **无法判定** | **必须标「未归因」**，不许猜 |

**红线**：只要证据落在后两行，就不许写成「调度造成」。评审验收第 7 条要求的正是这个：
「对无法解释的异常明确标注未归因」。

## 五、timerlat 只用于系统本底（方案 B 的边界）

- 采一次 `timerlat` 3–5 秒，记录 IRQ 延迟与 Thread 延迟的分布（p50/p99/max），
  作为「这台机器在该时刻的系统本底」。
- 允许的说法：「系统本底的线程延迟 max 到了 X µs，与应用观测到的尖峰同量级，
  因此尖峰**有可能**来自系统级调度」。
- **不允许**的说法：「因为 timerlat 线程延迟是 X，所以应用延迟就是 X」。
- timerlat 与应用并存时，必须记录「timerlat 进程也在跑」这一事实，
  并注明此时应用数据是**被观测干扰过的**，不与无 timerlat 的基线数据直接比较。

## 六、采集顺序（避免把观测开销当原生延迟）

1. **stable 内核**：无 tracing，跑正式性能数字（已有 PR-10E-A 的 40 轮）。
2. **stable 内核 + ftrace 开启但不跟踪**：确认 tracing 基础设施本身的开销（对照组）。
3. **tracing 内核 + sched 事件跟踪**：只在需要解释异常时用，数据**不用于**性能结论。
4. **tracing 内核 + timerlat**：只取系统本底。
5. **回 stable 复测**：确认没有把 tracing 开销算成原生延迟（评审阶段 D 最后一条）。

## 七、本阶段明确不做

- 不做「timerlat 差值 = 应用开销」的任何计算。
- 不在丢事件的情况下声称「未观察到异常」。
- 不对超过阈值但无 sched 证据的拍做任何猜测性归因。
- 不用 tracing 内核的数字与 stable 的数字直接并列比较。
