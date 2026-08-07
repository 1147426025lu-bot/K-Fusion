/*
 * Jailhouse, a Linux-based partitioning hypervisor
 *
 * Copyright (c) Siemens AG, 2013-2017
 * Copyright (c) Valentine Sinitsyn, 2014
 *
 * Authors:
 *  Jan Kiszka <jan.kiszka@siemens.com>
 *  Valentine Sinitsyn <valentine.sinitsyn@gmail.com>
 *
 * This work is licensed under the terms of the GNU GPL, version 2.  See
 * the COPYING file in the top-level directory.
 */

/* For compatibility with older kernel versions */
#include <linux/version.h>

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/firmware.h>
#include <linux/mm.h>
#include <linux/kallsyms.h>
#include <linux/vmalloc.h>
#if defined(CONFIG_ARM64)
#include <asm/barrier.h>
#include <asm/pgtable.h>
#include <asm/tlbflush.h>
#include <linux/memremap.h>
#include <linux/sched.h>
#include <linux/delay.h>
#include <linux/kprobes.h>
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,11,0)
#include <linux/sched/signal.h>
#endif
#include <linux/slab.h>
#include <linux/smp.h>
#include <linux/uaccess.h>
#include <linux/reboot.h>
#include <linux/vmalloc.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/memremap.h>
#include <asm/barrier.h>
#include <asm/smp.h>
#include <asm/cacheflush.h>
#include <asm/tlbflush.h>
#ifdef CONFIG_ARM
#include <asm/virt.h>
#endif
#if defined(CONFIG_ARM64)
#include <asm/jailhouse_header.h>
#include <asm/pgtable-prot.h>
#endif
#ifdef CONFIG_X86
#include <asm/msr.h>
#include <asm/apic.h>
#endif

#include "cell.h"
#include "jailhouse.h"
#include "main.h"
#include "pci.h"
#include "sysfs.h"

#include <jailhouse/header.h>
#include <jailhouse/hypercall.h>
#include <generated/version.h>

#ifdef CONFIG_X86_32
#error 64-bit kernel required!
#endif

#if LINUX_VERSION_CODE < KERNEL_VERSION(5,6,0)
#define MSR_IA32_FEAT_CTL			MSR_IA32_FEATURE_CONTROL
#define FEAT_CTL_VMX_ENABLED_OUTSIDE_SMX \
	FEATURE_CONTROL_VMXON_ENABLED_OUTSIDE_SMX
#endif

#if JAILHOUSE_CELL_ID_NAMELEN != JAILHOUSE_CELL_NAME_MAXLEN
# warning JAILHOUSE_CELL_ID_NAMELEN and JAILHOUSE_CELL_NAME_MAXLEN out of sync!
#endif

#ifdef CONFIG_X86
#define JAILHOUSE_AMD_FW_NAME	"jailhouse-amd.bin"
#define JAILHOUSE_INTEL_FW_NAME	"jailhouse-intel.bin"
#else
#define JAILHOUSE_FW_NAME	"jailhouse.bin"
#endif

MODULE_DESCRIPTION("Management driver for Jailhouse partitioning hypervisor");
MODULE_LICENSE("GPL");
#ifdef CONFIG_X86
MODULE_FIRMWARE(JAILHOUSE_AMD_FW_NAME);
MODULE_FIRMWARE(JAILHOUSE_INTEL_FW_NAME);
#else
MODULE_FIRMWARE(JAILHOUSE_FW_NAME);
#endif
MODULE_VERSION(JAILHOUSE_VERSION);

extern char __hyp_stub_vectors[];

struct console_state {
	unsigned int head;
	unsigned int last_console_id;
};

DEFINE_MUTEX(jailhouse_lock);
bool jailhouse_enabled;
void *hypervisor_mem;

static struct device *jailhouse_dev;
static unsigned long hv_core_and_percpu_size;
static atomic_t call_done;
static int error_code;
static struct jailhouse_virt_console* volatile console_page;
static bool console_available;
static struct resource *hypervisor_mem_res;

static bool dry_run;
module_param(dry_run, bool, 0644);
MODULE_PARM_DESC(dry_run,
		 "If true, map hypervisor (RW only) and skip make_exec/EL2; MUST set at insmod");

static bool el2_stop;
module_param(el2_stop, bool, 0644);
MODULE_PARM_DESC(el2_stop,
		 "If true, stop after memremap+config (skip make_exec/EL2)");

static bool make_exec_stop;
module_param(make_exec_stop, bool, 0644);
MODULE_PARM_DESC(make_exec_stop,
		 "If true, stop after make_exec succeeds (skip EL2 entry)");

static bool hyp_probe;
module_param(hyp_probe, bool, 0644);
MODULE_PARM_DESC(hyp_probe,
		 "If true, run inline hvc SET/RESET vectors on each CPU before enable");

static bool trace_el2;
module_param(trace_el2, bool, 0644);
MODULE_PARM_DESC(trace_el2,
		 "If true, log each CPU around EL2 entry (Pi5/netconsole debug)");

static bool uart_trace;
module_param(uart_trace, bool, 0644);
MODULE_PARM_DESC(uart_trace,
		 "If true, write PL011 markers on Pi5 UART 0x107d001000 (Pi5 debug)");

static bool sequential_el2;
module_param(sequential_el2, bool, 0644);
MODULE_PARM_DESC(sequential_el2,
		 "If true, enter EL2 one CPU at a time (Pi5 debug; not for production)");

static bool scratch_trace;
module_param(scratch_trace, bool, 0644);
MODULE_PARM_DESC(scratch_trace,
		 "If true, log EL1 stages via scratch_trace_base (Pi5 debug)");

static bool stub_arch_entry;
module_param(stub_arch_entry, bool, 0644);
MODULE_PARM_DESC(stub_arch_entry,
		 "If true, skip real arch_entry (BLR isolation test; Pi5)");

#define JH_SCRATCH_PHYS		0x1ffb0000ULL
/* Byte 0-3: EL1 A/B/C/R; byte 7: cpuid for EL2; byte 8+cpu: driver; byte 4-7: EL2 stages */
#define JH_SCRATCH_DRV_BASE	8
#define JH_SCRATCH_STAGE_OFF	16
#define JH_UART_PHYS		0x107d001000ULL

static void *scratch_kpage;
static void __iomem *scratch_base;
static void __iomem *scratch_mmio;
static bool scratch_is_memremap;
static void __iomem *uart_trace_base;

#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
static void jailhouse_pi5_stage(const char *msg, char mark)
{
	pr_emerg("jailhouse: Pi5 stage %c: %s\n", mark, msg);
	if (scratch_trace && scratch_base) {
		writeb(mark, scratch_base + JH_SCRATCH_STAGE_OFF);
		wmb();
	}
	mdelay(25);
}
#endif

static typeof(ioremap_page_range) *ioremap_page_range_sym;
#ifdef CONFIG_X86
#if LINUX_VERSION_CODE < KERNEL_VERSION(5,3,0)
#define lapic_timer_period	lapic_timer_frequency
#define lapic_timer_period_sym	lapic_timer_frequency_sym
#endif
static typeof(lapic_timer_period) *lapic_timer_period_sym;
#endif
#ifdef CONFIG_ARM
static typeof(__boot_cpu_mode) *__boot_cpu_mode_sym;
#endif
#if defined(CONFIG_ARM) || defined(CONFIG_ARM64)
static typeof(__hyp_stub_vectors) *__hyp_stub_vectors_sym;
#endif
#if defined(CONFIG_ARM64)
#define HVC_SET_VECTORS		0
#define HVC_RESET_VECTORS	2

extern bool arm64_use_ng_mappings;

#ifdef JAILHOUSE_PI5_CANONICAL_VA
/* Canonical base at VMALLOC_START; reserve 4 MiB for hv EXEC alias */
#define JAILHOUSE_VMAP_END	(JAILHOUSE_BASE + 0x400000UL)
#else
/* Legacy non-canonical slot (Pi4 / 48-bit vmalloc carve-out) */
#define JAILHOUSE_VMAP_END	0xffffc0604000UL
#endif
#ifndef VM_NO_GUARD
#define VM_NO_GUARD		0x00000040
#endif

static bool hypervisor_memremapped;
#ifdef JAILHOUSE_PI5_CANONICAL_VA
static bool hypervisor_direct_mapped;
static unsigned long hypervisor_direct_map_size;
static struct vm_struct *hypervisor_direct_vma;
static struct mm_struct *jailhouse_kernel_mm;
typedef void (*cpu_soft_restart_fn)(unsigned long el2_switch, unsigned long entry,
				    unsigned long arg0, unsigned long arg1,
				    unsigned long arg2);
static cpu_soft_restart_fn jailhouse_cpu_soft_restart;

static int jailhouse_kallsyms_lookup(const char *name, unsigned long *val)
{
	struct kprobe kp = { .symbol_name = "kallsyms_lookup_name" };
	unsigned long (*lookup_name)(const char *n);
	unsigned long sym;
	int ret;

	ret = register_kprobe(&kp);
	if (ret < 0)
		return ret;
	lookup_name = (void *)kp.addr;
	sym = lookup_name(name);
	unregister_kprobe(&kp);
	if (!sym)
		return -ENOENT;
	*val = sym;
	return 0;
}

static int __init jailhouse_resolve_kernel_mm(void)
{
	struct mm_struct *mm;
	unsigned long val;
	int ret;

	/*
	 * Pi5: init_task.mm may be non-NULL but is NOT init_mm. Kernel vmalloc /
	 * fixed ioremap PTEs for JAILHOUSE_BASE must be installed in init_mm.
	 */
	ret = jailhouse_kallsyms_lookup("init_mm", &val);
	if (ret)
		return ret;
	mm = (struct mm_struct *)val;

	jailhouse_kernel_mm = mm;
	pr_info("jailhouse: kernel mm from kallsyms init_mm (%px init_task.mm=%px)\n",
		mm, init_task.mm);

	ret = jailhouse_kallsyms_lookup("cpu_soft_restart", &val);
	if (ret) {
		pr_warn("jailhouse: cpu_soft_restart not found (%d) — "
			"Pi5 EL2 will use bare HVC_SOFT_RESTART\n", ret);
		jailhouse_cpu_soft_restart = NULL;
	} else {
		jailhouse_cpu_soft_restart = (cpu_soft_restart_fn)val;
		pr_info("jailhouse: cpu_soft_restart at %px (MMU-off EL2 entry)\n",
			jailhouse_cpu_soft_restart);
	}
	return 0;
}

static unsigned long jailhouse_runtime_vabits(void)
{
	unsigned long tcr;

	asm volatile("mrs %0, tcr_el1" : "=r" (tcr));
	return 64 - ((tcr >> 16) & 0x3f);
}

static unsigned long jailhouse_runtime_page_offset(void)
{
	return -(1UL << jailhouse_runtime_vabits());
}

static unsigned long jailhouse_runtime_modules_end(void)
{
	unsigned long va = jailhouse_runtime_vabits();

	return (-(1UL << (va - 1))) + (2UL << 30);
}

