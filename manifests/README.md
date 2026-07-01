# manifests/ — 融合清单（SWAPPABLE）

**角色：SWAPPABLE（每应用一份，可复制替换）**

每个 `.env` 描述**测哪个应用、用哪份源码、Pass/宿主选项**。换应用 = 换 manifest，无需改融合脚本。

## 清单类型

| 前缀 | 源码位置 | 示例 |
|------|----------|------|
| `manifest_cyclictest__*` | `test/rt-tests/src/cyclictest/` | 主线压测、多 TU |
| `manifest_signaltest__*` / `manifest_ptsematest__*` | `test/rt-tests/src/...` | 多线程 rt-tests |
| `manifest_plc_cc_*__*` | `examples/plc-cc__低抖动示例/` | plc-cc 六例 |
| `manifest_github_*__*` | `test/github_demo__本地demo/` | 本地 RT demo |
| `manifest_tacle_*__*` | `test/tacle-bench/bench/...` | TACLeBench / Mälardalen WCET |
| `manifest_template__*` | — | 新应用模板 |

## 平台参数（FIXED 默认值，可按板子覆盖）

| 文件 | 说明 |
|------|------|
| `platform/rpi5.env` | Raspberry Pi 5 |
| `platform/x86_64.env` | x86_64 交叉/本地 |
| `platform/generic.env` | 通用 fallback |

## 新建应用

```bash
cp manifests/manifest_template__清单模板.env manifests/manifest_myapp.env
# 编辑 FUSE_NAME、FUSE_SOURCE、FUSE_KTHREAD_ENTRY 等
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_myapp.env
```
