# Gate 1 — stage_extract 板上验证证据（2026-10-27）

`kfusion/frontend/stage_extract/` 已在本项目目标板 Pi4 上**真实编译并运行**通过。

## 环境

| 项 | 值 |
|----|----|
| 板 | Raspberry Pi 4（192.168.1.3），aarch64，内核 5.15.86 |
| clang | Debian clang version 16.0.6 (15~deb11u2) |
| LLVM | 16.0.6（`llvm-16-dev`，含 `llvm-c/*`） |
| 链接 | `llvm-config-16 --ldflags --libs` = `-L/usr/lib/llvm-16/lib -lLLVM-16` |

> 说明：C++ Clang Tooling 头（`clang/Tooling`）Debian 不打包；`libclang-16-dev`
> 在板上 apt 下载 ~13 KB/s 不可用。故实现走 **clang-16 出 bitcode + LLVM C API**。

## 构建

```
$ bash kfusion/frontend/stage_extract/build_on_pi.sh
built: .../build/stage_extract
```

## 运行（样例 /tmp/ctl.c：3 阶段闭环 + 3 全局）

```
$ bash run_on_pi.sh /tmp/ctl.c
{
  "tool": "stage_extract", "version": 2,
  "entry": "plc_cycle",
  "stages": [
    {"name": "stage_sample", "fn_type": "void ()"},
    {"name": "stage_pid",    "fn_type": "void ()"},
    {"name": "stage_clamp",  "fn_type": "void ()"}
  ],
  "globals": [
    {"name": "g_target", "type": "i32"},
    {"name": "g_output", "type": "i32"},
    {"name": "g_integral", "type": "i32"}
  ],
  "ok": true
}
```

## 负向

无 `plc_cycle/plc_main/plc_logic` 的源 → `"entry": null, "ok": false`，退出码 4（正确拒绝）。

## 结论

- 提取器本身**已验证可用**（入口/阶段顺序/全局/类型 均正确）。
- 这是 Gate 1 的**提取前端**，不等于 Gate 1 验收完成；仍需：
  `ast_to_plan`（JSON→plan/dag/contract）→ 接 `emit.py` → 目标构建 → 「新 C 程序不手写
  DAG 即可生成用户态/内核态源码」端到端验收。
