# 文件索引（FILES）

> `FILES.md` 指向本页。仓库继续采用部分 `name__中文说明.ext` 文件名，但本页不再维护易失真的文件总数快照。

## 稳定入口

- [项目说明](README.md)
- [仓库结构](REPO_LAYOUT.md)
- [文档入口](docs/README.md)
- [项目状态与证据边界](docs/PROJECT_STATUS__项目状态.md)
- [开发指南](docs/DEV__开发指南.md)

## 主线模块

| 模块 | 入口 |
|---|---|
| K-Fusion 工具链 | [`kfusion/README.md`](kfusion/README.md) |
| 阶段提取与 K-C0 | [`kfusion/frontend/stage_extract/README.md`](kfusion/frontend/stage_extract/README.md) |
| TEIR / 版本依赖 | [`kfusion/ir/`](kfusion/ir/) |
| 契约 | [`kfusion/contracts/`](kfusion/contracts/) |
| ECoS / STKP 工具 | [`kfusion/scripts/ecos/`](kfusion/scripts/ecos/) |
| 独立验证 | [`kfusion/verify/`](kfusion/verify/) |
| 后端生成 | [`kfusion/backend/synthesis/`](kfusion/backend/synthesis/) |
| 门禁结果 | [`results/`](results/) |

## 对照与实验

- [cRTOS / Jailhouse](crtos/)
- [Timed C / KTC](timedc/)
- [本地对比输出说明](compare/README.md)
- [K-Fusion 设计与实验记录](kfusion/docs/)

## 维护原则

- 文件级查找使用 GitHub 搜索或本地 `rg --files`，不在文档中硬编码总数。
- 新增顶层模块时更新 [REPO_LAYOUT.md](REPO_LAYOUT.md)。
- 新增关键研究证据时更新 [docs/README.md](docs/README.md) 和项目状态页。
- 不把本地构建目录、临时日志或大批测量输出加入本索引。
