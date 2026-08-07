# K-Fusion

**An LLVM-based Kernelization and Fusion Compiler**

将用户态 C 通过 LLVM Pass **内核化（kernelization）** 并 **融合（fusion）** 进 PREEMPT_RT 内核模块，在隔离的执行域中降低周期抖动。

> 目录结构：[REPO_LAYOUT.md](REPO_LAYOUT.md) · 文件索引：[FILES.md](FILES.md)

## 核心（本仓库主线）

| 路径 | 内容 |
|------|------|
| [`kfusion/`](kfusion/) | **K-Fusion 工具链**：Clang/LLVM Pass、manifest、`.ko` 构建与 cyclictest 验证 |
| [`compare/kfusion/`](compare/) | 本地 jitter 基准（soak/stress，gitignore） |

```bash
git clone git@github.com:1147426025lu-bot/k-fusion.git
cd k-fusion/kfusion

cmake -B build -G Ninja && ninja -C build
bash scripts/run_ci__CI门禁.sh
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_cc_hello__入门.env
```

## 对照与参考（非主线）

| 路径 | 角色 |
|------|------|
| [`crtos/`](crtos/) | **论文对照**：cRTOS / Jailhouse 分区 baseline（Pi5 实验脚本与补丁） |
| [`timedc/`](timedc/) | **灵感来源**：Timed C / KTC 周期 C 移植（三基线对比用） |

## 设计要点

1. **Split kernelization** — 保留算法 IR，POSIX → `plc_*`，链入 freestanding `kernel.o`
2. **Manifest 驱动** — 每应用一份 `.env`，可复现融合与 WCET Pass 策略
3. **隔离 + 降抖动** — CPU 隔离 / RT 优先级 + 内核态热路径（相对用户态 cyclictest）

## 环境

- 主要平台：Raspberry Pi 5 · Linux `PREEMPT_RT`（`6.12.x-jh` 等）
- 构建：clang/llvm · 内核 headers · cmake/ninja

## 许可证

K-Fusion 工具链见各源文件头；Jailhouse / KTC 等上游遵循各自许可证。
