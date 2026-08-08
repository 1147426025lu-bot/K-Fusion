# 内核化流程（收敛后）

**一条主路径**，三个阶段可选拆分：

```bash
cd kfusion

# 全流程：Pass 融合 → 覆盖率校验 → 链接 .ko
bash scripts/plc_kernelize__内核化.sh manifests/manifest_signaltest__信号测试.env

# 仅融合 IR → kernel.o
PLC_KERNELIZE_STAGE=fuse bash scripts/plc_kernelize__内核化.sh manifests/foo.env

# 已有 kernel.o，仅链接 .ko
PLC_KERNELIZE_STAGE=ko bash scripts/plc_kernelize__内核化.sh manifests/foo.env
```

## cyclictest 宿主 profile

| Profile | 命令 | 用途 |
|---------|------|------|
| `generic`（默认，非 cyclictest 固定 generic） | `bash scripts/plc_kernelize__内核化.sh manifests/manifest_cyclictest__主线压测.env` | CI、跑完整 `main()` |
| `l2` | `FUSE_RUNNER_PROFILE=l2 bash scripts/plc_kernelize__内核化.sh manifests/manifest_cyclictest__主线压测.env` | L2 测量、浸泡 |

加载 L2 模块（build 后）：

```bash
bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh   # 默认 l2，含 insmod
IGNITE_BUILD_ONLY=1 bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh
```

## 脚本分层

```
plc_kernelize__内核化.sh          ← 用户主入口（fuse + check + ko）
├── plc_fuse__内核化主流程.sh      ← Pass 融合（IR → kernel.o）
├── plc_fuse_check / validate      ← 覆盖率 / JSON 门禁
└── lib/plc_ignite__ko构建公共.sh  ← Kbuild 链接
    └── lib/plc_kbuild__Kbuild公共.sh
```

**兼容别名**（内部转调 lib，勿在新文档中推广）：

- `ignite_fused__通用ko构建.sh` → `PLC_KERNELIZE_STAGE=ko` + generic 宿主
- `deploy/ignite_official_cycletest__cyclictest主线.sh` → l2 build + insmod

## CI

```bash
bash scripts/run_ci__CI门禁.sh          # fuse + validate（无 insmod）
bash scripts/run_ko_build__全类ko编译.sh  # 全 manifest .ko（经 ignite_fused → lib）
```

## 实验 / 论文（非主路径）

WCET autotune、sweep、deploy soak、paper 矩阵仍在 `scripts/fuse/`、`scripts/deploy/`、`scripts/paper/`——不纳入 `plc_kernelize` 默认流程。
