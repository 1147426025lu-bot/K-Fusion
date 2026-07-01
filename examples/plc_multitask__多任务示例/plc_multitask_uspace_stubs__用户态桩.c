/* 仅用户态 gcc 演示链接用；不参与 PLCFusion 融合 */
#include <stdint.h>

int64_t plc_fix_mul_i64(int64_t a, int64_t b, int frac_bits)
{
	return (int64_t)(((__int128)a * (__int128)b) >> frac_bits);
}

int64_t plc_fix_div_i64(int64_t a, int64_t b, int frac_bits)
{
	if (!b)
		return 0;
	return (int64_t)(((__int128)a << frac_bits) / (__int128)b);
}
