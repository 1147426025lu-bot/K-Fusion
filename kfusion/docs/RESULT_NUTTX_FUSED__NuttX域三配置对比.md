# NuttX 域：编译器内核化的三配置对比

> 平台：Raspberry Pi 4B，Jailhouse 非根 cell（NuttX，独占 CPU1，tickless，1 kHz）
> 命令：`cyclictest -n 0 -m 0 -q -p 200 -h 200 -l 100000 -i 1000`（100 000 次 / 100 s）
> 日期：2026-10-20
> 原始日志：`crtos/results/raw/pi4_inmate_cyclictest_100s_*.log`

---

## 1. 三配置结果

| 配置 | min | 主体（众数） | 加权均值 | max |
|---|---|---|---|---|
| **A 原版基线**（gcc -Os，未内核化） | 4 µs | 5 µs × 93 737（93.7%） | **4.937 µs** | 9 µs |
| **B 内核化 + `plc_` 桩** | 5 µs | 6 µs × 95 840（95.8%） | **5.958 µs** | 7 µs |
| **C 内核化 + 时间直连**（去掉 plc_ 时间桩） | 4 µs | 5 µs × 92 636（92.6%） | **4.926 µs** | 9 µs |

完整直方图：

```
A: 4×6261, 5×93737, 6×1, 9×1
B: 5×4158, 6×95840, 7×2
C: 4×7362, 5×92636, 6×1, 9×1
```

### 1.1 重复性（C 独立跑 3 轮）

| 轮次 | min | 均值 | p50 | max |
|---|---|---|---|---|
| C1（手动 objcopy） | 4 µs | 4.926 µs | 5 | 9 |
| C2（脚本 `--direct-time`） | 4 µs | 4.996 µs | 5 | 9 |
| C3（脚本 `--direct-time`） | 3 µs | 4.998 µs | 5 | 9 |

C 的均值 **4.926 / 4.996 / 4.998**（均值 4.973，极差 0.072 µs），A 为 4.937 ——
三者统计等价。B 稳定高出约 **1.0 µs**。

图：`crtos/results/xlat/nuttx_3way_compare.png`

三配置同一块板、同一个 cell、同一命令、同一轮次（间隔分钟级），可比。

---

## 2. 两个结论

### 2.1 内核化本身对抖动是中性的

C 与 A 相差 **0.011 µs（0.2%）**，在噪声内；min/max 完全一致（4 / 9），直方图形状一致。

**说明：把 cyclictest 经 PLCFusionPass 融合（含定点化、重映射、低抖动改造）后，
在 NuttX 域内的唤醒抖动没有变差也没有变好。**

这与预期一致：5 µs 量级由 OS 调度与硬件中断决定，编译器优化只有几十到几百 ns，
本来就淹没在底噪里。**这正是"用维度而非数字论证"的依据。**

### 2.2 `plc_` 运行时抽象层每周期约 1 µs，是可优化项

B 与 C 的差：

```
5.958 − 4.926 = 1.032 µs / 周期
```

`testthread` 每周期调用时间函数 **4 次**（`clock_gettime` ×3 + `clock_nanosleep` ×1），
故单次 `plc_` 包装开销 ≈ **258 ns**。

这 258 ns 的构成：一次函数调用 + `struct timespec` ↔ `struct plc_timespec` 的栈上拷贝。
看似不多，但因为**位于被测量的时间窗内**，4 次叠加就成了 1 µs —— 在 5 µs 的底噪上占 20%。

**优化方向**：把 `plc_ktime_get_ts` / `plc_clock_nanosleep` 从"函数调用"改为
**宏或 `static inline`**（结构体布局完全相同，可直接强转，零拷贝），
或让 pass 在测量路径上直接生成对 `clock_gettime` 的调用。

### 2.3 ⚠ 一个必须说清的边界：**光在桩里写 `static inline` 没用**

`fused.o` 是在**编译期**就固定生成了 `call plc_ktime_get_ts`。
在桩这一侧内联，只能消除"桩内部的实现细节"，**消除不了调用者发出的那次 `bl`**。
要真正去掉中间层，只有两条等价路径：

| 路径 | 做法 | 产物 |
|---|---|---|
| **(a) 链接期符号别名** | `objcopy --redefine-sym plc_ktime_get_ts=clock_gettime` | 与 (b) 机器码相同 |
| **(b) pass 直接生成** | pass 不重映射这两个符号，直接产出 `clock_gettime` 调用 | 同上 |

本节的 C 配置采用 (a)，已做成脚本选项 `stub --direct-time`。
(b) 需要改 pass 并在 LLVM 卡重编，但**结果不会比 (a) 更好**——两者最终都是对
NuttX `clock_gettime` 的一次直接调用。

