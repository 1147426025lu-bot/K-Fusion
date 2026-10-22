# 文件索引（FILES）

> 本文件由根目录 `FILES.md` 指向。K-Fusion 仓库统一采用 `name__中文说明.ext` 自文档化命名约定
>（双下划线后接中文描述），所有文件名均为合法 UTF-8，在 Linux / macOS / Windows(UTF-8 locale) 下均正常解析。
>
> 快照日期：**2026-10-22**（含当日新增：`crtos/results/figures` 两域 vs 单域架构对照 SVG、`crtos/scripts/sync_pi4`、`kfusion/docs/paper` 论文稿与图）。

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

- `crtos/docs/` （12 文件）
- `crtos/jailhouse_overlay_pi4/` （24 文件）— **NuttX-inmate 移植产物**（cell 配置 / defconfig / 补丁 / 部署脚本 / rtlat + xshm app）
- `crtos/results/` （109 文件，含 `figures/`）— **Pi4 实测数据 + 架构对照图**（对照表 / schema JSON / 2 张「两域 vs 单域」SVG）
- `crtos/logs/` （1 文件）
- `crtos/scripts/` （51 文件，含 `pi4/`）
- `crtos/upstream/` （526 文件）

### `docs/` — 跨模块文档（开发指南、仓库结构）与已归位的参考文件

- `docs/DEV__开发指南.md/` （1 文件）
- `docs/gpio_map_info__GPIO映射.txt/` （1 文件）
- `docs/kernel_version__内核版本.txt/` （1 文件）
- `docs/PAPER_WALKTHROUGH__论文全貌与实验设计.md/` （1 文件）— **项目全貌说明书**（论文赌什么/实验设计四原则/E1–E12 矩阵）

### `kfusion/` — PLCFusion 内核化工具链主体：LLVM Pass、运行时 ABI、示例、manifest、测量脚本

- `kfusion/CMakeLists.txt/` （1 文件）
- `kfusion/README.md/` （1 文件）
- `kfusion/backend/` （23 文件）
- `kfusion/diagnostics/` （1 文件）
- `kfusion/docs/` （85 文件，含 `paper/` 论文稿与 `figures/`）
- `kfusion/examples/` （24 文件）
- `kfusion/frontend/` （2 文件）
- `kfusion/include/` （5 文件）
- `kfusion/manifests/` （22 文件）
- `kfusion/scripts/` （157 文件）
- `kfusion/src/` （13 文件）
- `kfusion/test/` （27 文件）

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

- 工作树文件总数：**1117**
- `.github/`：3 文件
- `compare/`：2 文件
- `crtos/`：723 文件（docs 12 · jailhouse_overlay_pi4 24 · results 109 · logs 1 · scripts 51 · upstream 526）
- `docs/`：4 文件
- `kfusion/`：361 文件
- `results/`：3 文件
- `scripts/`：1 文件
- `timedc/`：11 文件

> 提示：本索引为静态快照，新增/重命名文件后请重新生成。`.workbuddy/`（会话记忆）不纳入统计。
