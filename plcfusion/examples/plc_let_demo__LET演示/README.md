# plc_let_demo — 单 job STRICT LET 最小示例

验证 `include/plc_let__LET.h` + `src/runtime/plc_let__LET.c` 在 userspace 下按逻辑 release 调度。

调度器为**单线程** STRICT 模式：一个 job、固定 period，release 时刻不随执行时间漂移。

```bash
export PRJ=/home/pi/plc_compiler
gcc -O2 -I"$PRJ/include" \
  "$PRJ/examples/plc_let_demo__LET演示/plc_let_demo__主程序.c" \
  "$PRJ/src/runtime/plc_let__LET.c" \
  -o /tmp/plc_let_demo && /tmp/plc_let_demo
```

退出时应看到 `LetSummary:` 行。

多 job W5 负载见 [plc_multitask__多任务示例](../plc_multitask__多任务示例/README.md)。
