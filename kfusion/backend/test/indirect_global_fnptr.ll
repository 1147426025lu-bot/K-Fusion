; PASS: plc-fusion-remap
; ENV: PLC_FUSION_BLACKHOLE=1
; FileCheck: plc-fusion-remap — 全局函数指针 load 间接调用

@fp_target = internal dso_local constant void ()* @fp_target_impl

define internal void @fp_target_impl() {
entry:
  ret void
}

define void @plc_cycle() {
entry:
  %fn = load void ()*, void ()** @fp_target
  call void %fn()
  ret void
}

; CHECK: load {{.*}} @fp_target
; CHECK: call void {{.*}}%fn
; CHECK-NOT: call void null
