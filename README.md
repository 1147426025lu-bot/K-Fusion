# K-Fusion

**面向实时周期 C 程序的语义—时间约束选择性内核化研究型编译工具链**

K-Fusion 从周期程序中提取阶段、状态读写和时间约束，生成候选部署方案，并在约束满足的前提下联合选择用户态/内核态、融合分组和通信方式。目标不是“尽量把代码塞进内核”，而是让每个部署决定都能追溯到语义、时间和平台证据。

> 当前主线是可运行的研究原型。仓库中的板上最大观测值属于经验测量，**不等同于 WCET 证明**。

## 当前主线

```text
周期 C 程序
  → Clang/LLVM 阶段与 Effect Summary 提取
  → TEIR + 同拍/跨拍数据版本依赖
  → 内核安全筛选（K-C0，fail-closed）与域可执行性检查
  → STKP 候选枚举、剪枝与成本选择
  → 独立 plan / 生成代码 / 事件语义验证
  → 用户态或内核态代码生成与板上测量
```

已经进入仓库主线的能力包括：

- 周期入口、阶段、全局状态和读写集合的自动提取；
- TEIR（Temporal Effect IR）与数据版本约束；
- 禁止调用、未知副作用、域能力等保守拒绝；
- 部署域、融合/通信、时间窗口和状态版本的联合候选选择；
- 小规模穷举 + 剪枝的 STKP v1，以及独立验证器；
- Linux 用户态、Linux 内核态和跨域实验代码生成；
- 五阶段闭环、反例/消融、Pi 板短测等可复核证据。

尚未宣称：通用 C 全语义等价、完整别名证明、形式化 WCET 证明，或对任意平台的自动最优部署。详见 [项目状态与证据边界](docs/PROJECT_STATUS__项目状态.md)。

## 快速开始

```bash
git clone git@github.com:1147426025lu-bot/K-Fusion.git
cd K-Fusion/kfusion
cmake -B build -G Ninja
ninja -C build
bash scripts/run_ci__CI门禁.sh
```

传统单域内核化演示：

```bash
bash scripts/plc_kernelize__内核化.sh manifests/manifest_plc_cc_hello__入门.env
```

从 C 到 ECoS/STKP 选择的入口、参数和样例见：

- [阶段提取与从 C 编译](kfusion/frontend/stage_extract/README.md)
- [脚本索引](kfusion/scripts/README.md)
- [Manifest 说明](kfusion/manifests/README.md)

## 仓库导航

| 路径 | 角色 |
|---|---|
| [`kfusion/`](kfusion/) | 主工具链：前端、IR、规划、验证、代码生成、运行时与测试 |
| [`kfusion/frontend/stage_extract/`](kfusion/frontend/stage_extract/) | 周期入口/阶段/状态/读写提取与 K-C0 拒绝 |
| [`kfusion/ir/`](kfusion/ir/) | TEIR、数据版本依赖、事件解释器、生成物检查 |
| [`kfusion/scripts/ecos/`](kfusion/scripts/ecos/) | 候选生成、时间模型、跨域与联合决策工具 |
| [`kfusion/verify/`](kfusion/verify/) | 独立 plan 验证入口 |
| [`kfusion/docs/`](kfusion/docs/) | 设计、实验结果与论文材料 |
| [`results/`](results/) | 受控门禁证据；不是所有本地测量数据的归档 |
| [`crtos/`](crtos/) | cRTOS/Jailhouse 空间隔离对照 |
| [`timedc/`](timedc/) | Timed C/KTC 语言时间语义对照 |
| [`compare/`](compare/) | 本地对比输出位置，默认不跟踪大批实验产物 |

完整结构见 [REPO_LAYOUT.md](REPO_LAYOUT.md)，文档入口见 [docs/README.md](docs/README.md)。

## 证据阅读顺序

1. [项目状态与证据边界](docs/PROJECT_STATUS__项目状态.md)
2. [开发指南](docs/DEV__开发指南.md)
3. [阶段提取与 TEIR](kfusion/frontend/stage_extract/README.md)
4. [Gate 6 消融与反例](kfusion/docs/RESULT_GATE6__消融与反例.md)
5. [STKP 板上闭环](kfusion/docs/pr4c_stkp_board/RESULT_PR4C__STKP实机闭环.md)

## 环境

主要实验环境为 Raspberry Pi 4/5、Linux PREEMPT_RT、Clang/LLVM、CMake/Ninja 与对应内核头文件。不同实验文档记录各自内核版本和调度条件；请勿跨环境直接比较数字。

## 许可证

见各子目录源文件头；Jailhouse、KTC 等上游组件遵循各自许可证。
