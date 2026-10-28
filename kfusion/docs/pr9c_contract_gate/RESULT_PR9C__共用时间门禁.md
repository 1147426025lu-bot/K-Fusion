# PR-9C 共用时间/版本门禁

经验值，不是 WCET。LET 合法不等于可部署。

STKP、LET 与 codegen 共用 `contract_gate.py`：TEIR 数据版本 + 无 LET 窗口/周期检查。`deploy_ok` 要求两者都过。

## W3 chain_window

- STKP 入选 `L[s1+s2]|L[s3+s4]` 用户态；`contract_gate deploy_ok=true`（钉扎 s1 采样、s3 提交）。
- LET `full_fuse` 因 `E_PIN_EARLY_COMMIT` 非法。
- LET `full_fuse_let` 分析合法、`deployable=false`；流水线 **不生成** `let_host`。
- 部署源仍是 STKP `plan.json`，不是 LET 全融合旁路。

`test_stkp`：37 通过。规划证据，不是板级 WCET。
