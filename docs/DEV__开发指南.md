# K-Fusion 开发指南

面向维护者与外部贡献者：脚本约定、环境依赖、测试与后续重构方向。

## 1. 脚本体系

| 约定 | 说明 |
|------|------|
| **命名** | `动作__中文说明.sh` — 中文后缀描述用途，便于在 Pi 上浏览；**路径即文档** |
| **入口** | 优先从仓库根或 `kfusion/` 执行；需 `bash script.sh`（依赖 execute bit 时见各 README） |
| **公共库** | `kfusion/scripts/lib/repo_paths__仓库路径.sh` 提供 `REPO_ROOT`、`KFUSION_ROOT` |
| **融合核心** | `kfusion/scripts/fuse/` — 长脚本；复杂逻辑逐步迁入 `scripts/lib/*.sh` 或可测 Python（见 §5） |

### 环境依赖（最小）

| 组件 | 用途 | 检查 |
|------|------|------|
| clang/llvm ≥17 | IR 生成与 Pass | `clang --version` |
| cmake + ninja | 构建 Pass | `kfusion/build/` |
| linux headers | `.ko` 链接 | `/lib/modules/$(uname -r)/build` |
| python3 | WCET 搜索、AST 计划 | `scripts/plc_fusion_wcet_search__*.py` |
| PREEMPT_RT 内核 | 实机 jitter / insmod | Pi: `uname -v` 含 PREEMPT |

**平台变量**：`PLC_PLATFORM=rpi5|x86_64`（见 `kfusion/manifests/platform/`）。

### 对外贡献提示

- 新增脚本请保留 `__中文说明` 后缀，并在同目录或上级 `README.md` 增加一行说明。
- 避免在脚本内写死 `/home/pi/...`；使用 `repo_paths` 或 `$REPO_ROOT`。
- 文件名含 Unicode 时，文档中给出 **ASCII 复制路径**（如 `manifest_plc_cc_hello__入门.env`）。

## 2. Manifest 与演示

最小字段与 5 分钟流程见 [`kfusion/manifests/README.md`](../kfusion/manifests/README.md#最小必需字段)。

## 3. LLVM Pass（`kfusion/backend/pass/`）

| 文件 | 行数（约） | 职责 |
|------|-----------|------|
| `PLCFusionPass__内核化Pass.cpp` | ~860 | POSIX→`plc_*`、DCE、内核化 |
| `PLCFusionFixedPoint__定点Pass.cpp` | ~890 | Q-only 定点 |
| `PLCLowJitterPass__低抖动Pass.cpp` | ~100 | plc-cc 低抖动 |

**测试现状**：以 **CI 门禁**（`run_ci__CI门禁.sh`）与 manifest 矩阵为主；Pass 级 **LLVM lit/单元测试尚未覆盖**，审阅大文件时建议配合 `test/*_pre.ll` 快照。

**维护建议**（未实施，路线图）：

- 按 Pass 职责拆分为 `Remap/`、`DCE/`、`Cleanup/` 子目录
- 新增 `backend/test/` + FileCheck 用例
- 长 shell 中的 IR 分析迁入 Python（已有 `plc_fusion_wcet_search__*.py` 先例）

详见 [`kfusion/backend/README.md`](../kfusion/backend/README.md)。

## 4. 平台快照（根目录）

| 文件 | 性质 | 使用 |
|------|------|------|
| `kernel_version__内核版本.txt` | **手工快照**（论文/复现记录） | 脚本应以 `uname -r` 为准，勿依赖此文件 |
| `gpio_map_info__GPIO映射.txt` | **某次 `gpioinfo` 导出** | PLC GPIO 示例对照；硬件变更需重新导出 |

建议在实验日志中记录 `uname -r` 与 manifest 路径；CI 不读取上述 txt。

## 5. `compare/` 与 Git

| 路径 | Git 中 | 本地 |
|------|--------|------|
| `compare/README.md` | ✅ 跟踪 | 说明 |
| `compare/.gitignore` | ✅ 跟踪 | 忽略 `*` |
| `compare/kfusion/`、`paper/`、`crtos/` | ❌ 不跟踪 |  soak/stress/日志 |

`.gitignore` 规则：`compare/*` + `!compare/README.md` + `!compare/.gitignore`。  
**勿**在 `compare/` 下提交 bin/log；`results/` 仅为指向 `compare/` 的符号链接。

## 6. 文档索引

| 文档 | 内容 |
|------|------|
| [README.md](../README.md) | 项目总览 |
| [REPO_LAYOUT__仓库结构.md](../REPO_LAYOUT__仓库结构.md) | 目录角色 |
| [kfusion/scripts/README.md](../kfusion/scripts/README.md) | 脚本入口表 |
| [kfusion/manifests/README.md](../kfusion/manifests/README.md) | Manifest 字段 |
| [crtos/docs/](../crtos/docs/) | Jailhouse 对照实验（非主线） |
