# chain_window 窗口反例（经验值，不是 WCET）

s3 契约窗口 **[380, 520] µs**。STKP 入选 `[['s1', 's2'], ['s3', 's4']]`。

| 驱动 | s3 p50 (µs) | 落在窗口 | digest |
|---|---:|---|---|
| STKP 合法 plan | 412 | True | 50df863d |
| 同驱动 flat | 424 | True | 50df863d |
| 非法全融合（对照） | 45 | False | 129cacd5 |

STKP 选两段且无 kmod：True。非法全融合 s3 提前：True。合法 plan/flat digest 一致：True。

日志：`logs/`。
