# NuttX / cRTOS sRTOS on Pi 5 (ARM) — gap analysis

Fixstars [cRTOS-nuttx](https://github.com/fixstars/cRTOS-nuttx) ships **`qemu-intel64:crtos`** — x86_64 NuttX with Linux System V ABI compatibility.

Our Jailhouse cell `rpi5-nuttx.c` reserves:

| Region | Value |
|--------|-------|
| Load stub | phys `0x1f900000`, virt `0`, 64 KiB |
| NuttX RAM | phys `0x10000000`, virt `0`, **1.5 GiB** (`0x60000000`) |
| IVSHMEM | shared with root cell (indices 0–9) |
| CPUs | 2–3 (`0b1100`) |

## What Fixstars provides (x86)

- `tools/configure.sh qemu-intel64:crtos`
- `CONFIG_TUX_USER_ADDR_*` for Linux binary compatibility
- Daemon + loader in `crtos-nuttx-apps` (cRTOS-Daemon branch)

## ARM port work (not upstream)

1. **NuttX board config** for Jailhouse inmate on ARM64 (no `qemu-intel64` on Pi).
   - Start from Apache NuttX `boards/arm64`-class jailhouse/qemu configs if present.
   - Map UART `0x107d001000` (PL011), GIC `0x107c7fff9000`, memory per table above.

2. **Linux ABI layer** — Fixstars x86_64 syscall shim does not transfer; IEEE Access 2025 Pi4 paper reimplemented stack.

3. **Build output** — flat `nuttx.bin` loaded by:
   ```bash
   jailhouse cell load nuttx ./nuttx.bin 0
   ```

## Practical order

1. jailhouse-enabling kernel + `jailhouse.ko`
2. DT overlay + reboot
3. `jailhouse enable` + **ivshmem-demo** or **linux-demo** inmate (validate partition)
4. Minimal NuttX bring-up (printk only) in `rpi5-nuttx` cell
5. Port loader/shadow drivers from `crtos-drivers` + `crtos-loader`

## Scripts

```bash
bash scripts/crtos/init_crtos_submodules__初始化cRTOS子模块.sh
bash scripts/crtos/build_nuttx_srtos_rpi5__编译NuttX.sh   # fails until ARM defconfig exists
```

Reference install flow: `third_party/crtos/Installation.md` § NuttX.
