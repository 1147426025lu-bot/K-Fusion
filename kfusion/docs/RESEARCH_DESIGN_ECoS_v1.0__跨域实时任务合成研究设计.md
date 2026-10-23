# K-Fusion 研究设计 v1.0：语义约束下的跨域实时任务合成与时间感知联合优化

> **来源**：2026-10-22 外部研究设计讨论（ChatGPT 会话导出），原文为桌面
> `K Fusion审查 siunners.html`。本文由该 HTML **自动抽取正文**后入库，
> 公式（MathJax）在抽取过程中被打散，**精确公式请看原 HTML**。
>
> **性质**：这是**研究设计 / 拟实现规格**，不是已有结果。
> 原文第 16 节自己写明：「所有算法、性能改进与 CLI 均为拟实现 / 待验证」。
>
> **本仓库对它的核对**（2026-10-23）：
> | 文档引用 | 核对结果 |
> |---|---|
> | [R1] 默认严格外部调用策略 | ✅ 属实，且已比文档假设更强：`PLC_FUSION_UNMAPPED_POLICY=error` 默认、
> | | 违例时 `report_fatal_error` 拒绝产出，并有 `unmapped_strict.ll` 负向用例
> | | （见 `kfusion/docs/VERIFY_20261022__P1修复验证.md`） |
> | [R2] 搜索代理 `hot_inst×10000 + obj_bytes` | ✅ 属实，
> | | `kfusion/scripts/plc_fusion_wcet_per_function_search__函数级Association.py:49-52` |
> | [R3] 指标不可混用（P1 间隔偏差 / P2 唤醒延迟 / P3 BM2） | ✅ 与 `RESULTS_ALL` §9.0 一致 |
> | §13「修复 NuttX 行为与签名差异」 | ⏳ 部分已完成：`pthread_t` 写回宽度已修并反汇编验证 |
>
> **⚠️ 我补的一条结构性依赖**（原文档未强调）：
> ECoS 的目标 J(θ) 用「端到端响应时间 R」和「deadline miss M」，
> 但现有 Linux fused 宿主测的是**间隔偏差**（`FUSED_WAKE_TIMERTHREAD` 默认 0，
> 连测量线程都不唤醒），**产不出 R**。所以 §13 里 `feat(cost)` / `feat(planner)` /
> `feat(feedback)` 之前，必须先有**统一 R 探针**（三个时间戳 r_k / s_k / f_k + 统一 trace schema），
> 否则搜索目标不可测。BM2 已具备这三个量（2026-10-22 补了 `Deadline Misses = f_k > r_k + T`），
> 可作参考实现。

---

