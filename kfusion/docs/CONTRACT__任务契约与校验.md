# 任务契约与 fail-closed 校验（v1）

> 状态：**已实现并自测通过**（2026-10-23）。这是 ECoS §13 `feat(contract)` 的落地。
> 相关：`RESEARCH_DESIGN_ECoS_v1.0` §3 输入契约、§13 最小 PR 清单；
> `PROBE_CONTRACT__统一测量契约.md`（产物的测量侧，与本文件是配套的两面）

---

## 1. 它解决什么

ECoS 的核心主张是「**无法证明合法就拒绝**」。这句话要有可验收的落地物，否则只是措辞。

本项目现在有**两层 fail-closed**：

| 层 | 对象 | 机制 | 状态 |
|---|---|---|---|
| **产物层** | 编译器输出 | `PLC_FUSION_UNMAPPED_POLICY=error`：未映射/不可解析调用 → `report_fatal_error`，不出目标文件 | ✅ 已实现（`VERIFY_20261022`） |
| **输入层** | 任务契约 | 本文件：契约 + 平台能力 → 判定能否部署到某域，违规即拒 | ✅ 已实现（本轮） |

两层合起来才完整：**输入层拒绝"本来就不该部署的方案"，产物层拒绝"转不干净的方案"**。

---

## 2. 契约长什么样

`kfusion/contracts/examples/plc_chain.contract.json`

```json
{
  "version": 1,
  "task": { "name": "plc_chain", "entry": "chain_main",
            "period_us": 1000, "deadline_us": 800 },
  "stages": [
    { "name": "sensor_prepare", "fn": "sensor_prepare",
      "depends_on": [], "context": "thread",
      "effects": ["mmio", "state_write"] },
    { "name": "fir_filter", "fn": "fir_filter",
      "depends_on": ["sensor_prepare"], "context": "thread",
      "effects": ["state_read", "state_write"] },
    { "name": "pid_step", "fn": "pid_step",
      "depends_on": ["fir_filter"], "context": "thread",
      "effects": ["state_read", "state_write"] }
  ],
  "observations": [ { "name": "actuator_out", "kind": "mmio", "release_id": true } ]
}
```

平台能力 `kfusion/contracts/examples/platform_caps.json`：

| 平台 | 支持的上下文 | allow_mmio | max_rt_priority | supports_float |
|---|---|---|---|---|
| `linux_kernel` | thread, softirq | **false** | 99 | true |
| `nuttx` | thread | true | 255 | false |

> `linux_kernel` 设 `allow_mmio=false` 是有意的：本项目的融合内核模块不映射设备 MMIO。
> 于是「含 MMIO 的阶段」会被自动挡在内核域之外——这正是 §10 期望的 **A@Linux | (B+C)@NuttX 拆分信号**。

---

## 3. 诊断码（10 个，稳定）

| 码 | 触发 |
|---|---|
| `E_STRUCT` | 结构缺失 / 类型错 / 版本不是 1 |
| `E_RANGE` | `period_us` 或 `deadline_us` < 1 |
| `E_DEADLINE_GT_PERIOD` | `deadline_us > period_us` |
| `E_EFFECT_UNKNOWN` | `effects` 出现白名单外的值（等价 UNKNOWN） |
| `E_CONTEXT_UNKNOWN` | `context` 不在全局白名单 |
| `E_CONTEXT` | 平台不支持该 `context` |
| `E_SLEEP_IN_IRQ` | `softirq`/`hardirq` 上下文里声明 `sleep`/`blocking_io` |
| `E_MMIO_FORBIDDEN` | 平台 `allow_mmio=false`，阶段却声明 `mmio` |
| `E_DEP_UNKNOWN` | `depends_on` 指向不存在的阶段 |
| `E_DEP_CYCLE` | 阶段依赖成环 |

**fail-closed 的三处细节**：
1. 白名单以外的一切效果都按 `UNKNOWN` 拒绝（不猜）。
2. 平台没列出的能力按「不允许」处理（`allow_mmio` 缺失即 false）。
3. `jsonschema` 不在环境里时**不静默跳过**——规则由代码实现，schema 只做额外一层；
   若装了 jsonschema 则两份 `.schema.json` 也一起校验。

---

## 4. 自测（8 个用例，全部通过）

```
$ bash kfusion/scripts/contract_check_selftest__契约校验自测.sh <python>

-- 正例 --
  ✅ plc_chain.contract.json@nuttx          → PASS
-- 同一份契约在不允许 MMIO 的域：必须被拒（这是特性）--
  ✅ plc_chain.contract.json@linux_kernel   → REJECT[E_MMIO_FORBIDDEN]
-- 6 个 fail-closed 负例 --
  ✅ n1_sleep_in_irq@linux_kernel           → REJECT[E_SLEEP_IN_IRQ]
  ✅ n2_effect_unknown@nuttx                → REJECT[E_EFFECT_UNKNOWN]
  ✅ n3_deadline_gt_period@nuttx            → REJECT[E_DEADLINE_GT_PERIOD]
  ✅ n4_context_not_allowed@nuttx           → REJECT[E_CONTEXT]
  ✅ n5_dep_unknown@nuttx                   → REJECT[E_DEP_UNKNOWN]
  ✅ n6_dep_cycle@nuttx                     → REJECT[E_DEP_CYCLE]

✅ 8 个用例通过
```

诊断示例（n1 在 linux_kernel 上）：

```
== n1_sleep_in_irq.json  (task=n1_sleep_in_irq)
   [linux_kernel] ❌ REJECT
      E_MMIO_FORBIDDEN     sensor_prepare    平台 'linux_kernel' 的 allow_mmio=false，但阶段声明了 mmio
      E_SLEEP_IN_IRQ       pid_step          上下文 'softirq' 里声明了 ['sleep']：中断上下文不能睡眠
```

---

## 5. 现在**还不是**什么（避免过度主张）

- ❌ **契约是人工写的**，不是从 C 源码自动推导的。
  自动推导（把 `effects`/`context` 从调用图 + 别名分析 + API 语义摘要里算出来）才是 ECoS 的 M1，
  也是真正的算法工作量。本轮只建立了**判定面**与**可验收的门禁**。
- ❌ 校验器不会证明"FFI 语义等价"，只做执行上下文与效果层面的合法性判定。
- ❌ `observations.release_id` 目前只是声明，尚未与运行时 trace 对齐（等 `feat(feedback)`）。

## 6. 下一步

1. `kfusion/analysis/EffectSummaryAnalysis`（LLVM）：从 IR 推导每个函数的
   `may_sleep / may_mmio / shared_state / allowed_contexts`，输出 JSON 并能**直接喂给本校验器**；
2. 契约生成：`plc_fuse` 在融合前自动产出 `task_contract.json`；
3. 把校验器接进 `plc_fuse` 入口：**校验不过就不生成产物**（与产物层的 strict 呼应）。
