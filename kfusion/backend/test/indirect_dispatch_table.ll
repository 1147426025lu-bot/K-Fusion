; PASS: plc-fusion-remap
; ENV: PLC_FUSION_BLACKHOLE=1
; FileCheck: plc-fusion-remap — 常量下标函数指针表 GEP

@dispatch = internal constant [2 x ptr] [ptr @handler_a, ptr @handler_b]

define internal void @handler_a() {
entry:
  ret void
}

define internal void @handler_b() {
entry:
  ret void
}

define void @call_slot0() {
entry:
  %gep = getelementptr [2 x ptr], ptr @dispatch, i64 0, i64 0
  %fn = load ptr, ptr %gep
  call void %fn()
  ret void
}

define void @call_slot1() {
entry:
  %gep = getelementptr [2 x ptr], ptr @dispatch, i64 0, i64 1
  %fn = load ptr, ptr %gep
  call void %fn()
  ret void
}

; CHECK-LABEL: define void @call_slot0
; CHECK: call void {{.*}}%fn
; CHECK-NOT: call void null
