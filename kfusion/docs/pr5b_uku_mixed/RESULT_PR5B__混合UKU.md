# PR-5B UKU 混合部署（ioctl，不是 IVSHMEM）

STKP 入选 `STKP:L[stage_sample]|L[stage_add+stage_shift]|L[stage_sink]`，适配器 `uku_ioctl`。组：用户态 sample → 内核 add+shift → 用户态 sink。Ĉ=58µs（explore_uncertain，人为分域成本，不是实测结题）。

200 周期，CPU0，T=1000µs。同一份计算体。

| | gold 全用户态 | mixed 内核计算 |
|---|---|---|
| digest | 4356494d | 4356494d |
| last seq / g_in / g_out | 200 / 3403 / 6808 | 200 / 3403 / 6808 |
| seq mismatch / ioctl error | — | 0 / 0 |
| R p50 (µs) | 63 | 65 |
| R 观测最大 (µs) | 154.176 | 86.186 |
| 内核交接 (µs) | — | min 0.148 / avg 0.163 / max 0.407 |

digest 一致。seq 与终态一致。ioctl 无错。经验值，不是 WCET；本例也不表明内核更快。

交接：miscdevice `/dev/kfusion_uku`，copy_from_user `g_in`/`g_mid`，内核跑 add+shift，copy_to_user `g_mid`/`g_out`。

日志：`logs/`。接口：`interface.json`。
