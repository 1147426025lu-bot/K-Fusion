# K-Fusion

**An LLVM-based Kernelization and Fusion Compiler**

将用户态 C 通过 LLVM Pass **内核化（kernelization）** 并 **融合（fusion）** 进 PREEMPT_RT 内核模块，在隔离的执行域中降低周期抖动。

> 目录结构：[REPO_LAYOUT.md](REPO_LAYOUT.md)

## 克隆

```bash
# 仓库在 GitHub 重命名为 k-fusion 后：
git clone git@github.com:1147426025lu-bot/k-fusion.git
cd k-fusion

# 若尚未重命名，暂用旧 URL（内容已是最新 main）：
# git clone git@github.com:1147426025lu-bot/plc_compiler_rpi5.git
```

重命名：`bash scripts/rename_github_repo__重命名GitHub仓库.sh`  
或 GitHub → Settings → Repository name → `k-fusion`

## 核心（主线）

| 路径 | 内容 |
|------|------|
| [`kfusion/`](kfusion/) | **K-Fusion 工具链**：LLVM Pass、manifest、`.ko` 构建 |
| [`compare/kfusion/`](compare/) | 本地 jitter 基准（gitignore） |

```bash
cd kfusion
cmake -B build -G Ninja && ninja -C build
bash scripts/run_ci__CI门禁.sh
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_cc_hello__入门.env
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
