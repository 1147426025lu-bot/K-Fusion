# 多平台支持

通过 **`PLC_PLATFORM`** 选择目标平台，应用 manifest 与平台配置分离。

## 可选平台

| `PLC_PLATFORM` | 架构 | 典型硬件 | 状态 |
|----------------|------|----------|------|
| `rpi5`（默认 on aarch64） | AArch64 | Raspberry Pi 5 + `rpt-rpi-v8-rt` | **已验证**（浸泡/加压测） |
| `x86_64` | x86-64 | PREEMPT_RT PC / 工控机 | **构建已支持**；insmod/抖动需实机 |

**ARM 与 x86 对照表**：[ARM_vs_x86__平台对照.md](ARM_vs_x86__平台对照.md)

x86 一键脚本：`bash scripts/platform/quickstart_x86_64__x86快速开始.sh all`

配置文件：`manifests/platform/<id>.env`

## 快速用法

```bash
export PATH="/usr/local/llvm-17/bin:$PATH"

# Raspberry Pi（默认）
export PLC_PLATFORM=rpi5
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_cyclictest__主线压测.env

# x86_64 交叉构建（可在 Pi 上验证 .o 架构，无需 x86 机器）
export PLC_PLATFORM=x86_64
bash scripts/platform/validate_build__平台构建验证.sh

# x86 实机（需 PREEMPT_RT 内核 + headers）
export PLC_PLATFORM=x86_64
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_signaltest__信号测试.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_signaltest__信号测试.env
sudo insmod test/signaltest_mod.ko
```

## 无实机时可做的验证

| 项 | 命令 | 说明 |
|----|------|------|
| 平台 LLC 交叉编译 | `PLC_PLATFORM=x86_64 bash scripts/platform/validate_build__平台构建验证.sh` | `file`/`readelf` 看架构 |
| CI 门禁 | `bash scripts/run_ci__CI门禁.sh` | Pi 上 Pass + fuse |
| 可行性扫描 | `bash scripts/paper/run_paper_feasibility__论文可行性扫描.sh` | manifest 覆盖率 |
| 隔离脚本语法 | `PLC_PLATFORM=x86_64 bash scripts/tune/rt_host_isolate__CPU隔离.sh setup` | 需 root；不保证数字 |

## 实机必做（x86 首次）

见 [x86_64__设置指南.md](x86_64__设置指南.md)

## 扩展新平台

1. 复制 `manifests/platform/x86_64.env` → `manifests/platform/myboard.env`
2. 设置 `FUSE_LLC_ARCH`、`JITTER_PROBE_CPU`、`RT_NETDEV`
3. `export PLC_PLATFORM=myboard`
