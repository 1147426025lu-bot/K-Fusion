#ifndef _ECOS_COMPAT_STDINT_H
#define _ECOS_COMPAT_STDINT_H
#include <linux/types.h>
typedef __s8 int8_t; typedef __u8 uint8_t;
typedef __s16 int16_t; typedef __u16 uint16_t;
typedef __s32 int32_t; typedef __u32 uint32_t;
typedef __s64 int64_t; typedef __u64 uint64_t;
#endif
