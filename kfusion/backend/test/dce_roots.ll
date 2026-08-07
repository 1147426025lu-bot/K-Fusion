; PASS: plc-fusion-dce
; ENV: PLC_FUSION_ROOTS=main
; FileCheck: plc-fusion-dce — 不可达 cold 删除

define i32 @main() {
entry:
  ret i32 0
}

define void @dead_helper() {
entry:
  ret void
}

; CHECK: define i32 @main
; CHECK-NOT: define void @dead_helper