K-Fusion：语义约束下的跨域实时任务合成与时间感知联合优化
高水平系统
编译器论文导向的算法设计与工程实施规格（Research
Implementation
Design
v1.0）
2026-10-22
K-Fusion：语义约束下的跨域实时任务合成与时间感知联合优化
文档类型：
研究设计 + 数学模型 + LLVM 工程落地 + 实验验收规范
版本：
v1.0 / 2026-10-22
项目：
1147426025lu-bot/K-Fusion
研究定位：
优先面向高水平实时系统、编译器与嵌入式系统论文；本文件不是既有结果报告。
建议方法名（暂定）：
ECoS —
Effect-Constrained Cross-domain Synthesis
（效果约束的跨域任务合成）。名称待系统查重，
不作为创新性证明
核心主张（待验证）
：从用户态 C 周期程序自动推导“执行效果—上下文—依赖”约束，合成语义合法的 Linux 内核线程 / NuttX 执行单元、跨域通信和任务融合边界，并以端到端响应时间及期限要求联合选择部署和代码方案。创新不在“会调用 LLVM Pass”或“能运行在两个 OS”，而在
从程序语义推导可行执行结构并约束其时间优化
0. 决策摘要：做什么、不做什么
三项必须交付的技术机制：
（M1）保守的跨过程效果分析与上下文类型检查；（M2）保持周期 / 状态 / 可观测 I/O 语义的任务分段和融合代码合成；（M3）任务放置、融合和代码变体的联合时序优化及候选证据包。三者必须组合验证，单独任何一项都不自动构成论文创新。
明确暂不承诺：
支持任意 C/POSIX 程序、自动获得静态 WCET、跨 Linux/NuttX 逐字节相同机器码、全面击败 cRTOS 的唤醒延迟、把观测最大值作为硬实时保证、证明任意跨 OS 转换的完整等价性。
优先最小闭环：
Sensor → FIR → PID
（单一 1 ms 释放、三阶段有状态图）→ Linux 单域安全融合 → 加入 NuttX 后端 → 引入 IVSHMEM 明确定义跨域传输 → 联合求解器 → A/B 与消融实验。先做好
线程上下文
，不把业务代码默认塞进 HRTimer 回调或硬 IRQ。
Go/No-go 判据：
如果端到端合成相对“固定域 + 固定融合 + -O2”、或相对“仅任务映射”的强基线没有可重复改善，就不把联合优化声称为高水平论文的核心贡献；转而收缩到可形式化的“安全执行上下文推导 / 自动变换”问题。
1. 现有仓库的真实起点（截至 2026-10-22）
已存在模块 / 文件
可复用能力
必须补齐的缺口
kfusion/backend/pass/PLCFusionPass__内核化Pass.cpp
LLVM NewPM；normalize/remap/dce/export/wcet-mark；POSIX→plc_*；间接调用保守化
无统一效果摘要、上下文约束、跨域执行图及融合合法性证明
kfusion/backend/pass/PLCFusionFixedPoint__定点Pass.cpp
浮点→定点的初步转换
fmuladd、loop-carried PHI 等边界；数值语义 / 误差预算必须单独证明
kfusion/backend/pass/PLCLowJitterPass__低抖动Pass.cpp
标注或函数名识别并施加 noinline/optnone
optnone 不是低抖动算法，已有数据表明其会增加计算成本
kfusion/backend/pass/KFusionWCETSchedulePass__函数级WCET.cpp
从 JSON 载入函数级 LLVM 序列
仅执行给定 schedule；不形成任务融合 / 执行域联合求解
kfusion/scripts/plc_fusion_wcet_per_function_search__函数级Association.py
函数级 Pass 顺序搜索框架
适应度主要为 hot_inst×10000 + obj_bytes，不代表 WCET/WCRT
kfusion/manifests/ 与 scripts/plc_kernelize__内核化.sh
基于清单的构建、后端链接、CI
入口及目标选项大量依赖人工清单；需要新“需求契约→自动 plan”接口
kfusion/scripts/closure_audit__依赖闭包审计.py
未定义符号审计，出错不再伪报空闭包
nm -u 不是传递调用闭包；需类型 / 上下文 / 后端实现完整校验
crtos/、NuttX / Jailhouse / IVSHMEM 脚本
双域部署和跨域测量经验
尚无对任意受支持任务图自动合成跨域协议的验证
事实边界：
仓库最新文档显示 Pi4B 平台实验为主、Raspberry Pi 5 配置亦存在；不能混用不同板卡、内核和会话的数据。
RESULTS_ALL
区分 P1 定时器回调间隔偏差、P2 cyclictest 唤醒延迟和 P3 BM2 唤醒延迟 / 执行时间 / deadline miss。已有 BM2 在 NuttX 上“经过 Pass 与否”执行均值约 1.203 与 1.205 µs，不能据此宣称编译器带来降抖收益。
直接工程结论：
保留现有 backend、manifest 和运行时桩，但将“低抖动”重新定义为
端到端响应时间与期限可行性
的系统级属性；传统定时器抖动继续作为分解后的辅助指标。
2. 研究问题、差异化边界与可证伪假设
2.1 精确研究问题
给定受支持的周期 C 程序
、目标执行域集合
、周期 / 期限契约
、后端能力表
与可观测行为契约
，自动构造一个候选执行计划
，使得：
每个阶段的 OS/内核执行上下文合法且依赖可解析；
对契约所指定的输入、状态、可观测输出，变换前后保持定义好的语义关系；
在处理器容量、通信协议、内存与同步约束下，使端到端响应时间或可验证期限裕度更优；
无法证明合法或无法部署时明确给出拒绝原因，而不是替换为“成功返回 0”。
重要区分：
程序“可转换”与“满足硬实时期限”是两个独立判定。前者可通过保守静态检查与有限范围验证建立证据；后者只有在可信 WCET、抢占 / 干扰和通信界限齐备时才能给出硬实时结论。默认实现为
经验性时间优化系统
，不把统计预测冒充硬保证。
2.2 相关研究与预期差异（需要继续系统性查新）
研究 / 公开事实
已有能力
K-Fusion 不能重复的叙事
需要检验的新增点
Magnani 等，Lavinium，RTSS 2025
LLVM 函数级 WCET 导向的 Pass 选择与排序
“首次函数级搜索 / 遗传搜索降低 WCET”
语义约束下改变执行图、融合边界和执行域
Multi-Criteria Optimization of Real-Time DAGs on Heterogeneous Platforms under P-EDF，ACM DOI:10.1145/3592609
已建模实时 DAG 的异构部署优化
“首次优化异构 DAG 任务映射”
从 C/LLVM 自动推导合法合成单元，而非将 DAG 和备选实现视为已给定
cRTOS (2025)，Jailhouse/NuttX
Linux 与轻量 RTOS 并存、跨域服务与兼容机制
“首次让 Linux 程序在 NuttX 跑”
静态效果约束 + 合成边界 + 可解释时间决策
Logical Execution Time (LET) 系列
定时输入/输出、时序可组合性
“首次提出确定性 I/O 时刻”
把现有时间语义用作编译合法性约束，不改写未授权的应用语义
Alive2 / translation validation
LLVM IR 优化正确性检查
“用 Alive2 就证明跨 OS 行为等价”
仅对其支持的局部 IR 变换使用；跨过程 / 外部行为另行验证
新意成立的必要条件：
论文必须提供至少一个“单独做任务映射 / 仅做 Pass 调优 / 固定规则融合无法复现”的真实程序，且能解释性能收益源于
变换后的合法候选集合扩大或可行性更好
，而非平台天生更快。
2.3 可检验研究假设
H1 可转换性：
自动效果分析在预先定义的程序子集上，能无人工逐函数白名单地生成可执行、可诊断的两个后端；恶意负例不能静默通过。
H2 联合优化：
在存在至少两个合法融合边界与两个合法部署方案的任务图上，联合策略相对于“仅放置”
“仅融合”
能够提高 deadline feasibility 或端到端尾部裕度。
H3 归因与泛化：
收益在多任务负载与跨域通信成本变化下可解释，而非仅依赖某个编译器版本、优先级或某次会话。
H4 搜索有效性：
合法性剪枝 + 路径驱动搜索，在相同编译/实机测量预算下，比随机 / 通用黑盒搜索达到更高的可行解率。
3. 编程模型与输入契约（第一版必须收窄）
3.1 受支持程序子集 S0
第一阶段只支持：单个周期事务（一个周期内最多一批采样、一次计算和一次提交），有界循环、确定性的 C 数值阶段、显式输入输出结构体、显式状态对象、静态可解析或保守可枚举调用、无未建模动态分配 / 阻塞 I/O / 外设访问。多个阶段可以用注释或属性标明：
用户可以标明周期任务的外部入口，但不能手工指定融合方案或所有适配调用
。第二阶段再扩展不同频率、共享状态和跨域设备代理。
两类状态：
private_state
（仅当前任务）、
shared_state
（必须说明同步协议）。区分“中间结果尚不可见”与“已经提交到外部设备”的操作。对时间语义采用明确策略：
immediate
保留原提交顺序与时机约束；只有用户声明
LET
时，才允许把结果延迟到逻辑提交点。
3.2 三阶段示例（说明性输入，不是仓库现有接口）
// @kf.periodic period_us=1000 deadline_us=500 semantic=immediate
void
plc_cycle
const
struct
Input
*in,
struct
State
*st,
struct
Output
*out){
struct
Sample s
sensor_prepare
(in);
// 阶段 A
struct
Filtered f
fir_step
(s,&st->fir);
// 阶段 B
out->cmd=
pid_step
(f,&st->pid);
// 阶段 C
约束默认：
A→B→C 同一次激活，顺序一致；三个阶段不能因融合被重排到另一周期；如果 A 读取 Linux 专有设备或 C 写 Linux 专有执行器，编译器必须插入经审核的服务代理，或者判定 NuttX 方案不合法。
3.3 契约及目标平台描述示例（拟新增）
application
plc_chain
source
examples/plc_chain/plc_chain.c
root
plc_cycle
clock
monotonic
period_us
1000
deadline_us
500
io_semantics
immediate
observable
output.cmd
state.pid.integral
allowed_domains
linux_kthread
nuttx_task
constraint_mode
empirical
# empirical | bounded
numeric
exact_int
# fixed_point 需显式 epsilon
"platform"
"pi4b-jailhouse-nuttx"
"domains"
"linux_kthread"
"cpus"
"can_sleep"
true
"interfaces"
"sensor_proxy"
"ivshmem"
]},
"nuttx_task"
"cpus"
"can_sleep"
true
"interfaces"
"ivshmem"
]}},
"links"
:[{
"src"
"linux_kthread"
"dst"
"nuttx_task"
"transport"
"ivshmem_v1"
}],
"capsules"
"ivshmem_v1"
"ordering"
"release_acquire"
"cache_maintenance"
"required"
}}}
以上能力表是
声明性示例
，不能直接当成真实驱动可用性证明。每个能力项需要由后端适配器自检、构建链接和板级探针共同确认。
4. 形式化模型：语义合法的联合部署—融合—代码优化
4.1 图和决策变量
输入为带效应的周期阶段图
G=(V,E)。阶段v
具有周期
Tv、相位ϕv
、相对期限
、读写集
(Rv,Wv)、效果ϵv
，以及可用的目标代码变体集合
。第一版只接受单一同步周期；第二版才展开超周期或跨速率语义。
定义二元变量：
zv,d,c,o=
：阶段v
选择执行域
、上下文
、代码变体
；每个阶段恰选一个合法组合。
yuv=
：依赖边
(u,v)
两端被合并到
同一执行单元
；不等同于普通函数内联。
quv=
：边(u,v)
跨越执行域，需经明确定义的跨域数据协议。
基本选择约束：
∑(d,c,o)∈A(v)​zv,d,c,o=
,zv,d,c,o=
Safe
(v,d,c,o)=
.A(v)
由静态效果检查、接口支持和上下文安全共同限定，而不是人工给出所有合法组合。
融合一致性：
只有边的两个端点处于同域、兼容上下文且满足时间/状态语义时，才允许
yuv=
。若最终执行单元以无向“融合”关系定义，则使用连通分量，必须
显式检查传递闭包
：u–v与v–w
的合并不能绕过
u–w
的禁止约束。实施时推荐“合法集合分区”模型，而不是仅凭相邻两边各自合法就盲目合并。
对需要独占所有权的设备
，定义其可访问域
Owner
(r)
，要求所有直接 MMIO / 设备调用阶段部署在允许域，其他域只能通过声明的代理路径访问。
4.2 语义与上下文合法性谓词
将函数效果映射到有限格：
PURE
READ_STATE
WRITE_STATE
BLOCKING
ALLOC
DEVICE_IO
SLEEP
SYNC
UNKNOWN
，以及锁类型、资源所有权、共享可见性和潜在调用上下文。用跨过程不动点传播形成函数摘要：
ϵ(f)=ϵlocal(f)⊔⨆g∈
Callees
(f)ϵ(g).
对于未知函数指针、未建模外部符号或不可靠别名，使用
UNKNOWN
顶元素并
保守拒绝
危险上下文。该近似应满足“宁可拒绝可合法程序，也不把不可证明合法的程序静默接受”的设计原则。对于递归、全局动态调用表以及未有界循环，第一版不进入可证明时间模式。
融合的必要条件（初版充分条件可更严格）：
相同事务释放/周期与观测提交规则；没有必须保留的中间可观测输出；无会被改变的同步、锁、原子性或优先级语义；共享状态和外设所有权得到证明；跨域发送 / 接收完整保留需要的 happens-before；合并后线程上下文仍合法。
可观测语义定义为
ObsO(P,I)，其中I
是输入序列。对精确整数子集，要求：
∀I∈I:ObsO(P,I)=ObsO(Pθ,I).
对定点转换，仅当契约明确允许
数值误差、溢出行为与舍入规则时，才把关系替换为
∥Obs(P)−Obs(Pθ)∥≤δ
；这不是默认的 bitwise equivalence。时间可观测事件的顺序和跨周期读写关系必须另行验证。
4.3 分层时间模型（避免把“唤醒抖动”当编译器目标）
对一次激活
，定义释放
、首个实际计算开始
、最后可观测提交完成
。三个指标分开报告：
Lk=sk−rk,=fk−sk,Rk=fk−rk.
Deadline miss 的判定是
Rk>D；不是
单独的计算时间
。对于单个任务图，在可用的上下界假设下，对每一条依赖边记算子执行代价
Cv(θ,ω)
与交接代价
Huv(θ,ω)，ω
表示平台扰动情景。用 max-plus 递推：
Fv(θ,ω)=Cv(θ,ω)+maxu∈
pred
(v)(Fu(θ,ω)+Huv(θ,ω)),
输入结点以
Fsource=
初始化，最终给出图内完成上界估计
graph=maxv∈
sinks
，再加入与激活有关的调度释放成本
以及模型尚未覆盖的保守干扰项
I：(θ,ω)=(θ,ω)+graph(θ,ω)+(θ,ω).限制：
这是第一版的依赖路径模型，并非一般多核抢占调度的准确 WCRT；发生并行资源争用、优先级倒置或多任务抢占时，必须采用资源感知调度分析 / 显式事件仿真，或经验测量反馈，不能简单相加声称为上界。
交接成本分解为：
Huv={,yuv=
,+,du=dv,yuv=
,+++,du≠dv.
跨域成本必须包含共享内存一致性协议（release/acquire 与 cache clean/invalidate）、通知/轮询方式、缓存干扰和时钟误差，并根据环形缓冲区容量以及消费者能否阻塞进行可行性检查。
4.4 两套互不混淆的优化目标
经验性软实时模式（先做）：
先拒绝非法候选，再按字典序选择“违约率小 → 尾部响应时间小 → 资源/搜索成本小”。对于样本响应时间
),…,R(N)
，尾部目标可采用经验 CVaR：
α(R)=minη[η+
−α)N∑j=
Nmax(
,R(j)−η)],α∈{
0.99
0.999
先选合规的尾部等级，再优化；对于 1 kHz 短窗口，
CVaR_0.9999
不稳定，应等有足够样本和多会话后再采用。若零 miss，不能宣称风险为零；在独立伯努利近似下，
次零 miss 的单侧 95% 上限约为
，但周期样本往往相关，应按会话分块分析。
受界模式（后做）：
如果Cv、Huv
、调度 / 干扰上界
经独立工具或保守分析支持，则最优化最小可证明裕度：
maxθ∈Θlegalminω∈Ω(D−R(θ,ω)),R(θ,ω)≤D.不能
用“观测最大值”直接代替
。如果存在未经证明的界限，结论必须标记为
PROFILED_ONLY
，而非
DEADLINE_CERTIFIED
4.5 语义与时间的两个证明等级
编译器输出必须随计划携带
evidence.json
，逐项声明：
SEMANTIC_STATIC_CHECK_PASS
DIFFERENTIAL_TEST_PASS
IR_LOCAL_TV_PASS
TIMING_PROFILED
TIMING_BOUNDED
REJECTED
等证据标签。
局部 Alive2 通过不等于跨系统完整语义证明
；测试通过也不等于安全证明。若硬实时证书依赖时间上界，必须记录方法、假设与覆盖范围。
5. ECoS：效果约束引导的分层联合搜索算法
5.1 为什么不是直接 GA/BO 或直接 CP-SAT
现有 Pass 搜索维度只有代码序列，搜索数据可能仅是 IR 指令数和目标文件体积；新的变量包括域
、上下文
、合法分段
、合并关系
、LLVM 代码变体
、通信协议
和核绑定
。盲目笛卡儿积会产生大量
不可编译、不合法或时间结构等价
的候选。
采用三层：
静态合法性剪枝 → 时间代理驱动的组合求解 → 有限预算的板级反馈
。小图用 CP-SAT/整数规划求得约束模型下最优解；大图用关键路径引导的 beam search / Large Neighborhood Search。求解器算法本身不是新贡献；可发表点在静态导出的合法性约束和合成决策之间的耦合。
5.2 第一层：效果约束分析（Constraint Extraction）
CallGraph
LazyCallGraph
AliasAnalysis
MemorySSA
LoopInfo
ScalarEvolution
和目标 API 效果库，对每个函数生成
effect-summary.json
。通过 SCC / 不动点处理递归调用图；不收敛或无法解开的运行时调用集合标为未知。目标专属能力表 (
linux-kthread
nuttx-task
hrtimer-soft
hardirq
) 由适配器维护并版本化。
精简合法性规则示例：
may_sleep
禁止进入 hard IRQ；
device_read(sensor0)
仅允许在 Linux 所有者域或可验证的代理接口；
spin_lock
与 PREEMPT_RT 锁语义需按内核版本区分；
malloc
允许与否不仅看函数名，还看 allocation flags、调用上下文和最坏阻塞假设；未建模外部函数禁止出现在需要受界保证的热路径。
5.3 第二层：合法分区与候选生成（Code Synthesis）
先按阶段依赖图构造可融合候选集合，合并后重新检查整个集合的效果并集 / 可观测点，而非单边贪心。候选编译时不需要生成不同的“任意程序移植”：只在受支持的任务段之间生成
确定、版本化的 glue
：输入快照、缓冲读写、周期序列号、状态对象、错误传播、后端入口和交接协议。
部署位置与融合一起求解，因为把
Sensor
固定在 Linux 后，
Filter+PID
一起放到 NuttX 可能只需一次跨域交接；若分开放在两域就可能引入第二次跨域交接。这种
编译自动合成造成的图结构变化
是实验中必须单独证明的作用机制。
5.4 第三层：成本模型和候选反馈
成本数据来自同一板卡、内核、频率、负载情景的基准：阶段计算
Cv,d,o
；线程/周期机制
Ld,c
；同域交接
Hlocal/queue
；IVSHMEM 消息大小、轮询/中断方式对应的跨域交接
Hcross
。每项记录样本数、分位数、会话、CPU affinity、IRQ 和温度/频率配置。
结构化成本模型
（可解释、可直接消融）先筛选，再仅对 Pareto 前沿候选做实机验证；根据残差更新置信区间。在尾部极稀疏时，不把贝叶斯模型点预测当作可行性结论。所有候选由
hash(source, IR, policy, target ABI, flags, kernel, workload)
去重与缓存。
5.5 算法伪代码（接口定义，不是现有可运行代码）
Algorithm ECoS(P, T, K, O, budget B):
G, E, S   <- ExtractGraphEffectsAndState(P, O)
C         <- GenerateLegalityConstraints(G, E, S, T, K)
if C.unsatisfied_for_all(): return REJECT(C.unsat_core)
L         <- EnumerateLegalPartitions(C, max_stages, bounded_loops)
M         <- LoadCalibratedCostModel(K.hardware_fingerprint)
Q         <- SolverRank(L, M, objective=deadline_first,
use_critical_path_lb=True)
Pareto    <- []
while B > 0 and Q.not_empty():
theta <- Q.pop_best_valid_candidate()
code  <- SynthesizeAndCompile(P, theta, K)
if compile_error or ABI_check_failed: RecordReject(theta); continue
if not ValidateObservableSemantics(P, code, O):
RecordCounterexample(theta); RefineConstraints(C); continue
trace <- DeployAndMeasure(code, K, T, repeat_sessions=True)
M     <- UpdateCostModel(M, theta, trace)
Pareto <- UpdatePareto(Pareto, theta, trace, evidence)
Q     <- ReRankAndPrune(Q, M, C)
B     <- B - 1
return BestValidated(Pareto), evidence_bundle, rejected_candidates
第一版实现可用 OR-Tools CP-SAT 建模 domain/context/fusion 的离散选择；对小图（3–8 阶段）使用穷举列出“算法真实最优”作为正确性 oracle。超过 8 个阶段时，用强制合法性剪枝 + beam search，并报告求解时间和最优性差距或“无法证明最优”。
5.6 关键路径引导但不破坏可行性
对候选求最长依赖路径
，优先变更路径上的融合边、交接协议和部署位置，而不是平均对所有 LLVM 函数探索。为尚未生成的候选使用
乐观下界
剪枝：只有当
在有效数学假设下保证不会超过真实最优（最小化问题）时才可安全裁剪；经验学习的预测不能被当作可证明下界。
搜索要比较两种拓扑：
Linux-only
Linux↔NuttX
。若某外设只在 Linux 可读写，且跨域时间预算不足，算法应自动选择 Linux-only 并解释不可迁移的原因；
自动决定不跨域也属于正确输出
6. 语义保持的融合变换规则（论文最硬的技术内容）
6.1 事务语义与状态快照
每个周期事务
读输入快照
，在状态
上计算，形成输出
和下一状态
Sk+
：(Yk,Sk+
)=F(Xk,Sk).
目标程序必须保持已约定的输入采样、状态提交和外部输出顺序。
在用户未允许的情况下把
immediate
I/O 自动替换成 LET 缓冲提交，即使这样看起来时间更稳定。对允许 LET 的配置，单独定义输入采样、输出提交时刻和端到端 age 约束。
6.2 局部融合规则（最小充分条件）
A → B
可融合的初始规则：同一释放事务；无中间外部副作用 / 可观测提交；
不依赖其他可能改变顺序的并行分支；读写集冲突可被证明按原顺序执行；两者可在同一线程上下文合法执行；所有权/锁/错误处理行为一致；融合不造成超出目标上下文的动态分配或阻塞。
volatile MMIO
、原子操作、信号 / 等待、文件 I/O、未知调用、不可界循环时默认“禁止融合”或仅保留为不可拆分原子阶段，必须把原因报告到源码位置。对函数间指针别名要结合
AAResults
和程序注解，不能靠简单函数名猜测没有数据依赖。
6.3 多分支融合规则
A→B与A→C
的扇出节点应首先验证两个消费者之间的相对顺序是否可交换。若
B、C
写同一状态或有时序可观测副作用，就不能仅依据拓扑相邻关系合并。若存在实时优先级不同的周期线程，跨线程合并还可能改变抢占次序；第一版只允许
同一周期线程内部可顺序化
的融合，第二版才研究多速率 / 不同优先级的变换。
6.4 跨域胶水协议 V1
建议对
u@Linux → v@NuttX
使用固定大小、单生产者/单消费者（SPSC）环形缓冲：
{version, seq, release_id, payload_len, payload, crc(optional)}
。写端按平台的缓存一致性要求发布 payload，再使用 release 语义写序号；读端 acquire 读取序号和 payload，进行溢出 / 过期检查。设备内存属性、cache maintenance、障碍指令和 IVSHMEM 布局必须在目标 Pi4B/Jailhouse 组合上验证，不能假定普通 C11 atomics 自动覆盖跨 cell 缓存一致性。
跨域传输失败采用
显式的丢帧 / 故障状态
，不能吞掉并返回成功。每个周期只消费具有匹配
release_id
的消息；选择 drop-latest、drop-oldest、block 等策略必须由任务契约指定，并纳入端到端时序分析。绝不允许忙等消费者占满非根 cell 导致其它实时任务饥饿而仍称方案“更优”。
7. Linux / NuttX 后端的安全执行策略
上下文
允许的初始执行策略
主要限制
在本研究中的角色
Linux kthread（PREEMPT_RT）
周期唤醒后执行可睡眠业务逻辑
内核 API、锁、优先级和退出路径需审核
主目标后端
Linux HRTimer（默认 RT softirq 路径）
仅做唤醒或时间标记
不把任意可能阻塞业务代码直接运行于回调
触发器，而非默认计算载体
HRTIMER_MODE_HARD
仅极短、审核过的 hardirq-safe 操作
无睡眠、无阻塞，最严约束
初版不合成业务逻辑
NuttX task / thread
定期运行受支持业务函数
目标系统 API、优先级与栈限制
第二目标后端
跨 cell 共享内存 worker
显式生产 / 消费事务
内存屏障、可见性、溢出、超时、外设归属
第二阶段启用
PREEMPT_RT HRTimer 默认执行位置不同于普通 Linux；要按内核版本、实际初始化模式及
HRTIMER_MODE_HARD
语义做精确检查。Linux 内核官方文档是唯一有效的执行上下文参考之一，不能直接从普通 Linux 经验外推。
8. 仓库改造：具体文件、接口与代码责任
8.1 增量目录（下列文件
拟新增）
kfusion/
analysis/
TaskGraphAnalysis.cpp           # 从 AST/LLVM IR 找任务阶段与依赖
EffectSummaryAnalysis.cpp       # 跨过程 effects + UNKNOWN 传播
StateAndObservableAnalysis.cpp  # 状态、提交点、读写集合
ContextCapability.cpp          # target capability + API 语义库
optimizer/
schemas/task_contract.schema.json
schemas/effect_summary.schema.json
schemas/plan.schema.json
plans/                          # 可追溯、版本化 plan
constraint_encoder.py           # CP-SAT 约束与不合法核心
partition_generator.py          # 合法候选划分及融合边
timing_model.py                 # 分阶段与跨域成本分布
planner.py                      # 联合求解与结果选择
feedback.py                     # 板级测量反馈、预算控制
backend/synthesis/
TaskFusionPass.cpp              # 仅对已证明合法的 stage 进行变换
LinuxGlueEmitter.cpp            # kthread / trigger / state adapter
NuttXGlueEmitter.cpp            # NuttX task / state adapter
IVSHMEMGlueEmitter.cpp          # 协议固定、功能受限
validation/
semantic_diff.py                # golden trace 与目标 trace 对照
profile_trace.py                # 统一响应/延迟/期限口径
evidence_bundle.py              # 归档证据、环境、源码 hash
tests/ecos/
positive/ negative/ differential/ oracle/
docs/
ECOS__研究与实施方案.md          # 本文档放仓库的建议位置
现有文件需修改：
backend/pass/PLCFusionPass__内核化Pass.cpp
：导出可重用 remap/closure 与功能模块接口；删除正式路径下“未知调用可黑洞化”的所有入口，默认严格
error
backend/pass/PLCLowJitterPass__低抖动Pass.cpp
：把无条件
optnone
降级为实验性开关；默认不冻结实时函数。
backend/pass/KFusionWCETSchedulePass__函数级WCET.cpp
：可作为候选代码变体执行后端，但不再承担全局决策逻辑。
scripts/plc_fusion_wcet_per_function_search__函数级Association.py
：把当前指令数/字节数适应度显式命名
proxy_size
，仅作初筛，
禁止显示为 WCET
manifests/
scripts/plc_kernelize__内核化.sh
：保持向后兼容，但允许
--plan plan.json
覆盖手工
FUSE_*
部署选项。
8.2 核心中间表示：
plan.json
"schema_version"
"application"
"plc_chain"
"source_sha256"
"<sha256>"
"contract"
"period_us"
1000
"deadline_us"
500
"mode"
"empirical"
"stages"
:[{
"id"
:"A",
"symbol"
"sensor_prepare"
"domain"
"linux_kthread"
"context"
"thread"
},{
"id"
:"B",
"symbol"
"fir_step"
"domain"
"nuttx_task"
"context"
"thread"
},{
"id"
:"C",
"symbol"
"pid_step"
"domain"
"nuttx_task"
"context"
"thread"
}],
"groups"
:[["A"],["B","C"]],
"channels"
:[{
"src"
:"A",
"dst"
:"B",
"protocol"
"ivshmem_v1"
"queue"
}],
"codegen"
"opt"
"O2"
"fixed_point"
false
"legality_evidence"
"CTX_PASS"
"OWNER_PASS"
"STATE_PASS"
"timing_status"
"PROFILED_ONLY"
接口限制：
plan.json
只承载编译器已经审核的目标；后端禁止从未经校验的 JSON 直接拼接内核源码命令。每条
plan
包含工具链/内核版本、配置、输入 hash、输出 hash、ABI 版本；否则实验不可复现。
8.3 对外 CLI（
规划接口，当前不存在
）kf
analyze
--source
examples/plc_chain/plc_chain.c
--contract
experiments/plc_chain.yaml
--out
build/plc_chain/analysis.json
plan
--analysis
build/plc_chain/analysis.json
--platform
platforms/pi4b-jailhouse.json
--objective
response-p999
--budget
--out
build/plc_chain/plan.json
build
--plan
build/plc_chain/plan.json
--check-strict
validate
--plan
build/plc_chain/plan.json
--oracle
tests/oracle/plc_chain.json
evaluate
--plan
build/plc_chain/plan.json
--session
--trace
build/plc_chain/trace/
执行命令由新增 Python CLI 包装已有
plc_kernelize__内核化.sh
、目标平台工具与校验器。
kf ...
是设计目标，不是现在可以复制运行的命令。
9. 实现步骤与阶段门禁（建议 12 个开发迭代）
主要任务
产出物
严格验收条件
锁定 Pi4B/RT+Jailhouse/NuttX 构建环境；冻结仓库基线与 SHA
environment.lock.json、三阶段原生基线
相同版本、同窗口数据能复现；Pi5 单独标记
契约 S0 与测试语料库；定义输入输出观察边界
3 正例 + ≥6 负例 + task_contract schema
每个负例有唯一诊断码
TaskGraphAnalysis：阶段切分、调用图、状态边
task_graph.json
手算小图边与结果完全一致；识别不可解析调用
EffectSummaryAnalysis 和 context legality
effect_summary.json / reject.json
对 sleep/lock/MMIO/未知调用 fail-closed
单域融合 TaskFusionPass / Linux glue
linux_fused.ko、对照 trace
三阶段输出与状态一致，卸载安全
NuttX 单域合成与 ABI 签名校验
NuttX 应用产物
同源控制律功能对照，不借助黑洞策略
IVSHMEM V1 执行胶水
双域运行样例、端到端 seq trace
无错序、无静默丢帧；故障注入有诊断
微基准成本数据库（多情景）
costs.json + CI
阶段/交接/唤醒分别测，环境指纹完整
离散求解器 + 小图 exhaustive oracle
planner.py
3–8 阶段与穷举一致或有明确 gap
反馈测量、Pareto/早停、缓存
feedback.py + 证据归档
相同预算可重现选择，不污染热路径
多任务、高利用率、对照/消融矩阵
原始 trace、复现实验脚本
同板、同指标、同窗口、多会话
论文主张审核与 artifact 打包
方法学、结果图、威胁分析、复现包
每条结论可追溯到计划 / trace / SHA
以上“迭代”是按依赖排序的工程单元，不是承诺每单元恰好一周。若迭代 04 的保守效果分析不能正确拒绝负例，
禁止跳到跨域部署与“优化”阶段
10. 先做的最小可验证实验：三阶段、两域、三种融合边界
10.1 实验对象
三个阶段 A=Sensor（含可替换的 Linux-only 驱动读取）、B=FIR（可调 tap 数）、C=PID（可调三轴计算），周期
1000
µs，期限候选
D∈{
200
500
800
}µs。
这些期限值是实验扫描配置，不保证当前平台满足。
任务计算利用率分 5%、20%、50%、80% 档（通过可重复、语义有效的计算工作量调节；不要只插空转延时模拟优化空间）。
三个合法候选边界：
A|B|C
（三任务）、
AB|C
（单域时）、
A|BC
（跨域一条边）；另外用
ABC
作为在所有资源约束允许时的候选。Linux-only、NuttX-only 与 Linux→NuttX 三类布局必须由
程序效果允许才进入
。若 A 是 Linux-only MMIO，NuttX-only 应被合法性过滤。
10.2 基线矩阵
ID基线
为何必须保留
原生 Linux 用户线程 clang -O2
证明有没有“需要内核化”的场景
现有 K-Fusion 固定 Linux 内核线程 + -O2
区分新机制与旧工具链
现有 K-Fusion 固定 NuttX task + -O2
区分平台天然优势
固定域、全合法融合
证明选择性融合优于简单“全合”
固定域、不融合，优化 Pass 序列
对比 Lavinium 风格目标，而非复刻
任务图已给定的异构映射算法 + 固定代码实现
排除“只是普通 DAG mapping”
小图穷举 / 全组合验证
判断算法是否接近模型最优解
ECoS
合法性 + 自动融合 + 联合域选择 + 时间反馈
验证完整主张
公平性：
同一原始逻辑、相同可观测行为、相同算术精度、同一核资源预算；Linux root cell 与 NuttX inmate 需记录具体资源分配。手写
B3/B5
可以拥有与 ECoS 相同的候选空间，但必须报告人工准备成本。不能把 cRTOS 的
唤醒延迟
与本系统的
端到端完成响应时间
混为一个胜负指标。
10.3 原始埋点和时间轴
记录每个周期
release_id
（计划释放）、
（进入阶段 A）、
t_publish
t_receive
（最终执行器提交），以及
domain/cpu/irq/config_hash
。热路径只写固定大小的预分配 per-CPU ring buffer；离线导出，避免
printk
、磁盘 I/O 和频繁进程扫描造成测量自扰。
时钟关键风险：
Linux 与 NuttX 的
CLOCK_MONOTONIC
不应默认可直接相减。跨域
treceive−tpublish
需要经验证的共享硬件时基或时钟偏移 / 漂移标定，并对误差给出界限；若做不到，只报告每域本地阶段量与外部 GPIO/逻辑分析仪提供的端到端测量。未校准的跨域端到端数据
不允许进入论文主图
10.4 核心图表与统计
因素拆分：原生 vs 内核化、固定 vs 选择性融合、仅映射 vs 联合、固定 Pass vs 时间感知；
时间分解：release-to-start、stage compute、handoff/IVSHMEM、finish-to-deadline；
响应分布：p50、p99、p99.9、CVaR、期限违约率与其置信区间、最坏观测值（明确窗口）；
结构指标：跨域交接次数、队列大小、代码大小、栈上界估计、静态拒绝/诊断覆盖；
优化器自身：搜索候选数、合法候选率、编译次数、板级验证预算、最优性 gap / Pareto 超体积；
跨平台重复：Pi4B 作为 cRTOS/Jailhouse 对照主平台；Pi5 作为额外泛化验证，绝不跨板算“改善倍数”。
11. 验证与安全性：三层门禁
V1 静态拒绝（强制）：
未映射符号、ABI 类型尺寸不一致、
may_sleep
进入 hard IRQ、未明示外设代理、未有界循环进入 bounded 模式、未知函数指针、循环 PHI 非法 IR、共享状态竞态风险，默认构建失败并标注来源。基于过去发生的
fwrite
静默删除、
sext→zext
select
选错 callee、
pthread_t
宽度覆盖等回归持续扩展。
V2 语义差分（强制）：
同一输入种子、输入阶跃 / 饱和 / 边界 / 错误路径，比较所有已声明 observable 的周期序列与状态更新；负例和故障注入检测意外丢帧、重复处理、超期数据消费。精确整数可比较 bitwise；浮点/定点需要独立的误差契约、溢出策略与反例。
V3 局部 formal + 全链路经验（增强）：
对单函数局部优化用 Alive2/SMT translation validation，但 Alive2 当前对跨过程转换有明确限制；对特定有界融合图可探索 CBMC / bounded model checking，明确循环展开与环境假设。通过实机功能/时间实验补充证据，不把二者混作完整形式化证明。
不得允许的实现捷径：
自动黑洞外部调用、把
ioctl/pthread_setschedparam
删除后返回 0、把
optnone
当 WCET 证明、在异常路径偷偷切换到不同输出语义、用不同采样窗口的极值推性能倍数。
12. 论文贡献、消融实验及失败判据
12.1 候选贡献表述（只有验证后才可写入摘要）
Effect-guided cross-domain execution synthesis：
从 LLVM C 程序抽取可保守验证的执行上下文/共享状态约束，并自动合成 Linux/NuttX 受支持执行单元。
Semantics-constrained selective fusion：
自动寻找保持事务 / I/O 顺序的合法融合分区，而不依赖手工写好的静态 DAG 划分。
Deadline-oriented joint planner：
联合搜索执行域、融合边界和代码变体，以端到端时间目标筛选并利用板级反馈修正模型，输出可审计的证据包。
以上是否达到 RTSS/ECRTS/EMSOFT 或编译器会议水平，取决于相关研究的新颖性证明、机制深度、完整实验与复现，而不是名称或算法叠加。
12.2 消融（缺一项就无法归因）
Ablation
去掉什么
要观测什么
A0 完整 ECoS
功能合法性、deadline feasibility、搜索预算
A1 No effect pruning
不使用效果剪枝，只离线生成候选，禁止运行不安全产物
非法候选数、后续编译/检查成本
A2 No fusion search
融合边界固定
deadline miss / 交接成本
A3 No domain search
执行域固定
是否错过更优的合法布局
A4 No timing feedback
仅使用离线代理模型
模型残差与实际部署差距
A5 No critical-path ordering
通用候选顺序、相同预算
达到可行计划的编译次数
A6 No code variants
固定 -O2
代码选择相对执行结构选择的边际贡献
12.3 预注册停止条件（防止“为了论文”选择性报数）
若 S0 正例无法保持每周期 observable 一致，暂停性能比较，先修语义；
若静态效果检查对有意设计的危险用例出现假安全（false accept），停止真实内核部署；
若相同预算下完整 ECoS 与强启发式差异落在测量不确定性内，
不宣称优于基线
若跨域交接成本长期高于任何可节省的调度/计算成本，诚实报告“Linux-only 最优”与适用边界；
若无法形成可信 WCET/WCRT 上界，论文不出现“hard real-time guarantee”字样，只写实测 deadline feasibility。
13. 下一轮最小 PR 清单（可直接拆成 Issue）
feat(contract):
task_contract.schema.json
platform_caps.schema.json
、三阶段
plc_chain.c
+ golden trace；新增 6 个 fail-closed 负例。
feat(analysis):
kfusion/analysis/
EffectSummaryAnalysis
，先识别纯计算、状态读写、sleep、external、MMIO；输出 JSON + 诊断码。
feat(graph):
提取 A→B→C 调用 / 数据依赖以及状态对象，验证与人工真值图一致。
feat(fusion):
仅 Linux kthread 语义下实现合法
A|BC
AB|C
、ABC
，失败拒绝；进行差分验证。
feat(nuttx):
将相同阶段变成 NuttX task，复用
plc_*
ABI，修复行为与签名差异。
feat(ivshmem):
固定 SPSC + release_id/seq 协议、缓存同步、丢帧检测，做故障注入与跨域时钟标定。
feat(cost):
增加分阶段、交接、唤醒独立测量，记录环境指纹。
feat(planner):
CP-SAT 模型 + 3 阶段 exhaustive oracle + UNSAT 解释。
feat(feedback):
统一 trace schema、有限预算候选评估、Pareto + 缓存、逐步替换旧 proxy fitness。
test(paper):
B0–B6 对照 + A0–A6 消融、同窗口多会话脚本、自动生成 provenance 归档。
第一个真正可展示的里程碑
plc_chain.c
一份周期/期限契约，编译器自动拒绝
sensor_prepare@NuttX
的非法直接 MMIO，随后自动合成
A@Linux | (B+C)@NuttX
ABC@Linux
两种合法部署，给出选择依据，并以匹配 release_id 的端到端可观测 trace 证明两者业务语义一致。这个里程碑比再缩小 1 µs 的 HRTimer 极值更能展示编译器创新。
14. 风险清单与降级路线
风险影响
处理方案
C 动态语义与函数指针太复杂
无法自动证明安全
S0 收窄 + UNKNOWN 拒绝，报告覆盖率与假拒绝率
代码融合改变可观测时间语义
输出看似相同但周期行为改变
契约化观察点、release_id、immediate/LET 区分
NuttX/Jailhouse 共享内存一致性不明
隐式数据竞态或长尾
校验 cache maintenance、SPSC 数据协议、外部探针
时间成本非平稳、长尾样本稀少
搜索过拟合
多会话、扰动情景、置信区间和 hold-out 验证
静态 WCET 工具无法适配 AArch64/OS
不能给硬实时保证
保持 PROFILED_ONLY 并按软实时论文贡献写
现有 DAG / 编译优化研究覆盖新方法
论文新颖性不足
强化“效果推导→语义合法合成→联合决策”机制及细粒度对照
跨域总比同域慢
没有正向跨域收益
搜索器选单域，发表问题需转为自动合法性 / 适用边界
15. 论文结构建议（与实施结果一一对应）
Introduction
：跨域实时任务部署不是仅靠“低 jitter”能回答的问题；自动转换缺少执行合法性和时间结构联合决策。
Motivating Example
：单一
Sensor→FIR→PID
源码，三种合法执行结构、一个非法结构，揭示跨域通信与状态顺序的取舍。
Programming & System Model
：支持程序子集、effect lattice、domain/context、observable、release/deadline。
ECoS Design
：静态约束抽取、合法分区、跨域 glue、时序模型和联合搜索。
Correctness & Scope
：保守性条件、可能的 false reject、局部翻译验证与全链路差分。
Implementation
：LLVM NewPM、Linux kthread、NuttX task、IVSHMEM、CP-SAT、测量框架。
Evaluation
：S0 扩展语料 + 强基线 + 消融 + 多负载 + 两板与不确定性。
Related Work / Limitations
：Lavinium、DAG mapping、cRTOS、LET、Alive2 及不能声称的保证。
16. 引用与证据入口
仓库证据（已阅读，避免和未来计划混为一谈）
[R0] K-Fusion
README.md
REPO_LAYOUT.md
：当前目录和实验主线。https://github.com/1147426025lu-bot/K-Fusion
[R1]
kfusion/backend/pass/PLCFusionPass__内核化Pass.cpp
：实际内核化、remap、DCE、默认严格外部调用策略。
[R2]
kfusion/scripts/plc_fusion_wcet_per_function_search__函数级Association.py
：当前
hot_inst×10000 + obj_bytes
搜索代理。
[R3]
kfusion/docs/RESULTS_ALL__全量实验汇总.md
：现有 Pi4B 数据与不可混用指标口径。
[R4]
docs/PAPER_WALKTHROUGH__论文全貌与实验设计.md
：现有论文假设、撤回的跨指标倍数与后端事实。
对标研究（优先官方论文 / 项目）
[1] G. Magnani et al.,
Modern LLVM-based Compiler Autotuning for WCET Optimization,
IEEE RTSS
, 2025. DOI: 10.1109/RTSS66672.2025.00043. https://re.public.polimi.it/handle/11311/1298226
[2]“
Multi-Criteria Optimization of Real-Time DAGs on Heterogeneous Platforms under P-EDF,
ACM Transactions on Embedded Computing Systems
, DOI: 10.1145/3592609. https://dl.acm.org/doi/10.1145/3592609
[3] C. F. Yang et al.,
Compounded Real-Time Operating Systems for Rich Real-Time Applications,
IEEE Access
, 2025. https://ieeexplore.ieee.org/document/10870260
[4] B. Gemlau et al.,
System-level Logical Execution Time,
2021. https://inria.hal.science/hal-03125851/
[5] Alive2 Project, LLVM Optimization Translation Validation (known interprocedural limitation). https://github.com/AliveToolkit/alive2
[6] Linux Kernel Documentation,
How realtime kernels differ,
section
Timers.
https://cdn.kernel.org/doc/html/latest/core-api/real-time/differences.html
[7] Siemens Jailhouse, IVSHMEM v2 Specification (marked work-in-progress). https://github.com/siemens/jailhouse/blob/master/Documentation/ivshmem-v2-specification.md
研究诚信约束
：本文件中所有算法、性能改进与 CLI 均为
拟实现 / 待验证
，不是“已完成”。相关工作对比只是初步查新；正式投稿前必须逐篇阅读完整论文、列出方法层面的交集，并重新检索新增工作。
K-Fusion  •  2026-10-22     |
While dragging, use the arrow keys to move the item.
Press space again to drop the item in its new position, or press escape to cancel.
While dragging, use the arrow keys to move the item.
Press space again to drop the item in its new position, or press escape to cancel.
K Fusion审查 siunners
