# 统一测量契约（r / s / f v1）

> 状态：**v1 已落地**（BM2 为参考实现，2026-10-23）
> 起因：2026-10-22 代码审查第 2/5 条 —— 三套探针记录的量不同，导致「架构层 1.6×」不成立。
> 相关：`RESULTS_ALL` §9.0（指标定义与可比性矩阵）、`RESEARCH_DESIGN_ECoS_v1.0` §13 `feat(cost)`

---

## 1. 为什么必须有契约

同一块板、同一个 1 kHz 周期，项目里同时存在三种记录方式：

| 探针 | 记录的量 | 出处 |
|---|---|---|
| **P1** Linux fused 宿主（kernel hrtimer） | **间隔偏差** `d_k = t_k − t_{k−1} − T` | `plc_runner_official__cyclictest宿主.c:411` |
| **P2** cyclictest（stock / NuttX 移植） | **唤醒延迟** `s_k − r_k` | rt-tests 算法 |
| **P3** BM2 | 唤醒延迟 + 执行时间 | `bm2pid_main.c` |

三者相除会得到看似漂亮、实则无意义的倍数（`7.834 / 4.9 = 1.6×` 就是这么来的）。
更麻烦的是：ECoS 的目标函数 `J(θ)=αQ₀.₉₉₉(R)+βCVaR(R)+γM` 需要**端到端响应 R**，
而 P1 根本不记录 R —— **搜索目标不可测**。

契约的作用：把「每个周期的三个时间戳」定为唯一事实，所有派生量都从它算，
任何探针只要能给出这三个时间戳，它的数字就可以进同一张表。

---

## 2. 契约本体

### 2.1 三个时间戳（单位 ns，同一时钟源 `CLOCK_MONOTONIC`）

| 符号 | 含义 | 取法 |
|---|---|---|
| `r_k` | 标称释放时刻 | absolute 定时表里的第 k 个 `next`（**不随实际唤醒漂移**） |
| `s_k` | 实际开始 | 唤醒后第一次 `clock_gettime` |
| `f_k` | 完成 | 周期体跑完后的那次 `clock_gettime` |

### 2.2 三条派生序列

| 序列 | 公式 | 含义 | 谁能用 |
|---|---|---|---|
| `start_lat` | `s_k − r_k` | 唤醒延迟（旧称 jitter） | 同指标内比较；等价于 cyclictest 的循环时间 |
| `exec` | `f_k − s_k` | 执行时间 | 与抖动**无关**（已有两条独立证据） |
| `response` | `f_k − r_k` | **端到端响应 R** | **ECoS 的 J(θ) 只能用它** |
| `miss` | `f_k > r_k + T` | 完成期限错过 | 硬指标；`exec > T` **不等于** miss |

> `exec > T` 只说明"这一周期算得久"；晚醒 900 µs + 执行 200 µs 的 exec 没超周期，
> 但本周期已经交不出结果 —— 这正是 `miss` 存在的理由。

---

## 3. 输出格式（机器可解析）

```
# probe: r/s/f v1
# period_ns: 1000000
# samples: 5000
# series_us: start_lat exec response  (1us buckets, cap 199)
# stat_start_lat_us: min=9.284 avg=12.036 p50=11 p90=13 p99=14 p99.9=24 p99.99=37 max=37.478 over=0 n=5000
# stat_exec_us: min=0.814 avg=0.954 p50=0 p90=0 p99=1 p99.9=1 p99.99=2 max=2.463 over=0 n=5000
# stat_response_us: min=10.244 avg=12.990 p50=12 p90=14 p99=15 p99.9=25 p99.99=39 max=39.941 over=0 n=5000
# deadline_miss: 0/5000 (none)
# exec_overrun: 0/5000
```

- `p*` 是 **1 µs 桶**的桶下界（整数）。`min/avg/max` 是 0.001 µs 精度。
- 前缀 `stat_` 是为了不与旧的兼容行 `# exec_us: avg=…`（cycletest 口径）撞名。
- 解析器：`crtos/scripts/pi4/probe_report__统一探针报告.py`
- 实现：`crtos/jailhouse_overlay_pi4/nuttx/apps/benchmarks/bm2pid/plc_probe__统一测量探针.h`（纯 `static inline`，不动物建系统）

---

## 4. 各域怎么落地

| 域 | 状态 | 做法 |
|---|---|---|
| **BM2（Linux root cell / NuttX cell）** | ✅ 已实现 | 直接按契约记录（本文件就是用它验证的） |
| cyclictest（stock / inmate 移植） | ⏳ | 已是 P2 唤醒延迟，可视为 `start_lat`；**没有 `f_k`，所以没有 R** |
| Linux fused 内核宿主 | ⏳ **需改** | 两条路：(a) `FUSED_WAKE_TIMERTHREAD=1` 让测量线程真被唤醒再记 `s_k`；(b) 保持 0，但把记录改成 `s_k − r_k`（当前是 `t_k − t_{k−1}`，缺 `r_k`）。**未改之前，P1 的数字不得与 P2/P3 并列。** |

> 结论：**R 目前只有 BM2 能提供**。ECoS 的 `feat(cost)` 若要在三域都拿到 R，
> 必须先改 Linux 内核宿主；这件事比写 CP-SAT 更靠前。

---

## 5. 使用纪律（引用任何数字前）

1. 进 `J(θ)` 的只能是 `response` 与 `miss`；`start_lat` 不是端到端时间。
2. `over > 0` 时 `p99.9+` 是**下界**，必须同时报 `over`。
3. `exec` 用 1 µs 桶，`p50=0` 表示"< 1 µs"，**不要**把它读成"零开销"。
4. 跨窗口只比分位数与量级，不比 `max`（60 s→1 h 的 `max` 涨了 11.6×）。
5. 老日志（无 `# probe:` 块）由解析器显式标为 `LEGACY`，禁止混入新表。

---

## 6. 验证记录（2026-10-23）

```
# Pi4B, gcc -O2 -Wall → 零警告
$ chrt -f 99 taskset -c 3 ./bm2 -l 5000
# stat_start_lat_us: min=9.284 avg=12.036 p50=11 ... max=37.478 over=0 n=5000
# stat_exec_us:      min=0.814 avg=0.954 p50=0  ... max=2.463  over=0 n=5000
# stat_response_us:  min=10.244 avg=12.990 p50=12 ... max=39.941 over=0 n=5000
# deadline_miss: 0/5000 (none)
```

自洽性检查：`response ≈ start_lat + exec`（12.990 vs 12.036+0.954=12.990）✅
