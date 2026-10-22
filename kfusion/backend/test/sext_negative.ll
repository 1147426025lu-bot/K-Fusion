; PASS: plc-fusion-fixed
; ENV: PLC_FUSION_FIXED_POINT=1
; FileCheck: 定点 Pass 必须保持 sext 的有符号语义
;
; 反例来源（2026-10-22 审查，已在 LLVM 18.1.3 复现）：
;   adjustIntWidth() 扩宽时无条件用 CreateZExt，
;   于是 `sext i32 %x to i64` 被改写成 `zext i32 %x to i64`。
;   输入 -1 时原程序得 -1，转换后得 4294967295 —— 纯整数、无浮点也会错。

define i64 @sext_keep(i32 %x) {
entry:
  %y = sext i32 %x to i64
  ret i64 %y
}

define i64 @zext_keep(i32 %x) {
entry:
  %y = zext i32 %x to i64
  ret i64 %y
}

; CHECK-LABEL: define i64 @sext_keep
; CHECK: sext i32 %x to i64
; CHECK-NOT: zext i32 %x to i64
; CHECK-LABEL: define i64 @zext_keep
; CHECK: zext i32 %x to i64
; CHECK-NOT: sext i32 %x to i64
