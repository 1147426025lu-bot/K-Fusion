# K-Fusion 对标 Lavinium（RTSS 2025）— 函数级 WCET Autotuning

论文：[Modern LLVM-based Compiler Autotuning for WCET Optimization](https://doi.org/10.1109/rtss66672.2025.00043)（Magnani et al., **Lavinium**）

## K-Fusion 已具备 vs Lavinium

| 能力 | Lavinium | K-Fusion（当前） |
|------|----------|------------------|
| Pass 序列 + **顺序**搜索 | ✅ Genetic / Association / Greedy | ✅ `plc_fusion_wcet_passes__Pass序列库.py` + genetic/autotune |
| **函数级**不同 pass 序列 | ✅ 核心创新 | ✅ `plc-fusion-wcet-schedule` + partition JSON |
| 热路径 optnone | ✅ | ✅ `plc-fusion-wcet-mark` |
| **静态 WCET**（LLVMTA）反馈 | ✅ 编译期内循环 | ⚠️ 板级 cyclictest probe / obj_bytes 代理 |
| MIR 级 rescheduler | ✅ | ❌ 未集成（Pi PREEMPT_RT 路径不同） |
| 处理代码量 | 单 TU C benchmark | manifest 多 TU + rt-tests cyclictest |

## 推荐工作流（Pi / cyclictest）

```bash
# 1. 生成 _pre.ll（或完整 fuse 前半段）
bash kfusion/scripts/fuse/plc_fuse__内核化主流程.sh \
  kfusion/manifests/manifest_plc_cc_hello__入门.env

# 2. 函数级分区 + schedule（Lavinium outer loop 简化版）
bash kfusion/scripts/plc_fusion_wcet_per_function__函数级WCET.sh \
  kfusion/manifests/manifest_plc_cc_hello__入门.env

# 3. 带 schedule 重新 fuse（或 ignite）
source kfusion/test/plc_cc_hello.wcet_per_function.env
FUSE_WCET_MODE=1 FUSE_PIPELINE=wcet \
  bash kfusion/scripts/fuse/plc_fuse__内核化主流程.sh \
  kfusion/manifests/manifest_plc_cc_hello__入门.env
```

## 环境变量

| 变量 | 含义 |
|------|------|
| `FUSE_WCET_PER_FUNCTION=1` | pipeline tail 改为 `plc-fusion-wcet-schedule` |
| `PLC_FUSION_WCET_SCHEDULE_FILE` | JSON：`hot_functions`, `cold_sequences`, `module_passes` |
| `FUSE_WCET_ASSOC_BUDGET` | 总搜索预算（会均分到各 cold 函数，见 `FUSE_WCET_ASSOC_BUDGET_PER_FN`） |
| `FUSE_WCET_ASSOC_MAX_FUNCS` | 最多 tuning 的 cold 函数数（按 IR 行数降序） |
| `FUSE_WCET_ASSOC_BUDGET_PER_FN` | 每个 cold 函数的 Association 预算（默认 `BUDGET/MAX_FUNCS`） |
| `FUSE_WCET_ASSOC_SKIP=1` | 仅分区，不搜索 |
| `PLC_FUSION_WCET_HOT_FUNCTIONS` | 周期体/回调 → optnone |

## 与「能处理更多代码」的关系

Lavinium 主要扩的是 **WCET 优化空间**（每函数 pass 顺序），不是 POSIX 覆盖。K-Fusion 要同时扩两类能力：

1. **WCET 轴**（本文档）— 函数级 schedule + autotune；后续可接 LLVMTA 或 aiT 作静态 fitness。
2. **内核化轴** — 扩展 `plc-fusion-remap` 规则、桩合并、多 TU；见 `backend/pass/PLCFusionPass__内核化Pass.cpp`。

## 路线图

- [x] 每 cold 函数独立 association 搜索（`plc_fusion_wcet_per_function_search__函数级Association.py`）
- [x] CI 门禁 `run_ci_wcet_per_function__函数级WCET门禁.sh`
- [ ] Greedy / Cartesian-pruned 策略（论文 §IV-C）
- [ ] LLVMTA 或 `llvm-mca` 作 CI 静态 fitness（无 insmod）
- [ ] loop bound metadata（Lavinium §III-B）供未来静态分析
