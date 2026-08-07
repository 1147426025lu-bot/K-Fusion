obj-m += official_cycletest_mod.o
official_cycletest_mod-objs := plc_runner_official.o plc_runtime_stubs.o official_cycletest_kernel.o
KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)
ccflags-y += -O2 -I/home/pi/plc_compiler/plcfusion/include
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
