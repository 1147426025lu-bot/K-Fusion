; PASS: plc-fusion-remap
; FileCheck: plc-fusion-remap — malloc → plc_kmalloc (i64 size)

declare i8* @malloc(i64)

define i8* @cold_alloc(i64 %n) {
entry:
  %p = call i8* @malloc(i64 %n)
  ret i8* %p
}

; CHECK-NOT: call {{.*}} @malloc
; CHECK: call i8* @plc_kmalloc
