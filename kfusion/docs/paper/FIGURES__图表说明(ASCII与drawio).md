# K-Fusion 论文图表说明（ASCII + draw.io）

> 风格基线（已对照原文 Figure 逐项校准）：**图 1** 严格模仿 **cRTOS（VEE 2020）Figure 1** 的"双 realm 并排 + 顶部 realm 标签 + 蓝色 hypervisor 横条 + 底部设备/PIC"层叠箱线画法；**图 6** 严格模仿 **Timed C（RTAS 2018）Fig.1 / Fig.2** 的"代码 + 控制流图（圆=时间点、方框=语句） + 水平时间线刻度条"三合一画法；**图 2** 为同类实时系统论文通用的黑白箱线流水线；**图 3–5** 为 IEEE 双栏论文常见的 CDF / 柱状样式。所有图统一采用浅灰底 + 黑框 + 单一蓝色强调（取自 cRTOS 的配色语法）。
> 数据约束：图 3–5 的**曲线与柱高为示意**，用于表达**趋势**；真实数值须由实验产生后替换。所有坐标轴单位与量级随实测数据确定。
> draw.io 源文件位于 `figures/` 目录，可用 [app.diagrams.net](https://app.diagrams.net) 或 draw.io 桌面版直接打开编辑。

---

## 图 1　架构对比：cRTOS 双域 vs K-Fusion 单域

**ASCII 示意**

```
┌────────────────────(a) cRTOS：双域 + 分区 hypervisor─────────────────────┐
│                                                                          │
│   ┌───────────────────────┐            ┌───────────────────────┐         │
│   │   普通域 (Normal)     │  remote    │  实时域 (Real-time)   │         │
│   │   Linux  (GPOS)       │  syscall   │  NuttX  (sRTOS)       │         │
│   │  ┌────────┐ ┌───────┐ │ ◄────────► │  ┌─────────────────┐  │         │
│   │  │进程/网络│ │文件系统│ │  共享内存  │  │  实时控制任务    │  │         │
│   │  │GUI/驱动│ │       │ │            │  │  (硬实时, 有界)  │  │         │
│   │  └────────┘ └───────┘ │            │  └─────────────────┘  │         │
│   └───────────┬───────────┘            └───────────┬───────────┘         │
│               └────────────────┬───────────────────┘                     │
│                        ┌───────┴────────┐                                │
│                        │   Jailhouse    │  静态分区 (VT-x / ARM-VE)       │
│                        └────────────────┘                                │
└──────────────────────────────────────────────────────────────────────────┘

┌────────────────────(b) K-Fusion：单域 + 编译内核化────────────────────────┐
│                                                                          │
│                    ┌──────────────────────────────────────┐              │
│                    │         单一 PREEMPT_RT 域            │              │
│                    │  ┌──────────────────┐ ┌────────────┐ │              │
│                    │  │   用户态 Linux    │ │ 融合模块 .ko│ │              │
│                    │  │ 服务:网络/文件/GUI│ │ kthread +  │ │              │
│                    │  │ (GPOS 生态保留)   │ │ pinned     │ │              │
│                    │  └──────────────────┘ │ hrtimer    │ │              │
│                    │       (无跨域调用)     │ ← 原始 C   │ │              │
│                    │                      └────────────┘ │              │
│                    └──────────────────────────────────────┘              │
│                          无 hypervisor · 无第二 OS                        │
└──────────────────────────────────────────────────────────────────────────┘
```

**图例**：左图为分区方案，两个域经共享内存进行远程系统调用，底层为分区 hypervisor；右图为本文方案，仅一个 PREEMPT_RT 域，用户态 Linux 服务与内核态融合模块共存，图中标出"无 hypervisor、无第二 OS、无跨域调用"三点差异。
**draw.io**：`figures/K-Fusion_fig1_architecture.drawio`

---

## 图 2　K-Fusion 端到端工具链流水线

**ASCII 示意**

```
 ┌──────────┐   ┌────────┐   ┌─────────┐   ┌────────────────────┐   ┌────────────────┐
 │ Manifest │   │  AST   │   │ Clang   │   │ 内核化 Pass 流水线  │   │  kernel.o      │
 │ + C 源码 │─► │  预检  │─► │  → IR   │─► │ Remap/DCE/定点/WCET │─► │ + validate.json│
 └──────────┘   └────────┘   └─────────┘   └────────────────────┘   └───────┬────────┘
                                                                            │
        ┌───────────────────────────────────────────────────────────────────┘
        ▼
 ┌──────────────┐   ┌─────────┐   ┌──────────────────────────────────────┐   ┌──────────┐
 │ ignite_fused │─► │  *.ko   │─► │ 部署与测量: insmod 冒烟 / soak / stress│─► │ results/ │
 │(链接宿主+桩) │   │可加载模块│   │  soak=安静 · stress=加压背景          │   │ CSV/日志 │
 └──────────────┘   └─────────┘   └──────────────────────────────────────┘   └──────────┘
                          ▲                                     │
                          └──────────── 可替换宿主 (host) ───────┘
```

**Pass 流水线细节（对应图 2 中部方框）**

```
 IR ─► [1 Normalize] ─► [2 FloatKill#1] ─► [3 Remap μ] ─► [4 DCE(R*)]
    ─► [5 FloatKill#2] ─► [6 Export] ─► [7 WcetMark H] ─► [8 Cleanup] ─► 后端(aarch64)
         ▲                                                   │
         └── 算法 2 ReachFixpoint 为 [4] 提供可达集 R* ────────┘
```

**图例**：工具链为左→右线性流水，编译器位于**构建期**；产物 `kernel.o + validate.json` 经 `ignite_fused` 与可替换宿主（host）链接为 `.ko`，再进入部署与测量。图中不含 hypervisor 层，与 cRTOS 流水线正交。
**draw.io**：`figures/K-Fusion_fig2_toolchain.drawio`

---

## 图 3　三基线 soak 周期抖动分布（CDF，示意）

**ASCII 示意（趋势，非真实数值）**

```
 累计比例 F(|J|)
   1.0 ┤                                            ______________●  fused（最左、最陡）
       │                                     ______/
   0.8 ┤                              ______/
       │                        ______/     ______
   0.5 ┤                  ______/     _____/    ○ baseline_ko
       │            ______/      _____/
   0.2 ┤      ______/       _____/
       │  ____/       _____/
   0.0 ┤__/______ ____/____________________________△ userspace（尾部最长）
       └──┬──────┬──────┬──────┬──────┬────────► |J|
         小                                 大（对数轴）
```

**趋势性图例（描述性）**：横轴为抖动幅度 $|J|$（建议对数轴），纵轴为累计比例 $F(|J|)$；三条曲线分别对应用户态、手写内核模块与 K-Fusion 融合路径。**预期趋势**：融合曲线整体左移且更陡，说明其抖动的多数样本集中在更小幅度；用户态曲线右尾最长，反映 wake-up 路径引入的长尾；手写模块与融合曲线接近，差异主要体现**自动化程度**而非执行域。读图要点是**曲线左移幅度**与**尾部（F→1 处）位置的对比**。
**draw.io**：`figures/K-Fusion_fig3_soak_cdf.drawio`

---

## 图 4　stress 条件下各基线 $J_{\max}$ 对比（柱状，示意）

**ASCII 示意（趋势，非真实数值）**

```
 J_max
  高 ┤   ███
     │   ███
     │   ███    ███
     │   ███    ███    ██
  低 ┤   ███    ███    ██
     └───┬──────┬──────┬───►
      userspace baseline_ko fused
      升幅最大            升幅最小
```

**趋势性图例（描述性）**：横轴为三种基线，纵轴为最大绝对抖动 $J_{\max}$；建议并排绘制 **soak** 与 **stress** 两组柱，以阴影表示标准差或分位区间。**预期趋势**：加压后各基线 $J_{\max}$ 均上升，其中用户态基线对背景负载最敏感、升幅最大；融合路径升幅相对平缓，手写模块介于其间。读图要点是**加压前后柱高的相对增幅**，而非绝对量级。
**draw.io**：`figures/K-Fusion_fig4_stress_absmax.drawio`

---

## 图 5　消融实验：关闭各策略后的 Δ$J_{\max}$（柱状，示意）

**ASCII 示意（趋势，非真实数值）**

```
 ΔJ_max (相对默认)
 +  ┤              ███                    ███
    │              ███                    ███   ← 关闭间接调用解析：
    │              ███                    ███     可致可达函数被误删，
  0 ┼──────────────███────────────────────███──►    表现为模块加载失败
    │  默认(基准=0)  关WCET标记            关间接调用解析
    │                (尾部变差,Δ>0)         (非单纯抖动变化)
```

**趋势性图例（描述性）**：横轴为被关闭的策略，纵轴为 $J_{\max}$ 相对默认配置的增量 Δ。**预期趋势**：关闭 WCET 标记后，热路径基本块被优化重排，抖动尾部变差，Δ 为正；关闭定点下降可能引入浮点路径附加延迟，Δ 为正或轻微波动；关闭间接调用解析若导致可达函数被误删，则**不再表现为抖动变化，而是模块不可链接或运行期符号缺失**（以注释框特别标注）。读图要点是**各策略各自贡献的方向与量级**。
**draw.io**：`figures/K-Fusion_fig5_ablation.drawio`

---

## 图 6　内核化周期循环：源码 + 控制流图 + 时间线（Timed C 风格）

**ASCII 示意**

```
 ┌────────────────────────┐            ┌──────────────┐
 │ 1: while (1) {         │            │      (t)     │  ← 时间点/周期释放
 │ 2:  wait_next_period();│            └──────┬───────┘
 │ 3:  control_step();    │            ┌──────┴────────────┐
 │ 4:  log_metrics();     │            │ wait_next_period()│
 │ 5: }                   │            ├───────────────────┤
 └────────────────────────┘            │ control_step()[fused]│
   source C  (→ kernel.o)              ├───────────────────┤
                                       │ log_metrics()     │
        ┌─────回边: next period────────┘                   │
        └──────────────────────────────────────────────────┘

  execution timeline (T = nominal period)
 ┌────┬────┬────┬────┬────┬────┐
 │ I1 │ I2 │ I3 │ I4 │ I5 │ I6 │
 └────┴────┴────┴────┴────┴────┘
 0    1T   2T   3T   4T   5T   6T
 release via hrtimer (kernel) — no wake-up syscall on the hot path
```

**图例**：左为周期循环 C 源码；中为控制流图——**圆**表示时间点（周期释放），**方框**表示执行语句，含回边 "next period"；下为**水平时间线刻度条**（分段 I1–I6，横轴以周期 $T$ 为单位）。此三合一画法直接对标 **Timed C（RTAS 2018）Fig.1 / Fig.2**，用于说明"内核化后由 hrtimer 直接释放，热路径上不再有唤醒系统调用"。
**draw.io**：`figures/K-Fusion_fig6_timed_loop.drawio`

---

## draw.io 源文件清单

| 文件 | 对应图 | 类型 |
|------|--------|------|
| `figures/K-Fusion_fig1_architecture.drawio` | 图 1 | 架构箱线图 |
| `figures/K-Fusion_fig2_toolchain.drawio` | 图 2 | 线性流程/流水线图 |
| `figures/K-Fusion_fig3_soak_cdf.drawio` | 图 3 | CDF 曲线（示意） |
| `figures/K-Fusion_fig4_stress_absmax.drawio` | 图 4 | 柱状图（示意） |
| `figures/K-Fusion_fig5_ablation.drawio` | 图 5 | 柱状图（示意） |

> 打开方式：draw.io 桌面版或 https://app.diagrams.net → Open Existing Diagram → 选择 `.drawio` 文件。图中的**数值与曲线为示意**，导出投稿图前请以实测数据替换坐标点。

---

## 导出格式一览（draw.io / SVG / PNG 三套）

| 图 | draw.io 源文件 | 矢量图 SVG | 位图 PNG |
|----|----------------|-----------|----------|
| 图 1 架构对比 | `figures/K-Fusion_fig1_architecture.drawio` | `figures/K-Fusion_fig1_architecture.svg` | `figures/K-Fusion_fig1_architecture.png` |
| 图 2 工具链流水线 | `figures/K-Fusion_fig2_toolchain.drawio` | `figures/K-Fusion_fig2_toolchain.svg` | `figures/K-Fusion_fig2_toolchain.png` |
| 图 3 soak CDF | `figures/K-Fusion_fig3_soak_cdf.drawio` | `figures/K-Fusion_fig3_soak_cdf.svg` | `figures/K-Fusion_fig3_soak_cdf.png` |
| 图 4 stress J_max | `figures/K-Fusion_fig4_stress_absmax.drawio` | `figures/K-Fusion_fig4_stress_absmax.svg` | `figures/K-Fusion_fig4_stress_absmax.png` |
| 图 5 消融 ΔJ_max | `figures/K-Fusion_fig5_ablation.drawio` | `figures/K-Fusion_fig5_ablation.svg` | `figures/K-Fusion_fig5_ablation.png` |
| 图 6 周期循环时序 | `figures/K-Fusion_fig6_timed_loop.drawio` | `figures/K-Fusion_fig6_timed_loop.svg` | `figures/K-Fusion_fig6_timed_loop.png` |

**使用建议**：
- **投稿正文**优先用 **SVG**（矢量、无损缩放，IEEE/Elsevier 均接受 SVG/PDF/EPS）。PNG 为 200 dpi 位图，适合快速预览或版面占位。
- **PNG 说明**：图 3/4/5 由 matplotlib 程序化生成（坐标轴与图例为真实绘图组件）；图 1/2 由同一布局程序化绘制。三者与 SVG/draw.io 版**同布局、同配色**，但**曲线与柱高为示意趋势**，请以实测数据替换后再定稿。
- 如需 **EPS/PDF**（部分期刊要求），可由 draw.io 直接导出，或用 `svglib`/`cairosvg` 从 SVG 转换。
