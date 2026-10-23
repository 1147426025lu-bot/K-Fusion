# 内核宿主 R 探针设计

> 状态：**✅ 已实现并实测（2026-10-23）**。结果见 `RESULT_RSF_3domain__三域统一探针.md`：
> 内核态 `response` p50 = **22 µs**、p99 = 25、p99.9 = 32、miss **0/60012**，三轮复现。
>
> **阻塞是怎么解开的**（留给后来者）：不需要 `kernel8-jh.img` 的那份定制树 ——
> 板上有 `/boot/config-5.15.86`，且 `CONFIG_MODVERSIONS` / `CONFIG_MODULE_SIG` 都没开，
> 因此用 kernel.org 原版 5.15.86 + `make modules_prepare` 即可（vermagic 精确匹配）。
> 详见 `RESULT_RSF_3domain` §6 复现命令。
>
> 原文（设计阶段）保留如下，改动点与验收标准均已按此落地；
> 唯一偏离：`plc_probe_dump()` 除在 `plc_timer_delete` 处调用外，还在 `runner_exit` 兜底一次（幂等）。
>
> 相关：`PROBE_CONTRACT__统一测量契约.md`（r/s/f v1）、`RESULTS_ALL` §9.0、
> `RESEARCH_DESIGN_ECoS_v1.0` §13 `feat(cost)`

---

## 1. 现状：内核宿主只能给「间隔偏差」，给不出 R

`kfusion/src/plc_runner_official__cyclictest宿主.c`

| 位置 | 现在记什么 | 问题 |
|---|---|---|
| `plc_cyclic_handler()` L411 | `jitter_ns = (now − last_tick) − T` | **间隔偏差**，不是 `s−r` |
| L442 | `if (ct->task && fused_wake_timertthread) wake_up_process()` | 默认 `FUSED_WAKE_TIMERTHREAD=0` → **任务不被唤醒**，没有 `s_k`/`f_k` |
| L449 | `ct->next_nominal += interval` | 有 `r_k` 的素材，但没被当作 `r_k` 记录 |

结论：`start_lat`、`exec`、`response` 三者一个都拿不到，`deadline_miss` 也无法判定。

---

## 2. 改动点（三处，均为增量、默认关闭）

新增模块参数 `probe_rsf`（默认 0，保持现有 L2/L4 口径不变）：

```c
static bool probe_rsf;                    /* module_param(probe_rsf, bool, 0644) */
```

### 2.1 定时器回调：把 `r_k` 落下来

```c
/* 在 L449 之前（此刻 next_nominal 仍是"刚过去的那一拍"的标称时刻） */
if (probe_rsf)
        ct->probe_r = ct->next_nominal;   /* r_k */
```

再照常推进 `next_nominal`。

### 2.2 任务唤醒路径：记 `s_k`

`wake_up_process()` 之前不行（那时任务还没跑）。正确位置是**任务侧**：
`plc_sigwait()`（L698-720）与 `plc_clock_nanosleep()`（L553）是任务从等待中返回的唯一入口，
在返回前取一次 `ktime_get()` 作为 `s_k`：

```c
if (probe_rsf) {
        ct->probe_s = ktime_get();        /* s_k */
        plc_probe_add(&g_probe.start_lat,
                      ktime_to_ns(ktime_sub(ct->probe_s, ct->probe_r)));

        /* 上一周期的 f_k：任务重新进入运行时的这一刻，等价于"上一周期体已跑完" */
        if (ct->probe_have_prev) {
                s64 exec = ktime_to_ns(ktime_sub(ct->probe_s, ct->probe_prev_s));
                s64 resp = ktime_to_ns(ktime_sub(ct->probe_s, ct->probe_prev_r));
                plc_probe_add(&g_probe.exec, exec);
                plc_probe_add(&g_probe.resp, resp);
                if (resp > ct->interval) g_probe.miss++;
        }
        ct->probe_prev_s = ct->probe_s;
        ct->probe_prev_r = ct->probe_r;
        ct->probe_have_prev = true;
}
```

> ⚠️ 这里用「下一次进入运行时的时刻」当 `f_k`，等于假设任务在两次 `sigwait` 之间不再睡眠。
> 该假设必须写进论文，并用外部探针（UART/GPIO 打点）抽检 20 个周期验证。

### 2.3 导出：与用户态同一块格式

内核侧没有 `printf`，用 `printk` 复刻同一文本（`stat_*_us` 前缀不变），
由 `fused_export_samples_file()`（L356）或 debugfs 读点输出。
**契约解析器 `probe_report__统一探针报告.py` 不改**——这是"同一契约"的验收标准。

---

## 3. 验收标准（实现后必须逐条过）

1. `modprobe` 后置 `probe_rsf=1`，跑 60 s，`probe_report` 能解析出三序列 + miss；
2. 自洽性 `response.avg ≈ start_lat.avg + exec.avg`（误差 < 2%）；
3. 与 BM2 同核同优先级同周期对照：`start_lat` 应在同一量级（±30%），否则说明两条路径边界不同；
4. 关闭 `probe_rsf` 时，输出与本设计之前**逐字节相同**（口径零回归）。

---

## 4. 为什么这项不能省

ECoS 的 `J(θ)=αQ₀.₉₉₉(R)+βCVaR(R)+γM` 只认 R 与 M。
现在三域里只有 BM2（用户态 + NuttX 任务）能给 R；**"内核态"那一格是空的**。
在补上之前：
- 论文里"同一产物跨 Linux 内核态 / NuttX / 裸机"的可比性不成立；
- `feat(planner)` 的候选评分会退化成只能比较用户态方案。

---

## 5. 阻塞与解阻塞

| 阻塞 | 解 |
|---|---|
| 无匹配内核树 | 用构建 `kernel8-jh.img` 的那份源码树（`rpi-5.15.y` + Jailhouse 补丁）在 PC 上做 `make prepare modules_prepare`，只取头文件与 `Module.symvers` 同步到 Pi；或直接在那台构建机上编模块 |
| 无法重载验证 | 需要一次板级重启窗口；注意当前板子存在不明硬复位（约 1/3 概率），长跑前先解决供电/散热 |