static int jailhouse_validate_canonical_base(void)
{
	unsigned long va = jailhouse_runtime_vabits();
	unsigned long page_offset = jailhouse_runtime_page_offset();
	unsigned long modules_end = jailhouse_runtime_modules_end();

	if (JAILHOUSE_BASE < page_offset) {
		pr_err("jailhouse: JAILHOUSE_BASE 0x%llx is non-canonical for "
		       "%lu-bit kernel (PAGE_OFFSET=%#lx, MODULES_END=%#lx)\n",
		       (unsigned long long)JAILHOUSE_BASE, va, page_offset,
		       modules_end);
		pr_err("jailhouse: rebuild HV+driver with JH_VA_BITS=%lu "
		       "(built with JAILHOUSE_VA_BITS=%d)\n",
		       va, JAILHOUSE_VA_BITS);
		return -EINVAL;
	}

	if (JAILHOUSE_BASE < modules_end)
		pr_warn("jailhouse: JAILHOUSE_BASE 0x%llx below MODULES_END %#lx "
			"(must be above KIMAGE_VADDR)\n",
			(unsigned long long)JAILHOUSE_BASE, modules_end);

	pr_info("jailhouse: VA %lu-bit PAGE_OFFSET=%#lx JAILHOUSE_BASE=%#llx "
		"(JAILHOUSE_VA_BITS=%d)\n",
		va, page_offset, (unsigned long long)JAILHOUSE_BASE,
		JAILHOUSE_VA_BITS);
	return 0;
}
#endif
/* EXEC alias at fixed JAILHOUSE_BASE; hypervisor_mem stays on memremap (Pi5) */
static void *hypervisor_exec;
static phys_addr_t jailhouse_hv_phys;
static phys_addr_t jailhouse_bootstrap_pa;

#ifdef JAILHOUSE_PI5_CANONICAL_VA
static void jailhouse_direct_unmap(unsigned long virt, unsigned long size);
#endif

static void jailhouse_unmap_hypervisor_exec(void)
{
	if (!hypervisor_exec)
		return;
#ifdef JAILHOUSE_PI5_CANONICAL_VA
	if (hypervisor_direct_mapped && hypervisor_exec) {
		jailhouse_direct_unmap((unsigned long)hypervisor_exec,
				       hypervisor_direct_map_size);
		hypervisor_exec = NULL;
		return;
	}
#endif
	if (hypervisor_exec != hypervisor_mem)
		vunmap(hypervisor_exec);
	hypervisor_exec = NULL;
}

#ifdef JAILHOUSE_PI5_CANONICAL_VA
static int jailhouse_direct_clear_pte(pte_t *pte, unsigned long addr, void *data)
{
	if (!pte_none(*pte))
		pte_clear(jailhouse_kernel_mm, addr, pte);
	return 0;
}

static void jailhouse_direct_unmap(unsigned long virt, unsigned long size)
{
	struct mm_struct *mm = jailhouse_kernel_mm;
	struct vm_struct *area;

	if (!virt || !size || !mm)
		return;

	size = PAGE_ALIGN(size);
	mmap_write_lock(mm);
	apply_to_page_range(mm, virt, size, jailhouse_direct_clear_pte, NULL);
	mmap_write_unlock(mm);
	flush_tlb_kernel_range(virt, virt + size);

	area = hypervisor_direct_vma;
	if (area)
		vunmap(area->addr);

	hypervisor_direct_vma = NULL;
	hypervisor_direct_mapped = false;
	hypervisor_direct_map_size = 0;
}
#endif

static void jailhouse_unmap_hypervisor_mem(void)
{
	if (!hypervisor_mem)
		return;
#ifdef JAILHOUSE_PI5_CANONICAL_VA
	/*
	 * Pi5: EXEC direct_map at JAILHOUSE_BASE; RW memremap is separate.
	 */
	if (hypervisor_direct_mapped && hypervisor_exec) {
		jailhouse_direct_unmap((unsigned long)hypervisor_exec,
				       hypervisor_direct_map_size);
	}
	if (hypervisor_memremapped && hypervisor_mem) {
		memunmap(hypervisor_mem);
		hypervisor_mem = NULL;
		hypervisor_memremapped = false;
	} else if (hypervisor_mem) {
		vunmap(hypervisor_mem);
		hypervisor_mem = NULL;
	}
	hypervisor_exec = NULL;
	hypervisor_direct_mapped = false;
	hypervisor_direct_map_size = 0;
	hypervisor_direct_vma = NULL;
	return;
#endif
	if (hypervisor_memremapped)
		memunmap(hypervisor_mem);
	else
		vunmap(hypervisor_mem);
	hypervisor_mem = NULL;
	hypervisor_memremapped = false;
#ifdef JAILHOUSE_PI5_CANONICAL_VA
	hypervisor_direct_mapped = false;
	hypervisor_direct_map_size = 0;
	hypervisor_direct_vma = NULL;
#endif
}

static void *jailhouse_map_hypervisor_rw(phys_addr_t phys, unsigned long size)
{
	void *map;

	map = memremap(phys, size, MEMREMAP_WB);
	if (!map) {
		pr_err("jailhouse: memremap failed phys=0x%llx size=0x%lx\n",
		       (unsigned long long)phys, size);
		return NULL;
	}
	hypervisor_memremapped = true;
	return map;
}

static pgprot_t jailhouse_pgprot_rw(void)
{
	if (arm64_use_ng_mappings)
		return __pgprot(pgprot_val(PAGE_KERNEL) | PTE_NG);
	return PAGE_KERNEL;
}

static pgprot_t jailhouse_pgprot_exec(void)
{
	/*
	 * Pi5 nomap HV RAM: WB cacheable, kernel-executable (PXN clear),
	 * PTE_GP when CONFIG_ARM64_BTI (BLR to arch_entry from driver).
	 */
#ifdef JAILHOUSE_PI5_CANONICAL_VA
	pgprot_t prot = __pgprot(pgprot_val(PAGE_KERNEL_EXEC) | PTE_MAYBE_GP);

	if (arm64_use_ng_mappings)
		prot = __pgprot(pgprot_val(prot) & ~PTE_NG);
	return prot;
#else
	return __pgprot(pgprot_val(PAGE_KERNEL_EXEC) | PTE_MAYBE_GP);
#endif
}

struct jh_pte_change {
	pgprot_t set_mask;
	pgprot_t clear_mask;
};

static int jailhouse_pte_change(pte_t *ptep, unsigned long addr, void *data)
{
	struct jh_pte_change *cdata = data;
	pte_t pte = READ_ONCE(*ptep);

	if (!pte_present(pte))
		return 0;

	pte = clear_pte_bit(pte, cdata->clear_mask);
	pte = set_pte_bit(pte, cdata->set_mask);
	set_pte(ptep, pte);
	return 0;
}

/*
 * ioremap_page_range on Pi5 leaves PXN set (non-exec); CONFIG_ARM64_BTI
 * also needs PTE_GP for BLR to arch_entry. Mirrors kernel set_memory_x().
 */
static int jailhouse_arm64_mark_exec(unsigned long virt, unsigned long size)
{
	struct jh_pte_change data = {
		.set_mask = __pgprot(PTE_MAYBE_GP),
#ifdef JAILHOUSE_PI5_CANONICAL_VA
		/*
		 * EXEC map is PAGE_KERNEL_EXEC (PTE_RDONLY). arch_entry writes
		 * header/debug fields and bootstrap PTs from EL1 before EL2.
		 */
		.clear_mask = __pgprot(PTE_PXN | PTE_RDONLY),
#else
		.clear_mask = __pgprot(PTE_PXN),
#endif
	};
	struct mm_struct *mm = jailhouse_kernel_mm;
	int err;

	if (!mm)
		return -EINVAL;

	size = PAGE_ALIGN(size);
	mmap_write_lock(mm);
	err = apply_to_page_range(mm, virt, size, jailhouse_pte_change, &data);
	mmap_write_unlock(mm);
	if (!err)
		flush_tlb_kernel_range(virt, virt + size);
	return err;
}

#if defined(JAILHOUSE_PI5_CANONICAL_VA)
/*
 * Pi5: one large flush_icache_range on the EXEC alias can hang; page-at-a-time
 * is safe and covers the full image (PIPT I-cache is per-PFN).
 */
static void jailhouse_pi5_flush_icache_paged(unsigned long start,
					     unsigned long size)
{
	unsigned long addr, end = start + PAGE_ALIGN(size);

	for (addr = start; addr < end; addr += PAGE_SIZE)
		flush_icache_range(addr, addr + PAGE_SIZE);
	isb();
}
#endif

static int jailhouse_arm64_mark_exec_logged(unsigned long virt, unsigned long size)
{
	int err = jailhouse_arm64_mark_exec(virt, size);

	if (err)
		pr_err("jailhouse: mark_exec (clear PXN/set GP) failed: %d\n", err);
	else
		pr_emerg("jailhouse: mark_exec OK virt=0x%lx size=0x%lx"
#ifdef JAILHOUSE_PI5_CANONICAL_VA
			 " (PXN+RDONLY cleared)"
#endif
			 "\n", virt, size);
	return err;
}

#ifdef JAILHOUSE_PI5_CANONICAL_VA
static void jailhouse_log_first_pte(unsigned long virt)
{
	pgd_t *pgd;
	p4d_t *p4d;
	pud_t *pud;
	pmd_t *pmd;
	pte_t *pte;

	if (!jailhouse_kernel_mm) {
		pr_emerg("jailhouse: PTE walk: no kernel mm\n");
		return;
	}

	pgd = pgd_offset(jailhouse_kernel_mm, virt);
	if (pgd_none(*pgd) || pgd_bad(*pgd)) {
		pr_emerg("jailhouse: PTE walk: pgd fail virt=0x%lx pgd=%llx\n",
			 virt, (unsigned long long)pgd_val(*pgd));
		return;
	}
	p4d = p4d_offset(pgd, virt);
	if (p4d_none(*p4d) || p4d_bad(*p4d)) {
		pr_emerg("jailhouse: PTE walk: p4d fail virt=0x%lx\n", virt);
		return;
	}
	pud = pud_offset(p4d, virt);
	if (pud_none(*pud) || pud_bad(*pud)) {
		pr_emerg("jailhouse: PTE walk: pud fail virt=0x%lx\n", virt);
		return;
	}
	pmd = pmd_offset(pud, virt);
	if (pmd_none(*pmd) || pmd_bad(*pmd)) {
		pr_emerg("jailhouse: PTE walk: pmd fail virt=0x%lx\n", virt);
		return;
	}
	pte = pte_offset_kernel(pmd, virt);
	if (!pte) {
		pr_emerg("jailhouse: PTE walk: null pte virt=0x%lx\n", virt);
		return;
	}

	pr_emerg("jailhouse: PTE[0] virt=0x%lx val=0x%016llx PXN=%d GP=%d\n",
		 virt, (unsigned long long)pte_val(*pte),
		 !!(pte_val(*pte) & PTE_PXN),
		 !!(pte_val(*pte) & PTE_GP));
}
#endif

/* Must cover bootstrap page tables + trampoline (see hypervisor.lds ~0x17000) */
#define JH_EXEC_MIN_SIZE		0x18000UL

/* JAILHOUSE vmalloc slot is 0x404000 — full 4 MiB + vmalloc overhead does not fit */
static unsigned long jailhouse_exec_map_size(unsigned long hv_size,
					     unsigned long core_and_percpu,
					     unsigned long cfg_size)
{
	unsigned long need = PAGE_ALIGN(core_and_percpu + cfg_size);
	unsigned long full = PAGE_ALIGN(hv_size);

	if (need < JH_EXEC_MIN_SIZE)
		need = JH_EXEC_MIN_SIZE;
	if (need < PAGE_SIZE)
		need = PAGE_SIZE;
	if (need > full)
		need = full;
	return need;
}

