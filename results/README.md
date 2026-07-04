# results/ — 测量结果目录

> 本地生成，默认不入库（见根目录 `.gitignore`）。仅保留 **≥15 分钟** 的正式测量。

## 目录结构

```
results/
├── soak/          # 安静浸泡测（≥15min）— run_soak_cycletest 产出
├── stress/        # 背景加压测 — run_stress_cycletest 产出
├── paper/         # 论文实验矩阵 — scripts/paper/
├── stability/     # 长时矩阵汇总 CSV + 8h 日志
├── raw/           # （遗留）历史原始日志，新跑请用 soak/ 或 stress/
└── png/           # 分布图（有 ringbuf 时）
```

## 命名约定

`{profile}_{YYYYMMDD_HHMMSS}_{N}min_{soak|stress}.{raw,watchdog,cpu3_monitor}.log`

## 清理

```bash
bash scripts/maintenance/cleanup_results__清理结果.sh
```

## 正式测量入口

| 类型 | 命令 |
|------|------|
| 浸泡 15min+ | `DURATION_MIN=15 bash scripts/deploy/run_soak_cycletest__浸泡长测.sh` |
| 加压 15min+ | `DURATION_MIN=15 bash scripts/deploy/run_stress_cycletest__加压长测.sh` |
| 论文矩阵 | `bash scripts/paper/run_paper_baseline_matrix__论文基线矩阵.sh` |
| **抖动/延迟图** | `DURATION_MIN=15 bash scripts/paper/run_paper_jitter_plots__论文抖动图.sh` |

由 `plot_frequency_polygon__抖动绘图.py` 生成 **4 宫格抖动直方图** + **延迟时序图**（PNG）。须 `profile_paper_plot__论文抖动出图.env.sh` 开启 ringbuf；默认 L2 浸泡 profile 为压低 jitter **关闭** ringbuf，故默认不出图。
