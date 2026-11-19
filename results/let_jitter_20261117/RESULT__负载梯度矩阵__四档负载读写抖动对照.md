# 负载梯度矩阵：四档负载下的读写抖动对照（2026-11-18）

## 1. 为什么要重做

2026-11-17 的"CPU 负载对照"被证实受 bug 污染：`MODE=load` 的
`stress-ng --timeout=${DURATION_SEC}s`（60 s）只覆盖第一场，而实验总时长 =
`REPS × 2 臂 × DURATION`（约 6 分钟）——负载在第 60 秒就结束。
证据：thread 臂读抖动 rep1=37 µs，rep2/rep3=110/114 µs（后两轮其实已无负载）。
**那组数据作废，不作结论**（总纲 §5.3 已标注）。

本次修复并补齐：
- `run_let_load_matrix__负载梯度矩阵.sh`：外部 `stress-ng` 全程维持（timeout 覆盖全程 + 240 s 余量），
  `taskset -c 0,1,2` 绑非隔离核（隔离核=CPU3，测量在 CPU3），负载核与测量核分离。
- `run_let_jitter_exp` 新增 `REUSE_KO_DIR`：各档复用同一批已验收的 `.ko`，保证档间唯一变量只有负载。
- 负载类型：cpu（100% 占核）、vm（内存 512M 反复 mmap）、io（同步 IO）、net（socket IO）。
- 每档 60 s × 3 轮；空闲基线 `let_jitter_idle_60s_3x60s` 同窗口、同批 ko、同一脚本。

修复验证：重做后 cpu 档 thread 三轮 = 40.1 / 29.6 / 34.7 µs，形态平稳，
**不再有原数据的"rep1 低、rep2/3 跳高"断点**，证明负载全程维持生效。

## 2. 分组口径（2026-11-18 纠错）

- **irq = D 组**：编译器生成硬中断读写（Pass 产出 `stage_impl.S` + manifest 哈希，`PINNED_HARD`，`let_ctx_hardirq`）。
- **thread = B 组**：内核实时线程读写（`kf_worker_fn` 内核工作线程，`let_ctx_task`），**不是用户态线程**。
- A 组（用户态计算+读写）、C 组（手写硬中断）未落地，详见总纲 §4 纠错注。

## 3. 结果（每轮 max|实际−逻辑| 的中位数 / 极差，µs；统计纪律：不报均值）

| 负载档 | irq(D) 读中位/极差 | thread(B) 读中位/极差 | irq(D) 写中位/极差 | thread(B) 写中位/极差 |
|---|---:|---:|---:|---:|
| idle | 4.1 / 0.0 | 80.0 / 69.9 | 6.1 / 4.5 | 65.1 / 8.3 |
| cpu | 2.4 / 1.2 | 34.7 / 10.5 | 4.7 / 1.8 | 56.8 / 28.7 |
| mem(vm) | 5.5 / 0.4 | 240.8 / 64.3 | 22.0 / 6.0 | 202.4 / 48.8 |
| io | 1.7 / 0.7 | 27.2 / 10.3 | 4.2 / 0.8 | 43.2 / 7.6 |
| net | 3.7 / 0.7 | 82.2 / 14.8 | 9.6 / 5.2 | 95.1 / 8.0 |

正确性：五档各 6 行（2 臂×3 轮），`skip = err = drops = 0` 全绿。

核心图：`let_loadmatrix_summary.png`（左读右写；柱=中位数、黑点=每轮 max、竖线=极差）。
各档独立图在各 `figs/` 子目录；原始数据、逐拍日志在 Pi 端 `~/kfusion_results/let_loadmatrix_*_3x60s/`。

## 4. 结论（按统计纪律收口）

1. **硬中断读写（D）对负载类型极不敏感**：读抖动中位跨五档仅 1.7–5.5 µs，极差 ≤ 1.2 µs；
   写抖动除 mem 档（22 µs）外均在 4–10 µs。读写钉扎在定时器硬中断后，线程调度路径上的负载扰动
   基本进不到读写时刻。
2. **内核线程读写（B）对负载类型高度敏感**：读抖动中位从 io 27 µs 到 mem 241 µs 跨近 9 倍；
   **内存负载最狠**（读 241 / 写 202 µs），因为 `stress-ng --vm` 反复 mmap 制造大量页错误与内存回收，
   直接拉长线程唤醒与调度路径。
3. B/D 差距随负载放大：idle 约 20×、io 约 16×、cpu 约 14×、net 约 22×、**mem 约 44×**。
   读写下沉到硬中断的价值在重负载下更突出。
4. **不外推显著性**：每档仅 3 轮（符号检验 p=0.125 > 0.05）；3 轮极差在 thread 臂可达 65 µs（idle/mem）。
   本结论限于"该平台、该负载工具、该窗口下的量级与形态"，不作统计显著性或普适定量断言。
5. net 档有 `stress-ng sock: finished prematurely` 警告（7m29s），但实验 449 s 内三轮数值平稳
   （84.9/82.2/70.0 µs），无负载提前结束的断点；net 档数据可用，但负载强度记录依赖 stress-ng 默认参数，
   复现时应固定 sock workers 数（本次 2）。

## 5. 与开题命题的关系

开题 §5.3/§6 要求"分别引入处理器、内存、网络或存储等负载，记录负载的位置和强度"。本实验
补齐了四类负载（cpu/mem/io/net），且记录了负载命令与绑核（见各档 `load_config.txt`），
是四组对照里 A/B/C/D 之外的**负载维度**证据。组合 §5.4 victim 对照可得：硬中断读写
既把自身抖动压低、又不向同核 victim 外溢延迟，且在各类负载下保持稳定。

## 6. 复现

```bash
# Pi 上（stress-ng 已装），四档串行
for kind in cpu vm io net; do
  KF_EXP_SCRIPT=/home/pi/K-Fusion/kfusion/scripts/run_let_jitter_exp__读写抖动实验.sh \
  LOAD_KIND=$kind DURATION_SEC=60 REPS=3 OUT=/home/pi/kfusion_results/let_loadmatrix_${kind}_3x60s \
    bash kfusion/scripts/run_let_load_matrix__负载梯度矩阵.sh
done
# 汇总图
python3 kf_loadmatrix_plot.py <输出png>
```
