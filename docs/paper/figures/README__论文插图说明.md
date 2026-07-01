# 论文插图（可直接投稿）

矢量源文件位于本目录，**印刷/投稿推荐用 PDF 或 EPS**。SVG 为源稿，可在 Inkscape / Illustrator 中微调字体与配色。

## 文件列表

| 文件 | 论文建议编号 | 用途 |
|------|--------------|------|
| [fig1_architecture_comparison__架构对照.svg](fig1_architecture_comparison__架构对照.svg) | **Figure 1** | cRTOS（空间分域）vs PLCFusion（编译器分裂内核化）总架构对照 |
| [fig2_plcfusion_toolchain__端到端工具链.svg](fig2_plcfusion_toolchain__端到端工具链.svg) | **Figure 2** | 端到端 toolchain + Pi 上三基线实验（仿 Timed C Fig.1） |
| [fig3_split_kernelization__分裂式内核化.svg](fig3_split_kernelization__分裂式内核化.svg) | **Figure 3** | `kernel.o` 与宿主 `.ko` 分裂链接细节 |

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
