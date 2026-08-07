# backend/ — LLVM Pass（FIXED）

**角色：FIXED**

| 路径 | 说明 |
|------|------|
| `pass/PLCFusionPass__内核化Pass.cpp` | 主 Pass：POSIX → `plc_*`，DCE，内核化 IR |
| `pass/PLCLowJitterPass__低抖动Pass.cpp` | plc-cc 低抖动 Pass |

构建：`cd build && cmake .. && make PLCFusionPass PLCLowJitterPass`

ABI 定义见 `include/plc_abi__运行时ABI.h`。
