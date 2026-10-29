# K-Fusion 方案与证据（单一文档，2026-10-29）

> 本文件合并了此前 PR-4C 至 PR-14 的全部分报告。旧分报告已从工作树移除，
> 需要细节可 `git log --diff-filter=D --name-only` 找回。**以本文件为准**。
>
> 一句话：把受约束的周期 C 程序编译成「多部署候选」——全用户态 / 部分内核化 /
> 全内核——用**实测尾部**而不是模型预测来选，并把**调度类**作为与部署并列的
> 决策变量；选不出证据就拒绝生成。

---

## 1. 判定红线（任何报告都不得越过）

1. **环分类**看相位反向边：生产者相位晚于消费者才允许 `z⁻¹`，同向即代数环，拒。
2. **局部谓词 ≠ 全局合法**；整方案合法性只认 verify 门禁，生成器 `--require-verified`。
3. 时间单递推 `S_g = max(φ_g, F_{g-1}) + L`，`F_g = S_g + ΣC`；窗口按绝对周期时刻判。
4. 反馈死区唯一公式 `temporal_model.feedback_delays()`。
5. 验证器只能叫「**基于模型的可行性筛选器**」，**不得**宣称硬实时可调度性。
6. 成本输入必须入库（`cost_stage_pin.json` 等），否则选型不可复现。
7. 对照必须**同一份二进制只改一个变量**；跨平台/跨构建的数不许无标识混表。

---

## 2. 管线与决策结构

```
周期 C 源
  → Clang AST 抽阶段（阶段名/相位/状态）
  → LLVM IR + EffectSummary（效果不是人写的）
  → 契约门禁（用户态为底线，内核域可行性交选型）
  → TEIR（每阶段 allowed_domains / 成本 / 依赖）
  → STKP 穷举：分组 × 域 → verify → 候选集
  → 部署目标 J(d,s) 选入选（d=分组×域，s=调度类）
  → 生成（用户态 driver / UKU ioctl / hrtimer 内核宿主）
  → 上板：digest 一致 + 逐拍探针
  → 调度感知可行性 v2（三列结论）
```

**J(d,s) = α·Q99(d,s) + β·R_miss(d,s) + γ·C_boundary(d)**，权重是策略输入、
量纲不齐，不是 WCET。γ 默认 0（Q99 已含跨界往返，避免重复计数）。

**形状键 = (域, 组数, 释放数, 组成员集合)**。缺一项就会串数据——见 §5。

---

## 3. 平台与环境（冻结证据，`pr11_deploy_objective/env_evidence.json`）

| 项 | 值 |
|---|---|
| 板 / SoC | Raspberry Pi 4 Model B **Rev 1.5** / bcm2711 |
| 内核 | `5.15.86 #1 SMP PREEMPT`，aarch64 |
| 抢占类别 | **PREEMPT（非 RT）**，取自 `/boot/config-5.15.86`；无 `PREEMPT_RT` |
| HZ / tick | 250 / `NO_HZ_IDLE`（无 `NO_HZ_FULL`） |
| ftrace | 未开（`CONFIG_FTRACE` 缺）→ `rtla timerlat` 不可用 |
| cmdline | 无 `isolcpus`、无 `nohz_full` |
| 亲和 / RT 配额 / 频率 | CPU3 / `rt_runtime_us=950000` / **无 cpufreq（固定时钟）** |
| 代码 | rev + 源码 sha256；`dirty` 分「源码脏/产物脏」，只有源码脏算不可复现 |

缺失关键字段 → 画像 `complete=False` → 决策直接 `env_incomplete` 拒绝。

---

## 4. 实测矩阵（10 轮 × 5000 拍；Q99 = 各轮 p99 的中位数）

### 4.1 静默（同核无背景负载）

