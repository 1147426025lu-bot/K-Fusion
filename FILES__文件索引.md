# 文件索引（FILES）

> 本文件由根目录 `FILES.md` 指向。K-Fusion 仓库统一采用 `name__中文说明.ext` 自文档化命名约定
>（双下划线后接中文描述），所有文件名均为合法 UTF-8，在 Linux / macOS / Windows(UTF-8 locale) 下均正常解析。
>
> 快照日期：**2026-10-15**（含当日新增：crtos/jailhouse_overlay_pi4 移植产物、crtos/results 实测数据、kfusion/docs/paper 论文稿与图）。

## 顶层文件

- `.gitignore`
- `FILES.md`
- `README.md`
- `README__项目说明.md`
- `REPO_LAYOUT.md`
- `REPO_LAYOUT__仓库结构.md`
- `plc-cc`

## 顶层目录（按模块）

### `.github/` —

- `.github/workflows/` （3 文件）

### `compare/` — PLCFusion vs cRTOS / KTC 的对比结果目录（结果文件按 .gitignore 不入库）

- `compare/.gitignore/` （1 文件）
- `compare/README.md/` （1 文件）

### `crtos/` — cRTOS / Jailhouse 对照实验脚手架（含 upstream Jailhouse 源码与脚本）

- `crtos/docs/` （9 文件）
- `crtos/jailhouse_overlay_pi4/` （11 文件）— **NuttX-inmate 移植产物**（cell 配置 / defconfig / 补丁 / 部署脚本 / rtlat app）
- `crtos/results/` （2 文件）— **Pi4 实测数据**（对照表 + schema 对齐的 JSON）
- `crtos/logs/` （1 文件）
- `crtos/scripts/` （33 文件，含 `pi4/`）
- `crtos/upstream/` （526 文件）

### `docs/` — 跨模块文档（开发指南、仓库结构）与已归位的参考文件

- `docs/DEV__开发指南.md/` （1 文件）
- `docs/gpio_map_info__GPIO映射.txt/` （1 文件）
- `docs/kernel_version__内核版本.txt/` （1 文件）

### `kfusion/` — PLCFusion 内核化工具链主体：LLVM Pass、运行时 ABI、示例、manifest、测量脚本

- `kfusion/CMakeLists.txt/` （1 文件）
- `kfusion/README.md/` （1 文件）
- `kfusion/backend/` （23 文件）
- `kfusion/diagnostics/` （1 文件）
- `kfusion/docs/` （78 文件，含 `paper/` 论文稿与 `figures/`）
- `kfusion/examples/` （24 文件）
- `kfusion/frontend/` （2 文件）
- `kfusion/include/` （5 文件）
- `kfusion/manifests/` （22 文件）
- `kfusion/scripts/` （152 文件）
- `kfusion/src/` （11 文件）
- `kfusion/test/` （18 文件）

### `results/` — 本地测量结果落盘目录

- `results/paper/` （1 文件）
- `results/soak/` （1 文件）
- `results/stress/` （1 文件）

### `scripts/` — 跨模块入口脚本

- `scripts/rename_github_repo__重命名GitHub仓库.sh/` （1 文件）

### `timedc/` — TimedC(KTC) 对照基线工具链

- `timedc/docs/` （1 文件）
- `timedc/scripts/` （10 文件）

## 统计

- 工作树文件总数：**950**
- `.github/`：3 文件
- `compare/`：2 文件
- `crtos/`：582 文件（docs 9 · jailhouse_overlay_pi4 11 · results 2 · logs 1 · scripts 33 · upstream 526）
- `docs/`：3 文件
- `kfusion/`：338 文件
- `results/`：3 文件
- `scripts/`：1 文件
- `timedc/`：11 文件

> 提示：本索引为静态快照，新增/重命名文件后请重新生成。`.workbuddy/`（会话记忆）不纳入统计。
