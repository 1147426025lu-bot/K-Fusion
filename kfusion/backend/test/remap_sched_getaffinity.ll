; PASS: plc-fusion-remap
; FileCheck: plc-fusion-remap — sched_getaffinity → plc_sched_getaffinity

declare i32 @sched_getaffinity(i32, i64, ptr)

define i32 @read_mask(ptr %mask) {
entry:
  %r = call i32 @sched_getaffinity(i32 0, i64 8, ptr %mask)
  ret i32 %r
}

; CHECK-NOT: call {{.*}} @sched_getaffinity
; CHECK: call i32 @plc_sched_getaffinity
