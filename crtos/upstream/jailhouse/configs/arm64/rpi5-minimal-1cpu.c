/*
 * Pi5 minimal cell — CPU0 only for first real enable (sequential_el2 + single core).
 * Same MMIO/RAM map as rpi5-minimal.c; reduces SMP arch_entry variables.
 */
#include <jailhouse/types.h>
#include <jailhouse/cell-config.h>

#define RPI5_SOC_MMIO_BASE	0x107c000000ULL
#define RPI5_AXI_MMIO_BASE	0x1000000000ULL
#define RPI5_AXI_MMIO_SIZE	0x10000000ULL
#define RPI5_GICD_BASE		0x107c7fff9000ULL
#define RPI5_GICC_BASE		0x107c7fffa000ULL
#define RPI5_GICH_BASE		0x107c7fffc000ULL
#define RPI5_GICV_BASE		0x107c7fffe000ULL
#define RPI5_UART_BASE		0x107d001000ULL

struct {
	struct jailhouse_system header;
	__u64 cpus[1];
	struct jailhouse_memory mem_regions[5];
	struct jailhouse_irqchip irqchips[2];
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
			.pci_mmconfig_base = 0,
			.pci_mmconfig_end_bus = 0,
			.pci_is_virtual = 0,
			.pci_domain = 0,
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
			.name = "Raspberry-Pi5-minimal-1cpu",
			.cpu_set_size = sizeof(config.cpus),
			.num_memory_regions = ARRAY_SIZE(config.mem_regions),
			.num_irqchips = ARRAY_SIZE(config.irqchips),
			.num_pci_devices = 0,
		},
	},

	.cpus = {
		0b0001,
	},

	.mem_regions = {
		{
			.phys_start = RPI5_SOC_MMIO_BASE,
			.virt_start = RPI5_SOC_MMIO_BASE,
			.size = 0x08000000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO,
		},
		{
			.phys_start = RPI5_AXI_MMIO_BASE,
			.virt_start = RPI5_AXI_MMIO_BASE,
			.size = RPI5_AXI_MMIO_SIZE,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_IO,
		},
		{
			.phys_start = 0x0,
			.virt_start = 0x0,
			.size = 0x1fa10000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE,
		},
		{
			.phys_start = 0x20000000,
			.virt_start = 0x20000000,
			.size = 0xdc000000,
			.flags = JAILHOUSE_MEM_READ | JAILHOUSE_MEM_WRITE |
				JAILHOUSE_MEM_EXECUTE,
		},
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
};
