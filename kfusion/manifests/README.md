# manifests/ — 融合清单（SWAPPABLE）

**角色：SWAPPABLE（每应用一份，可复制替换）**

每个 `.env` 描述**测哪个应用、用哪份源码、Pass/宿主选项**。换应用 = 换 manifest，无需改融合脚本。

---

## 最小必需字段

新建 manifest 时 **至少** 设置以下变量（其余可依赖自动探测）：

| 变量 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `FUSE_NAME` | ✅ | 模块/产物前缀，字母数字下划线 | `plc_cc_hello` |
| `FUSE_DESC` | ✅ | 人类可读描述 | `"hello plc_main"` |
| `FUSE_SOURCE` | ✅ | 主 `.c` 路径（相对 `FUSE_SRC_ROOT` 或 `.`） | `examples/.../hello_plc__入门示例.c` |
| `FUSE_KTHREAD_ENTRY` **或** `FUSE_RUN_MAIN` | ✅ 其一 | 内核线程入口函数名，或 rt-tests 式 `main` | `plc_main` / `FUSE_RUN_MAIN=1` |
| `FUSE_HOST` | 推荐 | 定时宿主：`hrtimer`（周期 PLC）/ `generic` | `hrtimer` |
| `FUSE_LINK_RUNTIME_STUBS` | 推荐 | 链 POSIX 桩 | `1` |
| `FUSE_FIXED_POINT` | 推荐 | Q-only 定点（默认 CI 门禁） | `1` |

**可选但常用**：`FUSE_INCLUDE_DIRS`、`FUSE_CLANG_FLAGS`、`FUSE_EXTRA_SOURCES`（多 TU）、`FUSE_PIPELINE_POLICY`（`ast-auto` / `wcet-benchmark`）。

平台 LLC 默认由 `manifests/platform/rpi5.env` 等注入；单 manifest 可覆盖 `FUSE_LLC_ARCH` / `FUSE_LLC_ATTR`。

完整模板：[`manifest_template__清单模板.env`](manifest_template__清单模板.env)

---

## 5 分钟演示（入门 manifest）

```bash
cd kfusion

# 1. 构建 Pass（首次）
cmake -B build -G Ninja && ninja -C build

# 2. 融合 + 编 .ko（使用现成最小 manifest）
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_plc_cc_hello__入门.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_cc_hello__入门.env

# 3. 实机加载（需 PREEMPT_RT + 匹配内核头）
sudo insmod test/plc_cc_hello_mod.ko
sudo rmmod plc_cc_hello_mod   # 或 scripts/safe_rmmod_fused__安全卸载.sh
```

**仅验证编译、不 insmod**（CI 同款）：

```bash
bash scripts/run_ci__CI门禁.sh
```

---

## 清单类型

| 前缀 | 源码位置 | 示例 |
|------|----------|------|
| `manifest_cyclictest__*` | `test/rt-tests/src/cyclictest/` | 主线压测、多 TU |
| `manifest_signaltest__*` / `manifest_ptsematest__*` | `test/rt-tests/src/...` | 多线程 rt-tests |
| `manifest_plc_cc_*__*` | `examples/plc-cc__低抖动示例/` | plc-cc 六例 |
| `manifest_github_*__*` | `test/github_demo__本地demo/` | 本地 RT demo |
| `manifest_plc_multitask__*` | `examples/plc_multitask__*` | W5 STRICT LET |
| `manifest_tacle_*__*` | `test/tacle-bench/bench/...` | TACLeBench WCET |
| `manifest_template__*` | — | 新应用模板 |

## 平台参数（FIXED 默认值，可按板子覆盖）

| 文件 | 说明 |
|------|------|
| `platform/rpi5.env` | Raspberry Pi 5 |
| `platform/x86_64.env` | x86_64 交叉/本地 |
| `platform/generic.env` | 通用 fallback |

加载顺序：`platform/${PLC_PLATFORM}.env` → 你的 `manifest_*.env`（后者覆盖前者）。

## 新建应用

```bash
cp manifests/manifest_template__清单模板.env manifests/manifest_myapp.env
# 编辑 FUSE_NAME、FUSE_SOURCE、FUSE_KTHREAD_ENTRY 等（见上表「最小必需字段」）
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_myapp.env
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_myapp.env
```
