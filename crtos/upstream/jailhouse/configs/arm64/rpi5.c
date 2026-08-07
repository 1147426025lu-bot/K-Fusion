/*
 * Jailhouse system configuration — Raspberry Pi 5 (BCM2712) draft
 *
 * NOT upstream-validated. Derived from siemens/jailhouse configs/arm64/rpi4.c
 * with MMIO/GIC/UART addresses from Pi 5 device tree (soc@107c000000).
 *
 * Reservation via device tree overlay before enable:
 *   reg = <0x0 0x20000000 0x0 0x10000000>;  (256 MiB at 512 MiB)
 *
 * K-Fusion Pi5 draft, 2026
 * SPDX-License-Identifier: GPL-2.0
 */

#include <jailhouse/types.h>
#include <jailhouse/cell-config.h>

#define RPI5_SOC_MMIO_BASE	0x107c000000ULL
#define RPI5_AXI_MMIO_BASE	0x1000000000ULL
#define RPI5_GICD_BASE		0x107c7fff9000ULL
#define RPI5_GICC_BASE		0x107c7fffa000ULL
#define RPI5_GICH_BASE		0x107c7fffc000ULL
#define RPI5_GICV_BASE		0x107c7fffe000ULL
#define RPI5_UART_BASE		0x107d001000ULL
#define RPI5_VPCI_MMCFG		0x107c600000ULL

struct {
	struct jailhouse_system header;
	__u64 cpus[1];
	struct jailhouse_memory mem_regions[14];
	struct jailhouse_irqchip irqchips[2];
	struct jailhouse_pci_device pci_devices[2];
} __attribute__((packed)) config = {
	.header = {
		.signature = JAILHOUSE_SYSTEM_SIGNATURE,
		.revision = JAILHOUSE_CONFIG_REVISION,
		.architecture = JAILHOUSE_ARM64,
		.flags = JAILHOUSE_SYS_VIRTUAL_DEBUG_CONSOLE,
		.hypervisor_memory = {
			.phys_start = 0x1fc00000,
			.size       = 0x00400000,
		},
		.debug_console = {
			.address = RPI5_UART_BASE,
			.size = 0x1000,
			.type = JAILHOUSE_CON_TYPE_PL011,
			.flags = JAILHOUSE_CON_ACCESS_MMIO |
				 JAILHOUSE_CON_REGDIST_4,
		},
		.platform_info = {
			.pci_mmconfig_base = RPI5_VPCI_MMCFG,
			.pci_mmconfig_end_bus = 0,
			.pci_is_virtual = 1,
			.pci_domain = 1,
			.arm = {
				.gic_version = 2,
				.gicd_base = RPI5_GICD_BASE,
				.gicc_base = RPI5_GICC_BASE,
				.gich_base = RPI5_GICH_BASE,
				.gicv_base = RPI5_GICV_BASE,
				.maintenance_irq = 25,
			},
		},
		.root_cell = {
			.name = "Raspberry-Pi5",

			.cpu_set_size = sizeof(config.cpus),
			.num_memory_regions = ARRAY_SIZE(config.mem_regions),
			.num_irqchips = ARRAY_SIZE(config.irqchips),
			.num_pci_devices = ARRAY_SIZE(config.pci_devices),

			.vpci_irq_base = 182 - 32,
		},
	},

	.cpus = {
		0b1111,
	},

	.mem_regions = {
		/* IVSHMEM shared memory regions for 00:00.0 (demo) */
		{
			.phys_start = 0x1faf0000,
			.virt_start = 0x1faf0000,
			.size = 0x1000,
			.flags = JAILHOUSE_MEM_READ,
		},
		{
			.phys_start = 0x1faf1000,
			.virt_start = 0x1faf1000,
			.size = 0x9000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE,
		},
		{
			.phys_start = 0x1fafa000,
			.virt_start = 0x1fafa000,
			.size = 0x2000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE,
		},
		{
			.phys_start = 0x1fafc000,
			.virt_start = 0x1fafc000,
			.size = 0x2000,
			.flags = JAILHOUSE_MEM_READ,
		},
		{
			.phys_start = 0x1fafe000,
			.virt_start = 0x1fafe000,
			.size = 0x2000,
			.flags = JAILHOUSE_MEM_READ,
		},
		JAILHOUSE_SHMEM_NET_REGIONS(0x1fb00000, 0),
		/* BCM2712 SoC MMIO (permissive) */
		{
			.phys_start = RPI5_SOC_MMIO_BASE,
			.virt_start = RPI5_SOC_MMIO_BASE,
			.size = 0x08000000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO,
		},
		/* AXI domain MMIO (PCIe, DMA, RP1, ...) */
		{
			.phys_start = RPI5_AXI_MMIO_BASE,
			.virt_start = RPI5_AXI_MMIO_BASE,
			.size = 0x10000000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO,
		},
		/* RAM (0M-~506M) */
		{
			.phys_start = 0x0,
			.virt_start = 0x0,
			.size = 0x1fa10000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE,
		},
		/* RAM (512M-4032M) — hole 512M..768M reserved for inmate via DT */
		{
			.phys_start = 0x30000000,
			.virt_start = 0x30000000,
			.size = 0xd0000000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE,
		},
		/* RAM (4096M-8192M) */
		{
			.phys_start = 0x100000000,
			.virt_start = 0x100000000,
			.size = 0x100000000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE,
		},
	},

	.irqchips = {
		{
			.address = RPI5_GICD_BASE,
			.pin_base = 32,
			.pin_bitmap = {
				0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff
			},
		},
		{
			.address = RPI5_GICD_BASE,
			.pin_base = 160,
			.pin_bitmap = {
				0xffffffff, 0xffffffff
			},
		},
	},

	.pci_devices = {
		{
			.type = JAILHOUSE_PCI_TYPE_IVSHMEM,
			.domain = 1,
			.bdf = 0 << 3,
			.bar_mask = JAILHOUSE_IVSHMEM_BAR_MASK_INTX,
			.shmem_regions_start = 0,
			.shmem_dev_id = 0,
			.shmem_peers = 3,
			.shmem_protocol = JAILHOUSE_SHMEM_PROTO_UNDEFINED,
		},
		{
			.type = JAILHOUSE_PCI_TYPE_IVSHMEM,
			.domain = 1,
			.bdf = 1 << 3,
			.bar_mask = JAILHOUSE_IVSHMEM_BAR_MASK_INTX,
			.shmem_regions_start = 5,
			.shmem_dev_id = 0,
			.shmem_peers = 2,
			.shmem_protocol = JAILHOUSE_SHMEM_PROTO_VETH,
		},
	},
};
