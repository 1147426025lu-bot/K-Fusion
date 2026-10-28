# PR-10A：PR-9B 的一键上板复现（含内核会话状态读回）

日期：2026-10-28　机器：Pi4（Pi OS，内核 5.15.86 aarch64）　提交：`578055f`

## 一条命令

```bash
python3 kfusion/scripts/pr9b_pid_codegen__端到端.py --cycles 40 \
  --stage-cost kfusion/docs/pr9b_pid_codegen/cost_stage_pin.json \
  --ioctl-cost '{"value":0.3,"evidence":"pinned"}'
```

依次完成：生成（Clang AST → TEIR → STKP → 验证 → UKU 码生成）
→ 编译（内核模块 + 用户态驱动）→ gold 40 拍 → insmod → mixed 40 拍 → rmmod
→ 判定 → 写 `report.json` + `logs/{gold,mixed,pipeline}.log`。
`--no-board` 只跑到生成。

## 结果（`verdict=pass`）

| 项 | 值 |
|---|---|
| 选型 | `STKP:L[stage_sample]\|L[stage_pid]\|L[stage_sink]`，`domain=mixed` |
| digest | gold = mixed = `e9d8167c` |
| 末拍 `g_out` | gold = mixed = `-581` |
| **内核会话状态** | **`g_integ` 读回 `-420`，与 gold 一致** |
| 超期 / 序号错配 | `0/40` / `0/40` |

## 这次真正补上的洞

1. **状态延续性有证据了。** 之前 digest 明确不含 `g_integ`（gold 侧没有内核那份），
   40 拍 `g_out` 一致只说明输出轨迹对，没说明内核里那份状态对。现在 UKU 头文件加了
   `KFUSION_UKU_STATE_IOC`（`_IOWR('K',2)`），驱动在 mixed 模式跑完后读回内核会话状态，
   gold 侧读同名全局，两者比对。**读不回来（`readback=0`）直接判失败**，不许默认通过。

2. **复现需要的输入被入库了。** STKP 的入选方案完全由成本输入决定：不给成本就退回全用户态，
   `mixed` 根本不会出现——这就是「有日志但复现不出来」的原因。现在
   `cost_stage_pin.json` 把这份输入固定下来并写明「改它会改入选方案」。

3. **指纹钉死来源**：git commit/branch/dirty 文件数、`uname`/内核版本/架构、
   clang-16 / opt-16 / gcc 版本、源码 `sha256`、轮数，全部写进 `report.json`。

## 过程中发现并修掉的两个真问题

- **PR-9D 的初值门禁对真正的会话状态是失效的**：`meta.session_init` 只遍历了
  `persistent_state`，而 self-carry 状态（如 `g_integ`）只出现在**节点级**
  `node["persistent"]`。结果 `session_init` 恒为空，门禁要么漏声明、要么把合法方案拒掉。
  修于 `ac411c0`，现在 `session_init = {g_seq: zero, g_integ: zero}`。
- **修掉上面那条之前，`mixed` 在本机任何成本组合下都选不出来**（试了 9 组：
  同域成本 5 种 + 分域成本 4 种，全部 `chosen=B / linux_userspace`）。修完后同一批成本里
  分域口径立刻选出 `mixed`——说明之前的「选不出内核会话」不是成本问题，是门禁把合法方案判死了。

## 诚实边界

- **digest 与 PR-9B 原报告（`6f40d786`）不同**，但末拍 `g_out` 相同（`-581`）。
  差异来自 digest 参与的变量集合/采集口径，尚未逐项对齐。**本报告不宣称复现了同一个 digest**，
  只宣称「同一份源、同一套选型、输出与状态一致」。
- 40 拍一致只说明这台机器这次一致：**不是 WCET，不是形式化等价**。
- `cost_stage_pin.json` 是固定输入，不是本轮重测值。真实测出来的成本会改变入选方案，
  那是 PR-10B（论文级矩阵）要处理的事。
- `gen/` 是构建产物，已加入 `.gitignore`，不入库。
