# K-Fusion 仓库结构

```
K-Fusion/                          # 本地与 GitHub: 1147426025lu-bot/K-Fusion
├── kfusion/          FIXED       # K-Fusion 工具链（主线）
├── crtos/            REF         # cRTOS/Jailhouse 对照实验
├── timedc/           REF         # Timed C/KTC 灵感与三基线对照
├── compare/          LOCAL       # 基准数据（gitignore）
├── results/          → compare/
└── plc-cc            → kfusion/scripts/plc-cc__低抖动编译器
```

## kfusion/（主线）

```
kfusion/
├── backend/pass/     LLVM Pass（kernelization / fixed-point / low-jitter）
├── frontend/         AST 工具
├── include/          plc_* 运行时 ABI
├── src/              宿主与 POSIX 桩
├── manifests/        每应用融合清单（SWAPPABLE）
├── scripts/fuse/     融合主流程
├── scripts/deploy/   cyclictest 长测
└── test/             Kbuild 工作区
```

## crtos/ · timedc/（非主线）

- **crtos/** — Jailhouse Pi5 补丁与 enable 脚本；cRTOS 为论文 **spatial isolation 对照**
- **timedc/** — KTC 移植；Timed C 为 **语言层时间语义对照**

## compare/

本地 soak/stress/paper 输出；**仅 `compare/README.md` 与 `compare/.gitignore` 纳入 Git**（见 [`compare/README.md`](compare/README.md)）。`results/` 为符号链接，不跟踪数据。

## 根目录平台快照（非 Git 依赖）

| 文件 | 说明 |
|------|------|
| `kernel_version__内核版本.txt` | 手工记录的内核版本；脚本请用 `uname -r` |
| `gpio_map_info__GPIO映射.txt` | 某次 GPIO 映射导出，供 PLC 示例对照 |

维护与贡献约定见 [`docs/DEV__开发指南.md`](docs/DEV__开发指南.md)。
