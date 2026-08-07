# Jailhouse / cRTOS on Raspberry Pi 5

Phase-1 bring-up: **build** Jailhouse + Pi5 cell configs + cRTOS NuttX cell scaffold.  
**Enable** requires DT memory reservation and reboot (disruptive to PREEMPT_RT paper runs).

Check: `bash scripts/crtos/check_jailhouse_build__检查构建.sh`

**USB3 + 电脑 2.4GHz 热点**：插 U 盘时 WiFi/SSH 易断 → [USB_WIFI__U盘与WiFi干扰.md](USB_WIFI__U盘与WiFi干扰.md)  
挂载 U 盘：`bash scripts/crtos/mount_jhbuild_usb__挂载JHBUILD.sh`  
后台编内核：`bash scripts/crtos/run_jailhouse_kernel_background__后台JAIL内核.sh`

### jailhouse.ko blocker (stock Pi kernel)

Stock PREEMPT_RT kernel **does not export** symbols Jailhouse needs. Cells/firmware build; `.ko` requires a **jailhouse-enabling kernel**:

→ [KERNEL_ENABLE__Jailhouse内核.md](KERNEL_ENABLE__Jailhouse内核.md)  
→ Patch: `scripts/crtos/patches/jailhouse-arm64-ksyms.patch`

## Full runtime pipeline (Pi 5)

```bash
# 0. Check partial build
bash scripts/crtos/check_jailhouse_build__检查构建.sh

# 1. Jailhouse-enabling kernel (needs ~12+ GiB disk; 1-3 h)
bash scripts/crtos/build_jailhouse_kernel_rpi5__编译JAIL内核.sh
sudo bash scripts/crtos/install_jailhouse_kernel_rpi5__安装JAIL内核.sh
sudo reboot

# 2. DT memory reservation + rebuild ko against new kernel
bash scripts/crtos/install_crtos_dt_overlay__安装DT叠加.sh
sudo reboot
KDIR=$PWD/third_party/linux-rpi bash scripts/crtos/install_jailhouse_rpi5__安装Jailhouse.sh

# 3. Enable hypervisor
sudo bash scripts/crtos/deploy_crtos_jailhouse__部署cRTOS.sh enable

# 4. cRTOS submodules + drivers (loader is x86-only; NuttX ARM port incomplete)
bash scripts/crtos/init_crtos_submodules__初始化cRTOS子模块.sh
bash scripts/crtos/build_crtos_drivers__编译驱动.sh
bash scripts/crtos/build_nuttx_srtos_rpi5__编译NuttX.sh   # fails until ARM defconfig exists
```

See also: [KERNEL_ENABLE__Jailhouse内核.md](KERNEL_ENABLE__Jailhouse内核.md), [NUTTX_ARM__NuttX移植.md](NUTTX_ARM__NuttX移植.md), [LOADER_IVSHMEM_ARM__IVSHMEM与Loader.md](LOADER_IVSHMEM_ARM__IVSHMEM与Loader.md).

## Quick start

```bash
# 1. Platform probe (read-only)
bash scripts/crtos/probe_jailhouse_platform__平台探测.sh

# 2. Build Jailhouse + Pi5 cells
bash scripts/crtos/install_jailhouse_rpi5__安装Jailhouse.sh

# 3. (Optional) Clone Fixstars cRTOS reference (x86)
bash scripts/crtos/clone_crtos_reference__克隆cRTOS参考.sh
```

## Build artifacts

| File | Role |
|------|------|
| `third_party/jailhouse/driver/jailhouse.ko` | Kernel loader |
| `third_party/jailhouse/configs/arm64/rpi5.cell` | System (root Linux) cell |
| `third_party/jailhouse/configs/arm64/rpi5-nuttx.cell` | cRTOS sRTOS (NuttX) cell |
| `third_party/jailhouse/configs/arm64/rpi5-linux-demo.cell` | Demo inmate cell |
| `third_party/jailhouse/configs/arm64/dts/inmate-rpi5.dtb` | Inmate device tree |
| `third_party/jailhouse/tools/jailhouse` | CLI |

## Runtime deploy (after reboot with DT overlay)

```bash
# Compile cRTOS memory overlay (2 GiB @ 512 MiB)
dtc -@ -I dts -O dtb -o /tmp/jailhouse-crtos-mem.dtbo \
  scripts/crtos/dt-overlays/jailhouse-crtos-mem.dtso
sudo cp /tmp/jailhouse-crtos-mem.dtbo /boot/firmware/overlays/
# Add to /boot/firmware/config.txt:  dtoverlay=jailhouse-crtos-mem
sudo reboot

# After reboot
sudo bash scripts/crtos/deploy_crtos_jailhouse__部署cRTOS.sh enable
sudo bash scripts/crtos/deploy_crtos_jailhouse__部署cRTOS.sh nuttx-cell   # needs NuttX binary
sudo bash scripts/crtos/deploy_crtos_jailhouse__部署cRTOS.sh teardown
```

Deploy commands: `status` | `enable` | `disable` | `demo-cell` | `nuttx-cell` | `teardown`

## Pi5 vs upstream rpi4

| Item | Pi 4 | Pi 5 (draft) |
|------|------|--------------|
| GICD | `0xff841000` | `0x107c7fff9000` |
| UART | mini UART 8250 | PL011 `0x107d001000` |
| SoC MMIO | `0xfd500000` | `0x107c000000` |

## cRTOS gap (Phase 2)

Fixstars [cRTOS](https://github.com/fixstars/cRTOS) ships **x86_64 NuttX + Linux ABI + IVSHMEM loader** only.

To complete cRTOS on Pi5:

1. ✅ Jailhouse `rpi5.cell` + `rpi5-nuttx.cell` (this repo)
2. ⬜ Build **NuttX** for `rpi5-nuttx` memory map → `third_party/crtos-nuttx/nuttx.bin`
3. ⬜ Port **IVSHMEM remote syscall** layer (Fixstars loader + service daemon) to ARM
4. ⬜ Cyclictest in NuttX cell for paper-comparable jitter

IEEE Access 2025 demonstrated cRTOS on **Pi 4B**; no public Pi5 turnkey release.

## Paper use

Keep cRTOS as **literature cross-study** for PLCFusion paper. This tree enables optional same-board hypervisor baseline when hardware time allows.
