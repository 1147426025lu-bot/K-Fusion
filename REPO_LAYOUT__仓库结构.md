# 仓库结构说明

本文档说明 **固定代码**、**可替换测试代码** 与 **生成物** 的分工。完整文件列表见 [FILES.md](FILES.md)。

## 角色标记

| 标记 | 含义 |
|------|------|
| **FIXED** | 版本库长期维护的核心代码；改这里会直接影响编译/测量行为 |
| **SWAPPABLE** | 实验/对照用配置或样例；可复制改名替换，不影响核心管线 |
| **GENERATED** | 脚本输出；可删后重建，勿手改 |
| **UPSTREAM** | 第三方上游 clone；不更名，按需更新 |

## 顶层目录

```
plc_compiler/
├── backend/          FIXED   LLVM Pass（内核化 / 低抖动）
├── frontend/         FIXED   plc-cc AST 工具
├── include/          FIXED   plc_* 运行时 ABI 头文件
├── src/              FIXED   宿主 / 桩 / 基线（权威源码，带 __中文 后缀）
├── manifests/        SWAPPABLE  每应用融合清单 + 平台参数
├── examples/         SWAPPABLE  plc-cc 示例程序
├── scripts/          FIXED   自动化脚本（融合 / 部署 / 调优 / 论文）
├── test/             混合    Kbuild 工作区 + 上游 + 生成物
├── docs/             FIXED   文档与历史 benchmark 记录
├── results/          GENERATED  测量日志（本地，多数 gitignore）
├── diagnostics/      FIXED   故障诊断脚本
└── build/            GENERATED  CMake 产物（Pass .so、plc_ast）
```

## 数据流（固定管线）

```
manifests/*.env  (SWAPPABLE：选测哪个应用)
       ↓
scripts/fuse/plc_fuse__内核化主流程.sh  (FIXED)
       ↓
test/${FUSE_NAME}_kernel.o  (GENERATED：你的算法)
       ↓
src/*__*.c ──复制──► test/plc_*.c  (Kbuild 固定名，GENERATED 副本)
       ↓
scripts/ignite_fused__通用ko构建.sh  或  deploy/ignite_official_cycletest__*.sh
       ↓
test/${FUSE_NAME}_mod.ko  (GENERATED)
       ↓
scripts/deploy/run_* + profiles/*.env.sh  (SWAPPABLE profile)
       ↓
results/soak/ | results/stress/  (GENERATED)
```

---

## FIXED — 核心固定代码

### `backend/pass/`

| 文件 | 作用 |
|------|------|
| `PLCFusionPass__内核化Pass.cpp` | POSIX → `plc_*` 内核化（主 Pass） |
| `PLCLowJitterPass__低抖动Pass.cpp` | plc-cc 周期抖动优化 |

### `include/`

| 文件 | 作用 |
|------|------|
| `plc_abi__运行时ABI.h` | 内核运行时 ABI |
| `plc_shm__共享内存.h` | `/dev/plcfusion` 共享内存 |

### `src/` — 宿主与桩（**唯一权威源**）

| 文件 | 作用 |
|------|------|
| `plc_runtime_stubs__POSIX桩.c` | 通用 POSIX / rt-tests 桩 |
| `plc_fused_host__通用宿主.c` | 通用 fused kthread 宿主 |
| `plc_fused_timer_host__hrtimer宿主.c` | hrtimer 定时宿主 |
| `plc_pthread_host__pthread宿主.c` | pthread → kthread 宿主 |
| `plc_runner_official__cyclictest宿主.c` | cyclictest 极致抖动专用宿主 |
| `plc_compiler_rt__软浮点桩.c` | 软浮点 runtime |
| `plc_baseline_cyclic__手写基线.c` | 论文手写基线（非 Pass 路径） |
| `plot_frequency_polygon__抖动绘图.py` | 浸泡结果绘图 |

> Kbuild 要求 `test/plc_runner_official.c` 等**固定文件名**；构建时从 `src/` **复制**，勿直接改 `test/` 下副本。

### `scripts/` — 固定脚本（按子目录）

| 目录 | 角色 | 入口 |
|------|------|------|
| `scripts/fuse/` | FIXED 融合管线 | `plc_fuse__内核化主流程.sh` |
| `scripts/` 根 | FIXED 构建/CI | `ignite_fused__通用ko构建.sh`、`run_ci__CI门禁.sh` |
| `scripts/deploy/` | FIXED 测量部署 | `ignite_official_cycletest__cyclictest主线.sh`、`run_soak_cycletest__浸泡长测.sh` |
| `scripts/tune/` | FIXED RT 主机调优 | `rt_host_isolate__CPU隔离.sh` |
| `scripts/paper/` | FIXED 论文实验 | `run_paper_baseline_matrix__论文基线矩阵.sh` |
| `scripts/platform/` | FIXED 多平台 | `validate_build__平台构建验证.sh` |
| `scripts/maintenance/` | FIXED 维护 | `cleanup_results__清理结果.sh` |

