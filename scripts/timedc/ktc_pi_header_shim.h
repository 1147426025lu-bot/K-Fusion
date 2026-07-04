/* Shims so KTC/CIL 1.7.3 can parse modern aarch64 glibc/uapi headers on Pi. */
#ifndef KTC_PI_HEADER_SHIM_H
#define KTC_PI_HEADER_SHIM_H

typedef unsigned long long ktc_uint128_t;
#define __uint128_t ktc_uint128_t
#define __int128 long long
#define __signed__ signed

#endif
