; PASS: plc-fusion-remap
; FileCheck: plc-fusion-remap — malloc → plc_kmalloc (i64 size)

declare i8* @malloc(i64)

define i8* @cold_alloc(i64 %n) {
entry:
  %p = call i8* @malloc(i64 %n)
  ret i8* %p
}

; CHECK-NOT: call {{.*}}@malloc
; 指针类型随 LLVM 版本变化（16+ 为 opaque ptr），只断言重映射结果
; CHECK: call {{.*}}@plc_kmalloc