根目录 `scripts/plc_fuse__*.sh` 等为指向 `scripts/fuse/` 的**兼容符号链接**。

### `frontend/`、`diagnostics/`、`CMakeLists.txt`、`plc-cc__低抖动编译器`

均为 FIXED 工具链与诊断入口。

---

## SWAPPABLE — 可替换测试 / 实验代码

### `manifests/` — 融合清单（换应用 = 换 manifest）

| 类型 | 路径 | 说明 |
|------|------|------|
| 模板 | `manifest_template__清单模板.env` | 新应用从此复制 |
| rt-tests | `manifest_cyclictest__*.env`、`manifest_signaltest__*.env` … | 指向 `test/rt-tests/` 源码 |
| plc-cc 示例 | `manifest_plc_cc_*__*.env` | 指向 `examples/plc-cc__低抖动示例/` |
| 本地 demo | `manifest_github_*__*.env` | 指向 `test/github_demo__本地demo/` |
| 平台 | `manifests/platform/*.env` | `PLC_PLATFORM`、CPU 隔离默认值 |

**替换方式**：复制 template → 改 `FUSE_SOURCE` / `FUSE_NAME` → `bash scripts/plc_fuse__内核化主流程.sh 新清单.env`

### `scripts/deploy/profiles/` — 测量 profile（换对照 = 换 profile）

| 文件 | 用途 | 备注 |
|------|------|------|
| `profile_soak_l2_best__安静浸泡.env.sh` | **推荐** L2 安静浸泡 | 论文/opt5 默认 |
| `profile_soak_l2_honest__诚实浸泡.env.sh` | resync=0 对照 | 实验用，非生产默认 |
| `profile_stress_l2__背景加压.env.sh` | hackbench 加压 | stress 长测 |
| `profile_light__默认测量.env.sh` | L3 开发快测 | 非正式 ≥15min 默认 |

**替换方式**：`PLC_PROFILE=scripts/deploy/profiles/xxx.env.sh bash run_soak_cycletest__浸泡长测.sh`

`scripts/deploy/profile_*.env.sh` 为指向 `profiles/` 的兼容符号链接。

### `examples/plc-cc__低抖动示例/`

plc-cc 路线示例 C 源（SWAPPABLE）；对应 manifest 在 `manifests/manifest_plc_cc_*`.

### `test/github_demo__本地demo/`

本地 RT demo 源码（SWAPPABLE）；manifest 中 `FUSE_SOURCE` 相对 `test/`。

### `test/rt-tests/`（UPSTREAM）

rt-tests 上游 clone；cyclictest/signaltest 等源码。**不更名**。manifest 通过 `FUSE_GIT_URL` 或已有 `test/rt-tests/src/...` 引用。

---

## GENERATED — 生成物（勿手改）

| 位置 | 内容 |
|------|------|
| `build/` | `PLCFusionPass.so`、`PLCLowJitterPass.so`、`plc_ast` |
| `test/${FUSE_NAME}_kernel.o` | 融合后的应用算法 |
| `test/${FUSE_NAME}_mod.ko` | 可 insmod 的模块 |
| `test/plc_runner_official.c` 等 | 从 `src/` 复制的 Kbuild 副本 |
| `test/*.fusion_report`、`*.validate.json`、`*.detected.env` | 融合/验证中间文件 |
| `results/soak/`、`results/stress/` | 长测 raw 日志、矩阵 CSV |

删除 `test/*_kernel.o` 与 `.ko` 后，重新 `plc_fuse` + `ignite_fused` 即可重建。

---

## 常用入口速查

```bash
# 融合 + 通用 .ko
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_signaltest__信号测试.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_signaltest__信号测试.env

# cyclictest 专用宿主 + 15min 浸泡
bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh
DURATION_MIN=15 bash scripts/deploy/run_soak_cycletest__浸泡长测.sh

# CI（无 insmod）
bash scripts/run_ci__CI门禁.sh
```

---

## 子目录 README 索引

| 目录 | 说明文件 |
|------|----------|
| `src/` | [src/README.md](src/README.md) |
| `backend/` | [backend/README.md](backend/README.md) |
| `include/` | [include/README.md](include/README.md) |
| `manifests/` | [manifests/README.md](manifests/README.md) |
| `examples/` | [examples/README.md](examples/README.md) |
| `test/` | [test/README.md](test/README.md) |
| `scripts/` | [scripts/README.md](scripts/README.md) |
| `scripts/fuse/` | [scripts/fuse/README.md](scripts/fuse/README.md) |
| `scripts/deploy/` | [scripts/deploy/README.md](scripts/deploy/README.md) |
| `results/` | [results/README.md](results/README.md) |
