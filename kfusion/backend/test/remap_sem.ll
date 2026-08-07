; FileCheck: plc-fusion-remap — sem_wait → plc_sem_wait

declare i32 @sem_wait(ptr)

define dso_local i32 @cold_fn(ptr noundef %sem) local_unnamed_addr {
entry:
  %r = call i32 @sem_wait(ptr noundef %sem)
  ret i32 %r
}

; CHECK-NOT: call {{.*}} @sem_wait
; CHECK: call {{.*}} @plc_sem_wait
; CHECK: declare i32 @plc_sem_wait
