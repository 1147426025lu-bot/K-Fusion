; FileCheck: plc-fusion-remap — vsnprintf → plc_snprintf

declare i32 @vsnprintf(i8*, i64, i8*, ...)

define dso_local i32 @cold_fn(i8* noundef %buf, i64 noundef %sz, i8* noundef %fmt) local_unnamed_addr {
entry:
  %r = call i32 (i8*, i64, i8*, ...) @vsnprintf(i8* noundef %buf, i64 noundef %sz, i8* noundef %fmt)
  ret i32 %r
}

; CHECK-NOT: call {{.*}} @vsnprintf
; CHECK: call {{.*}} @plc_snprintf
; CHECK: declare i32 @plc_snprintf
