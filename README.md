# K-Fusion

**An LLVM-based Kernelization and Fusion Compiler**

将用户态 C 通过 LLVM Pass **内核化（kernelization）** 并 **融合（fusion）** 进 PREEMPT_RT 内核模块，在隔离的执行域中降低周期抖动。

> 目录结构：[REPO_LAYOUT.md](REPO_LAYOUT.md)

## 克隆

```bash
git clone git@github.com:1147426025lu-bot/K-Fusion.git
cd K-Fusion
```

仓库地址：https://github.com/1147426025lu-bot/K-Fusion

**Pi 本机推荐路径**：`/home/pi/K-Fusion`（旧名 `~/plc_compiler` 为指向该目录的符号链接，可继续使用）。

## 核心（主线）

| 路径 | 内容 |
|------|------|
| [`kfusion/`](kfusion/) | **K-Fusion 工具链**：LLVM Pass、manifest、`.ko` 构建 |
| [`compare/kfusion/`](compare/) | 本地 jitter 基准（gitignore） |
| [`docs/architecture_unify__统一架构方案.md`](docs/architecture_unify__统一架构方案.md) | 统一流水线架构（AST/TEIR/STKP → Pass → 宿主 → `.ko`） |

```bash
cd kfusion
# 统一入口：plan → PLCFusionPass → stage_impl.S → 单工作线程宿主 → .ko
FORCE_CHOSEN=D@linux_kernel \
  bash scripts/kf_unify__统一流水线.sh examples/ecos/four_stage_chain__四阶段示例.c
# 旧对照宿主：HOST_MODEL=per_group
```

## 对照与参考（非主线）

| 路径 | 角色 |
|------|------|
| [`crtos/`](crtos/) | 论文对照：cRTOS / Jailhouse baseline |
| [`timedc/`](timedc/) | 灵感来源：Timed C / KTC 三基线对比 |

## 环境

- Raspberry Pi 5 · Linux PREEMPT_RT（`6.12.x-jh` 等）
- clang/llvm · cmake/ninja · 内核 headers

## 许可证

见各子目录源文件头；Jailhouse / KTC 等上游遵循各自许可证。

## 开发与贡献

- [`docs/DEV__开发指南.md`](docs/DEV__开发指南.md) — 脚本约定、环境依赖、Pass 测试与 `compare/` 说明  
- [`kfusion/manifests/README.md`](kfusion/manifests/README.md) — manifest 最小字段与 5 分钟演示  
- [`REPO_LAYOUT.md`](REPO_LAYOUT.md) — 目录角色（FIXED / SWAPPABLE / LOCAL）
