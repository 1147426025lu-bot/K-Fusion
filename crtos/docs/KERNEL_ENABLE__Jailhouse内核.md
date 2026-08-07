# Jailhouse-enabling kernel (Pi 5)

Stock Raspberry Pi OS **PREEMPT_RT** kernel headers can compile most of Jailhouse, but **`jailhouse.ko` fails at modpost** because these symbols are not exported to out-of-tree modules:

- `__hyp_stub_vectors`
- `ioremap_page_range`
- `__get_vm_area_caller`

Symbols exist in the running kernel (`/proc/kallsyms`) but are not in `Module.symvers` for GPL export.

## What already builds (partial)

Without a custom kernel you still get:

| Artifact | Status |
|----------|--------|
| `hypervisor/jailhouse.bin` | ✅ |
| `configs/arm64/rpi5.cell` | ✅ |
| `configs/arm64/rpi5-nuttx.cell` | ✅ (cRTOS sRTOS cell) |
| `configs/arm64/dts/inmate-rpi5.dtb` | ✅ |
| `tools/jailhouse` | ✅ |
| `driver/jailhouse.ko` | ❌ needs enabling kernel |

Check: `bash scripts/crtos/check_jailhouse_build__检查构建.sh`

## Option A — Patch Pi kernel source (recommended for Pi 5)

1. Fetch kernel source (Pi OS / slow network: prefer tarball):
   ```bash
   # auto: git then tarball fallback
   CLONE_ONLY=1 bash scripts/crtos/build_jailhouse_kernel_rpi5__编译JAIL内核.sh

   # or resumable wget only (after curl 56 / TLS errors):
   CLONE_METHOD=tarball CLONE_ONLY=1 bash scripts/crtos/build_jailhouse_kernel_rpi5__编译JAIL内核.sh
   ```
2. Apply export patch (if not using full build script):
   ```bash
   patch -p1 < ../../scripts/crtos/patches/jailhouse-arm64-ksyms.patch
   ```
3. Use Pi defconfig + modules:
   ```bash
   make bcm2712_defconfig   # Pi 5; use bcm2711_defconfig for Pi 4
   # Ensure: CONFIG_KALLSYMS_ALL=y, CONFIG_TRIM_UNUSED_KSYMS is not set
   make -j$(nproc) Image modules dtbs
   ```
4. Install new kernel/image to boot partition (backup first), reboot.
5. Rebuild Jailhouse against **built** tree:
   ```bash
   KDIR=$PWD/build bash scripts/crtos/install_jailhouse_rpi5__安装Jailhouse.sh
   ```

Upstream reference: Siemens `ci/gen-kernel-build.sh` embeds the same export hunks; full trees also exist at [siemens/linux](https://github.com/siemens/linux) branches `jailhouse-enabling/*`.

## Option B — jailhouse-images SD card (Pi 4 only)

[Siemens jailhouse-images](https://github.com/siemens/jailhouse-images) ships a **Pi 4** reference image with enabling kernel pre-built. Useful to validate cRTOS methodology before Pi 5 custom kernel work.

## Option C — Paper path (no custom kernel)

Continue PLCFusion paper with **literature cRTOS** cross-study; Pi5 hypervisor baseline remains optional.

## After kernel + ko

1. DT overlay `jailhouse-crtos-mem.dtso` (2 GiB @ 512 MiB)
2. `sudo bash scripts/crtos/deploy_crtos_jailhouse__部署cRTOS.sh enable`
3. Load NuttX: `NUTTX_BIN=... sudo bash ... nuttx-cell`

NuttX + Fixstars IVSHMEM loader for ARM remain **Phase 2** (see main README).
