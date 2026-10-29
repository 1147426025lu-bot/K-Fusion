# RESULT PR-12/13：形状覆盖 2/5 → 4/5，并把平台身份冻住

**一句话**：补齐 3 类未测形状 + 1 类按正确释放结构重测后，可排名候选从 **2/5 到 4/5**（mixed 因缺 plan 口径测量被挡），入选从「gold（J=29.471）」翻回「**B（全融合，J=15.000）**」——也就是说 PR-11 那句「模型选错了」
有一半是我自己没测造成的，必须当场纠正：

> 模型对「**融合优于不融合**」判断正确（实测省 14.47 µs）；
> 对「**内核化优于融合**」判断错误（实测差 19.64 µs，方向相反）。

---

## 1. 为什么这一轮必须先补测量，而不是先改模型

PR-11 的决策只能给 2 个候选排名，其余 3 个（全融合、两种两组切分）被 fail-closed
挡住。当时写的是「模型选 mixed 输给全用户态 5.171 µs」，但**这个结论建立在
「B 没测 ⇒ 不可排名」之上**——把「没测」当成了「不值得选」。补测之后 B 直接赢了
14.47 µs。这不是模型变好，是证据变全。

## 2. 环境证据冻结（PR-12 最小集）

审查的 P0：画像里 `measure_env.kernel` 是 `null`，等于没有平台身份。现在由
`pr11_env_evidence__环境证据.py` 现场取证，缺关键字段就 `complete=False`，
**决策门禁直接拒绝**（`env_incomplete`，除非 `--allow-incomplete-env`，且那种结果
不得进论文表）。

| 项 | 值 | 来源 |
|---|---|---|
| 板 | Raspberry Pi 4 Model B **Rev 1.5** | `/proc/device-tree/model` |
| SoC | `raspberrypi,4-model-b` / `brcm,bcm2711` | `/proc/device-tree/compatible` |
| 内核 | `5.15.86 #1 SMP PREEMPT`，aarch64 | `uname -a` / `/proc/version` |
| **抢占类别** | **PREEMPT（非 RT）** | `/boot/config-5.15.86`: `CONFIG_PREEMPT=y`，无 `CONFIG_PREEMPT_RT` |
| HZ / tick | HZ=250，`NO_HZ_IDLE=y`（无 `NO_HZ_FULL`） | 同上 |
| ftrace | 未开（`CONFIG_FTRACE` 缺） | 同上 |
| cmdline | `console=ttyS1,115200`…；**无** `isolcpus` / `nohz_full` | `/proc/cmdline` |
| 亲和 | pin CPU3，nproc=4 | 采集脚本 |
| RT 配额 | `sched_rt_runtime_us=950000` | `/proc/sys/kernel/` |
| 频率控制 | **无 cpufreq 节点**（固定时钟，`governor=null`） | `/sys/.../cpufreq` 不存在 |
| 代码 | `578994f`，`dirty_source=0`，源码 `6cee00a2ac9b651b…` | `git status` + sha256 |

`git dirty` 现在分成**源码脏**与**产物脏**两类——采集本身在写 `docs/` 下的 json，
若不分，每次取证都 dirty=True，这个字段就废了。

> 注意：这是 **PREEMPT 非 RT**。这份数据的任何结论都不许写成 PREEMPT_RT 成果，
> 也不许与其它板子的数混表。

## 3. 形状补测怎么做的（三条不许省的校验）

1. `FORCE_CHOSEN=<id>@<domain>` 按形状点名构建（新参数，标 `forced_for_measurement`，
   与 `--deploy-objective` 互斥）。不靠调成本参数「凑」出目标形状——那会让
   「测的是哪个部署」取决于一串参数，事后无法复核。
2. **构建后回读 `plan.json` 的组结构**，与期望不符就报错。强制没生效被当成
   「这个形状就是这个数」，是这类实验最容易出的假数据。
3. 与 PR-10E-A 同口径：同源码、同生成器、CPU3、CFS/FIFO99、10 轮 × 5000 拍、
   每轮原始输出留档（含 1µs 直方图）。

## 4. 结果（Pi4 5.15.86 PREEMPT，10 轮 × 5000 拍）

> **⚠️ 口径修正（见 `docs/pr14_sched_aware/RESULT_PR14` §4）**：首版把
> `gold`（原程序，一个周期内三段顺序执行，探针不打印释放数）按「3 组」匹配给了
> 候选 A（`plan` 模式三组各释放一次）。两者差 **382.5 µs**。下表已按释放结构拆开，
> A 用新测的 plan 形状。

| 形状 | 组 | 释放/周期 | CFS Q99 | FIFO99 Q99 | CFS 超期 |
|---|---|---|---|---|---|
| **fused_user（B）** | 1 | 1 | **66.00** | **15.00** | 5/50000 |
| gold（PR-10E user_only，原程序） | 3 | 未记录 | 78.27 | 29.47 | 33/50000 |
| mixed（PR-10E partial_kernel） | 3 | 未记录 | 83.37 | 34.64 | 33/50000 |
| split_s_pk_user | 2 | 2 | 263.00 | 213.00 | 0 |
| split_sp_k_user | 2 | 2 | 462.00 | 414.00 | 1/50000 |
| **split3_user_plan（A）** | 3 | **3** | **462.00** | **412.00** | 0 |

## 5. 三个量级：分组 ≫ 调度类 ≫ 域

同一调度类（FIFO99）内比较，各项代价差一个数量级：

| 维度 | 代价 | 证据 |
|---|---|---|
| **释放结构**（1 → 2 或 3 次释放） | **+198 / +397 µs** | fused 15.00 → 213.00 / 412.00 |
| **调度类**（CFS → FIFO99） | **−51.0 µs**（融合）/ −48.8 µs（gold） | 66.00→15.00 / 78.27→29.47 |
| **执行域**（user → mixed，同 1 释放左右） | **+5.17 µs**（更差） | 29.47 → 34.64（gold vs mixed） |

