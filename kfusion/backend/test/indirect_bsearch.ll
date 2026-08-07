; PASS: plc-fusion-remap
; ENV: PLC_FUSION_BLACKHOLE=1
; FileCheck: plc-fusion-remap — bsearch + compar 经 Argument 解析，勿 blackhole

declare i8* @bsearch(i8*, i8*, i64, i64, i32 (i8*, i8*)*)

define internal i32 @compar(i8* %a, i8* %b) {
entry:
  ret i32 0
}

define i8* @lookup(i32 (i8*, i8*)* %fn, i8* %key, i8* %base) {
entry:
  %hit = call i8* @bsearch(i8* %key, i8* %base, i64 4, i64 8, i32 (i8*, i8*)* %fn)
  %cmp = call i32 %fn(i8* %key, i8* %base)
  ret i8* %hit
}

define i8* @main(i8* %key, i8* %base) {
entry:
  %r = call i8* @lookup(i32 (i8*, i8*)* @compar, i8* %key, i8* %base)
  ret i8* %r
}

; CHECK: call {{.*}}@bsearch
; CHECK: call i32 {{.*}}%fn
; CHECK-NOT: call i32 null
