obj-m += plc_multitask_mod.o
plc_multitask_mod-objs := plc_fused_host.o plc_fused_timer_host.o plc_multitask_runtime_stubs.o plc_pthread_host.o plc_multitask_kernel.o
KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)
ccflags-y += -O2 -I/home/pi/plc_compiler/include -DFUSED_RUN_MAIN=1 -DFUSED_HAVE_PTHREAD_HOST=1
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
