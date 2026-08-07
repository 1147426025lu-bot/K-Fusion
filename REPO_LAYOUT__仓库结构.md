# 仓库结构说明

2026-07 起仓库按 **子系统分顶栏**，原扁平目录已迁入对应子树。

## 角色标记

| 标记 | 含义 |
|------|------|
| **FIXED** | 长期维护的核心代码 |
| **SWAPPABLE** | 实验 manifest / profile / 示例 |
| **GENERATED** | 脚本输出，可删后重建 |
| **UPSTREAM** | 第三方源码（Jailhouse、NuttX、KTC 等） |
| **LOCAL** | 本地基准数据，gitignore |

## 顶层

```
plc_compiler/
├── plcfusion/     FIXED+SWAPPABLE   C→.ko 融合工具链
├── crtos/         FIXED+UPSTREAM     Jailhouse Pi5 + cRTOS 参考
├── timedc/        UPSTREAM+FIXED    TimedC / KTC
├── compare/       LOCAL              论文/浸泡/加压结果（不提交）
├── results/       → compare/         符号链接
├── plc-cc         → plcfusion/scripts/plc-cc__低抖动编译器
└── .github/       CI 工作流
```

## plcfusion/

原仓库根目录的 `backend/`、`frontend/`、`include/`、`src/`、`manifests/`、`examples/`、`scripts/`、`test/`、`docs/` 等均已移入此处。

```
plcfusion/
├── backend/pass/       LLVM Pass（内核化 / 定点 / 低抖动）
├── frontend/           plc-cc AST 工具
├── include/            plc_* ABI
├── src/                宿主与 POSIX 桩
├── manifests/          每应用融合清单
├── scripts/fuse/       融合主流程
├── scripts/deploy/     cyclictest 长测
└── test/               Kbuild 工作区
```

数据流：`manifests/*.env` → `scripts/fuse/` → `test/*_kernel.o` → `ignite_fused` → `.ko` → `compare/plcfusion/soak|stress/`。

## crtos/

```
crtos/
├── upstream/jailhouse/     Jailhouse 源码 + **Pi5 补丁**（driver、entry.S、configs）
├── upstream/fixstars/      cRTOS / NuttX / loader 参考 clone
├── scripts/                编内核、enable 分步测试、netconsole、scratch 读取
├── docs/                   Pi5 Jailhouse 实验文档
├── cache/                  本地 KDIR / 内核构建缓存（gitignore）
└── logs/                   enable 抓包日志（gitignore）
```

Pi5 Jailhouse 实验入口：`crtos/docs/README__Jailhouse树莓派5实验.md`。

## timedc/

```
timedc/
├── upstream/ktc/         KTC 上游
├── scripts/                编译 / 安装 / 论文周期 demo
└── docs/
```

## compare/ 与 results/

| 路径 | 内容 |
|------|------|
| `compare/paper/` | 论文三基线图表与 JSON |
| `compare/plcfusion/soak|stress/` | cyclictest 长测 |
| `compare/crtos/` | Jailhouse enable 调试 log |

根目录 `results/` 仅作快捷符号链接；**compare 内大文件默认 gitignore**。