**为什么多释放这么贵**：`split_*` 把后段放到 200/400 µs 相位单独成组，端到端响应
必须等它那一次释放——**相位跨度直接进响应**。所以这些数不是「调度差」，是「结构差」。
推论：

- 跨形状比 Q99 没有意义（只能在同释放结构内比）；
- STKP 用 `c=21 µs/组` 给分组定价，方向对（组多更贵）但量级严重低估
  （实测 198–397 µs vs 模型每组 21 µs）；
- 「融合」的真正价值不是省调用开销（那只有十几 µs），而是**把释放次数压到 1**。

## 6. 对 PR-11 结论的纠正（必须一起读）

| PR-11 原话 | 现在的事实 | 结论 |
|---|---|---|
| 「STKP 模型选的 mixed 在实测尾部上比全用户态差 5.171 µs」 | 仍成立（gold 29.47 vs mixed 34.64，同为单释放口径） | ✅ 保留 |
| 「模型与实测方向相反」 | **只对『混合 vs 不融合用户态』成立**；对「融合」不成立 | ⚠️ 收窄 |
| （隐含）「A 是实测最优」 | A 的 plan 形状是 **412.00**，比 B 的 15.00 差 397 µs；78.27 属于 gold | ❌ 撤回并重新测定 |

精确表述：**模型把 mixed 排在 B 前面（Ĉ 31.05 vs 36.0，模型认为快 4.95 µs），
实测 B 快 19.64 µs（15.00 vs 34.64）**。模型对融合的判断是对的，对内核化的
判断是错的——错在 `c=21 µs/组` 把分组价低估（且完全没建模释放次数带来的相位跨度），
又把 ioctl 边界价按 0.3 µs 计（实测 p50 2.0 µs）。

## 7. 决策与生成器（覆盖率 4/5，缺的那 1 个有明确原因）

```
deploy objective: B@linux_userspace @ fifo99 J=15.0 µs（模型原选 B；有画像 4/5）
STKP 入选 B  域 linux_userspace  调度类 fifo99
▶ 按部署决策加调度类：chrt -f 99
digest match: a0bf855e
hist_start_lat_us: 9 86 / 10 4 / 11 20 ...
```

10 个 (候选, 调度类) 组合全部可排名，`coverage.complete = true`。生成器选 B、
按 FIFO99 跑，digest 与 flat 一致（融合没有改变采样序列）。

## 8. 仍然没覆盖的（写清楚，不许含糊）

| 缺口 | 原因 | 影响 |
|---|---|---|
| **内核自主执行（C/D，`linux_kernel`）** | 它的响应语义与用户态不同（无每拍跨界、无用户态释放），PR-10C2 明确「不比较端到端 response」 | 不能塞进同一张 Q99 表；要进决策必须另立口径 |
| **加压条件（背景忙循环 0/4）** | 本轮只做静默条件 | PR-13 实验矩阵的另一半，未做 |
| 逐拍 csv | `plan` 模式不产 csv，只有统计块 | 不能事后改分位数、不能做超期拍 start_lat/work 分解 |
| 跨板 / 跨内核 | 只有这一块 Pi4 + 一个非 RT 内核 | 任何外推都是未验证 |
| 训练/验证分离 | 同一批 10 轮既建画像又评选择 | PR-15 的要求，未做——现在还不能说「自动选择泛化好」 |

## 9. 复现

```bash
# ① 环境证据（在板上，缺失即拒绝决策）
python3 kfusion/scripts/pr11_env_evidence__环境证据.py \
  --source kfusion/examples/ecos/mixed_pid_stateful__积分混合.c \
  -o kfusion/docs/pr11_deploy_objective/env_evidence.json

# ② 形状补测（每形状一批，可断点续采；--only 分批不互相覆盖）
python3 kfusion/scripts/pr11_shapes__形状补测.py --repeat 10 --cycles 5000

# ③ 画像 v2（基线 + 形状 + 环境证据，按形状索引）
python3 kfusion/scripts/pr11_deploy_objective__部署目标.py profile \
  --baseline kfusion/docs/pr10e_baseline/baseline.json \
  --shapes   kfusion/docs/pr11_shapes/shapes.json \
  --env      kfusion/docs/pr11_deploy_objective/env_evidence.json \
  --boundary kfusion/docs/pr10c_boundary/boundary.json \
  -o kfusion/docs/pr11_deploy_objective/sched_profile_v2.json

# ④ 决策 + 生成器闭环
python3 kfusion/scripts/pr11_deploy_objective__部署目标.py decide \
  --profile ...sched_profile_v2.json --stkp ...candidates_pr10e_stkp.json --deadline 1000 \
  -o ...decision_v2_a1_b0_g0.json
DEPLOY_OBJECTIVE=...sched_profile_v2.json APPLY_SCHED=1 \
  bash kfusion/scripts/ecos_from_c__从C编译.sh kfusion/examples/ecos/mixed_pid_stateful__积分混合.c
```

## 10. 诚实边界

- 只证明这台 Pi4、这个非 RT 内核、静默负载下的排序；加压、换板、换内核都没测。
- 4/5 覆盖只针对**这一个 PID 任务的 5 个候选**，不是「任意程序都能覆盖」；
  缺的 mixed 是因为没有 plan 口径（3 次释放）的测量，不是它更差。
- 两组形状的 Q99 之所以大，是相位结构所致，不能读成「调度更差」。
- 目标函数权重仍是策略输入；换 α/β 可能改变入选（本轮没做敏感性，PR-11 做过 4 组）。