| 部署 | 组 / 释放 | CFS Q99 | FIFO99 Q99 | CFS 超期 | FIFO 超期 |
|---|---|---|---|---|---|
| 全融合（B） | 1 / 1 | 66.00 | **15.00** | 5 | 0 |
| gold（原程序，单释放） | 3 / — | 78.27 | 29.47 | 33 | 0 |
| mixed（中段 ioctl） | 3 / — | 83.37 | 34.64 | 33 | 0 |
| 两组 `[s][pk]` | 2 / 2 | 263.00 | 213.00 | 0 | 0 |
| 两组 `[sp][k]` | 2 / 2 | 462.00 | 414.00 | 1 | 0 |
| 三组 plan（A） | 3 / 3 | 462.00 | 412.00 | 8 | 0 |

### 4.2 加压（4 个忙循环**钉在 CPU3**，与测量同核）

| 部署 | CFS Q99 | FIFO99 Q99 | CFS 超期 | FIFO 超期 | CFS 最差 |
|---|---|---|---|---|---|
| 全融合（B） | 74.00 | **24.00** | 77 | 0 | 14.57 ms |
| 三组 plan（A） | 473.00 | 422.00 | 101 | 0 | 16.64 ms |
| 两组 `[s][pk]` | 274.00 | 223.00 | 79 | 0 | 16.36 ms |
| 两组 `[sp][k]` | **1023.00** | 422.50 | **8336（16.7%）** | **1** | 43.01 ms |

> 加压方式与 PR-10B 不同：那边忙循环不钉核；本表钉在测量核上，因为要问的是
> **同核争用**。这一点写在 `pr11_shapes` 的 `start_load()` 里，不是暗改。
>
> **`[sp][k]` 那一行是坏天气样本**：同一份代码在 CFS 下 16.7% 的拍超期、
> 最差 43 ms；FIFO 也有 1 次超期（1013.7 µs）。所以「FIFO 零超期」只在
> 静默/中等加压成立——**FIFO 不是免疫，只是概率低**。

---

## 5. 五个已经站得住的结论

**① 释放结构 ≫ 调度类 ≫ 执行域。**
同一 FIFO99 下：多一次释放 +198~397 µs；CFS→FIFO −48~51 µs；用户态→混合 +5.17 µs。
「融合」的真正价值不是省调用开销（十几 µs），而是**把释放次数压到 1**。

**② p99 看不见加压，超期数看得见。**
全融合 CFS：Q99 66→74（+12%），超期 5→77（**15.4×**），最差 2.58 ms→14.57 ms。
`[sp][k]` 更极端：Q99 462→1023，超期 1→8336。
所以只用 α·Q99 的目标函数对加压**不敏感**，必须让 β>0 或直接看 `deadline_verdict`。
**FIFO 也不是免疫**：`[sp][k]` 加压下出现 1 次 1013.7 µs 超期。

**③ 毫秒尖峰在「等调度」，不在执行。**
超期拍的 work 与正常拍相同（用户态 1.3 µs、混合 4.3 µs），缺的全是 `start_lat`
（超期拍 start_lat 占 resp 的 98–100%）。内核化只减 work，不减 start_lat——
这解释了 PR-10B/10C 为什么内核化零收益。谁抢了 CPU 仍未归因（需 ftrace
`sched_switch`，受 p1 分区 32 MB 限制装不下第二个内核）。

**④ 旧成本模型与期限脱节，且方向可测。**
旧模型 `R̂ = ΣC+ΣL+ΣH` 对 4 个候选全判「可行」（36–78 µs ≪ 1000 µs deadline），
实测 CFS 下**全部超期**。模型没算错数，错在只有一个标量、且不含调度等待
（start_lat CFS 63 / FIFO 12 µs）与相位跨度（多释放 +398 µs）。
它对「融合优于不融合」判断正确，对「内核化优于融合」判断错误
（模型认为 mixed 快 4.95 µs，实测 B 快 19.64 µs）。

