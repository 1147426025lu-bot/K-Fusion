obj-m += official_cycletest_mod.o
official_cycletest_mod-objs := plc_fused_host.o plc_hrtimer_core.o plc_fused_timer_host.o official_cycletest_runtime_stubs.o plc_pthread_host.o official_cycletest_kernel.o
KDIR := /root/linux-5.15.86
PWD := $(shell pwd)
ccflags-y += -O2 -I/root/K-Fusion/kfusion/include -I/root/K-Fusion/kfusion/src -DFUSED_RUN_MAIN=1 -DFUSED_MAIN_ARGS_DEFAULT="\"-p 99 -i 1000 -m -q -h 100000\"" -DFUSED_HAVE_PTHREAD_HOST=1

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
