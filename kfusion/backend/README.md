# backend/ — LLVM Pass（FIXED）

**K-Fusion** 编译器核心：LLVM NewPM Pass，将用户态 C IR 内核化并可选定点/低抖动优化。

## Pass 一览

| 文件 | 规模（约） | 说明 |
|------|-----------|------|
| `pass/PLCFusionPass__内核化Pass.cpp` | ~860 行 | 主 Pass：POSIX → `plc_*`，DCE，内核化 IR |
| `pass/PLCFusionFixedPoint__定点Pass.cpp` | ~890 行 | float/double → Q16.16 / Q32.32 |
| `pass/PLCLowJitterPass__低抖动Pass.cpp` | ~100 行 | plc-cc 周期低抖动 |

构建：

```bash
cd build && cmake .. && ninja PLCFusionPass PLCLowJitterPass
# 或：ninja -C build
```

产物：`build/PLCFusionPass.so` 等。ABI 见 `include/plc_abi__运行时ABI.h`。

## 测试与审阅

| 层级 | 现状 |
|------|------|
| **集成** | `scripts/run_ci__CI门禁.sh` — 多 manifest、无 insmod |
| **IR 快照** | `test/*_pre.ll`、`.fusion_ast.json`（多数 gitignore） |
| **Pass 单元测试** | 暂无 FileCheck/lit；大文件审阅建议对照 pre/post `.ll` |

## 维护路线图（建议）

1. **拆分** — 将 `PLCFusionPass` 按 Remap / DCE / Cleanup 分子目录，单文件 ≤400 行便于 review  
2. **lit** — `backend/test/` + `-verify-each` 或 LLVM FileCheck 小用例  
3. **Python** — WCET/AST 已有 Python；新 IR 分析优先 Python 模块 + shell 薄封装  

见仓库根 [`docs/DEV__开发指南.md`](../../docs/DEV__开发指南.md)。
