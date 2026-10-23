# 效果摘要分析（feat(analysis)）：让编译器回答「这段代码会不会睡／碰 MMIO」

> 日期：2026-10-23 · 状态：**代码已写，编译与自测见 §5（待填实测）**
> 上游需求：`RESEARCH_DESIGN_ECoS_v1.0` §13 `feat(analysis)`
> 前置：`CONTRACT__任务契约与校验.md`（上一轮建立的门禁）

---

## 1. 它补的是哪一环

上一轮 `feat(contract)` 建立了「契约 + fail-closed 校验器」，但有一个明确的空洞：

> **契约里的 `effects` 是人写的。**

人写能证明门禁机制可用，却证不了「编译器真的知道这段代码会睡」。ECoS 的整个
`feat(graph) → feat(fusion) → feat(planner)` 链条都建立在这个判断上，
所以必须先把它从"声明"变成"推导"。

本轮补上：

```
    C 源码                     IR 分析                      契约                 门禁
 ─────────────►  ─────────────────────────►  ───────────────────►  ─────────────────
  cyclictest      EffectSummaryAnalysis        effect_to_contract     contract_check
  plc_chain.c     (plc-effect-summary)         （拓扑人工 + 效果自动）   （fail-closed）
                        │                            │                     │
                        └─ effects/contexts ──────────┘                     └─ 拒绝就报错
```

**分工原则**：人只声明**拓扑与上下文**（哪些函数是一个阶段、谁依赖谁、期望在哪个上下文跑），
**效果由编译器推导**。人负责"我想怎么切"，编译器负责"切了会不会出事"。

---

## 2. 判定规则（宁保守勿漏报）

| 效果 | 判定依据 |
|---|---|
| `sleep` | 调用名命中 sleep 类（`nanosleep/usleep/msleep/schedule_timeout/wait_event/mutex_lock/down_/sigwait/sem_wait` 等） |
| `mmio` | 调用名命中 MMIO 类（`ioremap/readl/writel/ioread/iowrite/memcpy_toio/regmap` 等），**或** `volatile` 的 load/store（内核里几乎只有 MMIO 这么用） |
| `blocking_io` | 调用名命中 `fwrite/fread/open/read/write/ioctl/mmap/printf` 等 |
| `state_read` / `state_write` | load / store 的指针根对象是 `GlobalVariable`；或是本函数的指针参数被写（对调用者即 `state_write`） |
| `external` | 调用了无函数体的符号且不在"已知纯函数"表里（`strlen/strcmp/memcmp` 等）；**间接调用解析不出目标时也算 external** |
| `pure` | 以上都没有 |

`allowed_contexts` 由效果反推：能睡（`sleep`/`blocking_io`）→ 只有 `thread`；
否则给 `thread + softirq`。**hardirq 一律不给**——没有证据就不放行。

### 三条刻意的保守决定

1. **间接调用不猜**。解析不到目标就记 `external`，不假装它纯。
2. **递归取不动点**并置 `recursive=true`，摘要里保留该标记，下游可以据此拒绝或降级。
3. **`volatile` load/store 归 MMIO**。会把少数"volatile 全局计数器"误判成 MMIO，
   但反过来的漏报（把真实寄存器写当普通内存）后果严重得多。

---

## 3. 输出格式

```json
{
  "version": 1,
  "module": "cyclictest_nuttx.i",
  "functions": {
    "fir_filter": {
      "effects": ["state_read", "state_write"],
      "allowed_contexts": ["thread", "softirq"],
      "recursive": false,
      "calls": ["memset"],
      "unknown_calls": []
    }
  }
}
```

`unknown_calls` 单独列出（而不是只给一个 `external` 位）——因为下游要能回答
"到底是哪个符号没认出来"，否则只能整体拒绝，无法逐步收窄。

---

## 4. 从摘要到契约

