/*
 * PLCFusionFixedPoint — LLVM IR 浮点 → Q 定点（Q16.16 / Q32.32）
 *
 * 替代 float_kill / compiler-rt：保留语义，仅使用整数运算。
 * 环境: PLC_FUSION_FIXED_POINT (默认 1)
 */
#ifndef PLC_FUSION_FIXED_POINT_PASS_H
#define PLC_FUSION_FIXED_POINT_PASS_H

namespace llvm {
class Module;
bool runFixedPointConvert(Module &M);
} // namespace llvm

#endif
