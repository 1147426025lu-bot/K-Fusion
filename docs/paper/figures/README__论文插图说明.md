# 论文插图（可直接投稿）

矢量源文件位于本目录，**印刷/投稿推荐用 PDF 或 EPS**。SVG 为源稿，可在 Inkscape / Illustrator 中微调字体与配色。

## 文件列表

| 文件 | 论文建议编号 | 用途 |
|------|--------------|------|
| [fig1_architecture_comparison__架构对照.svg](fig1_architecture_comparison__架构对照.svg) | **Figure 1** | cRTOS（空间分域）vs PLCFusion（编译器分裂内核化）总架构对照 |
| [fig2_plcfusion_toolchain__端到端工具链.svg](fig2_plcfusion_toolchain__端到端工具链.svg) | **Figure 2** | 端到端 toolchain + Pi 上三基线实验（仿 Timed C Fig.1） |
| [fig3_split_kernelization__分裂式内核化.svg](fig3_split_kernelization__分裂式内核化.svg) | **Figure 3** | `kernel.o` 与宿主 `.ko` 分裂链接细节 |
由 `plot_frequency_polygon__抖动绘图.py` 生成 **4 宫格抖动直方图** + **延迟时序图**（PNG）。须 `profile_paper_plot__论文抖动出图.env.sh` 开启 ringbuf；默认 L2 浸泡 profile 为压低 jitter **关闭** ringbuf，故默认不出图。

```bash
sudo -v
DURATION_MIN=15 bash scripts/paper/run_paper_jitter_plots__论文抖动图.sh
# 产出 → results/paper/plots/ 与 docs/paper/figures/fig_soak_jitter*.png
```
| `fig5_baseline_cdf__抖动CDF.png` | **Figure 5** | 抖动 CDF（需日志含 histogram / Jitter 行） |
| `fig6_ablation__消融对照.png` | **Figure 6** | 消融对照（跑完 ablation 矩阵后生成） |

## 数据图如何生成

架构图（Fig.1–3）为手工 SVG；**实验数据图（Fig.4–6）由脚本从 CSV/日志自动生成**：

```bash
# 跑完实验后（或仅有 CSV 时）
python3 scripts/paper/paper_plot_results__论文出图.py

# 或随整理一步出图
bash scripts/paper/paper_consolidate__整理结果.sh
```

若 Fig.4 只有 baseline_ko / fused、没有 userspace，说明 **userspace 基线尚未跑通**（cyclictest 参数或 abs_max 未写入 CSV）。  
CDF（Fig.5）需要 userspace 日志含 `# Histogram`，或 fused dmesg 含 `Jitter:` 行；默认 soak profile 关闭 ringbuf 时 fused CDF 可能为空。

## 导出 PDF（投稿用）

```bash
# 需安装 Inkscape 或 rsvg-convert
cd docs/paper/figures

# 方式 A：Inkscape
for f in fig*.svg; do
  inkscape "$f" --export-type=pdf --export-filename="${f%.svg}.pdf"
done

# 方式 B：rsvg-convert（librsvg）
for f in fig*.svg; do
  rsvg-convert -f pdf -o "${f%.svg}.pdf" "$f"
done
```

LaTeX 示例：

```latex
\usepackage{graphicx}
% PDF 放在 figures/ 下
\includegraphics[width=\linewidth]{figures/fig1_architecture_comparison.pdf}
```

若期刊要求 EPS：

```bash
inkscape fig1_architecture_comparison__架构对照.svg --export-type=eps --export-filename=fig1.eps
```

## 建议图题（中英）

### Figure 1
- **EN**: Architectural comparison between compounded RTOS with a partitioning hypervisor (cRTOS, VEE 2020) and compiler-guided split kernelization (PLCFusion) on COTS platforms.
- **ZH**: 组合实时系统（cRTOS）与编译器分裂式内核化（PLCFusion）在 COTS 平台上的架构对照。

### Figure 2
- **EN**: End-to-end PLCFusion toolchain and on-device evaluation baselines on Raspberry Pi (build → validate → `.ko` → soak/stress).
- **ZH**: PLCFusion 端到端工具链及树莓派本机评估基线。

### Figure 3
- **EN**: Internal structure of a fused module: replaceable host components linked with a freestanding fused `kernel.o`.
- **ZH**: 融合内核模块内部结构：可替换宿主与 freestanding 算法对象的分裂链接。

## 设计说明

- **配色**：蓝 = PLCFusion / 本文方法；橙 = cRTOS / 文献基线；绿 = 内核算法或测量；灰 = 硬件与通用组件。  
- **黑白印刷**：各区域均有 **线型 + 文字标签**，灰度打印可区分。  
- **字体**：Helvetica Neue / Arial；终稿可在 Inkscape 中统一改为期刊模板字体（Times 仅建议用于图内短标注，框图仍推荐 sans-serif）。

## 与大纲对应

详见 [OUTLINE_PLCFUSION_vs_cRTOS__论文大纲.md](../OUTLINE_PLCFUSION_vs_cRTOS__论文大纲.md) 中「图表清单」一节。
