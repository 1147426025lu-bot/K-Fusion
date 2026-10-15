/*
 * Jailhouse inmate cell configuration: NuttX (sRTOS) on Raspberry Pi 4
 *
 * 基于 siemens/jailhouse configs/arm64/rpi4-inmate-demo.c 修改，仅两处差异：
 *   1. RAM 区域改为预留池 0x10000000 起 128MB（guest 虚拟地址 0，与上游
 *      NuttX CONFIG_RAM_START=0 / CONFIG_LOAD_BASE=0x480000 对齐；
 *      `jailhouse cell load rpi4-nuttx nuttx.bin -a 0x10480000`）。
 *   2. cell 名改为 "rpi4-nuttx"。
 * 其余（mini-UART 0xfe215040 / GIC 0xff841000 / IVSHMEM / 通信区）与
 * 已实测可运行的 inmate-demo 完全一致，最小化验证风险。
 *
 * 注意：与 rpi4-linux-demo.cell 共用 0x10000000 区域——两者不可同时创建。
 * CPU1 分配与 RT 测量核一致。
 *
 * 编译（在 Pi 或主机，需 jailhouse 头文件与 gcc）：
 *   gcc -c rpi4-nuttx.c -o rpi4-nuttx.o
 *   objcopy -O binary -j .config rpi4-nuttx.o rpi4-nuttx.cell
 */

#include <jailhouse/types.h>
#include <jailhouse/cell-config.h>

