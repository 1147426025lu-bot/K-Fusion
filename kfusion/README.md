# K-Fusion Toolchain

本目录是 K-Fusion 主代码树：从实时周期 C 程序提取语义与时间事实，筛选可执行域，联合选择部署/融合/通信方案，并生成可验证的用户态或内核态实现。

## 两条入口

### 选择性内核化研究流水线（主研究线）

```text
C → stage_extract → TEIR / version deps
  → candidate generation + STKP v1
  → independent verification
  → synthesis + board measurement
```

- [阶段提取、K-C0 与从 C 编译](frontend/stage_extract/README.md)
- [ECoS 工具](scripts/ecos/)
- [TEIR 与版本依赖](ir/)
- [独立验证](verify/)
- [实验与设计文档](docs/)

### 传统单域内核化流水线（兼容入口）

```bash
cmake -B build -G Ninja
ninja -C build
bash scripts/run_ci__CI门禁.sh
bash scripts/plc_kernelize__内核化.sh manifests/manifest_plc_cc_hello__入门.env
```

该入口通过 LLVM Pass 和 manifest 构建 Linux 内核模块，仍用于兼容、回归和对照；它不等同于 STKP 的联合候选选择。

## 模块职责

| 路径 | 职责 |
|---|---|
| `frontend/stage_extract/` | 入口/阶段/状态/读写提取，K-C0 fail-closed |
| `contracts/` | 时间、平台和跨域约束 |
| `ir/` | TEIR、版本依赖、事件语义与生成物验证 |
| `scripts/ecos/` | 候选、时间模型、LET/重同步与跨域工具 |
| `scripts/ecos_plan_joint.py` | STKP 联合选择与小规模穷举/剪枝 |
| `verify/` | 独立 plan 复核 |
| `backend/synthesis/` | 用户态、Linux 内核态、NuttX/跨域生成 |
| `runtime/` | 运行时和通信支持 |
| `scripts/test/` | 自测与门禁 |
| `docs/` | 设计、实验说明和机器可读证据 |

## 证据边界

- K-C0/域检查采用保守拒绝：无法证明安全时不生成可部署内核产物。
- 板上 `max`、p50、miss 等是指定环境下的经验结果，不是 WCET 证明。
- 验证器检查仓库当前编码的契约，不宣称覆盖通用 C 的全部语义。
- 结果必须连同源码版本、平台、调度、周期、成本输入和契约一起解释。

仓库级导航与当前状态见 [../README.md](../README.md) 和 [../docs/PROJECT_STATUS__项目状态.md](../docs/PROJECT_STATUS__项目状态.md)。
