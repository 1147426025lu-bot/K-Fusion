# K-Fusion Toolchain

**LLVM-based kernelization and fusion compiler** — 本目录为 K-Fusion 主代码树。

将用户态 C 编译、变换并链入 Linux 内核模块（`.ko`），在 PREEMPT_RT 上运行周期控制逻辑。

| 索引 | 路径 |
|------|------|
| 脚本 | [`scripts/README.md`](scripts/README.md) |
| Manifest | [`manifests/README.md`](manifests/README.md) |
| 示例 | [`examples/README.md`](examples/README.md) |
| plc-cc 入口 | [`scripts/plc-cc__低抖动编译器`](scripts/plc-cc__低抖动编译器) |

## 快速开始

```bash
cmake -B build -G Ninja && ninja -C build
bash scripts/run_ci__CI门禁.sh
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_cc_hello__入门.env
```

## 流水线

```
manifests/*.env → plc_fuse (LLVM Pass) → *_kernel.o → ignite_fused → *.ko
```

默认 **Q-only 定点**（`FUSE_FIXED_POINT=1`）：见 `include/plc_fixed__定点Q.h`。

## LLVM Pass

| Pass | 文件 |
|------|------|
| 内核化 | `backend/pass/PLCFusionPass__内核化Pass.cpp` |
| 定点 | `backend/pass/PLCFusionFixedPoint__定点Pass.cpp` |
| 低抖动 | `backend/pass/PLCLowJitterPass__低抖动Pass.cpp` |

（Pass 源文件名保留历史前缀；项目对外名称为 **K-Fusion**。）
