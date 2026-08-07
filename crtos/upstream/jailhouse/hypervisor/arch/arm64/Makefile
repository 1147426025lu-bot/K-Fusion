#
# Jailhouse AArch64 support
#
# Copyright (C) 2015 Huawei Technologies Duesseldorf GmbH
#
# Authors:
#  Antonios Motakis <antonios.motakis@huawei.com>
#
# This work is licensed under the terms of the GNU GPL, version 2.  See
# the COPYING file in the top-level directory.
#

LINUXINCLUDE += -I$(src)/arch/arm-common/include

KBUILD_CFLAGS += -march=armv8-a+nofp

# Pi5 47-bit VA: hypervisor must link at canonical vmalloc address
ifneq ($(JH_PI5_MINIMAL_DRIVER),)
KBUILD_CFLAGS += -DJAILHOUSE_PI5_CANONICAL_VA
KBUILD_CFLAGS += -DJAILHOUSE_VA_BITS=$(JH_VA_BITS)
KBUILD_AFLAGS += -DJAILHOUSE_VA_BITS=$(JH_VA_BITS)
# Kernel 6.12+ Pi5 has CONFIG_ARM64_BTI — indirect call to arch_entry needs BTI landing pads
KBUILD_CFLAGS += -mbranch-protection=bti
ifneq ($(JH_ARCH_ENTRY_SMOKE),)
ifeq ($(JH_ARCH_ENTRY_SMOKE),early)
KBUILD_CFLAGS += -DJAILHOUSE_PI5_ARCH_ENTRY_SMOKE_EARLY
KBUILD_AFLAGS += -DJAILHOUSE_PI5_ARCH_ENTRY_SMOKE_EARLY
else
KBUILD_CFLAGS += -DJAILHOUSE_PI5_ARCH_ENTRY_SMOKE
KBUILD_AFLAGS += -DJAILHOUSE_PI5_ARCH_ENTRY_SMOKE
endif
endif
endif