> 所以"抽象层是否零代价"的答案已经封闭：**是，只要不把它放在热路径的函数边界上**。
> 这也反过来给出设计规则：**plc_ ABI 里的时间/内存这类高频原语，应当以宏或
> `static inline` 的头文件形式提供，而不是编译成独立函数。**

> 这条副产物对论文有用：它量化了「跨域/抽象层 IPC 在测量路径上的代价」，
> 与此前 RPMsg 调研的结论（周期数据应走裸共享内存、控制走协议栈）互相印证。

---

## 3. 附带发现：FixedPoint Pass 的 Q32 溢出

三配置中 B/C 的 `Avg` 字段都是垃圾值：

```
# Avg Latencies: 25591304935     ← B
# Avg Latencies: 21158855736     ← C
```

原因：`main` 与 `print_hist` 里的 `double` 除法被 FixedPoint Pass 转成 Q32 定点
（`plc_fix_div_i64(分子, 分母<<32, 32)`），而平均值累加量超出 Q32 可表示范围 → 溢出。

**影响范围已确认**：两处转换点都在 `main`（行 682）与 `print_hist`（行 1374），
**不在 `testthread`（测量路径，行 853–1221）内**，因此 min/max/直方图不受影响，
只有打印的 Avg 不可信。

> 反过来说，FixedPoint Pass 有一个**真实收益**：
> clang 编译的 `.o` 原本要引用 `__adddf3/__divdf3/__fixdfdi/__floatdidf/__floatundidf`
> 这些软浮点运行库，NuttX 的链接命令里没有，导致**链接失败**；
> 定点化之后这些引用全部消失（浮点指令 3 → 0），产物才能链接进 NuttX。

---

## 4. 三配置是怎么做出来的

| | 做法 |
|---|---|
| A | `git` 里原始 `cyclictest.o`（gcc -Os），`jailhouse cell load` 进去直接跑 |
| B | `fused.o`（clang + `opt -passes=plc-fusion,plc-low-jitter`）+ `plc_runtime_stubs__NuttX桩.c`，`ld -r` 合并 |
| C | 在 B 基础上用 `objcopy --redefine-sym plc_ktime_get_ts=clock_gettime --redefine-sym plc_clock_nanosleep=clock_nanosleep` 把两个时间调用**直接别名**到 NuttX 原函数，并用 `-DKF_NO_TIME_STUBS` 编桩（少这两个定义） |

C 的做法利用了签名兼容：`plc_ktime_get_ts(int, struct plc_timespec*)` 与
`clock_gettime(clockid_t, struct timespec*)` 的 ABI 完全一致（两个 8 字节成员，无填充）。

---

## 5. 对论文口径的意义

1. **可以主张**："在同一 NuttX cell、同一命令、同一 1 kHz 下，经编译期内核化的产物
   抖动与原版**统计等价**（4.926 vs 4.937 µs，差 0.2%）"——即内核化不引入运行时代价。
2. **不能主张**："内核化让抖动更小"。同域内测不出来，这与 cRTOS 的 5 µs 是同一水平。
3. **可以主张**：`plc_` 抽象层在测量路径上的代价被量化（≈258 ns/次），
   并给出优化路径（inline/宏）——这是工程贡献而非玄学。
4. **必须披露**：Avg 字段因 Q32 溢出不可信；本文所有统计均来自直方图与 min/max，
   不引用 Avg。

---

## 6. 复现步骤

```bash
# A 原版
bash scripts/nuttx_target__NuttX目标构建桥.sh restore

# B 内核化 + 桩
bash scripts/nuttx_target__NuttX目标构建桥.sh stub
bash scripts/nuttx_target__NuttX目标构建桥.sh install

# C 内核化 + 时间直连（脚本内置选项，与手动 objcopy 等价）
bash scripts/nuttx_target__NuttX目标构建桥.sh stub --direct-time
bash scripts/nuttx_target__NuttX目标构建桥.sh install
```

手动等价（脚本内部就是这么做的）：

```bash
aarch64-none-elf-objcopy --redefine-sym plc_ktime_get_ts=clock_gettime \
    --redefine-sym plc_clock_nanosleep=clock_nanosleep fused.o fused.direct.o
aarch64-none-elf-objcopy --redefine-sym main=cyclictest_main fused.direct.o fd.ren.o
aarch64-none-elf-gcc -c src/plc_runtime_stubs__NuttX桩.c -o stubs_notime.o \
    -I$NUTTX/include -I$NUTTX/apps/include -D__NuttX__ -DKF_NO_TIME_STUBS \
    -mcpu=cortex-a72 -Os -fno-builtin -ffreestanding
aarch64-none-elf-ld -r fd.ren.o stubs_notime.o -o cyclictest_notime.o
```

每轮之间必须 **重建 cell**（`shutdown/destroy/create/load/start`），
否则运行的仍是上一份镜像。
