# test/ — Kbuild 工作区（混合）

**角色：混合（FIXED 骨架 + SWAPPABLE 样例 + GENERATED 产物）**

融合与 `.ko` 构建的**工作目录**。不要与 `src/` 混淆：`src/` 是权威源，`test/` 是构建时复制 + 输出。

## FIXED（勿删、勿更名）

| 路径 | 说明 |
|------|------|
| `Makefile` | 内核模块 Kbuild 入口 |
| `plc_runner_official.c` 等 | Kbuild **固定文件名**（构建时从 `src/*__*.c` 复制） |

## SWAPPABLE（可换测试源码）

| 路径 | 说明 |
|------|------|
| `github_demo__本地demo/` | 本地 RT demo（`rt_periodic__*.c` 等）；manifest `manifest_github_*` 引用 |
| `rt-tests/` | **UPSTREAM** rt-tests clone；cyclictest/signaltest 源码 |

## GENERATED（可删后重建）

| 模式 | 说明 |
|------|------|
| `${FUSE_NAME}_kernel.o` | `plc_fuse` 融合产物 |
| `${FUSE_NAME}_mod.ko` | 可 insmod 模块 |
| `*.fusion_report`、`*.validate.json`、`*.detected.env` | 融合/验证中间文件 |
| `platform_*_manifest_*/` | 平台 CI 输出 |
| `*.o`、`.*.cmd`、`Module.symvers` | Kbuild 中间文件 |

## 重建

```bash
# 仅重建 cyclictest 主线 .ko
bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh

# 任意 manifest
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_foo.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_foo.env
```
