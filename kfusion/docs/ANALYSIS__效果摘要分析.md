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

## 5.3 v2 修复（2026-10-23 代码审查后）

审查指出 v1 有三个**设计错误**（不是笔误），全部已改并补了回归：

| 问题 | v1 的错误 | v2 的做法 |
|---|---|---|
| `memcpy/memset` 判成 pure | 只想着"会不会睡/碰 MMIO"，忘了**数据依赖**也要靠这张摘要 | 新增 `reads`/`writes` 抽象区域；`memcpy(dst,src)` → writes `arg:0`、reads `arg:1` |
| 指针参数只判"被写" | `memcpy(out,in,n)` 这种"通过调用写 out"完全漏掉 | load → reads `arg:N`；store → writes `arg:N`；跨过程时把 callee 的 `arg:N` 映射成实参区域 |
| 递归靠"迭代 64 次上限" | 深非递归链会误标，真递归也可能提前收敛 | 对调用图做 **Tarjan SCC**，只标记 SCC size>1 或自环 |

判断标准也改了：**`pure` 现在是"什么效果都没有"**，不再允许"名字常见所以放行"。

新增 `unknown` 与 `external` 两级：

- `unknown` —— 解析不出的间接调用等，**一律拒绝**（诊断码 `E_UNPROVEN_EFFECT`）
- `external` —— 名字认识但语义未归档，**默认拒绝**，可 `--allow-external` 显式放行

### 验证

`effect_summary_basic.ll` 从 10 条断言扩到 **26 条**，全部通过：

```
✅ memcpy_stage: ['state_read','state_write']   reads 含 arg:1 / writes 含 arg:0
✅ memset_stage: ['state_write']                writes 含 arg:0
✅ ptr_reader:   reads 含 arg:0
✅ calls_writer: writes 含 @g_state             （跨过程参数映射正确）
✅ indirect_caller: ['unknown']                 不含 pure
✅ pure_libc:    ['pure']                       不含 external
```

拒绝策略实测：

```
external 默认            → E_EXTERNAL_UNVERIFIED  rc=1
external --allow-external → PASS                   rc=0
unknown  (+ --allow-external) → E_UNPROVEN_EFFECT  rc=1   ← 一律拒
platform_caps 为空        → rc=2（之前会静默成功）
```

## 5.4 别名分析（2026-10-23 追加）

**为什么必须补**：v2 的区域只有三类（`@global` / `arg:N` / `heap`）。所有堆指针
共用一个 `heap` 名字，等于告诉下游"它们全可能别名"。后续做数据依赖图时，
A 阶段的每个堆写都会连到 B 阶段的每个堆读——**假边爆炸**，DAG 就没法用了。

### 做法

用 LLVM 的 `getUnderlyingObject`（能穿透 GEP / bitcast / PHI / select）找根对象，
再按根对象种类给**稳定且唯一**的名字：

| 根对象 | 区域名 |
|---|---|
| `GlobalVariable` | `@变量名` |
| `AllocaInst` | `alloca:<函数>:<名字或序号>` |
| 分配型调用（malloc/calloc/realloc/kmalloc/operator new…） | `heap:<callee>#<调用点序号>` |
| `Argument` | `arg:N` |
| 归不到根 | `heap:?`（保守兜底） |

再用**真实的 `AAManager`** 做一致性校验，输出两个计数：

- `coarse_pairs`：AA 判定 `NoAlias`，但我们给了同一个名字 → **命名太粗**
  （安全方向：只会多出假依赖边）
- `split_pairs`：AA 判定 `MustAlias`，但我们给了不同名字 → **命名太细**
  （危险方向：会漏掉真依赖）

`split_pairs > 0` 直接判失败——这是回归门禁。

### 实测

自测用例（33 条，新增 3 条 alias 用例）：

```
✅ two_allocs:   writes 有 2 个不同区域 ['heap:malloc#0', 'heap:malloc#1']
✅ same_obj:     writes 有 1 个不同区域 ['arg:0']        ← GEP 穿透到同一根
✅ two_allocas:  writes 有 2 个不同区域 ['alloca:two_allocas:x', 'alloca:two_allocas:y']
✅ alias_stats:  检查 3 对，coarse=0（安全侧），split=0
```

