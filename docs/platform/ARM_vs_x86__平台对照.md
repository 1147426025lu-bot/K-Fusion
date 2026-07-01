# ARM（Raspberry Pi）与 x86_64 平台对照

同一仓库、同一分支；**用 `PLC_PLATFORM` 区分目标**，不要维护两份源码树。

| 维度 | ARM / Raspberry Pi | x86_64 PC |
|------|-------------------|-----------|
| **环境变量** | `PLC_PLATFORM=rpi5`（Pi 上默认） | `PLC_PLATFORM=x86_64` |
| **配置文件** | `manifests/platform/rpi5.env` | `manifests/platform/x86_64.env` |
| **LLC 架构** | `aarch64` + `-fp-armv8,-neon` | `x86-64` + `+sse4.2` |
| **内核** | `rpt-rpi-v8-rt` / PREEMPT_RT | `linux-image-rt-amd64` 等 |
| **验证状态** | 浸泡/加压/CI insmod **已测** | fuse + `kernel.o` 交叉构建 **已测**；insmod/抖动需 x86 实机 |
| **隔离 CPU 示例** | `isolcpus=3`（四核 Pi） | `isolcpus=1`（示例四核 PC） |
| **Git 标签** | `rpi5-verified` | `x86_64-supported`（同 commit，不同平台 env） |

## ARM 快速开始（Pi 默认）

```bash
git clone git@github.com:1147426025lu-bot/plc_compiler_rpi5.git
cd plc_compiler_rpi5
# Pi 上可省略 PLC_PLATFORM
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_signaltest__信号测试.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_signaltest__信号测试.env
sudo insmod test/signaltest_mod.ko
```

## x86_64 快速开始（PREEMPT_RT 实机）

```bash
git clone git@github.com:1147426025lu-bot/plc_compiler_rpi5.git
cd plc_compiler_rpi5
export PLC_PLATFORM=x86_64
export PATH="/usr/lib/llvm-19/bin:$PATH"

bash scripts/platform/quickstart_x86_64__x86快速开始.sh check   # 依赖检查
bash scripts/platform/quickstart_x86_64__x86快速开始.sh fuse    # 仅 fuse（可先在任意主机交叉编 kernel.o）
bash scripts/platform/quickstart_x86_64__x86快速开始.sh all     # fuse + ko + 短 insmod 测
```

## 无 x86 实机时（在 Pi 上交叉验证 x86 构建）

```bash
PLC_PLATFORM=x86_64 bash scripts/platform/validate_build__平台构建验证.sh
file test/platform_x86_64_*/*_kernel.o   # 应含 x86-64
```

## 常见误区

1. **在 Pi 上 `PLC_PLATFORM=x86_64` 后跑 `ignite_fused`**：会产出 **ARM 的 `.ko`**（链接本机 kernel build），仅 `kernel.o` 是 x86；**完整 x86 模块必须在 x86 主机 ignite**。
2. **manifest 里的 `FUSE_LLC_ARCH=aarch64`**：会被平台 env **覆盖**，无需为 x86 复制 manifest。
3. **抖动数字不可跨平台直接比**：仅在同平台内比较 userspace / baseline_ko / fused。

## 仓库命名说明

远程仓库名仍为 `plc_compiler_rpi5`（历史原因）；**内容已支持 x86_64**，见 `manifests/platform/x86_64.env` 与本文档。