static void jailhouse_hvc_set_vectors(phys_addr_t phys)
{
	register unsigned long x0 asm("x0") = HVC_SET_VECTORS;
	register unsigned long x1 asm("x1") = (unsigned long)phys;

	asm volatile("hvc #0" : "+r" (x0) : "r" (x1) : "memory");
}

static void jailhouse_hvc_soft_restart(phys_addr_t entry_phys)
{
	register unsigned long x0 asm("x0") = 1; /* HVC_SOFT_RESTART */
	register unsigned long x1 asm("x1") = (unsigned long)entry_phys;

	dsb(sy);
	isb();
	asm volatile("hvc #0" : "+r" (x0) : "r" (x1) : "memory");
	isb();
}

static void jailhouse_hvc_reset_vectors(void)
{
	register unsigned long x0 asm("x0") = HVC_RESET_VECTORS;

	asm volatile("hvc #0" : "+r" (x0) : : "memory");
}

#if defined(JAILHOUSE_PI5_CANONICAL_VA)
/* EL1 HVC → VBAR_EL2=bootstrap → el2_entry (normal Jailhouse path) */
static void jailhouse_pi5_hvc_trap_el2(void)
{
	asm volatile(
		"dsb	sy\n"
		"isb\n"
		"hvc	#0"
		: : : "memory");
}
#endif

static void jailhouse_hyp_stub_probe_cpu(void *info)
{
	unsigned int cpu = smp_processor_id();

	pr_emerg("jailhouse: hyp stub probe CPU%u SET_VECTORS pa=0x%llx\n",
		 cpu, (unsigned long long)virt_to_phys(*__hyp_stub_vectors_sym));
	jailhouse_hvc_set_vectors(virt_to_phys(*__hyp_stub_vectors_sym));
	pr_emerg("jailhouse: hyp stub probe CPU%u RESET_VECTORS\n", cpu);
	jailhouse_hvc_reset_vectors();
	pr_emerg("jailhouse: hyp stub probe CPU%u done\n", cpu);
}

static void jailhouse_scratch_init_once(void)
{
	if (scratch_base || !scratch_trace)
		return;

#ifdef JAILHOUSE_PI5_CANONICAL_VA
	scratch_mmio = memremap(JH_SCRATCH_PHYS, PAGE_SIZE, MEMREMAP_WB);
	if (scratch_mmio) {
		scratch_base = scratch_mmio;
		scratch_is_memremap = true;
		memset_io(scratch_base, 0, PAGE_SIZE);
		pr_emerg("jailhouse: scratch EL1+EL2 page memremap %px phys=0x%llx\n",
			 scratch_base, (unsigned long long)JH_SCRATCH_PHYS);
		return;
	}
	pr_warn("jailhouse: scratch memremap 0x%llx failed, kmalloc fallback\n",
		(unsigned long long)JH_SCRATCH_PHYS);
#endif
	scratch_kpage = (void *)get_zeroed_page(GFP_KERNEL);
	if (!scratch_kpage) {
		pr_warn("jailhouse: scratch page alloc failed\n");
		return;
	}
	scratch_base = scratch_kpage;
	pr_emerg("jailhouse: scratch EL1 page OK at %px (EL2 phys=0x%llx)\n",
		 scratch_base, (unsigned long long)JH_SCRATCH_PHYS);
}

static void jailhouse_scratch_free(void)
{
	if (scratch_is_memremap && scratch_mmio) {
		memunmap(scratch_mmio);
		scratch_mmio = NULL;
		scratch_is_memremap = false;
		scratch_base = NULL;
	} else if (scratch_kpage) {
		free_page((unsigned long)scratch_kpage);
		scratch_kpage = NULL;
		scratch_base = NULL;
	}
}

static void jailhouse_uart_init_once(void)
{
	/*
	 * PL011 debug via jailhouse_ioremap_prot hits vmalloc BUG on 39-bit Pi5;
	 * ioremap is not exported to modules. Use pr_emerg + scratch only.
	 */
}

static void jailhouse_trace_maps_init(void)
{
	jailhouse_uart_init_once();
	jailhouse_scratch_init_once();
}

#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
#define JAILHOUSE_PI5_FW_PATH	"/lib/firmware/jailhouse.bin"
#define JH_PI5_STALE_UART_INSN	0xb94019e3U
#define JH_PI5_INSN_MOV_W0_0	0x52800000U
#define JH_PI5_INSN_MOV_W0_11	0x5280000bU
#define JH_PI5_ARCH_ENTRY_EXIT_OFF 0x80U

static u32 jailhouse_pi5_insn_at(const void *image, unsigned long off)
{
	if (!image)
		return 0;
	return *(const u32 *)((const char *)image + off);
}

static u32 jailhouse_pi5_insn_probe(const void *image, unsigned long entry_off)
{
	return jailhouse_pi5_insn_at(image, entry_off + 0x14);
}

static u32 jailhouse_pi5_arch_entry_exit_insn(const void *image,
					      unsigned long entry_off)
{
	return jailhouse_pi5_insn_at(image, entry_off + JH_PI5_ARCH_ENTRY_EXIT_OFF);
}

/*
 * request_firmware() may return a stale cached image after on-disk updates.
 * Always seed HV RAM from /lib/firmware/jailhouse.bin on Pi5.
 */
static int jailhouse_pi5_copy_firmware(void *dest, unsigned long dest_size,
				       size_t fw_size, unsigned long entry_off)
{
	struct file *filp;
	loff_t pos = 0;
	ssize_t n;
	u32 insn;

	filp = filp_open(JAILHOUSE_PI5_FW_PATH, O_RDONLY, 0);
	if (IS_ERR(filp)) {
		pr_warn("jailhouse: Pi5 open %s: %ld\n",
			JAILHOUSE_PI5_FW_PATH, PTR_ERR(filp));
		return PTR_ERR(filp);
	}
	if (fw_size > dest_size) {
		filp_close(filp, NULL);
		return -EFBIG;
	}
	n = kernel_read(filp, dest, fw_size, &pos);
	filp_close(filp, NULL);
	if (n < 0 || (size_t)n != fw_size) {
		pr_err("jailhouse: Pi5 read %s: %zd (want %zu)\n",
		       JAILHOUSE_PI5_FW_PATH, n, fw_size);
		return n < 0 ? (int)n : -EIO;
	}
	insn = jailhouse_pi5_insn_probe(dest, entry_off);
	pr_emerg("jailhouse: Pi5 firmware file entry+0x14=0x%08x "
		 "exit+0x80=0x%08x (prod=0x%08x smoke=0x%08x)\n",
		 insn,
		 jailhouse_pi5_arch_entry_exit_insn(dest, entry_off),
		 JH_PI5_INSN_MOV_W0_0, JH_PI5_INSN_MOV_W0_11);
	if (jailhouse_pi5_arch_entry_exit_insn(dest, entry_off) ==
	    JH_PI5_INSN_MOV_W0_11) {
		pr_err("jailhouse: Pi5 %s is smoke HV (mov w0,#11) — rebuild production\n",
		       JAILHOUSE_PI5_FW_PATH);
		return -EINVAL;
	}
	return 0;
}
#endif

static void jailhouse_scratch_put(unsigned int cpu, char mark)
{
	if (!scratch_trace || !scratch_base || cpu > 15)
		return;

	writeb(mark, scratch_base + JH_SCRATCH_DRV_BASE + cpu);
	wmb();
}

#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
static int jailhouse_pi5_arch_entry_stub(unsigned int cpu)
{
	if (scratch_trace && scratch_base) {
		writeb('A', scratch_base);
		writeb('S', scratch_base + 1);
		wmb();
	}
	pr_emerg("jailhouse: arch_entry STUB cpu=%u (no EL2)\n", cpu);
	return -EAGAIN;
}

/*
 * HV lives at a driver-installed EXEC alias, not in the kernel image.
 * Raw BLR avoids compiler branch-protection/PAC assumptions on the pointer.
 */
static int jailhouse_pi5_call_arch_entry(int (*entry)(unsigned int),
					 unsigned int cpu)
{
	int ret;

	asm volatile(
		"mov	x0, %1\n"
		"blr	%2\n"
		"mov	%w0, w0"
		: "=r" (ret)
		: "r" (cpu), "r" (entry)
		: "x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7",
		  "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
		  "x16", "x17", "x30", "memory");

	return ret;
}
#endif

static int jailhouse_scratch_dump_set(const char *val, const struct kernel_param *kp)
{
	int i;

	jailhouse_scratch_init_once();
	if (!scratch_base)
		return -ENODEV;

	pr_info("jailhouse: scratch dump EL1@%px phys_el2=0x%llx "
		"(byte0-3=A/B/C/R byte4+=EL2 byte8=driver)\n",
		scratch_base, (unsigned long long)JH_SCRATCH_PHYS);
	for (i = 0; i < 64; i++) {
		u8 b = ((u8 *)scratch_base)[i];
		void __iomem *phys;
		u8 el2;

		/* EL2 stages live in nomap RAM at JH_SCRATCH_PHYS (not the EL1 kpage) */
		if (i >= 4 && i <= 7) {
			phys = memremap(JH_SCRATCH_PHYS + i, 1, MEMREMAP_WB);
			if (phys) {
				el2 = readb(phys);
				memunmap(phys);
				if (el2)
					b = el2;
			}
		}

		if ((i & 15) == 0)
			pr_cont("jailhouse: %04x:", i);
		pr_cont(" %02x", b);
		if ((i & 15) == 15)
			pr_cont("\n");
	}
	{
		void __iomem *trail;
		u8 len, t, k;
		char buf[16];

		trail = memremap(JH_SCRATCH_PHYS + 0x70, 16, MEMREMAP_WB);
		if (trail) {
			len = readb(trail);
			if (len > 15)
				len = 15;
			for (k = 0; k < len; k++)
				buf[k] = readb(trail + 1 + k);
			memunmap(trail);
			buf[len] = '\0';
			pr_info("jailhouse: EL2 trail @phys+0x70 len=%u \"%s\"\n",
				len, buf);
		} else {
			pr_info("jailhouse: EL2 trail memremap 0x%llx failed\n",
				(unsigned long long)(JH_SCRATCH_PHYS + 0x70));
		}
	}
	if (i & 15)
		pr_cont("\n");
	return 0;
}

static const struct kernel_param_ops jailhouse_scratch_dump_ops = {
	.set = jailhouse_scratch_dump_set,
	.get = NULL,
};

module_param_cb(scratch_dump, &jailhouse_scratch_dump_ops, NULL, 0200);
MODULE_PARM_DESC(scratch_dump,
		 "Write any value to hex-dump scratch via dmesg (Pi5; needs scratch_trace=1)");

static void jailhouse_uart_putc(char c)
{
	void __iomem *base;

	if (!uart_trace || !uart_trace_base)
		return;

	base = uart_trace_base;

	while (readl(base + 0x18) & 0x20)
		cpu_relax();
	writeb(c, base);
}

static void jailhouse_uart_mark(unsigned int cpu, char c)
{
	if (cpu > 9)
		return;

	jailhouse_uart_putc('0' + cpu);
	jailhouse_uart_putc(c);
}
#endif

/* last_console contains three members:
 *   - valid: indicates if content in the page member is present
 *   - id:    hint for the consumer if it already consumed the content
 *   - page:  actual content
 *
 * Those members are updated in following cases:
 *   - on disabling the hypervisor to print last messages
 *   - on failures when enabling the hypervisor
 *
 * We need this structure, as in those cases the hypervisor memory gets
 * unmapped.
 */
