# K-Fusion — 论文计划与实验指南

**An LLVM-based Kernelization and Fusion Compiler**

> 路径：`docs/paper/PAPER_PLAN__论文计划.md`  
> 实验脚本：`scripts/paper/`

---

## 1. 建议题目与贡献（草案）

**题目方向**：*K-Fusion: An LLVM-based Kernelization and Fusion Compiler for Low-Jitter Control on PREEMPT_RT*

| # | 贡献 | 仓库对应 |
|---|------|----------|
| C1 | **Split kernelization**：用户 C → `_kernel.o` + 可换宿主 `.ko`，非整进程进内核 | `plc_fuse` + 多宿主 |
| C2 | **Manifest 可行性分类**：周期定时 / 信号 / 锁 / 纯逻辑 / PLC IO / 多 TU | `manifests/` + `run_paper_feasibility` |
| C3 | **端到端 RT 评估**：浸泡 soak vs 加压 stress，三基线对照 + 消融 | `scripts/paper/` |

---

## 2. 实验步骤（按顺序执行）

```bash
cd ~/K-Fusion
export PATH="/usr/local/llvm-17/bin:$PATH"
sudo -v

# 快速冒烟（约 30–60 min，验证脚本）
PAPER_QUICK=1 bash scripts/paper/run_paper_all__论文全流程.sh

# 正式论文数据（数小时–数天，建议 reboot 后跑）
PAPER_RUNS=5 DURATION_MIN=15 bash scripts/paper/run_paper_baseline_matrix__论文基线矩阵.sh
DURATION_MIN=5 bash scripts/paper/run_paper_ablation_matrix__论文消融矩阵.sh
bash scripts/paper/run_paper_feasibility__论文可行性扫描.sh
DURATION_SEC=300 bash scripts/paper/run_paper_second_app__论文第二应用.sh

# 汇总
python3 scripts/paper/paper_summarize_results__论文结果汇总.py \
  --csv results/paper/baseline_matrix/paper_baseline_*.csv \
  --out results/paper/baseline_matrix/summary.md
```

### 步骤说明

| 步骤 | 脚本 | 产出 | 论文用途 |
|------|------|------|----------|
| 0 | `run_paper_missing__补跑缺失.sh` | 补 userspace/消融/第二应用 + 自动 `paper_consolidate` | **缺项一键补跑** |
| 1 | `run_paper_feasibility__论文可行性扫描.sh` | `results/paper/feasibility/LATEST_*.csv` | Table: manifest 可内核化率 |
| 2 | `run_paper_baseline_matrix__论文基线矩阵.sh` | 三基线 × soak/stress × N 次 | **主结果表** |
| 3 | `run_paper_ablation_matrix__论文消融矩阵.sh` | hist/wake/ring/iso 消融 | Figure: 每项贡献 ns |
| 4 | `run_paper_second_app__论文第二应用.sh` | signaltest 短测 | 泛化案例 |
| 5 | `run_paper_multitask__论文多任务.sh` | userspace vs fused × soak/stress × N | **W5 STRICT LET** 监督 1ms jitter + LetSummary |
| 5b | `paper_summarize_multitask__多任务汇总.py` | `paper_multitask_*_summary.md` | W5 表：按 baseline × measure 汇总 abs_max |
| 6 | `paper_summarize_results__论文结果汇总.py` | `*_summary.md` | 直接贴论文 |

### 三基线定义

| baseline | 含义 |
|----------|------|
| `userspace` | 同隔离条件下用户态 `cyclictest`（CPU3） |
| `baseline_ko` | **手写**最小 hrtimer 模块（`plc_baseline_cyclic__手写基线.c`） |
| `fused` | PLCFusion 官方 cyclictest 融合路径 |

### 测量类型

| measure_kind | 含义 |
|--------------|------|
| `soak` | 安静浸泡（L2） |
| `stress` | CPU0-2 `hackbench` 加压（L1） |

### W5 多任务（STRICT LET）

- **负载**：`plc_multitask` — 6 job 单线程 LET（非 OS 多上下文）；见 `docs/paper/MULTITASK_EVAL__多任务评估.md`。
- **主指标**：`MtSummary abs_max_ns`（supervisor 1 ms release jitter）；辅指标 `LetSummary`（overrun / skipped）。
- **Manifest**：`manifest_plc_multitask_paper__论文多任务测量.env`（`FUSE_LINK_PTHREAD_HOST=0`，debug pipeline）。
- **命令**：

```bash
PAPER_RUNS=3 DURATION_SEC=120 bash scripts/paper/run_paper_multitask__论文多任务.sh
python3 scripts/paper/paper_summarize_multitask__多任务汇总.py \
  --csv results/paper/multitask/paper_multitask_merged.csv \
  --out results/paper/multitask/paper_multitask_summary.md
```

- **注意**：测量进行中勿跑 `cleanup_repo_local`（`.multitask.lock` 保护 multitask 目录）。

---

## 3. 论文表模板

### Table 1 — 可行性（步骤 1）

| Category | Manifest | FUSE_HOST | unmapped | PASS |
|----------|----------|-----------|----------|------|

### Table 2 — 主结果（步骤 2，填 summarize 输出）

| Baseline | Soak mean±std (ns) | Stress mean±std (ns) | n |
|----------|-------------------|----------------------|---|

### Table 3 — 消融（步骤 3）

| Config | abs_max_ns | Δ vs default |
|--------|------------|--------------|

---

## 4. Related Work 定位（写作提纲）

- **用户态 RT**：cyclictest / PREEMPT_RT 文档 — 基线
- **内核线程 RT**：手写 `baseline_ko` — 证明「能写内核模块但不是 PLCFusion 目标」
- **eBPF / LKL**：边界 — PLCFusion 保留 **原始 C IR + manifest 复现**
- **WCET 工具链**：LLVM DCE + LowJitter — 工程组合，消融支撑

---

## 5. 还需要加代码吗？

| 优先级 | 工作 | 是否新功能代码 |
|--------|------|----------------|
| **P0** | 跑完步骤 2–3，填表 | **否**，用现有 `scripts/paper/` |
| **P1** | 第二平台（x86 PREEMPT_RT） | 少量 manifest/脚本复制 |
| **P1** | 论文 Figure（从 CSV 出图） | 可选 Python 绘图 ~100 行 |
| **P2** | Pass 形式化 / 自动 host 推断论文化 | 中等，非必须 |
| **P2** | Artifact Docker | 打包脚本，非编译器核心 |

**结论**：发嵌入式/RT workshop 或中文核心 **不必再大改编译器**；当前缺口是 **实验数据重复次数与写作**，不是功能堆叠。

---

## 6. 可选后续（冲更好 venue）

1. x86_64 板卡复现 Table 2 子集  
2. `demo_compare` 并入基线矩阵（已含 userspace）  
3. 用户态 **under-load** cyclictest 与 fused stress 并排（矩阵已覆盖）  
4. WCET autotune 一组数据作「编译–运行协同」小节  
5. GitHub Artifact + 固定 kernel `6.12.62+rpt-rpi-v8-rt` 复现包