struct {
	struct jailhouse_cell_desc cell;
	__u64 cpus[1];
	struct jailhouse_memory mem_regions[10];
	struct jailhouse_irqchip irqchips[2];
	struct jailhouse_pci_device pci_devices[1];
} __attribute__((packed)) config = {
	.cell = {
		.signature = JAILHOUSE_CELL_DESC_SIGNATURE,
		.revision = JAILHOUSE_CONFIG_REVISION,
		.architecture = JAILHOUSE_ARM64,
		.name = "rpi4-nuttx",
		.flags = JAILHOUSE_CELL_PASSIVE_COMMREG,

		.cpu_set_size = sizeof(config.cpus),
		.num_memory_regions = ARRAY_SIZE(config.mem_regions),
		.num_irqchips = ARRAY_SIZE(config.irqchips),
		.num_pci_devices = ARRAY_SIZE(config.pci_devices),

		.vpci_irq_base = 184 - 32,

		/* 关键！cell 首个 CPU 的启动地址(guest 虚拟地址)。NuttX 链接在
		 * 0x480000(dramboot.ld)，若这里不设，默认=0 → CPU 从 guest 0 启动、
		 * 跑的不是 NuttX(会静默挂死/无输出)。CLI 的 `cell load -a` 只改镜像
		 * 加载地址，不改这个入口点，所以必须在这里显式指定。 */
		.cpu_reset_address = 0x480000,

		.console = {
			.address = 0xfe215040,
			.type = JAILHOUSE_CON_TYPE_8250,
			.flags = JAILHOUSE_CON_ACCESS_MMIO |
				 JAILHOUSE_CON_REGDIST_4,
		},
	},

	.cpus = {
		0b0010,
	},

	.mem_regions = {
		/* IVSHMEM shared memory regions (与 root cell 对端，供后续
		 * remote-syscall / 通信通道使用) */
		{
			.phys_start = 0x1faf0000,
			.virt_start = 0x1faf0000,
			.size = 0x1000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_ROOTSHARED,
		},
		{
			.phys_start = 0x1faf1000,
			.virt_start = 0x1faf1000,
			.size = 0x9000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_ROOTSHARED,
		},
		{
			.phys_start = 0x1fafa000,
			.virt_start = 0x1fafa000,
			.size = 0x2000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_ROOTSHARED,
		},
		{
			.phys_start = 0x1fafc000,
			.virt_start = 0x1fafc000,
			.size = 0x2000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_ROOTSHARED,
		},
		{
			.phys_start = 0x1fafe000,
			.virt_start = 0x1fafe000,
			.size = 0x2000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_ROOTSHARED,
		},
		/* UART / AUX 块 (mini-UART + AUX 控制寄存器)
		 * 关键：NuttX 的 mini-UART 驱动(bcm2711_serial.c / bcm2711_lowputc.c)
		 * 会写 AUX_ENABLES(0xfe215004) 使能 AUX 模块，也会读 AUX_IRQ(0xfe215000)。
		 * 这些都在 mini-UART 数据寄存器(0xfe215040)之前，所以必须映射整个
		 * AUX 块 0xfe215000 起 0x100，否则越界访问 → data abort → inmate 卡死。
		 * (jailhouse 自带的 inmate-demo 只写数据寄存器，故其 0xfe215040/0x40 够用；
		 *  NuttX 需要更宽，这是踩过的坑。) */
		{
			.phys_start = 0xfe215000,
			.virt_start = 0xfe215000,
			.size = 0x100,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO | JAILHOUSE_MEM_IO_8 |
				JAILHOUSE_MEM_IO_32 | JAILHOUSE_MEM_ROOTSHARED,
		},
		/* GPIO 控制器 (0xfe200000)：NuttX 串口驱动初始化时会
		 * bcm2711_gpio_set_func(14/15, ALT5) 配置 UART 引脚，必须映射。 */
		{
			.phys_start = 0xfe200000,
			.virt_start = 0xfe200000,
			.size = 0x1000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO | JAILHOUSE_MEM_IO_8 |
				JAILHOUSE_MEM_IO_32 | JAILHOUSE_MEM_ROOTSHARED,
		},
		/* 系统定时器 (0xfe003000)：NuttX oneshot 驱动用 BCM 系统定时器
		 * (非 ARM arch timer)，调度 tick 依赖它，必须映射。 */
		{
			.phys_start = 0xfe003000,
			.virt_start = 0xfe003000,
			.size = 0x1000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO | JAILHOUSE_MEM_IO_8 |
				JAILHOUSE_MEM_IO_32 | JAILHOUSE_MEM_ROOTSHARED,
		},
		/* RAM: 预留池 0x10000000 起 128MB, guest 虚拟地址 0
		 * (NuttX: CONFIG_RAM_START=0, CONFIG_RAM_SIZE=0x8000000,
		 *  CONFIG_LOAD_BASE=0x480000 → 加载物理地址 0x10480000) */ {
			.phys_start = 0x10000000,
			.virt_start = 0,
			.size = 0x08000000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE | JAILHOUSE_MEM_DMA |
				JAILHOUSE_MEM_LOADABLE,
		},
		/* communication region */ {
			.virt_start = 0x80000000,
			.size = 0x00001000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_COMM_REGION,
		},
	},

	.irqchips = {
		/* GIC */ {
			.address = 0xff841000,
			.pin_base = 160,
			.pin_bitmap = {
				1 << (184 - 160),
				0,
				0,
				0
			},
		},
		/* AUX mini-UART IRQ：NuttX 串口驱动是中断驱动(BCM_IRQ_VC_AUX=96+29=125)，
		 * 控制台输出靠 UART 发送中断冲刷缓冲，必须放行 SPI 125，否则横幅只进缓冲、
		 * 永不发出。(jailhouse 自带 demo 用轮询故不需要；NuttX 需要。) */
		{
			.address = 0xff841000,
			.pin_base = 96,
			.pin_bitmap = {
				1 << (125 - 96),
				0,
				0,
				0
			},
		},
	},

	.pci_devices = {
		{ /* IVSHMEM (与 root cell 00:00.0 对端) */
			.type = JAILHOUSE_PCI_TYPE_IVSHMEM,
			.domain = 1,
			.bdf = 0 << 3,
			.bar_mask = JAILHOUSE_IVSHMEM_BAR_MASK_INTX,
			.shmem_regions_start = 0,
			.shmem_dev_id = 1,
			.shmem_peers = 3,
			.shmem_protocol = JAILHOUSE_SHMEM_PROTO_UNDEFINED,
		},
	},
};
