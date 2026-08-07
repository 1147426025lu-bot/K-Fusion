/*
 * Jailhouse NuttX / cRTOS sRTOS cell — Raspberry Pi 5 (BCM2712) draft
 *
 * Mirrors Fixstars cRTOS nuttx.c layout (IVSHMEM + dedicated RAM) for ARM64.
 * NuttX binary must be built separately for this cell memory map.
 */
#include <jailhouse/types.h>
#include <jailhouse/cell-config.h>

#define RPI5_GICD_BASE		0x107c7fff9000ULL
#define RPI5_UART_BASE		0x107d001000ULL
#define NUTTX_RAM_PHYS		0x10000000ULL
#define NUTTX_RAM_SIZE		0x60000000ULL

struct {
	struct jailhouse_cell_desc cell;
	__u64 cpus[1];
	struct jailhouse_memory mem_regions[13];
	struct jailhouse_irqchip irqchips[2];
	struct jailhouse_pci_device pci_devices[2];
} __attribute__((packed)) config = {
	.cell = {
		.signature = JAILHOUSE_CELL_DESC_SIGNATURE,
		.revision = JAILHOUSE_CONFIG_REVISION,
		.architecture = JAILHOUSE_ARM64,
		.name = "nuttx",
		.flags = JAILHOUSE_CELL_PASSIVE_COMMREG,

		.cpu_set_size = sizeof(config.cpus),
		.num_memory_regions = ARRAY_SIZE(config.mem_regions),
		.num_irqchips = ARRAY_SIZE(config.irqchips),
		.num_pci_devices = ARRAY_SIZE(config.pci_devices),

		.vpci_irq_base = 185 - 32,

		.console = {
			.address = RPI5_UART_BASE,
			.type = JAILHOUSE_CON_TYPE_PL011,
			.flags = JAILHOUSE_CON_ACCESS_MMIO |
				 JAILHOUSE_CON_REGDIST_4,
		},
	},

	.cpus = {
		0b1100,
	},

	.mem_regions = {
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
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_ROOTSHARED,
		},
		{
			.phys_start = 0x1fafe000,
			.virt_start = 0x1fafe000,
			.size = 0x2000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_ROOTSHARED,
		},
		JAILHOUSE_SHMEM_NET_REGIONS(0x1fb00000, 1),
		{
			.phys_start = RPI5_UART_BASE,
			.virt_start = RPI5_UART_BASE,
			.size = 0x200,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO | JAILHOUSE_MEM_IO_8 |
				JAILHOUSE_MEM_IO_32 | JAILHOUSE_MEM_ROOTSHARED,
		},
		{
			.phys_start = 0x1f900000,
			.virt_start = 0,
			.size = 0x10000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE | JAILHOUSE_MEM_LOADABLE,
		},
		{
			.phys_start = NUTTX_RAM_PHYS,
			.virt_start = 0,
			.size = NUTTX_RAM_SIZE,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE | JAILHOUSE_MEM_DMA |
				JAILHOUSE_MEM_LOADABLE,
		},
		{
			.virt_start = 0x80000000,
			.size = 0x00001000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_COMM_REGION,
		},
	},

	.irqchips = {
		{
			.address = RPI5_GICD_BASE,
			.pin_base = 32,
			.pin_bitmap = {
				0, 0, 1 << (121 - 96), 0,
			},
		},
		{
			.address = RPI5_GICD_BASE,
			.pin_base = 160,
			.pin_bitmap = {
				(1 << (185 - 160)) | (1 << (186 - 160)),
				0, 0, 0,
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
			.shmem_dev_id = 2,
			.shmem_peers = 3,
			.shmem_protocol = JAILHOUSE_SHMEM_PROTO_UNDEFINED,
		},
		{
			.type = JAILHOUSE_PCI_TYPE_IVSHMEM,
			.bdf = 1 << 3,
			.bar_mask = JAILHOUSE_IVSHMEM_BAR_MASK_INTX,
			.shmem_regions_start = 5,
			.shmem_dev_id = 1,
			.shmem_peers = 2,
			.shmem_protocol = JAILHOUSE_SHMEM_PROTO_VETH,
		},
	},
};
