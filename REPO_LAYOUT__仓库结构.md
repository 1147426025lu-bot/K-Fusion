# K-Fusion 仓库结构

```
k-fusion/                          # GitHub: 1147426025lu-bot/k-fusion
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

本地 soak/stress/paper 输出；默认不提交 Git。`results/soak` 等符号链接指向此处。
