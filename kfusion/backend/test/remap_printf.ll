; FileCheck: plc-fusion-remap — printf → plc_printk

declare i32 @printf(i8*, ...)

define dso_local i32 @main() local_unnamed_addr {
entry:
  %call = call i32 (i8*, ...) @printf(i8* getelementptr inbounds ([3 x i8], [3 x i8]* @.str, i64 0, i64 0))
  ret i32 0
}

@.str = private unnamed_addr constant [3 x i8] c"hi\00", align 1

; CHECK-NOT: call {{.*}} @printf
; CHECK: call {{.*}} @plc_printk
; CHECK: declare i32 @plc_printk