static struct {
	bool valid;
	unsigned int id;
	struct jailhouse_virt_console page;
} last_console;

#ifdef CONFIG_X86
bool jailhouse_use_vmcall;

static void init_hypercall(void)
{
	jailhouse_use_vmcall = boot_cpu_has(X86_FEATURE_VMX);
}
#else /* !CONFIG_X86 */
static void init_hypercall(void)
{
}
#endif

static void copy_console_page(struct jailhouse_virt_console *dst)
{
	unsigned int tail;

	do {
		/* spin while hypervisor is writing to console */
		while (console_page->busy)
			cpu_relax();
		tail = console_page->tail;
		rmb();

		/* copy console page */
		memcpy(dst, console_page,
		       sizeof(struct jailhouse_virt_console));
		rmb();
	} while (console_page->tail != tail || console_page->busy);
}

static inline void update_last_console(void)
{
	if (!console_available || !console_page || !hypervisor_mem)
		return;

	copy_console_page(&last_console.page);
	last_console.id++;
	last_console.valid = true;
}

static long get_max_cpus(u32 cpu_set_size,
			 const struct jailhouse_system __user *system_config)
{
	u8 __user *cpu_set =
		(u8 __user *)jailhouse_cell_cpu_set(
				(const struct jailhouse_cell_desc * __force)
				&system_config->root_cell);
	unsigned int pos = cpu_set_size;
	long max_cpu_id;
	u8 bitmap;

	while (pos-- > 0) {
		if (get_user(bitmap, cpu_set + pos))
			return -EFAULT;
		max_cpu_id = fls(bitmap);
		if (max_cpu_id > 0)
			return pos * 8 + max_cpu_id;
	}
	return -EINVAL;
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(5,8,0)
#define __get_vm_area(size, flags, start, end)			\
	__get_vm_area_caller(size, flags, start, end,		\
			     __builtin_return_address(0))
#endif

#ifdef JAILHOUSE_PI5_CANONICAL_VA
static int jailhouse_direct_count_present_pte(pte_t *pte, unsigned long addr, void *data)
{
	if (pte_present(*pte))
		(*(int *)data)++;
	return 0;
}

/*
 * Pi5: ioremap_page_range hits "remapping already mapped page" on nomap HV
 * PFNs after memremap. Clear stale PTEs, then install via ioremap_page_range
 * (set_pte-only apply_to_page_range left level-0 faults on access).
 */
static void *jailhouse_direct_ioremap_prot(phys_addr_t phys, unsigned long virt,
					   unsigned long size, pgprot_t prot)
{
	struct vm_struct *vma;
	struct mm_struct *mm;
	int err, present = 0;

	size = PAGE_ALIGN(size);
	mm = jailhouse_kernel_mm;
	if (!mm) {
		pr_err("jailhouse: direct_map: kernel mm not resolved\n");
		return NULL;
	}

	vma = __get_vm_area(size, VM_IOREMAP | VM_NO_GUARD, virt,
			    JAILHOUSE_VMAP_END);
	if (!vma) {
		pr_err("jailhouse: direct_map: __get_vm_area failed virt=0x%lx "
		       "size=0x%lx\n", virt, size);
		return NULL;
	}
	vma->phys_addr = phys;

	pr_info("jailhouse: direct_map clear+ioremap virt=0x%lx phys=0x%llx size=0x%lx\n",
		virt, (unsigned long long)phys, size);

	mmap_write_lock(mm);
	err = apply_to_page_range(mm, virt, size, jailhouse_direct_clear_pte, NULL);
	if (!err)
		err = ioremap_page_range_sym(virt, virt + size, phys, prot);
	mmap_write_unlock(mm);
	if (err) {
		pr_err("jailhouse: direct_map: ioremap_page_range err=%d\n", err);
		vunmap(vma->addr);
		return NULL;
	}

	flush_tlb_kernel_range(virt, virt + size);

	mmap_read_lock(mm);
	apply_to_page_range(mm, virt, size, jailhouse_direct_count_present_pte,
			    &present);
	mmap_read_unlock(mm);
	if (!present) {
		pr_err("jailhouse: direct_map: no present PTEs after ioremap "
		       "(virt=0x%lx size=0x%lx)\n", virt, size);
		vunmap(vma->addr);
		return NULL;
	}

	hypervisor_direct_mapped = true;
	hypervisor_direct_map_size = size;
	hypervisor_direct_vma = vma;

	pr_info("jailhouse: direct_map OK virt=%px phys=0x%llx size=0x%lx "
		"(%d pages present)\n",
		(void __force *)virt, (unsigned long long)phys, size,
		present);
	return (void __force *)virt;
}
#endif

void *jailhouse_ioremap_prot(phys_addr_t phys, unsigned long virt,
			     unsigned long size, pgprot_t prot)
{
	struct vm_struct *vma;
	struct mm_struct *mm = jailhouse_kernel_mm;
	int err;

	if (!mm)
		return NULL;

	size = PAGE_ALIGN(size);
	if (virt) {
		unsigned long flags = VM_IOREMAP;
		unsigned long end = virt + size + PAGE_SIZE;

		if (virt == JAILHOUSE_BASE) {
			flags |= VM_NO_GUARD;
			end = JAILHOUSE_VMAP_END;
		} else if (end > JAILHOUSE_VMAP_END)
			end = JAILHOUSE_VMAP_END;
		vma = __get_vm_area(size, flags, virt, end);
	} else
		vma = __get_vm_area(size, VM_IOREMAP, VMALLOC_START,
				    VMALLOC_END);
	if (!vma) {
		pr_err("jailhouse: ioremap: __get_vm_area failed phys=0x%llx "
		       "virt=0x%lx size=0x%lx\n",
		       (unsigned long long)phys, virt, size);
		return NULL;
	}
	vma->phys_addr = phys;

	pr_info("jailhouse: ioremap: got vm_area %px, calling ioremap_page_range "
		"phys=0x%llx size=0x%lx\n",
		vma->addr, (unsigned long long)phys, size);

	mmap_write_lock(mm);
	err = ioremap_page_range_sym((unsigned long)vma->addr,
				     (unsigned long)vma->addr + size, phys,
				     prot);
	mmap_write_unlock(mm);
	if (err) {
		pr_err("jailhouse: ioremap: ioremap_page_range failed phys=0x%llx "
		       "virt=0x%lx size=0x%lx err=%d\n",
		       (unsigned long long)phys, (unsigned long)vma->addr,
		       size, err);
		vunmap(vma->addr);
		return NULL;
	}

	flush_tlb_kernel_range((unsigned long)vma->addr,
			       (unsigned long)vma->addr + size);

	pr_info("jailhouse: ioremap: OK virt=%px phys=0x%llx size=0x%lx\n",
		vma->addr, (unsigned long long)phys, size);

	return vma->addr;
}

void *jailhouse_ioremap(phys_addr_t phys, unsigned long virt,
			unsigned long size)
{
#if defined(CONFIG_ARM64)
	return jailhouse_ioremap_prot(phys, virt, size, jailhouse_pgprot_rw());
#else
	return jailhouse_ioremap_prot(phys, virt, size, PAGE_KERNEL_EXEC);
#endif
}

#if defined(CONFIG_ARM64)
static bool jailhouse_exec_map_readable(void *exec_base)
{
	char sig[8];

	/*
	 * Mapping was just verified via PTE walk; plain memcpy is safe here.
	 * copy_from_kernel_nofault can fault on fresh vmalloc/ioremap aliases.
	 */
	memcpy(sig, exec_base, sizeof(sig));
	if (memcmp(sig, JAILHOUSE_SIGNATURE, sizeof(sig)) != 0) {
		pr_err("jailhouse: EXEC probe bad signature (%8ph) at %px\n",
		       sig, exec_base);
		return false;
	}
	return true;
}

static int jailhouse_arm64_make_exec(phys_addr_t hv_phys, unsigned long map_size,
				     unsigned long core_size, void **out)
{
	void *mapped;
	unsigned long start;
	int err;

	jailhouse_unmap_hypervisor_exec();

#ifdef JAILHOUSE_PI5_CANONICAL_VA
	/*
	 * Pi5 nomap HV RAM: memremap RW for firmware/config, then drop that
	 * vmap alias and install EXEC at fixed JAILHOUSE_BASE via direct PTEs
	 * in init_mm (never vm_unmap_aliases — that can hard-freeze Pi5 #4).
	 */
	unsigned long rw_vaddr, rw_size;

	if (hypervisor_memremapped && hypervisor_mem) {
		rw_vaddr = (unsigned long)hypervisor_mem;
		rw_size = PAGE_ALIGN(map_size);
		/* Flush image only (not full map_size) — large EXEC/RW flush hangs Pi5 */
		flush_icache_range(rw_vaddr, rw_vaddr + PAGE_ALIGN(core_size));
		pr_emerg("jailhouse: make_exec memunmap RW at %px size=0x%lx\n",
			 hypervisor_mem, rw_size);
		memunmap(hypervisor_mem);
		hypervisor_memremapped = false;
		hypervisor_mem = NULL;
		flush_tlb_kernel_range(rw_vaddr, rw_vaddr + rw_size);
	} else if (hypervisor_mem) {
		start = (unsigned long)hypervisor_mem;
		flush_icache_range(start, start + PAGE_ALIGN(map_size));
		vunmap(hypervisor_mem);
		hypervisor_mem = NULL;
	}

	pr_info("jailhouse: make_exec direct_map EXEC at JAILHOUSE_BASE size=0x%lx\n",
		map_size);
	mapped = jailhouse_direct_ioremap_prot(hv_phys, JAILHOUSE_BASE, map_size,
					       jailhouse_pgprot_exec());
	if (!mapped)
		return -ENOMEM;

	err = jailhouse_arm64_mark_exec_logged(JAILHOUSE_BASE, map_size);
	if (err) {
		jailhouse_direct_unmap((unsigned long)mapped, map_size);
		return err;
	}
	jailhouse_log_first_pte(JAILHOUSE_BASE);

	pr_emerg("jailhouse: make_exec probe signature at %px\n", mapped);
	if (!jailhouse_exec_map_readable(mapped)) {
		pr_err("jailhouse: EXEC map at %px not readable (JAILHOUSE_BASE "
		       "probe failed)\n", mapped);
		jailhouse_direct_unmap((unsigned long)mapped, map_size);
		return -EFAULT;
	}
	pr_emerg("jailhouse: make_exec signature OK, icache flush 0x%lx begin\n",
		 PAGE_ALIGN(core_size));
	jailhouse_pi5_flush_icache_paged((unsigned long)mapped, core_size);
	pr_emerg("jailhouse: make_exec EXEC entry+0x14=0x%08x\n",
		 jailhouse_pi5_insn_probe(mapped,
			(unsigned long)((struct jailhouse_header *)mapped)->entry));
	pr_emerg("jailhouse: make_exec probe OK (Pi5 paged EXEC icache flush 0x%lx)\n",
		 PAGE_ALIGN(core_size));
#else
	start = (unsigned long)hypervisor_mem;
	flush_icache_range(start, start + core_size);
	if (hypervisor_memremapped) {
		memunmap(hypervisor_mem);
		hypervisor_memremapped = false;
		hypervisor_mem = NULL;
	}

	pr_info("jailhouse: make_exec ioremap EXEC at JAILHOUSE_BASE size=0x%lx\n",
		map_size);
	mapped = jailhouse_ioremap_prot(hv_phys, JAILHOUSE_BASE, map_size,
					jailhouse_pgprot_exec());
	if (!mapped)
		return -ENOMEM;

	err = jailhouse_arm64_mark_exec_logged(JAILHOUSE_BASE, map_size);
	if (err) {
		vunmap(mapped);
		return err;
	}
#endif

#ifndef JAILHOUSE_PI5_CANONICAL_VA
	if (!jailhouse_exec_map_readable(mapped)) {
		pr_err("jailhouse: EXEC map at %px not readable (JAILHOUSE_BASE "
		       "PTE probe failed)\n", mapped);
		vunmap(mapped);
		return -EFAULT;
	}
#endif

	hypervisor_exec = mapped;
#ifdef JAILHOUSE_PI5_CANONICAL_VA
	/*
	 * EXEC map at JAILHOUSE_BASE is RX; restore WB memremap for header/config
	 * writes. arch_entry reads the same physical header via EXEC alias.
	 */
	hypervisor_mem = jailhouse_map_hypervisor_rw(hv_phys, map_size);
	if (!hypervisor_mem) {
		jailhouse_direct_unmap((unsigned long)mapped, map_size);
		hypervisor_exec = NULL;
		return -ENOMEM;
	}
	pr_emerg("jailhouse: Pi5 dual map EXEC=%px RW=%px size=0x%lx\n",
		  mapped, hypervisor_mem, map_size);
#else
	hypervisor_mem = mapped;
#endif
	*out = mapped;
	return 0;
}
#endif

/*
 * Called for each cpu by the JAILHOUSE_ENABLE ioctl.
 * It jumps to the entry point set in the header, reports the result and
 * signals completion to the main thread that invoked it.
 */
static void enter_hypervisor(void *info)
{
	struct jailhouse_header *header = info;
	unsigned int cpu = smp_processor_id();
	int (*entry)(unsigned int);
	int err = -EINVAL;

	pr_emerg("jailhouse: enter_hypervisor entered cpu=%u max_cpus=%u\n",
		 cpu, header ? header->max_cpus : 0U);

#if defined(CONFIG_ARM64)
	if (hypervisor_exec)
		entry = header->entry + (unsigned long)hypervisor_exec;
	else
#endif
		entry = header->entry + (unsigned long)hypervisor_mem;

#if defined(CONFIG_ARM64)
	if (cpu < header->max_cpus) {
		pr_emerg("jailhouse: CPU%u enter_hypervisor entry=%px bootstrap_pa=0x%llx max_cpus=%u\n",
			 cpu, entry, (unsigned long long)jailhouse_bootstrap_pa,
			 header->max_cpus);
	}
#endif

	if (cpu < header->max_cpus) {
#if defined(CONFIG_ARM64)
		if (scratch_trace)
			jailhouse_scratch_put(cpu, '>');
#if defined(JAILHOUSE_PI5_CANONICAL_VA)
		jailhouse_pi5_stage("pre-BLR", 'M');
		if (jailhouse_pi5_insn_probe(entry, 0) == JH_PI5_STALE_UART_INSN) {
			pr_err("jailhouse: CPU%u stale UART insn at EXEC — abort BLR\n",
			       cpu);
			err = -EINVAL;
			goto arch_entry_done;
		}
		{
			u32 exit_insn = jailhouse_pi5_insn_at(entry,
					JH_PI5_ARCH_ENTRY_EXIT_OFF);

			if (exit_insn == JH_PI5_INSN_MOV_W0_11) {
				pr_err("jailhouse: CPU%u EXEC is smoke HV "
				       "(exit+0x80=0x%08x) — cp production "
				       "jailhouse.bin and retry\n",
				       cpu, exit_insn);
				err = -EINVAL;
				goto arch_entry_done;
			}
		}
		if (!stub_arch_entry && !header->el2_entry_off) {
			pr_err("jailhouse: CPU%u HV header missing el2_entry_off — "
			       "stale jailhouse.bin?\n", cpu);
			err = -EINVAL;
			goto arch_entry_done;
		}
		if (scratch_trace && hypervisor_exec) {
			u32 insn = jailhouse_pi5_insn_probe(entry, 0);

			pr_emerg("jailhouse: CPU%u pre-BLR OK insn@+0x14=0x%08x "
				 "el2_off=0x%lx scratch=%px\n",
				 cpu, insn, header->el2_entry_off,
				 ((struct jailhouse_header *)hypervisor_exec)
					 ->scratch_trace_base);
		} else if (!stub_arch_entry && header->el2_entry_off) {
			pr_emerg("jailhouse: CPU%u pre-BLR OK el2_off=0x%lx pa=0x%llx\n",
				 cpu, header->el2_entry_off,
				 (unsigned long long)(jailhouse_hv_phys +
						      header->el2_entry_off));
		}
		if (scratch_trace && scratch_base)
			writeb('N', scratch_base + JH_SCRATCH_STAGE_OFF);
		jailhouse_pi5_stage("BLR imminent", 'N');
		/* One page only — full EXEC flush before BLR hard-freezes Pi5 */
		if (hypervisor_exec)
			jailhouse_pi5_flush_icache_paged((unsigned long)entry &
							 PAGE_MASK, PAGE_SIZE);
		pr_emerg("jailhouse: CPU%u BLR arch_entry %px now\n", cpu, entry);
		if (stub_arch_entry)
			err = jailhouse_pi5_arch_entry_stub(cpu);
		else {
			if (scratch_trace && scratch_base)
				writeb('b', scratch_base + 9);
			err = jailhouse_pi5_call_arch_entry(entry, cpu);
			pr_emerg("jailhouse: CPU%u BLR arch_entry returned %d\n",
				 cpu, err);
			if (err == 11) {
				pr_err("jailhouse: CPU%u arch_entry ret 11 (smoke HV) "
				       "— run: bash crtos/scripts/rebuild_jailhouse_pi5__重编HV与驱动.sh "
				       "&& sudo cp hypervisor/jailhouse.bin /lib/firmware/\n",
				       cpu);
			}
			if (scratch_trace && scratch_base)
				writeb('c', scratch_base + 9);
		}
		if (!err && !stub_arch_entry && jailhouse_bootstrap_pa &&
		    header->el2_entry_off) {
			phys_addr_t el2_pa = jailhouse_hv_phys + header->el2_entry_off;

			if (scratch_trace)
				writeb('R', scratch_base + 3);
			if (scratch_trace && scratch_base)
				writeb(cpu, scratch_base + 7);
			if (hypervisor_exec) {
				unsigned long el2_va = (unsigned long)hypervisor_exec +
						       header->el2_entry_off;

				jailhouse_pi5_flush_icache_paged(el2_va & PAGE_MASK,
								 PAGE_SIZE);
			}
			/*
			 * Pi5 EL2: cpu_soft_restart(1, el2_pa) disables MMU then
			 * HVC_SOFT_RESTART — same as kexec. Bare hvc with PA while
			 * VHE MMU is on fetches via EL1 PT (0x1fc… unmapped) and hangs.
			 */
			if (jailhouse_cpu_soft_restart) {
				pr_emerg("jailhouse: CPU%u cpu_soft_restart el2 pa=0x%llx\n",
					 cpu, (unsigned long long)el2_pa);
				if (scratch_trace && scratch_base)
					writeb('S', scratch_base + 10);
				dsb(sy);
				isb();
				jailhouse_cpu_soft_restart(1, el2_pa, 0, 0, 0);
			} else {
				pr_emerg("jailhouse: CPU%u SOFT_RESTART el2 pa=0x%llx\n",
					 cpu, (unsigned long long)el2_pa);
				jailhouse_hvc_soft_restart(el2_pa);
			}
			pr_emerg("jailhouse: CPU%u SOFT_RESTART returned (unexpected)\n",
				 cpu);
			err = -EIO;
		}
#else
		err = entry(cpu);
#endif
arch_entry_done:
#if defined(CONFIG_ARM64)
		pr_emerg("jailhouse: CPU%u arch_entry returned %d\n", cpu, err);
#endif
#else
		err = entry(cpu);
arch_entry_done:
#endif
	} else {
		err = -EINVAL;
	}

#if defined(CONFIG_ARM64)
	if (trace_el2 && cpu <= 9)
		pr_info("jailhouse: CPU%d enter_hypervisor returned %d\n",
			cpu, err);

	if (scratch_trace)
		jailhouse_scratch_put(cpu, err ? '!' : '<');
#endif

	if (err && cpu < header->max_cpus)
		error_code = err;

#if defined(CONFIG_X86) && LINUX_VERSION_CODE >= KERNEL_VERSION(4,0,0)
	/* on Intel, VMXE is now on - update the shadow */
	if (boot_cpu_has(X86_FEATURE_VMX) && !err) {
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5,5,0)
		cr4_set_bits_irqsoff(X86_CR4_VMXE);
#else
		cr4_set_bits(X86_CR4_VMXE);
#endif
	}
#endif

	atomic_inc(&call_done);
}