```bash
# 1) IR → 摘要
PLC_EFFECT_SUMMARY_OUT=effect_summary.json \
  opt-16 -load-pass-plugin PLCEffectSummary.so \
         -passes=plc-effect-summary foo.ll -S -o /dev/null

# 2) 摘要 + 拓扑 → 契约
python kfusion/scripts/effect_to_contract__摘要转契约.py \
  --summary effect_summary.json \
  --skeleton kfusion/contracts/examples/plc_chain.stages.json \
  --out plc_chain.contract.json

# 3) 契约 → 门禁
python kfusion/scripts/contract_check__契约校验.py \
  plc_chain.contract.json \
  --platforms kfusion/contracts/examples/platform_caps.json \
  --platform linux_kernel
```

第 2 步的 `stages.json` 里**没有 `effects` 字段**——这是关键：人手写不了，也不该手写。
转换器会：

- 一个阶段可由多个函数组成 → 效果取并集
- 函数不在摘要里 → 记 `external` 并打 `[W_FN_MISSING]`
- 摘要标记 `recursive` → 打 `[W_RECURSION]`
- 保留 `derived_allowed_contexts` 供人工核对（`contract_check` 不读这个字段）

---

## 5. 验证（2026-10-23，Pi4B + LLVM 16.0.6）

| 项 | 结果 |
|---|---|
| 编译 | ✅ 零错误，`PLCEffectSummary.so` 44,840 B |
| `effect_summary_basic.ll` 自测 | ✅ **10/10**（`analysis/test/effect_summary_selftest__自测.sh`） |
| 真实产物（NuttX cyclictest `.i`） | ⚠️ 见 §5.1：`-O2` 下只剩 3 个函数 |
| 端到端：摘要 → 契约 → 校验 | ✅ `linux_kernel` **REJECT[E_MMIO_FORBIDDEN]**，`nuttx` **PASS** |

### 5.1 一个必须知道的限制：分析版 IR 必须禁用内联

同一份 `cyclictest_nuttx.i`：

| 编译选项 | IR 里 `define` 数 | 摘要里的函数数 |
|---|---|---|
| `-O2` | **3** | 3 |
| `-O1 -fno-inline -fno-inline-functions` | **13** | 13 |
| `-O0` | 13 | 13 |

`-O2` 把几乎所有函数内联进 `main`/`timerthread`，于是：

- 效果摘要退化为「每模块几个巨型函数」；
- `main` 的效果变成 `state_read, state_write, sleep, blocking_io, external` 的大杂烩；
- **阶段级契约无从谈起**——你没有"阶段"可切了。

所以：**分析用的 IR 一律加 `-fno-inline -fno-inline-functions`**。
产物用的 IR 仍然走 `-O2`（性能优先），两条链分开跑。

> 这不是 pass 的 bug，是"函数级分析"和"内联"的固有张力。
> ECoS 后续 `feat(graph)` 若要做跨过程图，同样要先固定这个前提。

### 5.2 端到端实测输出（`plc_chain` 三阶段）

```
sensor_prepare     state_write,mmio                   ctx=thread,softirq
fir_filter         state_read,state_write             ctx=thread,softirq
pid_step           state_read,state_write             ctx=thread,softirq
chain_main         state_read,state_write,mmio        ctx=thread,softirq
```

（`stages.json` 里只写了拓扑，`effects` 全部由编译器填。）

```
[linux_kernel] ❌ REJECT
   E_MMIO_FORBIDDEN  sensor_prepare  平台 'linux_kernel' 的 allow_mmio=false，但阶段声明了 mmio
[nuttx] ✅ PASS
```

→ 这就是 ECoS 里程碑里「编译器自动拒绝 `sensor_prepare@NuttX` 之外的部署」的第一步：
**依据不是人写的，是从 IR 推出来的。**

---

## 6. 明确不主张

1. **不证明语义等价**。这是"可能发生什么"的汇总，不是 refinement proof。
2. **不保证覆盖全部效果**。只认表里那些名字；未识别的会落进 `external` 而不是被忽略。
3. **`allowed_contexts` 不是安全证书**。它只说"按效果看能不能进中断上下文"，
   没说"进了之后 WCET 还成立"。
4. **假阳性是设计的一部分**。宁可把 `volatile` 计数器报成 MMIO，也不放过一次真实寄存器访问。
   假阳性率要单独统计（见 §5 待填项），但**不能用降低保守性来换**。
