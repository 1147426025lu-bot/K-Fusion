# K-C0 拒绝证据（Pi4，2026-10-27）

验证「不支持的操作必须报告拒绝理由」（fail-closed）。

## 反例：阶段里用 malloc/printf/free

```c
#include <stdio.h>
#include <stdlib.h>
int g_out;
void stage_bad(void){ int *p = malloc(4); if (p) *p = g_out; printf("x=%d\n", g_out); free(p); }
void plc_cycle(void){ stage_bad(); }
int main(void){ plc_cycle(); return 0; }
```

`stage_extract`（Pi4，clang/llvm 16.0.6）输出：

```json
{
  "entry": "plc_cycle",
  "stages": [ {"name": "stage_bad", "fn_type": "void ()"} ],
  "globals": [ {"name": "g_out", "type": "i32"} ],
  "k_c0": {
    "ok": false,
    "rejections": [
      {"function": "stage_bad", "call": "malloc", "kind": "mem",  "reason": "动态内存（K-C0 要求固定布局状态）"},
      {"function": "stage_bad", "call": "printf", "kind": "io",   "reason": "I/O/打印（增加抖动、非确定性）"},
      {"function": "stage_bad", "call": "free",   "kind": "mem",  "reason": "动态内存（K-C0 要求固定布局状态）"}
    ]
  },
  "ok": true
}
```

整链 `auto_kernelize.sh bad.c`：

```
== [3/4] 自动构造计划（经验证器） ==
ast_to_plan: K-C0 拒绝——以下构造不支持内核化（fail-closed）：
  · stage_bad() 调用 malloc()：动态内存（K-C0 要求固定布局状态）
  · stage_bad() 调用 printf()：I/O/打印（增加抖动、非确定性）
  · stage_bad() 调用 free()：动态内存（K-C0 要求固定布局状态）
  → 先改写成受支持子集（…），或改到用户态域。不要把不支持的东西硬塞进内核模块。
（中止，未产出 gen/）
```

## 正向

纯整数阶段闭环（`/tmp/ctl.c`）：`k_c0.ok=true`，正常生成源码；`globals` 只含用户状态
（`.str` 等编译器内部全局已过滤）。

## 说明

- 只查**直接外部调用**；本模块内的 helper 若也被入口调用会作为阶段一并检查。
- 循环边界、指针别名递归等细项未覆盖（Gate 2/3）。