真实产物（NuttX cyclictest，13 函数，`-O1 -fno-inline`）：

```
alias_stats: {'aa_available': True, 'pairs_checked': 3521,
              'coarse_pairs': 0, 'split_pairs': 0}
区域种类: alloca 16 / arg 7 / heap 12 / @全局 若干
```

**3521 对指针，两个方向都是 0** —— 说明 `getUnderlyingObject` 的划分在这份代码上
既不过粗也不过细。这不是"AA 说没问题"，而是"我们的近似与 AA 完全一致"。

### 不主张

1. **不是 full alias analysis**：没有流敏感、没有 field-sensitive（同一个 struct 的
   两个字段算同一区域）、没有跨函数别名推理（参数间 `noalias` 属性会被忽略）。
2. `heap:?` 仍是兜底：归不到根的指针依然共用一个名字，会保守地产生假边。
   这个比例可以统计（`区域种类` 里的 `heap`），但目前未单独计数。
3. **AA 只用于校验，不用于划分**：区域名完全由 `getUnderlyingObject` 决定，
   AA 只回答"我们这个划分错没错"。这样做的好处是划分结果可复现、不依赖 AA 的具体实现。
4. 没有验证 `split_pairs=0` 在所有输入上都成立——只在自测用例和一份真实产物上成立。

## 6. 明确不主张

1. **不证明语义等价**。这是"可能发生什么"的汇总，不是 refinement proof。
2. **不保证覆盖全部效果**。只认表里那些名字；未识别的会落进 `external` 而不是被忽略。
3. **`allowed_contexts` 不是安全证书**。它只说"按效果看能不能进中断上下文"，
   没说"进了之后 WCET 还成立"。
4. **假阳性是设计的一部分**。宁可把 `volatile` 计数器报成 MMIO，也不放过一次真实寄存器访问。
   假阳性率要单独统计（见 §5 待填项），但**不能用降低保守性来换**。

---

## 7. 下游：任务 DAG（2026-10-23 补充）

本文的 `reads` / `writes` / `calls` 是 `dag_build__任务DAG生成.py` 的唯一输入。
那份文档处理的是本文**刻意不处理**的问题：区域名跨函数怎么比、传播导致的假环怎么抑制。

五条衔接约定（改本文输出时必须同步）：

1. **`arg:N` 是函数局部名**，跨函数不可直接比较。DAG 侧靠 `callsites`
   （调用点明细，可递归）+ `thread_entries` + `interface_bindings`（兜底）围成三级：
   唯一绑定→已证 / 多绑定→候选 / 无绑定→标记未闭合。
   → **不要**为了"让 DAG 好看"而在本文里把 `arg:N` 改名成别的形态，语义会丢。
2. **`calls` 只记直接调用**。`pthread_create` 这类"把函数指针交给外部 API"的线程入口
   **不在里面**（`cyclictest` 的 `main → testthread` 就是），已在 v23 单独导出为
   `thread_entries`（`{caller, entry, arg}`），**不要塞进 `calls`**——
   否则会把"创建即执行"错当成调用，还会污染调用图的可达性计算。
3. **`callsites` 是接口闭合的唯一依据**。聚合的 `interface_bindings` 丢了调用者身份，
   遇到相对名 `arg:K` 就解不动；`callsites` 才能递归。两者都输出，聚合视图只作兜底。
4. **区域名只在同一次分析内可比**。`heap:<callee>#<N>` 的 `N` 是模块级单调计数，
   换优化级别就会变。跨 module 合并摘要在当前版本**不支持**。
5. **`heap:?` 有三种成因，别混为一谈**：经内存 load（31）、外部 API 返回（18）、
   `phi` 合流（2）——合计 51 处出现，去重后是 7 个不同名字。用 `PLC_EFFECT_SUMMARY_WHY=1` 可以把产生点逐条打出来
   （默认关闭，不污染正常输出）。

见 `DAG__任务依赖图生成.md`。
