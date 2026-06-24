obj-m += github_stb_sprintf_mod.o
github_stb_sprintf_mod-objs := plc_fused_host.o github_stb_sprintf_runtime_stubs.o plc_pthread_host.o plc_compiler_rt.o github_stb_sprintf_kernel.o
KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)
ccflags-y += -O2 -I/home/pi/plc_compiler/include -DFUSED_RUN_MAIN=1 -DFUSED_HAVE_PTHREAD_HOST=1
# 允许 compiler-rt 桩使用 FP 寄存器（移除内核模块默认 -mgeneral-regs-only）
CFLAGS_REMOVE_plc_compiler_rt.o := -mgeneral-regs-only
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
