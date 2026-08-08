# scripts/ — 自动化脚本（FIXED）

**角色：FIXED**（子目录内 profile/manifest 引用为 SWAPPABLE）

## 目录一览

| 目录 | 角色 | 说明 |
|------|------|------|
| **[fuse/](fuse/README.md)** | FIXED | C→IR→Pass→`_kernel.o` 融合管线 |
| **deploy/** | FIXED + SWAPPABLE profiles | cyclictest 构建、浸泡/加压长测 |
| **tune/** | FIXED | CPU 隔离、cmdline、cgroup、背景负载 |
| **paper/** | FIXED | 论文基线/消融/用户态矩阵 |
| **platform/** | FIXED | 多平台 `PLC_PLATFORM` 验证 |
| **maintenance/** | FIXED | 结果目录清理 |

## 根目录入口（FIXED）

| 脚本 | 用途 |
|------|------|
| **`plc_kernelize__内核化.sh`** | **主入口**：fuse → check → `.ko` |
| `plc_fuse__内核化主流程.sh` | 仅 Pass 融合 → `_kernel.o`（symlink → fuse/） |
| `ignite_fused__通用ko构建.sh` | 兼容：仅 Kbuild（generic 宿主） |
| `run_ci__CI门禁.sh` | 无 insmod：Pass + manifest + 覆盖率 |
| `run_smoke_tests__冒烟测试.sh` | CI + 可选 insmod |
| `verify_fused_apps__批量验证.sh` | 13 类批量 insmod/rmmod |
| `run_ko_build__全类ko编译.sh` | 全 manifest `.ko` 编译 |
| `safe_rmmod_fused__安全卸载.sh` | 通用模块卸载 |
| `demo_compare__用户态vs融合.sh` | 用户态 vs fused 短测 |

## 兼容符号链接

`scripts/plc_fuse__*.sh`、`scripts/plc_fusion_*.sh` 指向 **`scripts/fuse/`** 内真实文件，旧文档路径仍可用。

详见 [REPO_LAYOUT.md](../REPO_LAYOUT.md)。