**⑤ 形状键少一项就会串数据（自查出的错）。**
首版按「组数」把 `gold`（原程序单释放）配给候选 A（plan 三组各释放一次）：
组数相同，Q99 差 **382.5 µs**（29.47 vs 412.00）。修正为
`(域, 组数, 释放数, 成员集合)`，释放数从探针头 `releases_per_period` 解析。
因此历史结论「B 比 A 快 14.47 µs」应改为「B 比 A 的 plan 形状快 **397 µs**」，
14.47 µs 是相对 gold 的差。

---

## 6. 决策与可行性（当前实现）

**部署目标**（`ecos/deploy_objective.py`）：J 最小者入选；缺形状/缺调度类/缺边界单价
→ 该组合不可排名，**不许退而求其次**；全部不可排名 → `chosen=None` +
`objective_fail_closed`，上层拒绝生成。覆盖率不足时控制台与 `deploy.json` 都标
`⚠ 覆盖不全 x/y`。

**mixed 三释放已可测、且必须与单释放分表。** 驱动现有 `plan`/`plan_user`
（相位 0/200/400 µs）。`docs/pr17_uku_plan/report.json` 覆盖 **5/5**
（语义 digest `cc44ce29` + 矩阵 40/40 有效 Q99）。静默 Q99：
`mixed_plan` CFS 482 / FIFO99 427 µs。旧 `partial_kernel` 仍是
单释放 mixed（FIFO 34.64 µs，`n_releases=null`），**不得**配给 plan 候选。
排名用 `sched_profile_v3.json`（v2 ∪ mixed_plan load=0）；v2 原样保留。
同 STKP：v2 上 mixed 不可排名；v3 上 mixed_plan FIFO **J=427**。
入选仍是 `fused_user @ fifo99`（J=15）——三释放跨 400 µs 相位，Q99 大是结构差，
不是内核化优劣。加压格子在 `sched_profile_mixed_plan_load4.json`，未并进 v3。

**可行性 v2**（`ecos/sched_aware_cost.py`）三列、四档，**没有「可调度」档**：

| 列 | 含义 |
|---|---|
| `compile_legal` | 语义/结构合法（与时间无关） |
| `model.feasible` | 旧标量模型判断，仅作对照 |
| `deadline_verdict` | 只认实测 |

四档：`infeasible_observed`（实测超期>0）/ `cannot_certify`（0 超期但
Wilson 上界 × horizon 拍数 ≥1）/ `empirically_feasible`（只在给定 horizon 内）/
`insufficient_evidence`（缺证据）。

静默 1 小时：4 个候选里 **3 个 CFS `infeasible_observed`**、1 个 `cannot_certify`。
**加压 1 小时：4/4 候选 CFS 全部 `infeasible_observed`**；FIFO 除 `[sp][k]`
（1 次超期 → 也是 `infeasible_observed`）外均为 `cannot_certify`
（5 万拍 0 超期，上界 7.68e-5 → 1 小时上界 276 次；要 `empirically_feasible`
得把 horizon 缩到 ~13 s 内）。

---

## 6.5 训练/验证分离（PR-15）：通过是必然，单轮结论才是问题

之前的做法是「10 轮全用来建画像，再用同一批宣布选对了」——自证。现在按轮次切分，
留一法（9 轮建画像、被留那轮检验，共 10 折），静默与加压各跑一遍：

| 条件 | 折数 | 选对 | 最大遗憾 | 期限结论翻转 |
|---|---|---|---|---|
| 静默 β=0 / β=1e5 | 10 / 10 | 10 / 10 | 0.0 µs | 0 |
| 加压 β=0 / β=1e5 | 10 / 10 | 10 / 10 | 0.0 µs | 0 |

**但这不说明方法稳，而说明这个任务没有区分力**。量一下分辨率：

| 条件 | 最优 vs 次优 margin | 轮间 p99 极差 | margin / noise |
|---|---|---|---|
| 加压 β=0 | 50.0 µs（B@fifo vs B@cfs） | 21 µs | **2.38** |
| 加压 β=1e5 | 199.0 µs（B vs `[s][pk]`@fifo） | 21 µs | **9.48** |

