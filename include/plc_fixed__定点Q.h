/*
 * plc_fixed__定点Q.h — PLCFusion Q 定点运行时 ABI（与 Pass Q16.16 / Q32.32 一致）
 *
 * 源码可显式使用 plc_fix16_t / plc_fix32_t；也可写 double/float 由 Pass 自动转换。
 * printf 边界: Pass 插入 plc_fix_to_double() 供 %f 变参（仅 runtime 桩内用 FP）。
 */
#ifndef PLC_FIXED_Q_H
#define PLC_FIXED_Q_H

#include <stdint.h>

/* Q16.16 — float 对应 */
typedef int32_t plc_fix16_t;
#define PLC_FIX16_FRAC 16
#define PLC_FIX16_ONE ((plc_fix16_t)(1 << PLC_FIX16_FRAC))

/* Q32.32 — double 对应 */
typedef int64_t plc_fix32_t;
#define PLC_FIX32_FRAC 32
#define PLC_FIX32_ONE ((plc_fix32_t)(1LL << PLC_FIX32_FRAC))

static inline plc_fix16_t plc_fix16_from_int(int v)
{
	return (plc_fix16_t)v << PLC_FIX16_FRAC;
}

static inline int plc_fix16_to_int(plc_fix16_t x)
{
	return (int)(x >> PLC_FIX16_FRAC);
}

static inline plc_fix16_t plc_fix16_mul(plc_fix16_t a, plc_fix16_t b)
{
	return (plc_fix16_t)(((int64_t)a * (int64_t)b) >> PLC_FIX16_FRAC);
}

static inline plc_fix16_t plc_fix16_div(plc_fix16_t a, plc_fix16_t b)
{
	if (!b)
		return 0;
	return (plc_fix16_t)(((int64_t)a << PLC_FIX16_FRAC) / (int64_t)b);
}

static inline int plc_fix16_cmp(plc_fix16_t a, plc_fix16_t b)
{
	if (a < b)
		return -1;
	if (a > b)
		return 1;
	return 0;
}

static inline plc_fix32_t plc_fix32_from_int(int v)
{
	return (plc_fix32_t)v << PLC_FIX32_FRAC;
}

static inline int plc_fix32_to_int(plc_fix32_t x)
{
	return (int)(x >> PLC_FIX32_FRAC);
}

static inline int plc_fix32_cmp(plc_fix32_t a, plc_fix32_t b)
{
	if (a < b)
		return -1;
	if (a > b)
		return 1;
	return 0;
}

/* 编译期常量：PLC_FIX32(28.0) → Q32.32 原始值 */
#define PLC_FIX32(v) \
	((plc_fix32_t)((int64_t)((v) * (double)(1LL << PLC_FIX32_FRAC) + 0.5)))

/* Pass / printf 边界：用户态桩侧还原 double 供 %f（内核模块不提供） */
double plc_fix_to_double(int64_t fixed, int frac_bits);

/* Pass 定点乘除：避免 kernel .o 引用 __udivti3 / i128 IR */
int64_t plc_fix_mul_i64(int64_t a, int64_t b, int frac_bits);
int64_t plc_fix_div_i64(int64_t a, int64_t b, int frac_bits);

#endif
