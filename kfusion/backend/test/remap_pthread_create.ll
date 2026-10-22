; PASS: plc-fusion-remap
; FileCheck: plc-fusion-remap — pthread_create 提升 worker 入口

%struct.pthread_t = type { i64 }

declare i32 @pthread_create(%struct.pthread_t*, i8*, i8* (i8*)*, i8*)

define internal i8* @worker(i8* %arg) {
entry:
  ret i8* null
}

define i32 @spawn() {
entry:
  %t = alloca %struct.pthread_t, align 8
  %rc = call i32 @pthread_create(%struct.pthread_t* %t, i8* null, i8* (i8*)* @worker, i8* null)
  ret i32 %rc
}

; worker 的定义在模块里出现在调用之前，所以先 CHECK 定义再 CHECK 调用（旧顺序永远匹配不到）
; CHECK: define {{.*}}@worker
; CHECK: call i32 @plc_pthread_create