margin 是噪声的 2.4–9.5 倍，任何留出法都必然通过。**要说「选择稳」，得先有候选
差距接近轮间波动的任务**，那属于 PR-16 的设计，不是本任务能证明的。

真正被这轮检验出来的是另一件事——**单轮判定不可靠**。只用一轮出期限结论，
10 轮里会这样翻：

| 条件 | 会翻转的格子 | 例 |
|---|---|---|
| 静默 | 4 / 12 | gold@cfs 7 次超期 / 3 次不能背书；fused@cfs 2/8；A@cfs 6/4 |
| 加压 | 4 / 8 | fused@cfs 9/1；A@cfs 9/1；`[s][pk]`@cfs 9/1；**`[sp][k]`@fifo99 1/9** |

最后那一格尤其重要：FIFO 的 1 次 1013.7 µs 超期只出现在 **10 轮里的 1 轮**。
若只跑一轮，有 90% 概率得出「FIFO 可行」——所以 10 轮重复不是礼貌，是必需。

（结论：`deadline_verdict` 必须以多轮池化为准；单轮只能当冒烟测试。
脚本 `pr15_holdout__训练验证分离.py`，参数 `--protocol loo|half`。）

---

## 6.6 消融（PR-16）：去掉哪个机制会怎样

五个方法，每个只比前一个多一样。**选择的差别看不出来**（本任务 margin/noise=2.4–9.5，
M2–M4 都 10/10 选对 B@fifo99），差别全在**期限判定的可信度**上——所以主指标是
逐「候选 × 调度类」组合的漏报（假可行）率，80 个组合·折样本。

| 方法 | 定义 | 加压：漏报 | 检出 | 误报 | 静默：漏报 |
|---|---|---|---|---|---|
| M0 静态模型 | R̂ 最小，调度类固定 CFS，结论看 R̂≤D | 38/38（100%） | 0 | 0 | 9/9（100%） |
| M1 +调度类维度 | 同上但固定 FIFO99 | 38/38（100%） | 0 | 0 | 9/9（100%） |
| M2 +实测尾部 | 按 Q99 选，结论看 Q99≤D | 28/38（74%） | 26.3% | 0 | 9/9（100%） |
| M3 +违约风险 | J=Q99+β·R_miss（β=1e5），四档结论 | **1/38（3%）** | **97.4%** | 28.6% | **1/9（11%）** |
| M4 +边界成本 | M3 + γ·C_boundary（γ=1） | 1/38（3%） | 97.4% | 28.6% | 1/9（11%） |

四条读法：

1. **静态模型 100% 漏报**：它把 38 个真会超期的组合全判成可行——因为 R̂ 36–78 µs
   远低于 1000 µs deadline，模型里根本没有「等调度」这一项。
2. **光加调度类维度不改善判定**（M1 ≡ M0）：纯模型对 CFS/FIFO 给同一个预测。
   它只能让「选择」变好，不能让「结论」变好——两件事必须分开说。
3. **Q99 单独不够**：静默下 M2 仍 100% 漏报（66–462 µs 全部 < 1000 µs），
   加压下也只有 26% 检出。这直接解释了为什么 J 里必须有 β。
4. **M4 ≡ M3 是设计预期**：Q99 已含跨界往返，γ>0 只表达结构代价，
   不该改变期限判定。若哪天 M4 与 M3 不同，就是重复计数，得查。

代价也要写：M3 有 28.6% 误报（训练轮出现超期、验证轮没有）。这是稀有事件下的必然——
训练 5 万拍见 1 次超期，验证 5 千拍很可能再见不到。所以四档里 `infeasible_observed`
只代表「观测到过」，不代表「每次都会」。

> 消融只在这一个任务、这块 Pi4、这个非 RT 内核上成立。要检验「选择」本身
> （而不是判定），需要构造 margin ≈ 噪声的候选对——本任务做不到。

---

