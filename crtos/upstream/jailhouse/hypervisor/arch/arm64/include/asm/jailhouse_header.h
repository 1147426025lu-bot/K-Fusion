/*
 * Jailhouse, a Linux-based partitioning hypervisor
 *
 * Copyright (C) Siemens AG, 2017
 *
 * Authors:
 *  Henning Schild <henning.schild@siemens.com>
 *
 * This work is licensed under the terms of the GNU GPL, version 2.  See
 * the COPYING file in the top-level directory.
 */

/*
 * Pi5 canonical VA: TTBR1-canonical slot in vmalloc, above KIMAGE_VADDR
 * (MODULES_END == kernel image base — not usable).  Fixed +64 MiB offset.
 * JAILHOUSE_VA_BITS from kernel build (JH_VA_BITS in Kbuild).
 */
#if defined(JAILHOUSE_PI5_CANONICAL_VA)
#ifndef JAILHOUSE_VA_BITS
#define JAILHOUSE_VA_BITS		47
#endif
#if JAILHOUSE_VA_BITS == 39
#define JAILHOUSE_BASE			__JH_CONST_UL(0xffffffc0c0000000)
#elif JAILHOUSE_VA_BITS == 47
#define JAILHOUSE_BASE			__JH_CONST_UL(0xffffc000c0000000)
#elif JAILHOUSE_VA_BITS == 48
#define JAILHOUSE_BASE			__JH_CONST_UL(0xffff8000c0000000)
#else
#error "JAILHOUSE_PI5_CANONICAL_VA: unsupported JAILHOUSE_VA_BITS"
#endif
#else
#define JAILHOUSE_BASE			__JH_CONST_UL(0xffffc0200000)
#endif
