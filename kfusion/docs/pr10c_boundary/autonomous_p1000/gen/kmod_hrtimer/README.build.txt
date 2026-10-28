five_stage_loop —— 构建说明（必须在内核侧构建）

1) 先产阶段实现的内核化对象（复用现有 LLVM 内核化链路，不重写 POSIX 映射）：

   bash kfusion/scripts/plc_kernelize__内核化.sh <manifest>
   # 产物形如 kfusion/test/<FUSE_NAME>_kernel.o
   cp kfusion/test/<FUSE_NAME>_kernel.o stage_impl.o

2) 构建模块（需要目标侧的 KDIR，且 vermagic 必须与运行内核一致）：

   make -C /lib/modules/$(uname -r)/build M=$(pwd) modules
   sudo insmod five_stage_loop.ko period_us=1000
   dmesg | tail
   cat /sys/module/five_stage_loop/parameters/periods
   cat /sys/module/five_stage_loop/parameters/rsf_probe
   cat /sys/module/five_stage_loop/parameters/exec_probe
   # 取“同拍”快照：先冻结，再读，读完解冻（sysfs 分开读会跨周期）
   echo 1 | sudo tee /sys/module/five_stage_loop/parameters/frozen >/dev/null
   # 观察变量（int32_t 全局量）:
   cat /sys/module/five_stage_loop/parameters/g_in
   cat /sys/module/five_stage_loop/parameters/g_integ
   cat /sys/module/five_stage_loop/parameters/g_out
   cat /sys/module/five_stage_loop/parameters/g_seq
   echo 0 | sudo tee /sys/module/five_stage_loop/parameters/frozen >/dev/null
   sudo rmmod five_stage_loop

注意：
  · 本机（Windows）没有编译器/内核头，只生成了源码；不要在步骤 1/2 之外
    声称"已经构建"。
  · 阶段若含 sleep/blocking_io，生成器已拒绝进 ring-0（内核态会睡死整核）。
  · 相位由 hrtimer 的首次到期时间给出，之后按 period_us 周期前推；
    组间相位差保留在 ecos_phase_ns[] 里，与方案一致。
