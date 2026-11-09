# onyx-susfs-sukisu

SukiSU-Ultra + SUSFS kernel for the **POCO F7 / Redmi Turbo 4 Pro** (`onyx`,
Snapdragon 8s Gen 4 / SM8735) running **HyperOS 3 (Android 16)**.

The kernel is built from the exact AOSP GKI source commit that the stock
HyperOS kernel was built from, using the stock kernel config extracted from the
device's own `boot.img`. This keeps the release string, vermagic and the GKI
vendor-module interface compatible with the signed HyperOS vendor modules.

| Item | Value |
| --- | --- |
| Device | POCO F7 / Redmi Turbo 4 Pro (`onyx`) |
| Platform | SM8735 (Snapdragon 8s Gen 4), arm64 |
| Firmware | HyperOS 3 EU, Android 16 |
| Kernel | Linux 6.6.118 (AOSP GKI `e56cf6b09cca`) |
| Release | `6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k` |
| Root | SukiSU-Ultra (`7e4784ee1282`) |
| Hiding | SUSFS (`c7f670f57b7d`) |
| Toolchain | Ubuntu 24.04 clang/lld/LLVM 18 |

## What is changed compared to stock

* `CONFIG_KSU=y` and `CONFIG_KSU_SUSFS=y` (plus the default SUSFS features).
* The SUSFS kernel patch from the `gki-android15-6.6` branch, applied with
  fuzz because of a single context drift in `fs/proc/task_mmu.c`. The patch's
  legacy preinit hooks in `fs/exec.c` are replaced with inert stubs: pinned
  SukiSU-Ultra performs the preinit swap from its own syscall bridge
  (`hook/syscall_event_bridge.c`) and no longer exports those symbols.
* `CONFIG_MODULE_SIG_PROTECT=y` is kept, but the vendor protected-exports list
  is removed. This makes the GKI module protection a no-op so the signed
  HyperOS vendor modules can load.
* Symbol CRC mismatches are accepted (`kernel/module/version.c`), matching the
  behaviour of CLO/GKI vendor kernels. This is required because the kernel is
  rebuilt with a different clang build than the stock one.
* `CONFIG_WERROR` is disabled so a newer host clang does not turn vendor
  warnings into build failures.
* `CONFIG_TRIM_UNUSED_KSYMS` is disabled because the vendor
  `abi_symbollist.raw` whitelist is not published. Leaving it enabled would
  hide symbols that HyperOS vendor modules import.
* `CONFIG_LOCALVERSION_AUTO` is disabled and `scripts/setlocalversion` is
  pinned so the release string is byte-for-byte identical to stock.

Everything else (KASAN, CFI, BTF, MODVERSIONS, the stock cmdline, etc.) is
copied from the device config.

## Building

Push to `main` or run the `build` workflow manually. The GitHub Actions
workflow uses `ubuntu-24.04`, installs clang/lld 18, adds an 8 GiB swap file
and runs `scripts/build.sh`.

Locally on Debian/Ubuntu, the same script works:

```sh
INSTALL_DEPS=1 bash scripts/build.sh
```

Artifacts land in `artifacts/`:

* `Image` - raw arm64 kernel
* `Image.gz`
* `onyx-susfs-sukisu-anykernel3.zip`
* `onyx-susfs-sukisu.config`
* `kernel.release`

## Repacking boot.img

The stock `boot.img` is a kernel-only Android boot header v4 image: the ramdisk
lives in `init_boot.img` and `ramdisk_size` is 0. `tools/bootimg.py` replaces
the kernel while preserving the header page and the partition size:

```sh
python3 tools/bootimg.py info stock-boot.img
python3 tools/bootimg.py repack \
  --stock stock-boot.img \
  --kernel artifacts/Image \
  --output boot-susfs.img
```

The stock image has AVB metadata appended after the kernel. Repacking drops it:
a modified kernel invalidates that metadata anyway, and an unlocked bootloader
boots without it. The output image is padded to the original 96 MiB partition
size so no stale AVB footer is left behind.

## Flashing

> Unlock the bootloader first. Flashing a custom kernel on a locked bootloader
> will fail. Back up the stock `boot.img` before flashing anything.

```sh
adb reboot bootloader
fastboot flash boot boot-susfs.img
fastboot reboot
```

On A/B devices `fastboot flash boot` targets the active slot. To be explicit,
use `boot_a` or `boot_b` for the slot that is currently active.

After booting, install the **SukiSU-Ultra manager APK** matching the kernel
build (`com.sukisu.ultra`) and open it once to finish setup. SUSFS is built
into the kernel; enable the SUSFS features you need in the manager.

The AnyKernel3 zip can be flashed later with a kernel manager or a custom
recovery. It detects the active slot, locates `boot_a`/`boot_b` and preserves
the installed ramdisk, bootconfig and device trees.

### Restoring stock

```sh
fastboot flash boot stock-boot.img
```

or re-flash the `boot.img` from the matching HyperOS fastboot package.

## Notes and limitations

* This kernel targets HyperOS 3 EU on `onyx` with the 6.6.118 GKI base. A
  different firmware or kernel base can change the vendor-module ABI. Keep the
  matching stock `boot.img` handy.
* Verified boot will report an unlocked/modified device. This is expected and
  is why the bootloader must be unlocked. Do not relock the bootloader while a
  custom kernel is installed.
* The build uses the stock HyperOS kernel config, so the kernel is large
  (KASAN/CFI/debug info are kept for ABI compatibility). This is intentional.
* No proprietary firmware or stock boot image is stored in this repository.

## Credits

* [SukiSU-Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra) - kernel-based root
* [SUSFS](https://gitlab.com/simonpunk/susfs4ksu) by simonpunk - root hiding
* [WildKernels/AnyKernel3](https://github.com/WildKernels/AnyKernel3) - installer
* [Onyx-Wild-Performance](https://github.com/Henry-Hilton/Onyx-Wild-Performance) -
  SukiSU/SUSFS compatibility patch and onyx AnyKernel scripts
* [AOSP kernel/common](https://github.com/aosp-mirror/kernel_common) - GKI source

See `sources.lock.json` for the exact commits and `patches/LICENSE.susfs` for
the SUSFS license.
