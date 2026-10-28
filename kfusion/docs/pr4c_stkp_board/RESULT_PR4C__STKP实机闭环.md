# PR-4C STKP 实机闭环（经验值，不是 WCET）

宿主：`Linux version 5.15.86 (builder@e3665d6e1a6c) (aarch64-linux-gnu-gcc (Debian 10.2.1-6) 10.2.1 20210110, GNU ld (GNU Binutils for Debian) 2.35.2) #1 SMP PREEMPT Wed Oct 14 10:16:11 UTC 2026`

控制：CPU0 FIFO80 T=1000µs 用户态 200 周期。

## 选型驱动生成

| 成本输入 | STKP | 域 | driver | Kbuild |
|---|---|---|---|---|
| 占位 c_u=c_k=21 | B | linux_userspace | True | False |
| 分域成本 + c_k=10 | D | linux_kernel | True | True |

## 板上 R（p50 / max，µs）

| 格 | 域 | R p50 | R max | miss | digest |
|---|---|---:|---:|---:|---|
| A | linux_userspace | 811 | 829.128 | 0 | 5d4cda7d |
| B | linux_userspace | 13 | 46.039 | 0 | 5d4cda7d |
| D | linux_kernel | 12 | 40.695 | 0 |  |

A/B digest 一致：True

## 实测回填

claim=`empirical_model_not_wcet` chosen=`B` domain=`linux_userspace`

## chain_window 时间窗口

入选分组 `[['s1', 's2'], ['s3', 's4']]`（期望 `[['s1','s2'],['s3','s4']]`）。全融合被窗口拒绝：True。

原始日志：`logs/`，表：`report.json`。
