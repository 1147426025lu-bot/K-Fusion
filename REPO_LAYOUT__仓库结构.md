# K-Fusion 仓库结构

本页描述 **当前主线职责**，不是文件数量快照。历史数量索引见 [FILES__文件索引.md](FILES__文件索引.md)。

```text
K-Fusion/
├── .github/workflows/        # CI、Pi 探针与夜间测量
├── kfusion/                  # 选择性内核化主工具链
├── docs/                     # 跨模块入口文档
├── results/                  # 门禁与端到端证据
├── compare/                  # 本地对比输出（大部分不入库）
├── crtos/                    # cRTOS/Jailhouse 对照
├── timedc/                   # Timed C/KTC 对照
└── scripts/                  # 仓库级维护入口
```

## `kfusion/` 主工具链

```text
kfusion/
├── frontend/
│   ├── stage_extract/        # 周期入口、阶段、状态与读写提取
│   └── ast/                  # AST 相关工具/实验
├── analysis/                 # 门禁构建、冻结信息与分析中间产物
├── contracts/                # 时间、平台、跨域与示例契约
├── ir/                       # TEIR、版本依赖、事件解释与生成物验证
├── scripts/
│   ├── ecos/                 # 候选、时间、跨域、LET/重同步决策
│   ├── paper/                # 论文表格、消融与板上闭环脚本
│   └── test/                 # 自测与门禁脚本
├── verify/                   # 与候选生成分离的 plan 验证
├── backend/
│   ├── pass/                 # LLVM 变换 Pass
│   └── synthesis/            # 用户态/内核态/跨域代码生成
├── runtime/                  # 运行时与跨域通信
├── include/                  # 运行时 ABI 与测量探针
├── src/                      # 宿主与 POSIX 桩
├── examples/                 # 示例程序（含五阶段闭环）
├── manifests/                # 传统内核化入口清单
├── docs/                     # 设计与实验记录
└── test/                     # Kbuild 与回归工作区
```

## 关键数据流

```text
source.c
  ├─ frontend/stage_extract → extract.json
  ├─ ir + contracts         → dag / TEIR / time facts
  ├─ scripts/ecos           → candidates / chosen.plan
  ├─ verify                 → verification result
  └─ backend/synthesis      → userspace / kernel / cross-domain artifacts
```

候选生成器与验证器必须分离：生成器提出方案，验证器依据契约重新检查时间窗口、域能力、状态版本和保留义务。验证通过不代表 WCET 已被形式化证明。

## 结果目录约定

- `results/gate0_baseline/`、`results/gate1_stage_extract/`：受控门禁和端到端证据。
- `kfusion/docs/pr*_*/`：具体实验的日志、JSON 和说明。
- `compare/`：本地 soak/stress/paper 输出；遵循其 `.gitignore`。
- `results/paper`、`results/soak`、`results/stress` 可能是环境相关符号链接，不应被当作可移植依赖。

## 对照代码

- `crtos/`：空间隔离基线与 Pi4 NuttX inmate 实验。
- `timedc/`：语言层时间语义基线。
- 两者用于论文对照，不是 K-Fusion 主编译流水线的依赖。

维护约定见 [docs/DEV__开发指南.md](docs/DEV__开发指南.md)，项目状态见 [docs/PROJECT_STATUS__项目状态.md](docs/PROJECT_STATUS__项目状态.md)。
