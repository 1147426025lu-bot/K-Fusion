; PASS: plc-fusion-remap
; FileCheck: 不参与常规 FileCheck —— 供 run_pass_unit_tests__Pass单元测试.sh 的
;            「error 策略必须拒绝产出」负向用例使用（见 run_unmapped_strict_negative）。
;
; 反例来源（2026-10-22 审查）：
;   旧版默认黑洞化，device_write(42) 只打印一条 WARN，pass 仍然成功退出，
;   调用被删除、调用者直接返回 0 —— 没执行的设备写被伪装成成功。

declare i32 @device_write(i32)

define i32 @apply_device(i32 %v) {
entry:
  %r = call i32 @device_write(i32 %v)
  ret i32 %r
}