static inline const char * jailhouse_get_fw_name(void)
{
#ifdef CONFIG_X86
	if (boot_cpu_has(X86_FEATURE_SVM))
		return JAILHOUSE_AMD_FW_NAME;
	if (boot_cpu_has(X86_FEATURE_VMX))
		return JAILHOUSE_INTEL_FW_NAME;
	return NULL;
#else
	return JAILHOUSE_FW_NAME;
#endif
}

static int __jailhouse_console_dump_delta(struct jailhouse_virt_console
						*console,
					  char *dst, unsigned int head,
					  unsigned int *miss)
{
	int ret;
	unsigned int head_mod, tail_mod;
	unsigned int delta, missed = 0;

	/* we might underflow here intentionally */
	delta = console->tail - head;

	/* check if we have misses */
	if (delta > sizeof(console->content)) {
		missed = delta - sizeof(console->content);
		head = console->tail - sizeof(console->content);
		delta = sizeof(console->content);
	}

	head_mod = head % sizeof(console->content);
	tail_mod = console->tail % sizeof(console->content);

	if (head_mod + delta > sizeof(console->content)) {
		ret = sizeof(console->content) - head_mod;
		memcpy(dst, console->content + head_mod, ret);
		delta -= ret;
		memcpy(dst + ret, console->content, delta);
		ret += delta;
	} else {
		ret = delta;
		memcpy(dst, console->content + head_mod, delta);
	}

	if (miss)
		*miss = missed;

	return ret;
}

static void jailhouse_firmware_free(void)
{
	jailhouse_sysfs_core_exit(jailhouse_dev);
	if (hypervisor_mem_res) {
		release_mem_region(hypervisor_mem_res->start,
				   resource_size(hypervisor_mem_res));
		hypervisor_mem_res = NULL;
	}
	if (hypervisor_mem) {
#if defined(CONFIG_ARM64)
		jailhouse_unmap_hypervisor_mem();
#else
		vunmap(hypervisor_mem);
		hypervisor_mem = NULL;
#endif
	}
#if defined(CONFIG_ARM64)
	jailhouse_unmap_hypervisor_exec();
	jailhouse_scratch_free();
#endif
}

