; PASS: plc-fusion-remap
; FileCheck: select 的两个候选不同 → 不允许直接化（否则条件分支被抹掉）
;
; 反例来源（2026-10-22 审查）：
;   resolveIndirectCalleeImpl() 解析 Select 时只返回 true 值，
;   remapDirect() 随后把整个调用改写成 `call void @plc_kfree(%p)`。
;   条件为假时本应调用 release_device，结果也会 free。

declare void @free(ptr)
declare void @release_device(ptr)

define void @maybe_release(ptr %p, i1 %use_free) {
entry:
  %fn = select i1 %use_free, ptr @free, ptr @release_device
  call void %fn(ptr %p)
  ret void
}

; 同一函数出现在两个分支上 → 可以安全直接化，这一条用于确认没有"矫枉过正"
define void @always_free(ptr %p, i1 %cond) {
entry:
  %fn = select i1 %cond, ptr @free, ptr @free
  call void %fn(ptr %p)
  ret void
}

; CHECK-LABEL: define void @maybe_release
; CHECK: select i1 %use_free, ptr @free, ptr @release_device
; CHECK: call void %fn(ptr %p)
; CHECK-NOT: call void @plc_kfree
; CHECK-LABEL: define void @always_free
; CHECK: call void @plc_kfree(ptr %p)
