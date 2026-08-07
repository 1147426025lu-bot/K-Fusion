/* plc_cc_fuse_shim__融合头.h — plc-cc 融合/分析用最小 libc 声明（不含系统 #include） */
#ifndef PLC_CC_FUSE_SHIM_H
#define PLC_CC_FUSE_SHIM_H

/* 供 Clang 解析与 PLCFusionPass POSIX→plc_* 映射；勿在此 #include <stdio.h> */
extern void *malloc(unsigned long size);
extern void *calloc(unsigned long n, unsigned long size);
extern void free(void *ptr);
extern int printf(const char *fmt, ...);

#endif
