; PASS: plc-fusion-remap
; FileCheck: plc-fusion-remap — exit → plc_exit

declare void @exit(i32)

define i32 @cold_exit(i32 %code) {
entry:
  call void @exit(i32 %code)
  unreachable
}

; CHECK-NOT: call {{.*}} @exit
; CHECK: call void @plc_exit
