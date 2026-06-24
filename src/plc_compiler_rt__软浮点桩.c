/*
 * plc_compiler_rt__软浮点桩.c — compiler-rt 软浮点符号桩
 *
 * llc 对 fused .o 偶发引用 __adddf3 / __floatundidf 等；内核侧无 libgcc
 * 对应条目时由本文件提供。Kbuild 用 gcc 硬浮点编译，桩内 double 运算走硬件 FP。
 * 链接: ignite_fused 在 nm 检测到未定义软浮点符号时自动编入 .ko
 */
#include <linux/types.h>

#define PLC_RT_EXPORT __attribute__((used))

PLC_RT_EXPORT double __adddf3(double a, double b)
{
	return a + b;
}

PLC_RT_EXPORT double __subdf3(double a, double b)
{
	return a - b;
}

PLC_RT_EXPORT double __muldf3(double a, double b)
{
	return a * b;
}

PLC_RT_EXPORT double __divdf3(double a, double b)
{
	return b != 0.0 ? a / b : 0.0;
}

PLC_RT_EXPORT double __floatundidf(unsigned long long a)
{
	return (double)a;
}

PLC_RT_EXPORT double __floatdidf(long long a)
{
	return (double)a;
}

PLC_RT_EXPORT double __floatdisf(long long a)
{
	return (double)a;
}

PLC_RT_EXPORT double __floatunsidf(unsigned int a)
{
	return (double)a;
}

PLC_RT_EXPORT double __floatsidf(int a)
{
	return (double)a;
}

PLC_RT_EXPORT long long __fixdfdi(double a)
{
	return (long long)a;
}

PLC_RT_EXPORT unsigned long long __fixunsdfdi(double a)
{
	return (unsigned long long)a;
}

PLC_RT_EXPORT double __extendsfdf2(float a)
{
	return (double)a;
}

PLC_RT_EXPORT float __truncdfsf2(double a)
{
	return (float)a;
}

PLC_RT_EXPORT int __gtdf2(double a, double b)
{
	return a > b ? 1 : 0;
}

PLC_RT_EXPORT int __ltdf2(double a, double b)
{
	return a < b ? -1 : (a > b ? 1 : 0);
}

PLC_RT_EXPORT int __gedf2(double a, double b)
{
	return a >= b ? 1 : 0;
}

PLC_RT_EXPORT int __ledf2(double a, double b)
{
	return a <= b ? 1 : 0;
}

PLC_RT_EXPORT int __eqdf2(double a, double b)
{
	return a == b ? 1 : 0;
}

PLC_RT_EXPORT int __nedf2(double a, double b)
{
	return a != b ? 1 : 0;
}

PLC_RT_EXPORT double fma(double x, double y, double z)
{
	return x * y + z;
}

PLC_RT_EXPORT double pow(double x, double y)
{
	double r = 1.0;
	int i;

	if (y == 0.0)
		return 1.0;
	if (y < 0.0)
		return 1.0 / pow(x, -y);
	for (i = 0; i < (int)y; i++)
		r *= x;
	return r;
}
