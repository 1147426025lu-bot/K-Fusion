; PASS: plc-fusion-remap
; ENV: PLC_FUSION_BLACKHOLE=1
; FileCheck: plc-fusion-remap — qsort compar 经 Argument 解析

declare void @qsort(ptr, i64, i64, i32 (ptr, ptr)*)

define internal i32 @compar(ptr %a, ptr %b) {
entry:
  ret i32 0
}

define void @sort_buf(i32 (ptr, ptr)* %cmp, ptr %buf) {
entry:
  call void @qsort(ptr %buf, i64 4, i64 4, i32 (ptr, ptr)* %cmp)
  %v = call i32 %cmp(ptr %buf, ptr %buf)
  ret void
}

define void @main(ptr %buf) {
entry:
  call void @sort_buf(i32 (ptr, ptr)* @compar, ptr %buf)
  ret void
}

; CHECK: call {{.*}}@qsort
; CHECK: call i32 {{.*}}%cmp
; CHECK-NOT: call i32 null
