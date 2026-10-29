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
| 三组 plan（A） | 3 / 3 | 462.00 | 412.00 | 0 | 0 |

### 4.2 加压（4 个忙循环**钉在 CPU3**，与测量同核）

| 部署 | CFS Q99 | FIFO99 Q99 | CFS 超期 | FIFO 超期 | CFS 最差 |
|---|---|---|---|---|---|
| 全融合（B） | 74.00 | **24.00** | **69** | 0 | 14.28 ms |
| 三组 plan（A） | 473.00 | 422.00 | 81 | 0 | 16.48 ms |
| 两组 `[s][pk]` | 274.00 | 223.00 | 115 | 0 | 16.48 ms |
| 两组 `[sp][k]` | 473.00 | 422.00 | 78 | 0 | 14.57 ms |

> 加压方式与 PR-10B 不同：那边忙循环不钉核；本表钉在测量核上，因为要问的是
> **同核争用**。这一点写在 `pr11_shapes` 的 `start_load()` 里，不是暗改。

---

## 5. 五个已经站得住的结论

**① 释放结构 ≫ 调度类 ≫ 执行域。**
同一 FIFO99 下：多一次释放 +198~397 µs；CFS→FIFO −48~51 µs；用户态→混合 +5.17 µs。
「融合」的真正价值不是省调用开销（十几 µs），而是**把释放次数压到 1**。

**② p99 看不见加压，超期数看得见。**
全融合 CFS：Q99 66→74（+12%），超期 5→69（**13.8×**），最差 2.58 ms→14.28 ms。
所以只用 α·Q99 的目标函数对加压**不敏感**，必须让 β>0 或直接看 `deadline_verdict`。

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
`⚠ 覆盖不全 x/y`（当前 4/5，mixed 缺 plan 口径测量）。

**可行性 v2**（`ecos/sched_aware_cost.py`）三列、四档，**没有「可调度」档**：

| 列 | 含义 |
|---|---|
| `compile_legal` | 语义/结构合法（与时间无关） |
| `model.feasible` | 旧标量模型判断，仅作对照 |
| `deadline_verdict` | 只认实测 |

四档：`infeasible_observed`（实测超期>0）/ `cannot_certify`（0 超期但
Wilson 上界 × horizon 拍数 ≥1）/ `empirically_feasible`（只在给定 horizon 内）/
`insufficient_evidence`（缺证据）。

静默 1 小时：4 个候选里 2 个 CFS `infeasible_observed`，其余 `cannot_certify`。
**加压 1 小时：4/4 候选 CFS 全部 `infeasible_observed`**，FIFO 仍 `cannot_certify`
（5 万拍 0 超期，上界 7.68e-5 → 1 小时上界 276 次；要 `empirically_feasible`
得把 horizon 缩到 ~13 s 内）。

---

## 7. 不成立 / 不许写的说法

- ❌「验证器通过 = 可调度」——反例：CFS 5/50000（静默）、69–115/50000（加压）。
- ❌「FIFO 下没问题」——只能说 5 万拍没看到，1 小时上界 276 次。
- ❌「PREEMPT_RT 下……」——本平台是非 RT；RT 结论一条都没有。
- ❌「内核化加速」——本负载上已封闭：内核化只减 work（1.3 µs 量级），不减等调度。
- ❌「自动选择已泛化」——同一批 10 轮既建画像又评选择，没有训练/验证分离。
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

# ③ 画像（静默 / 加压各一份，不许混）
python3 kfusion/scripts/pr11_deploy_objective__部署目标.py profile \
  --baseline kfusion/docs/pr10e_baseline/baseline.json \
  --shapes kfusion/docs/pr11_shapes/shapes.json --load 0 \
  --env kfusion/docs/pr11_deploy_objective/env_evidence.json \
  --boundary kfusion/docs/pr10c_boundary/boundary.json \
  -o kfusion/docs/pr11_deploy_objective/sched_profile_v2.json

# ④ 决策 + 三列可行性
python3 .../pr11_deploy_objective__部署目标.py decide --profile ... --stkp ... --deadline 1000
python3 kfusion/scripts/pr14_sched_aware_verify__调度感知验证.py \
  --stkp ... --profile ... --deadline 1000 --horizon-s 3600 -o pr14_report.json

# ⑤ 生成器闭环（选 B、按 FIFO99 跑；REQUIRE_DEADLINE_EVIDENCE=1 可硬拒）
DEPLOY_OBJECTIVE=...sched_profile_v2.json APPLY_SCHED=1 \
  bash kfusion/scripts/ecos_from_c__从C编译.sh \
  kfusion/examples/ecos/mixed_pid_stateful__积分混合.c
```

回归：`python3 -m unittest discover -s kfusion/scripts/test -p "test_*.py"`（57 项）；
`python3 .../pr11_deploy_objective__部署目标.py selftest`（11 项）。

---

## 9. 文件索引（删掉的只是分报告，数据与代码都在）

| 路径 | 内容 |
|---|---|
| `docs/pr11_deploy_objective/env_evidence.json` | 平台身份冻结 |
| `docs/pr11_deploy_objective/sched_profile_v2.json` | 静默画像（5 形状 × 2 调度类） |
| `docs/pr11_deploy_objective/sched_profile_load4.json` | 加压画像（4 形状 × 2 调度类） |
| `docs/pr11_deploy_objective/decision_*.json` | 决策与权重敏感性 |
| `docs/pr11_deploy_objective/pr14_report*.json` | 三列可行性报告 |
| `docs/pr11_shapes/shapes.json` + `*/runs_*/r*.log.gz` | 形状/载荷原始输出（含 1 µs 直方图） |
| `docs/pr10e_baseline/baseline.json` + `runs_*/` | gold / mixed 逐拍基线 |
| `docs/pr10b_matrix/`、`docs/pr10c_boundary/` | 历史矩阵与边界/调度归因的原始 json |
| `scripts/ecos/deploy_objective.py` | J(d,s)、形状匹配、环境门禁 |
| `scripts/ecos/sched_aware_cost.py` | 三列可行性、四档判定 |
| `scripts/pr11_shapes__形状补测.py` | 形状/载荷采集（含回填） |
| `scripts/pr11_env_evidence__环境证据.py` | 环境取证 |
| `scripts/pr14_sched_aware_verify__调度感知验证.py` | 可行性报告 CLI |
| `scripts/ecos_plan_joint.py` | STKP、`--deploy-objective`、`--force-chosen` |

---

## 10. 未完成（按优先级）

1. **mixed 的 plan 口径测量**（需带 insmod 的 harness）→ 覆盖率 4/5 → 5/5。
2. **PR-15 训练/验证分离**：用一部分轮次建画像，独立轮次检验所选部署。
3. **PR-16 论文级消融**：静态模型 / +调度类 / +实测尾部 / +边界成本 四套对照，
   报错误选择率、违约率、p99、最差轮次最大值、候选覆盖率。
4. **谁抢了 CPU 的机制归因**：受 p1 32 MB 限制，需换分区或外部 trace。
