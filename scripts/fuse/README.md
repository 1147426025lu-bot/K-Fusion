# scripts/fuse/ — 融合管线（FIXED）

**角色：FIXED**

PLCFusion 核心：manifest → Clang IR → Pass → LLC → `_kernel.o` → 桩合并。

## 主入口

```bash
bash scripts/plc_fuse__内核化主流程.sh manifests/manifest_cyclictest__主线压测.env
# 或（等价）
bash scripts/fuse/plc_fuse__内核化主流程.sh manifests/manifest_cyclictest__主线压测.env
```

## 脚本分组

| 分组 | 脚本 |
|------|------|
| **主流程** | `plc_fuse__内核化主流程.sh` |
| **公共库** | `plc_fusion_common__公共库.sh`（被 source，勿直接执行） |
| **探测/桩** | `plc_fuse_detect__*`、`plc_fuse_merge_stubs__*`、`plc_fuse_stub_loop__*` |
| **IR/Pass** | `plc_fusion_analyze_ir__*`、`plc_fusion_pipeline__*`、`plc_fusion_ast_plan__*`、`plc_fusion_preflight__*` |
| **验证/报告** | `plc_fuse_validate__*`、`plc_fuse_report__*`、`plc_fuse_check__*`、`plc_fuse_fusion_report__*` |
| **WCET 实验** | `plc_fusion_wcet_sweep__*`、`plc_fusion_wcet_probe__*`、`plc_fusion_wcet_autotune__*` |
| **ko 链接** | `plc_fusion_modpost_fix__*`、`plc_fusion_remap_hints__*` |
| **plc-cc** | `plc_fuse_plc_cc__plc-cc融合.sh` |

## 输出（GENERATED，写入 `test/`）

`${FUSE_NAME}_kernel.o`、`.detected.env`、`.pipeline.log`、`.fusion_plan.json`、`.fusion_report` 等。

## AST → Pass 方案（`FUSE_AST_PLAN=1` 默认）

`plc_fusion_ast_plan__*` 读 AST JSON → `fusion_plan.json` → `plc_fusion_pipeline__*` 选 profile。

## Pass 策略（`FUSE_PIPELINE_POLICY`）

| 策略 | 适用 | 行为 |
|------|------|------|
| `ast-auto` | plc-cc、github demo | AST + pipeline 一次定案；不自动 WCET 搜索 |
| `wcet-benchmark` | cyclictest、signaltest、ptsematest | 首次融合同上；可选手动/CI 跑 autotune |
| `fixed` | `FUSE_PIPELINE` 非 auto | 关闭 AST profile 建议 |

解析：`plc_fusion_pipeline_policy__Pass策略解析.sh`（`plc_fuse` 开头自动 source）  
矩阵：`bash scripts/run_ci_pipeline_policy__CI_Pass策略矩阵.sh`

WCET 搜索（仅 wcet-benchmark，默认不自动跑）：

```bash
WCET_AUTOTUNE_SKIP_INSMOD=1 bash scripts/fuse/plc_fusion_wcet_autotune__WCET自动调优.sh manifests/manifest_cyclictest__主线压测.env
```

下一步：`bash scripts/ignite_fused__通用ko构建.sh <同一 manifest>`