## 7. 不成立 / 不许写的说法

- ❌「验证器通过 = 可调度」——反例：CFS 5/50000（静默）、69–115/50000（加压）。
- ❌「FIFO 下没问题」——静默只能说 5 万拍没看到（1 小时上界 276 次）；
  加压下 `[sp][k]` 已实测到 1 次 1013.7 µs 超期。
- ❌「PREEMPT_RT 下……」——本平台是非 RT；RT 结论一条都没有。
- ❌「内核化加速」——本负载上已封闭：内核化只减 work（1.3 µs 量级），不减等调度。
- ⚠️「自动选择已泛化」——留一法 10/10 一致，但 margin/noise 只有 2.4–9.5，
  通过是必然；真正被检验出的是**单轮判定不可靠**（8 格里 4 格逐轮翻转）。
- ❌ 跨释放结构比较 Q99——结构差 382 µs，比任何部署差都大。

---

## 8. 复现（全部命令在板上跑）

```bash
# ① 环境证据（缺失即拒绝决策）
python3 kfusion/scripts/pr11_env_evidence__环境证据.py \
  --source kfusion/examples/ecos/mixed_pid_stateful__积分混合.c \
  -o kfusion/docs/pr11_deploy_objective/env_evidence.json

# ② 形状 + 载荷采集（可断点续采；--backfill 从留档补释放结构，不重测）
python3 kfusion/scripts/pr11_shapes__形状补测.py --repeat 10 --cycles 5000
python3 kfusion/scripts/pr11_shapes__形状补测.py --only fused_user --load 4
# 统计只认原始日志：json 被覆盖/回退后用它重算
python3 kfusion/scripts/pr11_shapes__形状补测.py --rebuild

# ③ 画像（静默 / 加压各一份，不许混）
python3 kfusion/scripts/pr11_deploy_objective__部署目标.py profile \
  --baseline kfusion/docs/pr10e_baseline/baseline.json \
  --shapes kfusion/docs/pr11_shapes/shapes.json --load 0 \
  --env kfusion/docs/pr11_deploy_objective/env_evidence.json \
  --boundary kfusion/docs/pr10c_boundary/boundary.json \
  -o kfusion/docs/pr11_deploy_objective/sched_profile_v2.json

# ③b mixed_plan 并入 v3（不要覆盖 v2）
python3 kfusion/scripts/pr11_deploy_objective__部署目标.py decide \
  --profile kfusion/docs/pr11_deploy_objective/sched_profile_v3.json \
  --stkp kfusion/docs/pr17_uku_plan/build/gen/stkp.json \
  --deadline 1000 -o kfusion/docs/pr17_uku_plan/decision_v3_a1_b0_g0.json

# ④ 决策 + 三列可行性（历史复现仍用 v2）
python3 .../pr11_deploy_objective__部署目标.py decide --profile ... --stkp ... --deadline 1000
python3 kfusion/scripts/pr14_sched_aware_verify__调度感知验证.py \
  --stkp ... --profile ... --deadline 1000 --horizon-s 3600 -o pr14_report.json

# ⑤ 生成器闭环（选 B、按 FIFO99 跑；REQUIRE_DEADLINE_EVIDENCE=1 可硬拒）
DEPLOY_OBJECTIVE=...sched_profile_v2.json APPLY_SCHED=1 \
  bash kfusion/scripts/ecos_from_c__从C编译.sh \
  kfusion/examples/ecos/mixed_pid_stateful__积分混合.c
```

回归：`python3 -m unittest discover -s kfusion/scripts/test -p "test_*.py"`（83 项）；
`python3 .../pr11_deploy_objective__部署目标.py selftest`（11 项）。

---

## 9. 文件索引（删掉的只是分报告，数据与代码都在）