static int jailhouse_force_cleanup_set(const char *val, const struct kernel_param *kp)
{
	if (jailhouse_enabled)
		return -EBUSY;

	jailhouse_firmware_free();

	if (module_refcount(THIS_MODULE) > 0)
		module_put(THIS_MODULE);

	if (mutex_is_locked(&jailhouse_lock)) {
		pr_warn("jailhouse: force_cleanup: breaking stuck jailhouse_lock\n");
		mutex_unlock(&jailhouse_lock);
	}

	pr_warn("jailhouse: force_cleanup done (refcnt=%u); safe to rmmod\n",
		module_refcount(THIS_MODULE));
	return 0;
}

static const struct kernel_param_ops jailhouse_force_cleanup_ops = {
	.set = jailhouse_force_cleanup_set,
	.get = NULL,
};

module_param_cb(force_cleanup, &jailhouse_force_cleanup_ops, NULL, 0200);
MODULE_PARM_DESC(force_cleanup,
		 "Write any value after failed enable to unmap HV and drop module ref");

int jailhouse_console_dump_delta(char *dst, unsigned int head,
				 unsigned int *miss)
{
	int ret;
	struct jailhouse_virt_console *console;

	if (!jailhouse_enabled)
		return -EAGAIN;

	if (!console_available)
		return -EPERM;

	console = kmalloc(sizeof(struct jailhouse_virt_console), GFP_KERNEL);
	if (console == NULL)
		return -ENOMEM;

	copy_console_page(console);
	if (console->tail == head) {
		ret = 0;
		goto console_free_out;
	}

	ret = __jailhouse_console_dump_delta(console, dst, head, miss);

console_free_out:
	kfree(console);
	return ret;
}

