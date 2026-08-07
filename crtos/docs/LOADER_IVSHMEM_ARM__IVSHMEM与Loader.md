# IVSHMEM + cRTOS Loader on Pi 5 (ARM64)

Fixstars cRTOS on x86 uses Jailhouse IVSHMEM v2 + three Linux modules + a userspace **loader** that proxies Linux ELF binaries into NuttX via the **shadow** character device.

Pi 5 draft cell `rpi5-nuttx.c` maps the same logical layout at different physical addresses (512 MiB reservation base).

## Memory map (Pi 5 draft)

| Role | Physical | Size | Notes |
|------|----------|------|-------|
| IVSHMEM state (shadow dev) | `0x1faf0000` | 4 KiB | ROOTSHARED |
| IVSHMEM R/W (shadow payload) | `0x1faf1000` | 36 KiB | **shadow.ko `shmaddr=`** |
| IVSHMEM in/out rings | `0x1fafa000` … | 8+8 KiB | shadow vrings |
| IVSHMEM Ethernet | `0x1fb00000` | macro | `JAILHOUSE_SHMEM_NET_REGIONS` |
| NuttX load stub | `0x1f900000` | 64 KiB | `jailhouse cell load … 0` |
| NuttX RAM | `0x10000000` | 1.5 GiB | flat `nuttx.bin` |
| PL011 UART | `0x107d001000` | — | console SPI 121 |

Compare x86 reference: `third_party/crtos/crtos-jailhouse/configs/x86/nuttx.c` (shadow @ `0x108000000`).

## Linux-side stack (root cell)

After `deploy_crtos_jailhouse__部署cRTOS.sh enable` and `nuttx-cell`:

```bash
# 1. Build drivers against jailhouse-enabling kernel
KDIR=$PWD/third_party/linux-rpi bash scripts/crtos/build_crtos_drivers__编译驱动.sh

# 2. Load modules (order matters; adjust shmaddr if cell config changes)
sudo insmod third_party/crtos-drivers-built/uio_ivshmem.ko
sudo insmod third_party/crtos-drivers-built/ivshmem-net.ko
sudo insmod third_party/crtos-drivers-built/shadow.ko shmaddr=0x1faf1000

# 3. Build loader (x86_64 Linux ABI proxy — ARM guest still needs NuttX-side port)
cd third_party/crtos/crtos-loader && make
sudo chrt -f 90 ./loader /path/to/guest/elf args...
```

## ARM gaps vs Fixstars x86

| Component | x86 upstream | Pi 5 status |
|-----------|--------------|-------------|
| Jailhouse nuttx cell | `configs/x86/nuttx.c` | `rpi5-nuttx.c` draft ✅ |
| NuttX board | `qemu-intel64:crtos` | **missing** — see [NUTTX_ARM__NuttX移植.md](NUTTX_ARM__NuttX移植.md) |
| Loader syscall decode | x86_64 | **x86 only** in `crtos-loader` |
| shadow / IVSHMEM drivers | Linux 5.4 modules | build with `build_crtos_drivers__编译驱动.sh`; may need minor 6.12 API fixes |
| Guest ELF ABI | Linux x86_64 in NuttX | needs **aarch64** TUX/shim (IEEE Access 2025 Pi4 approach) |

## Validation without full cRTOS

1. `demo-cell` — linux-loader inmate only  
2. `ivshmem-demo` inmate (upstream jailhouse) — verify shared memory IRQ path  
3. Minimal NuttX printk in `nuttx` cell — no loader yet  
4. Load shadow + ping NuttX daemon when ARM NuttX apps exist  

## References

- `third_party/crtos/Installation.md` — IVSHMEM region splitting  
- `third_party/crtos/crtos-drivers/README.md` — shadow mmap semantics  
- `third_party/crtos/crtos-loader/README.md` — SCHED_FIFO requirement  
