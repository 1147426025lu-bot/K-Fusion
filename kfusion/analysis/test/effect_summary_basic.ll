; EffectSummaryAnalysis 回归用例（v2：执行效果 + 内存效果）
;
; 断言写在注释里，由 effect_summary_selftest__自测.sh 核对：
;   `; EXPECT: <函数> <effect>[,<effect>...]`   必须包含这些 effect
;   `; EXPECT_NOT: <函数> <effect>`             不得包含
;   `; EXPECT_READS: <函数> <区域>`              reads 里必须含该区域
;   `; EXPECT_WRITES: <函数> <区域>`             writes 里必须含该区域
;
; 区域命名：@全局名 / arg:N（第 N 个指针参数）/ heap

@g_state = global i32 0
@g_buf   = global [16 x i8] zeroinitializer

declare i32 @nanosleep(ptr, ptr)
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
declare void @llvm.memset.p0.i64(ptr, i8, i64, i1)
declare i32 @fwrite(ptr, i64, i64, ptr)
declare i32 @strlen(ptr)

; ---------- 纯粹的计算 ----------
; EXPECT: pure_math pure
define i32 @pure_math(i32 %a, i32 %b) {
entry:
  %m = mul i32 %a, %b
  %s = add i32 %m, 7
  ret i32 %s
}

; ---------- 全局状态读写 ----------
; EXPECT: state_writer state_write
; EXPECT_WRITES: state_writer @g_state
define void @state_writer(i32 %v) {
entry:
  store i32 %v, ptr @g_state
  ret void
}

; EXPECT: state_reader state_read
; EXPECT_READS: state_reader @g_state
define i32 @state_reader() {
entry:
  %v = load i32, ptr @g_state
  ret i32 %v
}

; ---------- 指针参数读写（v1 漏判，v2 必须识别）----------
; EXPECT: ptr_reader state_read
; EXPECT_READS: ptr_reader arg:0
define i32 @ptr_reader(ptr %p) {
entry:
  %v = load i32, ptr %p
  ret i32 %v
}

; EXPECT: ptr_writer state_write
; EXPECT_WRITES: ptr_writer arg:0
define void @ptr_writer(ptr %p, i32 %v) {
entry:
  store i32 %v, ptr %p
  ret void
}

; ---------- memcpy：v1 判成 pure 的典型反例 ----------
; 这个函数显然读 in、写 out，绝不能是 pure
; EXPECT: memcpy_stage state_write
; EXPECT_NOT: memcpy_stage pure
; EXPECT_READS: memcpy_stage arg:1
; EXPECT_WRITES: memcpy_stage arg:0
define void @memcpy_stage(ptr %out, ptr %in) {
entry:
  call void @llvm.memcpy.p0.p0.i64(ptr %out, ptr %in, i64 32, i1 false)
  ret void
}

; memset 只有写、没有源
; EXPECT: memset_stage state_write
; EXPECT_NOT: memset_stage pure
; EXPECT_WRITES: memset_stage arg:0
define void @memset_stage(ptr %dst) {
entry:
  call void @llvm.memset.p0.i64(ptr %dst, i8 0, i64 32, i1 false)
  ret void
}

; ---------- MMIO ----------
; EXPECT: mmio_writer mmio
define void @mmio_writer() {
entry:
  store volatile i32 1, ptr inttoptr (i64 4096 to ptr)
  ret void
}

; ---------- 睡眠 ----------
; EXPECT: sleeper sleep
define void @sleeper(ptr %req) {
entry:
  %r = call i32 @nanosleep(ptr %req, ptr null)
  ret void
}

; 跨过程：调用 sleeper 的也必须带 sleep
; EXPECT: calls_sleeper sleep
define void @calls_sleeper(ptr %req) {
entry:
  call void @sleeper(ptr %req)
  ret void
}

; 跨过程 + 参数映射：写 @g_state 的函数被调用，调用者必须带 @g_state
; EXPECT: calls_writer state_write
; EXPECT_WRITES: calls_writer @g_state
define void @calls_writer() {
entry:
  call void @state_writer(i32 5)
  ret void
}

; ---------- unknown：解析不出的间接调用 ----------
; EXPECT: indirect_caller unknown
; EXPECT_NOT: indirect_caller pure
define void @indirect_caller(ptr %fp) {
entry:
  call void %fp()
  ret void
}

; ---------- 已知 IO ----------
; EXPECT: blocking blocking_io
define void @blocking(ptr %p) {
entry:
  %r = call i32 @fwrite(ptr %p, i64 1, i64 4, ptr null)
  ret void
}

; ---------- 纯算术 libc：既不 external 也不产生读写 ----------
; EXPECT: pure_libc pure
; EXPECT_NOT: pure_libc external
define i64 @pure_libc(ptr %s) {
entry:
  %n = call i32 @strlen(ptr %s)
  %z = sext i32 %n to i64
  ret i64 %z
}
