# PLCFusion

将用户态 C 程序编译为可在 Linux 内核模块中链接运行的 freestanding `.o`。

- **脚本索引**：[`scripts/README.md`](scripts/README.md)
- **manifest 清单**：[`manifests/README.md`](manifests/README.md)
- **示例**：[`examples/README.md`](examples/README.md)
- **plc-cc 集成脚本**：[`scripts/plc-cc__低抖动编译器`](scripts/plc-cc__低抖动编译器)

## 快速开始

```bash
# 构建 Pass + 工具
cmake -B build -G Ninja && ninja -C build

# CI 门禁（无 insmod）
bash scripts/run_ci__CI门禁.sh

# 单应用融合
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_cc_hello__入门.env
```

## 定点策略（默认 Q-only）

默认 `FUSE_FIXED_POINT=1`：IR 内 float/double 由 `plc-fusion-fixed` Pass 转为 Q16.16 / Q32.32。API 见 `include/plc_fixed__定点Q.h`。

## 与根仓库关系

本目录为原 `plc_compiler` 扁平布局的 **plcfusion 子树**；cRTOS/Jailhouse 见仓库根 `crtos/`，TimedC 见 `timedc/`。