| 路径 | 内容 |
|---|---|
| `docs/pr11_deploy_objective/env_evidence.json` | 平台身份冻结 |
| `docs/pr11_deploy_objective/sched_profile_v2.json` | 静默画像（不含 mixed_plan；gold/mixed 的 n_releases=null） |
| `docs/pr11_deploy_objective/sched_profile_v3.json` | v2 + mixed_plan(load=0, n_releases=3)；排名用这份 |
| `docs/pr17_uku_plan/sched_profile_mixed_plan.json` | mixed 三释放静默格子（Q99 482/427） |
| `docs/pr17_uku_plan/decision_v3_a1_b0_g0.json` | 上述 STKP × v3 的 J(d,s) |
| `docs/pr17_uku_plan/stkp.json` | 决策用的候选快照（不依赖 gitignore 的 `build/gen`） |
| `docs/pr17_uku_plan/input_hashes.json` | 上述快照 sha256 |
| `docs/pr17_uku_plan/holdout_v3_quiet.json` | PR-15 留一 + mixed_plan（离线，未重测） |
| `docs/pr17_uku_plan/ablation_v3_quiet.json` | PR-16 消融 + mixed_plan（离线） |
| `docs/pr11_deploy_objective/sched_profile_load4.json` | 加压画像（4 形状 × 2 调度类；不含 mixed_plan_load4） |
| `docs/pr11_deploy_objective/decision_*.json` | 决策与权重敏感性 |
| `docs/pr11_deploy_objective/pr14_report*.json` | 三列可行性报告 |
| `docs/pr11_shapes/shapes.json` + `*/runs_*_loadN/r*.log.gz` | 形状/载荷原始输出（含 1 µs 直方图）；统计可由 `--rebuild` 从原始日志重算 |
| `docs/pr10e_baseline/baseline.json` + `runs_*/` | gold / mixed 逐拍基线 |
| `docs/pr10b_matrix/`、`docs/pr10c_boundary/` | 历史矩阵与边界/调度归因的原始 json |
| `scripts/ecos/deploy_objective.py` | J(d,s)、形状匹配、环境门禁 |
| `scripts/ecos/sched_aware_cost.py` | 三列可行性、四档判定 |
| `scripts/pr11_shapes__形状补测.py` | 形状/载荷采集（含回填） |
| `scripts/pr11_env_evidence__环境证据.py` | 环境取证 |
| `scripts/pr14_sched_aware_verify__调度感知验证.py` | 可行性报告 CLI |
| `scripts/ecos_plan_joint.py` | STKP、`--deploy-objective`、`--force-chosen` |
| `scripts/ecos/feedback_profile.py` | PR-18A 三层反馈画像（校准层空） |
| `scripts/pr18_feedback__反馈校准.py` | `errors` / `calibrate` / `shadow` / `ablate` |
| `scripts/ecos/calibrate_cost.py` | PR-18B 残差 Δ̂；跨键 fail-closed |
| `scripts/ecos/feedback_loop.py` | PR-18C shadow 重选 |
| `docs/pr18_feedback/` | 误差表、校准表、shadow、M0–M3 留出消融 |
| `docs/pr19_generalize/status.json` | 无新负载画像 → insufficient_evidence |
| `docs/pr20_rt/status.json` | 平台是 PREEMPT 非 RT → insufficient_evidence |

---

## 10. 未完成（按优先级）

1. **PR-17B–18D 代码与离线实验已落地**（17C 起尚未提交远端）。shadow 默认不改
   chosen；本 PID 任务上 M1≡M2≡M3（B@fifo99），校准没有改变选择。
2. **PR-19 泛化：insufficient_evidence**。更长阶段链示例没有对齐画像，
   禁止借用 mixed_pid 的 Δ̂。需要 margin≈噪声的新负载板上数据。
3. **PR-20 PREEMPT_RT：insufficient_evidence**。现平台是 PREEMPT。不得把
   已有格子写成 RT 结论。
4. **谁抢了 CPU 的机制归因**：受 p1 32 MB 限制，需换分区或外部 trace。
5. **构造 margin ≈ 噪声的候选对**：否则选择正确率永远满分。
