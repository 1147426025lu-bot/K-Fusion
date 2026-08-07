# 遗留 Makefile — 推荐改用:
#   bash scripts/deploy/ignite_official_cycletest__cyclictest主线.sh   # L2 runner
#   FUSE_RUNNER_PROFILE=generic bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_cyclictest__主线压测.env
obj-m += official_cycletest_mod.o
official_cycletest_mod-objs := plc_runner_official.o plc_runtime_stubs.o official_cycletest_kernel.o
KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)
ccflags-y += -O2 -I../include
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