/* See Documentation/bootstrap-interface.txt */
static int jailhouse_cmd_enable(struct jailhouse_system __user *arg)
{
	const struct firmware *hypervisor;
	struct jailhouse_system config_header;
	struct jailhouse_system *config;
	struct jailhouse_memory *hv_mem = &config_header.hypervisor_memory;
	struct jailhouse_header *header;
	unsigned long remap_addr = 0;
	void __iomem *console = NULL, *clock_reg = NULL;
	unsigned long config_size;
	unsigned int clock_gates;
	const char *fw_name;
	long max_cpus;
	int err;

	fw_name = jailhouse_get_fw_name();
	if (!fw_name) {
		pr_err("jailhouse: Missing or unsupported HVM technology\n");
		return -ENODEV;
	}

	if (copy_from_user(&config_header, arg, sizeof(config_header)))
		return -EFAULT;

	if (memcmp(config_header.signature, JAILHOUSE_SYSTEM_SIGNATURE,
		   sizeof(config_header.signature)) != 0) {
		pr_err("jailhouse: Not a system configuration\n");
		return -EINVAL;
	}
	if (config_header.revision != JAILHOUSE_CONFIG_REVISION) {
		pr_err("jailhouse: Configuration revision mismatch\n");
		return -EINVAL;
	}
	if (config_header.architecture != JAILHOUSE_ARCHITECTURE) {
		pr_err("jailhouse: Configuration architecture mismatch\n");
		return -EINVAL;
	}

	config_header.root_cell.name[JAILHOUSE_CELL_NAME_MAXLEN] = 0;

	max_cpus = get_max_cpus(config_header.root_cell.cpu_set_size, arg);
	if (max_cpus < 0)
		return max_cpus;
	if (max_cpus > UINT_MAX)
		return -EINVAL;

	if (mutex_lock_interruptible(&jailhouse_lock) != 0)
		return -EINTR;

	pr_info("jailhouse: enable start (dry_run=%d el2_stop=%d make_exec_stop=%d hyp_probe=%d)\n",
		dry_run, el2_stop, make_exec_stop, hyp_probe);

	err = -EBUSY;
	if (jailhouse_enabled || !try_module_get(THIS_MODULE))
		goto error_unlock;

#ifdef CONFIG_ARM
	/* open-coded is_hyp_mode_available to use __boot_cpu_mode_sym */
	if ((*__boot_cpu_mode_sym & MODE_MASK) != HYP_MODE ||
	    (*__boot_cpu_mode_sym) & BOOT_CPU_MODE_MISMATCH) {
		pr_err("jailhouse: HYP mode not available\n");
		err = -ENODEV;
		goto error_put_module;
	}
#endif
#ifdef CONFIG_X86
	if (boot_cpu_has(X86_FEATURE_VMX)) {
		u64 features;

		rdmsrl(MSR_IA32_FEAT_CTL, features);
		if ((features & FEAT_CTL_VMX_ENABLED_OUTSIDE_SMX) == 0) {
			pr_err("jailhouse: VT-x disabled by Firmware/BIOS\n");
			err = -ENODEV;
			goto error_put_module;
		}
	}
#endif

	/* Load hypervisor image */
	err = request_firmware(&hypervisor, fw_name, jailhouse_dev);
	if (err) {
		pr_err("jailhouse: Missing hypervisor image %s\n", fw_name);
		goto error_put_module;
	}

	header = (struct jailhouse_header *)hypervisor->data;
#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
	pr_emerg("jailhouse: request_firmware entry+0x14=0x%08x size=%zu\n",
		 jailhouse_pi5_insn_probe(hypervisor->data,
					  (unsigned long)header->entry),
		 hypervisor->size);
#endif

	err = -EINVAL;
	if (memcmp(header->signature, JAILHOUSE_SIGNATURE,
		   sizeof(header->signature)) != 0 ||
	    hypervisor->size >= hv_mem->size)
		goto error_release_fw;

	hv_core_and_percpu_size = header->core_size +
		max_cpus * header->percpu_size;
	config_size = jailhouse_system_config_size(&config_header);
	if (hv_core_and_percpu_size >= hv_mem->size ||
	    config_size >= hv_mem->size - hv_core_and_percpu_size)
		goto error_release_fw;

#if defined(CONFIG_ARM64)
	/*
	 * Pi5: memremap RW for all enable stages; make_exec maps EXEC at
	 * JAILHOUSE_BASE after memunmap (never ioremap+memremap same PFNs).
	 */
	remap_addr = 0;
#elif defined(JAILHOUSE_BORROW_ROOT_PT)
	remap_addr = JAILHOUSE_BASE;
#endif
	/* Unmap hypervisor_mem from a previous "enable". The mapping has to be
	 * redone since the root-cell config might have changed. */
	jailhouse_firmware_free();

	hypervisor_mem_res = request_mem_region(hv_mem->phys_start,
						hv_mem->size,
						"Jailhouse hypervisor");
	if (!hypervisor_mem_res) {
		pr_err("jailhouse: request_mem_region failed for hypervisor "
		       "memory.\n");
		pr_notice("jailhouse: Did you reserve the memory with "
			  "\"memmap=\" or \"mem=\"?\n");
		goto error_release_fw;
	}

	/* Map physical memory region reserved for Jailhouse. */
#if defined(CONFIG_ARM64)
	pr_info("jailhouse: memremap hv phys=0x%llx size=0x%lx\n",
		(unsigned long long)hv_mem->phys_start,
		(unsigned long)hv_mem->size);
	hypervisor_mem = jailhouse_map_hypervisor_rw(hv_mem->phys_start,
						     hv_mem->size);
	if (!hypervisor_mem) {
		pr_err("jailhouse: Unable to map RAM reserved for hypervisor "
		       "at %08lx\n", (unsigned long)hv_mem->phys_start);
		goto error_release_memreg;
	}

	pr_info("jailhouse: hv mapped at %px (phys=0x%llx size=0x%lx)\n",
		hypervisor_mem, (unsigned long long)hv_mem->phys_start,
		(unsigned long)hv_mem->size);

	console_page = (struct jailhouse_virt_console *)
		(hypervisor_mem + header->console_page);
	last_console.valid = false;

#if defined(JAILHOUSE_PI5_CANONICAL_VA)
	{
		unsigned long entry_off = (unsigned long)header->entry;
		int fw_err = jailhouse_pi5_copy_firmware(hypervisor_mem, hv_mem->size,
							 hypervisor->size,
							 entry_off);

		if (fw_err) {
			pr_err("jailhouse: Pi5 firmware load failed (%d) — "
			       "not using request_firmware cache\n", fw_err);
			err = fw_err;
			goto error_unmap;
		}
	}
#else
	memcpy(hypervisor_mem, hypervisor->data, hypervisor->size);
#endif
	memset(hypervisor_mem + hypervisor->size, 0,
	       hv_mem->size - hypervisor->size);

	header = (struct jailhouse_header *)hypervisor_mem;
	header->max_cpus = max_cpus;

#if defined(CONFIG_ARM) || defined(CONFIG_ARM64)
	header->arm_linux_hyp_vectors =
		virt_to_phys(*__hyp_stub_vectors_sym);
#if LINUX_VERSION_CODE < KERNEL_VERSION(4,12,0)
	header->arm_linux_hyp_abi = HYP_STUB_ABI_LEGACY;
#else
	header->arm_linux_hyp_abi = HYP_STUB_ABI_OPCODE;
#endif
#endif
#else
	pr_info("jailhouse: ioremap hv phys=0x%llx size=0x%lx (remap_addr=0x%lx)\n",
		(unsigned long long)hv_mem->phys_start,
		(unsigned long)hv_mem->size, remap_addr);
	hypervisor_mem = jailhouse_ioremap(hv_mem->phys_start, remap_addr,
					   hv_mem->size);
	if (!hypervisor_mem) {
		pr_err("jailhouse: Unable to map RAM reserved for hypervisor "
		       "at %08lx\n", (unsigned long)hv_mem->phys_start);
		goto error_release_memreg;
	}

	pr_info("jailhouse: hv mapped at %px (phys=0x%llx size=0x%lx)\n",
		hypervisor_mem, (unsigned long long)hv_mem->phys_start,
		(unsigned long)hv_mem->size);

	console_page = (struct jailhouse_virt_console*)
		(hypervisor_mem + header->console_page);
	last_console.valid = false;

	memcpy(hypervisor_mem, hypervisor->data, hypervisor->size);
	memset(hypervisor_mem + hypervisor->size, 0,
	       hv_mem->size - hypervisor->size);

	header = (struct jailhouse_header *)hypervisor_mem;
	header->max_cpus = max_cpus;

#if defined(CONFIG_ARM) || defined(CONFIG_ARM64)
	header->arm_linux_hyp_vectors = virt_to_phys(*__hyp_stub_vectors_sym);
#if LINUX_VERSION_CODE < KERNEL_VERSION(4,12,0)
	header->arm_linux_hyp_abi = HYP_STUB_ABI_LEGACY;
#else
	header->arm_linux_hyp_abi = HYP_STUB_ABI_OPCODE;
#endif
#endif
#endif

#if defined(CONFIG_ARM64)
	if (dry_run) {
		pr_info("jailhouse: dry_run OK - RW map + firmware copy done "
			"(core_size=%lu); skipping cell/sysfs/make_exec/EL2\n",
			(unsigned long)header->core_size);
		jailhouse_unmap_hypervisor_mem();
		release_mem_region(hypervisor_mem_res->start,
				   resource_size(hypervisor_mem_res));
		hypervisor_mem_res = NULL;
		release_firmware(hypervisor);
		module_put(THIS_MODULE);
		mutex_unlock(&jailhouse_lock);
		return 0;
	}
#endif

	err = jailhouse_sysfs_core_init(jailhouse_dev, header->core_size);
	if (err)
		goto error_unmap;

	/*
	 * ARMv8 requires to clean D-cache and invalidate I-cache for memory
	 * containing new instructions. On x86 this is a NOP. On ARMv7 the
	 * firmware does its own cache maintenance, so it is an
	 * extraneous (but harmless) flush.
	 */
	flush_icache_range((unsigned long)hypervisor_mem,
			   (unsigned long)(hypervisor_mem + header->core_size));

#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
	if (!dry_run) {
		pr_emerg("jailhouse: Pi5 skip pre-make_exec kick_all_cpus_sync\n");
		smp_mb();
	}
#else
	if (!dry_run)
		kick_all_cpus_sync();
#endif

	/* Copy system configuration to its target address in hypervisor memory
	 * region. */
	config = (struct jailhouse_system *)
		(hypervisor_mem + hv_core_and_percpu_size);
	if (copy_from_user(config, arg, config_size)) {
		err = -EFAULT;
		goto error_unmap;
	}

	if (config->debug_console.clock_reg) {
		clock_reg = jailhouse_ioremap_prot(config->debug_console.clock_reg,
						 0, sizeof(clock_gates),
						 jailhouse_pgprot_rw());
		if (!clock_reg) {
			err = -EINVAL;
			pr_err("jailhouse: Unable to map clock register at "
			       "%08lx\n",
			       (unsigned long)config->debug_console.clock_reg);
			goto error_unmap;
		}

		clock_gates = readl(clock_reg);
		if (CON_HAS_INVERTED_GATE(config->debug_console.flags))
			clock_gates &= ~(1 << config->debug_console.gate_nr);
		else
			clock_gates |= (1 << config->debug_console.gate_nr);
		writel(clock_gates, clock_reg);

		vunmap((void __force *)clock_reg);
	}

#ifdef JAILHOUSE_BORROW_ROOT_PT
	if (CON_IS_MMIO(config->debug_console.flags)) {
		console = ioremap(config->debug_console.address,
				  config->debug_console.size);
		if (!console) {
			err = -EINVAL;
			pr_err("jailhouse: Unable to map hypervisor debug "
			       "console at %08lx\n",
			       (unsigned long)config->debug_console.address);
			goto error_unmap;
		}
		/* The hypervisor has no notion of address spaces, so we need
		 * to enforce conversion. */
		header->debug_console_base = (void * __force)console;
	}
#endif

	console_available = SYS_FLAGS_VIRTUAL_DEBUG_CONSOLE(config->flags);

#ifdef CONFIG_X86
	if (config->platform_info.x86.tsc_khz == 0)
		config->platform_info.x86.tsc_khz = tsc_khz;
	if (config->platform_info.x86.apic_khz == 0)
		config->platform_info.x86.apic_khz =
			*lapic_timer_period_sym / (1000 / HZ);
#endif

	err = jailhouse_cell_prepare_root(&config->root_cell);
	if (err)
		goto error_unmap;

#if defined(CONFIG_ARM64)
	if (uart_trace || scratch_trace)
		jailhouse_trace_maps_init();
#ifdef JAILHOUSE_PI5_CANONICAL_VA
	/* Must land in physical header before make_exec marks EXEC map RX. */
	if (scratch_trace && scratch_base)
		header->scratch_trace_base = scratch_base;
	else
		header->scratch_trace_base = NULL;
#endif
#endif

#if defined(CONFIG_ARM64)
	if (el2_stop) {
		pr_info("jailhouse: el2_stop - memremap+config OK, skip "
			"make_exec/EL2 (core_size=%lu)\n",
			(unsigned long)header->core_size);
		err = 0;
		goto error_free_cell;
	}

	{
		int wx_err;
		phys_addr_t hv_phys = hv_mem->phys_start;

		jailhouse_hv_phys = hv_phys;
		unsigned long map_size = PAGE_ALIGN(hv_mem->size);
		unsigned long exec_size = jailhouse_exec_map_size(hv_mem->size,
								  hv_core_and_percpu_size,
								  config_size);
		void *exec_map;

		pr_info("jailhouse: make_exec exec_size=0x%lx (full hv=0x%lx)\n",
			exec_size, map_size);
#if defined(JAILHOUSE_PI5_CANONICAL_VA)
		jailhouse_pi5_flush_icache_paged((unsigned long)hypervisor_mem,
						 PAGE_ALIGN(header->core_size));
#else
		flush_icache_range((unsigned long)hypervisor_mem,
				   (unsigned long)hypervisor_mem +
					   PAGE_ALIGN(header->core_size));
#endif
		wx_err = jailhouse_arm64_make_exec(hv_phys, exec_size,
						   header->core_size, &exec_map);
		if (wx_err) {
			pr_err("jailhouse: make_exec failed: %d (phys=0x%llx "
			       "JAILHOUSE_BASE=0x%llx exec_size=0x%lx)\n",
			       wx_err, (unsigned long long)hv_phys,
			       (unsigned long long)JAILHOUSE_BASE, exec_size);
			err = wx_err;
			goto error_free_cell;
		}
		pr_emerg("jailhouse: make_exec done EXEC=%px RW=%px bootstrap_pa pending\n",
			 exec_map, hypervisor_mem);
		header = (struct jailhouse_header *)hypervisor_mem;
		config = (struct jailhouse_system *)
			(hypervisor_mem + hv_core_and_percpu_size);
		console_page = (struct jailhouse_virt_console *)
			(hypervisor_mem + header->console_page);
		jailhouse_bootstrap_pa = jailhouse_hv_phys +
					 header->bootstrap_vectors_off;
		pr_emerg("jailhouse: bootstrap_pa=0x%llx (hv=0x%llx off=0x%lx)\n",
			(unsigned long long)jailhouse_bootstrap_pa,
			(unsigned long long)jailhouse_hv_phys,
			header->bootstrap_vectors_off);
		pr_emerg("jailhouse: make_exec block done\n");
#ifdef JAILHOUSE_PI5_CANONICAL_VA
		if (scratch_trace && scratch_base) {
			header->scratch_trace_base = scratch_base;
			if (hypervisor_exec)
				((struct jailhouse_header *)hypervisor_exec)
					->scratch_trace_base = scratch_base;
			smp_wmb();
			jailhouse_pi5_flush_icache_paged(
				(unsigned long)hypervisor_exec +
					(unsigned long)header->entry,
				PAGE_SIZE);
			pr_emerg("jailhouse: scratch_trace_base re-patched %px\n",
				 header->scratch_trace_base);
		}
#endif
	}

	if (make_exec_stop) {
		pr_info("jailhouse: make_exec_stop - EXEC map OK, skip EL2 "
			"(core_size=%lu); leave maps up (reboot to clean)\n",
			(unsigned long)header->core_size);
		update_last_console();
		jailhouse_cell_delete_root();
		jailhouse_sysfs_core_exit(jailhouse_dev);
		release_firmware(hypervisor);
		module_put(THIS_MODULE);
		mutex_unlock(&jailhouse_lock);
		return 0;
	}
#endif

#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
	/*
	 * Post-make_exec kick_all_cpus_sync IPIs all online CPUs; on Pi5 this
	 * intermittently hard-freezes before EL2 (netconsole stops at bootstrap_pa).
	 * Pre-make_exec kick already ran; 1-CPU sequential EL2 only needs CPU0.
	 */
	pr_emerg("jailhouse: Pi5 skip post-make_exec kick_all_cpus_sync\n");
	smp_mb();
	pr_emerg("jailhouse: Pi5 post-make_exec barrier (before stage K)\n");
	jailhouse_pi5_stage("post-kick barrier", 'K');
#else
	pr_emerg("jailhouse: kick_all_cpus_sync begin\n");
	kick_all_cpus_sync();
	pr_emerg("jailhouse: kick_all_cpus_sync done\n");
#endif

#if defined(CONFIG_ARM64)
	if (hyp_probe) {
#ifdef JAILHOUSE_PI5_CANONICAL_VA
		/*
		 * on_each_cpu HVC on Pi5 can hang secondaries; 1-CPU cell only
		 * needs CPU0 verified before arch_entry SOFT_RESTART.
		 */
		pr_emerg("jailhouse: hyp stub probe CPU0 only (Pi5)...\n");
		jailhouse_hyp_stub_probe_cpu(NULL);
		pr_emerg("jailhouse: hyp stub probe OK\n");
#else
		pr_info("jailhouse: hyp stub probe (%u CPUs)...\n",
			num_online_cpus());
		on_each_cpu(jailhouse_hyp_stub_probe_cpu, NULL, 1);
		pr_info("jailhouse: hyp stub probe OK\n");
#endif
	}
#endif

	error_code = 0;

#if defined(CONFIG_ARM64)
	if (scratch_trace && scratch_base && header) {
		smp_wmb();
		writeb('T', scratch_base);
		wmb();
		pr_emerg("jailhouse: scratch_trace_base=%px phys=0x%llx probe=%02x\n",
			 scratch_base, (unsigned long long)JH_SCRATCH_PHYS,
			 readb(scratch_base));
	} else if (scratch_trace) {
		pr_emerg("jailhouse: scratch_trace_base=%px phys=0x%llx\n",
			 scratch_base, (unsigned long long)JH_SCRATCH_PHYS);
	}
#endif

#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
	jailhouse_pi5_stage("pre-enter_hypervisor", 'L');
#else
	preempt_disable();
#endif

	pr_emerg("jailhouse: enter_hypervisor begin (online=%u max_cpus=%u stub=%d)\n",
		 num_online_cpus(), header->max_cpus, (int)stub_arch_entry);

	header->online_cpus = num_online_cpus();

	pr_emerg("jailhouse: about to enter_hypervisor (sequential=%d cpus=%u max=%u)\n",
		 (int)sequential_el2, header->online_cpus, header->max_cpus);

#if defined(CONFIG_ARM64)
	pr_emerg("jailhouse: on_each_cpu enter_hypervisor (%u CPUs, rw=%px exec=%px "
		"phys=0x%llx JAILHOUSE_BASE=0x%llx)\n",
		num_online_cpus(), hypervisor_mem, hypervisor_exec,
		(unsigned long long)hv_mem->phys_start,
		(unsigned long long)JAILHOUSE_BASE);
#else
	pr_info("jailhouse: on_each_cpu enter_hypervisor (%u CPUs)\n",
		num_online_cpus());
#endif

	atomic_set(&call_done, 0);
#if defined(CONFIG_ARM64)
	if (sequential_el2) {
		unsigned int cpu;

		pr_emerg("jailhouse: sequential_el2 - one CPU at a time\n");
		for_each_online_cpu(cpu) {
			unsigned int me = smp_processor_id();

#if defined(JAILHOUSE_PI5_CANONICAL_VA)
			if (cpu >= header->max_cpus) {
				pr_emerg("jailhouse: sequential CPU%d skip IPI (max_cpus=%u)\n",
					 cpu, header->max_cpus);
				atomic_inc(&call_done);
				continue;
			}
#endif
			pr_emerg("jailhouse: sequential CPU%d caller=%u (max=%u)\n",
				 cpu, me, header->max_cpus);
			if (cpu == 0 && header && hypervisor_exec)
				pr_emerg("jailhouse: arch_entry=%px bootstrap=%px off=0x%lx\n",
					(void *)(header->entry +
						 (unsigned long)hypervisor_exec),
					(void *)((unsigned long)hypervisor_exec +
						 header->bootstrap_vectors_off),
					header->bootstrap_vectors_off);
			pr_emerg("jailhouse: sequential CPU%d invoking enter_hypervisor\n",
				 cpu);
			if (cpu == me)
				pr_emerg("jailhouse: sequential CPU%d enter_hypervisor direct\n",
					 cpu);
			if (cpu == me)
				enter_hypervisor(header);
			else
				smp_call_function_single(cpu, enter_hypervisor, header, 1);
			pr_emerg("jailhouse: sequential CPU%d done (still alive)\n", cpu);
		}
	} else
#endif
		on_each_cpu(enter_hypervisor, header, 0);
	while (atomic_read(&call_done) != num_online_cpus())
		cpu_relax();

#if defined(CONFIG_ARM64) && defined(JAILHOUSE_PI5_CANONICAL_VA)
#else
	preempt_enable();
#endif

	pr_emerg("jailhouse: all CPUs returned from enter_hypervisor (error_code=%d)\n",
		 error_code);

	if (error_code) {
		err = error_code;
		goto error_free_cell;
	}

	if (console)
		iounmap(console);

	release_firmware(hypervisor);

	jailhouse_cell_register_root();
	jailhouse_pci_virtual_root_devices_add(&config_header);

	jailhouse_enabled = true;

	mutex_unlock(&jailhouse_lock);

	pr_info("The Jailhouse is opening.\n");

	return 0;

error_free_cell:
	update_last_console();
	jailhouse_cell_delete_root();

error_unmap:
	jailhouse_firmware_free();
	if (console)
		iounmap(console);

error_release_memreg:
	/* jailhouse_firmware_free() could have been called already and
	 * has released hypervisor_mem_res. */
	if (hypervisor_mem_res)
		release_mem_region(hypervisor_mem_res->start,
				resource_size(hypervisor_mem_res));
	hypervisor_mem_res = NULL;

error_release_fw:
	release_firmware(hypervisor);

error_put_module:
	module_put(THIS_MODULE);

error_unlock:
	mutex_unlock(&jailhouse_lock);
	return err;
}

