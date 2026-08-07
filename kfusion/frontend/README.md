# frontend/ — AST 工具（FIXED）

**角色：FIXED**

| 路径 | 说明 |
|------|------|
| `ast/ast_tool__AST工具.cpp` | **plc-cc 静态分析器**（`build/plc_ast`） |

## 职责（2026 演进）

| 能力 | 默认 | 说明 |
|------|------|------|
| 入口探测 | ✅ | `plc_cycle` / `plc_main` / `plc_logic` |
| 周期函数内阻塞 API | ✅ error | `nanosleep`、`pthread_join` 等 |
| 周期函数内 malloc/IO | ⚠️ warn | `--strict` 升为 error |
| JSON 报告 | ✅ | `--json=path` 或 `<source>.plc_ast.json` |
| 周期函数内浮点 / `sin`/`sqrt` 等 | ⚠️ warn | `--strict` 升为 error |
| 融合可行性 | ✅ | `fusion_eligible` / `fusion_issues`（fork/dlopen/C++/间接调用） |
| manifest 建议 | ✅ | `--suggest-manifest=path.env` 或 `plc_ast_suggest_manifest__manifest建议.sh` |
| 可调度性 v1 | ✅ | 周期内 mutex/sem、无限 loop、递归 → `sched_issues` |
| 间接调用 1 层 | ✅ | 函数指针初始化 → `indirect_resolved_count` |
| C++ extern-C 子集 | ✅ | 无 class/template/异常；`FUSE_ALLOW_CXX=1`（默认） |
| manifest fill-empty | ✅ | `FUSE_AST_APPLY_SUGGEST=1`（ast-auto 默认，fuse `[2a]`） |
| Pass 候选 | ✅ | `fusion_plan.json` → `candidates[]`（最多 3 条） |
| JSON 报告 v3 | ✅ | `call_graph` / `manifest_suggestions` / `float_anywhere` |
| 生成 `.kernel.c` | ❌ 默认关 | 仅 `--emit-kernel-c`（legacy） |

**融合集成**：
- `manifest_plc_cc_*` → `plc_fuse` 步骤 `[2c]` 自动 AST
- 全部 manifest → `plc_fusion_preflight` 内 `plc_fusion_ast_preflight__AST融合预检.sh`（`--no-shim --fusion-strict`）

**CI**：`run_plc_cc_ast_ci` + AST 矩阵 + `fusion_plan` → `plc_fusion_pipeline` + apply dry-run smoke

**POSIX 映射** 由 `PLCFusionPass` + `plc_cc_fuse_shim__融合头.h` 完成，不再在 AST 里默认改写。

## 用法

```bash
cd build && cmake .. && make plc_ast

# 分析（推荐）
./plc_ast ../examples/plc-cc__低抖动示例/gpio_blink__GPIO闪烁.c -- --analyze-only

# 融合前（plc_fuse_plc_cc 已自动调用）
bash scripts/plc_fuse_plc_cc__plc-cc融合.sh examples/plc-cc__低抖动示例/pure_logic__纯逻辑.c

# 旧式 .kernel.c（不推荐）
./plc_ast foo.c -- --emit-kernel-c
```

`PLCLowJitterPass` 会按函数名自动标记 `plc_cycle` / `plc_main` / `plc_logic`，无需 AST annotate。

详见 [REPO_LAYOUT.md](../REPO_LAYOUT.md)。
