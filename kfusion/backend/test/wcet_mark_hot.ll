; PASS: plc-fusion-wcet-mark
; ENV: PLC_FUSION_WCET_HOT_FUNCTIONS=hot_fn
; FileCheck: plc-fusion-wcet-mark — hot 函数 optnone

define void @hot_fn() {
entry:
  ret void
}

define void @cold_fn() {
entry:
  ret void
}

; CHECK: define {{.*}}@hot_fn{{.*}}#[[HOT:[0-9]+]]
; CHECK: define void @cold_fn() {
; CHECK: attributes #[[HOT]] = { {{.*}}optnone{{.*}}plc-wcet-hot{{.*}} }