static void leave_hypervisor(void *info)
{
	void *page;
	int err;

	/* Touch each hypervisor page we may need during the switch so that
	 * the active mm definitely contains all mappings. At least x86 does
	 * not support taking any faults while switching worlds. */
	for (page = hypervisor_mem;
	     page < hypervisor_mem + hv_core_and_percpu_size;
	     page += PAGE_SIZE)
		readl((void __iomem *)page);

	/* either returns 0 or the same error code across all CPUs */
	err = jailhouse_call(JAILHOUSE_HC_DISABLE);
	if (err)
		error_code = err;

#if defined(CONFIG_X86) && LINUX_VERSION_CODE >= KERNEL_VERSION(4,0,0)
	/* on Intel, VMXE is now off - update the shadow */
	if (boot_cpu_has(X86_FEATURE_VMX) && !err) {
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5,5,0)
		cr4_clear_bits_irqsoff(X86_CR4_VMXE);
#else
		cr4_clear_bits(X86_CR4_VMXE);
#endif
	}
#endif

	atomic_inc(&call_done);
}

static int jailhouse_cmd_disable(void)
{
	int err;

	if (mutex_lock_interruptible(&jailhouse_lock) != 0)
		return -EINTR;

	if (!jailhouse_enabled) {
		err = -EINVAL;
		goto unlock_out;
	}

	err = jailhouse_cmd_cell_destroy_non_root();
	if (err)
		goto unlock_out;

	jailhouse_pci_virtual_root_devices_remove();

	error_code = 0;

	preempt_disable();

	if (num_online_cpus() != cpumask_weight(&root_cell->cpus_assigned)) {
		/*
		 * Not all assigned CPUs are currently online. If we disable
		 * now, we will lose the offlined ones.
		 */

		preempt_enable();

		err = -EBUSY;
		goto unlock_out;
	}

#ifdef CONFIG_ARM
	/*
	 * This flag has been set when onlining a CPU under Jailhouse
	 * supervision into SVC instead of HYP mode.
	 */
	*__boot_cpu_mode_sym &= ~BOOT_CPU_MODE_MISMATCH;
#endif

	atomic_set(&call_done, 0);
	/* See jailhouse_cmd_enable while wait=true does not work. */
	on_each_cpu(leave_hypervisor, NULL, 0);
	while (atomic_read(&call_done) != num_online_cpus())
		cpu_relax();

	preempt_enable();

	err = error_code;
	if (err)
		goto unlock_out;

	update_last_console();

	jailhouse_cell_delete_root();
	jailhouse_enabled = false;
	module_put(THIS_MODULE);

	pr_info("The Jailhouse was closed.\n");

unlock_out:
	mutex_unlock(&jailhouse_lock);

	return err;
}

static long jailhouse_ioctl(struct file *file, unsigned int ioctl,
			    unsigned long arg)
{
	long err;

	switch (ioctl) {
	case JAILHOUSE_ENABLE:
		err = jailhouse_cmd_enable(
			(struct jailhouse_system __user *)arg);
		break;
	case JAILHOUSE_DISABLE:
		err = jailhouse_cmd_disable();
		break;
	case JAILHOUSE_CELL_CREATE:
		err = jailhouse_cmd_cell_create(
			(struct jailhouse_cell_create __user *)arg);
		break;
	case JAILHOUSE_CELL_LOAD:
		err = jailhouse_cmd_cell_load(
			(struct jailhouse_cell_load __user *)arg);
		break;
	case JAILHOUSE_CELL_START:
		err = jailhouse_cmd_cell_start((const char __user *)arg);
		break;
	case JAILHOUSE_CELL_DESTROY:
		err = jailhouse_cmd_cell_destroy((const char __user *)arg);
		break;
	default:
		err = -EINVAL;
		break;
	}

	return err;
}

static int jailhouse_console_open(struct inode *inode, struct file *file)
{
	struct console_state *user;

	user = kzalloc(sizeof(struct console_state), GFP_KERNEL);
	if (!user)
		return -ENOMEM;

	file->private_data = user;

	return 0;
}

static int jailhouse_console_release(struct inode *inode, struct file *file)
{
	struct console_state *user = file->private_data;

	kfree(user);

	return 0;
}

static ssize_t jailhouse_console_read(struct file *file, char __user *out,
				      size_t size, loff_t *off)
{
	struct console_state *user = file->private_data;
	char *content;
	unsigned int miss;
	int ret;

	content = kmalloc(sizeof(console_page->content), GFP_KERNEL);
	if (content == NULL)
		return -ENOMEM;

	/* wait for new data */
	while (1) {
		if (mutex_lock_interruptible(&jailhouse_lock) != 0) {
			ret = -EINTR;
			goto console_free_out;
		}

		if (last_console.id != user->last_console_id &&
		    last_console.valid) {
			ret = __jailhouse_console_dump_delta(&last_console.page,
							     content,
							     user->head,
							     &miss);
			if (!ret)
				user->last_console_id =
					last_console.id;
		} else {
			ret = jailhouse_console_dump_delta(content, user->head,
							   &miss);
		}

		mutex_unlock(&jailhouse_lock);

		if ((!ret || ret == -EAGAIN) && file->f_flags & O_NONBLOCK)
			goto console_free_out;

		if (ret == -EAGAIN)
			/* Reset the user head, if jailhouse is not enabled. We
			 * have to do this, as jailhouse might be reenabled and
			 * the file handle was kept open in the meanwhile */
			user->head = 0;
		else if (ret < 0)
			goto console_free_out;
		else if (ret)
			break;

		schedule_timeout_uninterruptible(HZ / 10);
		if (signal_pending(current)) {
			ret = -EINTR;
			goto console_free_out;
		}
	}

	if (miss) {
		/* If we missed anything, warn user. We will dump the actual
		 * content in the next call. */
		ret = snprintf(content, sizeof(console_page->content),
			       "<missed %u bytes of console log>\n",
			       miss);
		user->head += miss;
		if (size < ret)
			ret = size;
	} else {
		if (size < ret)
			ret = size;
		user->head += ret;
	}

	if (copy_to_user(out, content, ret))
		ret = -EFAULT;

console_free_out:
	set_current_state(TASK_RUNNING);
	kfree(content);
	return ret;
}


static const struct file_operations jailhouse_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = jailhouse_ioctl,
	.compat_ioctl = jailhouse_ioctl,
	.llseek = noop_llseek,
	.open = jailhouse_console_open,
	.release = jailhouse_console_release,
	.read = jailhouse_console_read,
};

static struct miscdevice jailhouse_misc_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "jailhouse",
	.fops = &jailhouse_fops,
};

static int jailhouse_shutdown_notify(struct notifier_block *unused1,
				     unsigned long unused2, void *unused3)
{
	int err;

	err = jailhouse_cmd_disable();
	if (err && err != -EINVAL)
		pr_emerg("jailhouse: ordered shutdown failed!\n");

	return NOTIFY_DONE;
}

static struct notifier_block jailhouse_shutdown_nb = {
	.notifier_call = jailhouse_shutdown_notify,
};

static int __init jailhouse_init(void)
{
	int err;

#if defined(CONFIG_KALLSYMS_ALL) && LINUX_VERSION_CODE < KERNEL_VERSION(5,7,0)
#define __RESOLVE_EXTERNAL_SYMBOL(symbol)			\
	symbol##_sym = (void *)kallsyms_lookup_name(#symbol);	\
	if (!symbol##_sym)					\
		return -EINVAL
#else
#define __RESOLVE_EXTERNAL_SYMBOL(symbol)			\
	symbol##_sym = &symbol
#endif
#define RESOLVE_EXTERNAL_SYMBOL(symbol...) __RESOLVE_EXTERNAL_SYMBOL(symbol)

	RESOLVE_EXTERNAL_SYMBOL(ioremap_page_range);
#ifdef CONFIG_X86
	RESOLVE_EXTERNAL_SYMBOL(lapic_timer_period);
#endif
#ifdef CONFIG_ARM
	RESOLVE_EXTERNAL_SYMBOL(__boot_cpu_mode);
#endif
#if defined(CONFIG_ARM) || defined(CONFIG_ARM64)
	RESOLVE_EXTERNAL_SYMBOL(__hyp_stub_vectors);
#endif
#ifdef JAILHOUSE_PI5_CANONICAL_VA
	err = jailhouse_resolve_kernel_mm();
	if (err)
		return err;
	err = jailhouse_validate_canonical_base();
	if (err)
		return err;
#endif

	jailhouse_dev = root_device_register("jailhouse");
	if (IS_ERR(jailhouse_dev))
		return PTR_ERR(jailhouse_dev);

	err = jailhouse_sysfs_init(jailhouse_dev);
	if (err)
		goto unreg_dev;

	err = misc_register(&jailhouse_misc_dev);
	if (err)
		goto exit_sysfs;

	err = jailhouse_pci_register();
	if (err)
		goto exit_misc;

	register_reboot_notifier(&jailhouse_shutdown_nb);

	init_hypercall();

	return 0;
exit_misc:
	misc_deregister(&jailhouse_misc_dev);

exit_sysfs:
	jailhouse_sysfs_exit(jailhouse_dev);

unreg_dev:
	root_device_unregister(jailhouse_dev);
	return err;
}

static void __exit jailhouse_exit(void)
{
	unregister_reboot_notifier(&jailhouse_shutdown_nb);
	misc_deregister(&jailhouse_misc_dev);
	jailhouse_sysfs_exit(jailhouse_dev);
	jailhouse_firmware_free();
	jailhouse_pci_unregister();
	root_device_unregister(jailhouse_dev);
}

module_init(jailhouse_init);
module_exit(jailhouse_exit);
