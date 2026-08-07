; PASS: plc-fusion-remap
; ENV: PLC_FUSION_BLACKHOLE=1
; FileCheck: plc-fusion-remap — alloca store/load 间接调用

define internal void @fp_target() {
entry:
  ret void
}

define void @via_alloca() {
entry:
  %slot = alloca void ()*
  store void ()* @fp_target, void ()** %slot
  %fn = load void ()*, void ()** %slot
  call void %fn()
  ret void
}

; CHECK: store void ()* @fp_target
; CHECK: call void {{.*}}%fn
; CHECK-NOT: call void null
