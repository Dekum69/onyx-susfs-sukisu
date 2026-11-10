# onyx-susfs-sukisu

Root + hiding kernel packages for the **POCO F7 / Redmi Turbo 4 Pro** (`onyx`,
Snapdragon 8s Gen 4 / SM8735) running **HyperOS 3 (Android 16)**.

The kernels are built from the exact AOSP GKI source commit that the stock
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
| Root | SukiSU-Ultra, KernelSU, KernelSU-Next |
| Hiding | SUSFS v2.4.0 (built in, or guard LKM on KernelSU-Next) |
| Toolchain | Ubuntu 24.04 clang/lld/LLVM 18 |

## Packages

Every package is a complete AnyKernel3 zip. Pick the one that matches the root
manager you want:

| Package | Root | Hiding |
| --- | --- | --- |
| `onyx-sukisu-susfs-anykernel3.zip` | SukiSU-Ultra | SUSFS v2.4.0 built into the kernel |
| `onyx-kernelsu-susfs-anykernel3.zip` | official KernelSU | SUSFS v2.4.0 built into the kernel |
| `onyx-ksunext-susfs-anykernel3.zip` | KernelSU-Next | SUSFS guard LKM |

"Built in" means `CONFIG_KSU_SUSFS=y`: no module is needed, the SUSFS
features are enabled from the root manager.

**KernelSU-Next has no built-in SUSFS support.** It ships SUSFS as a guard LKM
(`kernel/susfs/`) that its manager loads on demand. The `onyx-ksunext-susfs`
kernel image is therefore unpatched; enable SUSFS auto-load in the
KernelSU-Next manager after flashing.

## Quick start

* **Install a kernel** - flash one of the packages above from the matching
  manager's kernel-flash screen or a custom recovery. Do not flash a repacked
  `boot.img`; see [Flashing](#flashing).
* **Update after a HyperOS firmware change** - drag the new firmware's
  `boot.img` onto `scripts\update-base.cmd`, or run
  `python scripts/update-base.py --boot new-boot.img --push --wait`. It
  updates the pins, runs the GitHub Actions build and downloads all three
  packages; see [Automated re-base](#automated-re-base).
* **Just inspect a boot image** - `python scripts/update-base.py --boot boot.img
  --dry-run` changes nothing and prints what it detected.

## What is changed compared to stock

The base changes are the same for every variant:

* `CONFIG_KSU=y` with the selected backend integrated as `drivers/kernelsu`.
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

The SukiSU-Ultra and official KernelSU kernels add:

* `CONFIG_KSU_SUSFS=y` and the SUSFS feature options (default `y`).
* The SUSFS kernel patch from the `gki-android15-6.6` branch, applied with
  fuzz because of a single context drift in `fs/proc/task_mmu.c`.
* For SukiSU, the reviewed `patches/sukisu-susfs.patch` compatibility patch.
  Its legacy preinit hooks in `fs/exec.c` are replaced with inert stubs:
  pinned SukiSU-Ultra performs the preinit swap from its own syscall bridge
  (`hook/syscall_event_bridge.c`) and no longer exports those symbols.
* For official KernelSU, `patches/kernelsu-susfs.patch` (the SUSFS
  `10_enable_susfs_for_ksu.patch`). It supplies the preinit hooks itself, so
  `fs/exec.c` is left alone.

The KernelSU-Next kernel is plain: no SUSFS patch and no `CONFIG_KSU_SUSFS`,
because that backend loads SUSFS from its manager as a guard LKM.

Everything else (KASAN, CFI, BTF, MODVERSIONS, the stock cmdline, etc.) is
copied from the device config.

## Building

Push to `main` or run the `build` workflow manually. The workflow is a matrix:
one GitHub Actions job per variant, all in parallel, `ubuntu-24.04` with
clang/lld 18 and an 8 GiB swap file.

Locally on Debian/Ubuntu, the same script builds one variant at a time:

```sh
# SukiSU-Ultra + built-in SUSFS
INSTALL_DEPS=1 ROOT_BACKEND=sukisu bash scripts/build.sh

# official KernelSU + built-in SUSFS
ROOT_BACKEND=kernelsu bash scripts/build.sh

# KernelSU-Next (SUSFS guard LKM)
ROOT_BACKEND=ksunext bash scripts/build.sh
```

| Variable | Values | Meaning |
| --- | --- | --- |
| `ROOT_BACKEND` | `sukisu`, `kernelsu`, `ksunext` | which root solution is compiled in |
| `WORK` | directory | source/build tree, defaults to `work/<variant>` |
| `ARTIFACTS` | directory | output directory, defaults to `artifacts/<variant>` |

Each variant gets its own work tree because the kernel source is patched
differently. Artifacts land in `artifacts/<variant>/`:

* `<variant>-anykernel3.zip` - the flashable package
* `Image`, `Image.gz` - raw kernel
* `<variant>.config` - exact kernel config
* `kernel.release`, `build.log`

## Inspecting the boot image

The stock `boot.img` is a kernel-only Android boot header v4 image: the ramdisk
lives in `init_boot.img` and `ramdisk_size` is 0. `tools/bootimg.py` can read
it and extract the kernel and the embedded config:

```sh
python3 tools/bootimg.py info stock-boot.img
python3 tools/bootimg.py extract-kernel stock-boot.img stock-Image
python3 tools/bootimg.py extract-config stock-boot.img stock.config
```

The tool also has a `repack` subcommand, but it is **not** a supported install
path. A repacked image loses the stock AVB metadata and HyperOS can refuse to
boot it, dropping the device to fastboot. Use the AnyKernel3 package instead:
it patches the on-device boot image and keeps the stock header, cmdline and
metadata layout.

## Flashing

> Unlock the bootloader first. Flashing a custom kernel on a locked bootloader
> will fail. Back up the stock `boot.img` before flashing anything.

Flash **only** one of the AnyKernel3 packages, from the matching manager's
kernel-flash screen or a custom recovery.

| Package | Manager | Notes |
| --- | --- | --- |
| `onyx-sukisu-susfs-anykernel3.zip` | SukiSU-Ultra `com.sukisu.ultra`, manager v4.3.0-r2 (41150) or newer | SUSFS v2.4.0 built in |
| `onyx-kernelsu-susfs-anykernel3.zip` | official KernelSU `me.weishu.kernelsu`, v3.3.1 (32680) or newer | SUSFS v2.4.0 built in |
| `onyx-ksunext-susfs-anykernel3.zip` | KernelSU-Next `com.rifsxd.ksunext`, v3.4.2 (33355) or newer | enable SUSFS auto-load in the manager |

The installer detects the active slot, repacks the installed boot image and
preserves the stock header, cmdline, bootconfig and device trees. Your existing
ramdisk/root manager stays in place; only the kernel changes.

After booting, verify root:

```sh
adb shell su -c id
```

### SUSFS

On the SukiSU-Ultra and official KernelSU packages SUSFS v2.4.0 is already in
the kernel. Enable the features you need from the manager.

On the KernelSU-Next package, open the manager and enable SUSFS auto-load; the
manager loads its guard LKM, which is how KernelSU-Next ships SUSFS.

### Restoring stock

```sh
fastboot flash boot stock-boot.img
```

or re-flash the `boot.img` from the matching HyperOS fastboot package.

Do **not** flash a locally repacked `boot.img`. It cannot carry the stock AVB
metadata, and HyperOS may reject it, leaving the device in fastboot. If that
already happened, flash your stock `boot.img`, boot once, then flash the
AnyKernel3 package.

## When HyperOS updates the kernel

The kernel is tied to three things: the exact GKI source commit, the stock
kernel config, and the exact release string (which becomes the module
vermagic). A firmware update can change any of them, so treat every HyperOS
update as a re-base.

### Automated re-base

`scripts/update-base.py` does the whole loop from one boot image. It needs
Python 3 and an authenticated `gh`; the kernels are still compiled on GitHub
Actions, so no local Linux toolchain is required:

```sh
# inspect first: extract the kernel/config, resolve the GKI commit, show the plan
python scripts/update-base.py --boot new-boot.img

# full pipeline: commit/push, wait for the build, download the three packages
python scripts/update-base.py --boot new-boot.img --push --wait

# ...and publish the packages as a release
python scripts/update-base.py --boot new-boot.img --push --wait --release v1.1.0
```

On Windows, drag the new `boot.img` onto `scripts\update-base.cmd`: it shows
the plan, asks for confirmation, then runs the full pipeline (commit, CI
build, download, package). Extra options are forwarded, for example
`update-base.cmd new-boot.img --release v1.1.0`.

What it does:

* reads the release string from the boot image's kernel and resolves the
  matching `kernel_common` commit, verifying that the commit's Makefile really
  is that Linux version;
* extracts the embedded stock config to
  `configs/onyx-<version>-<ab-build>.config`;
* updates `scripts/build.sh` (`KERNEL_COMMIT`, `STOCK_RELEASE`, config path),
  `sources.lock.json` and the table at the top of this README;
* with `--push` commits those files and pushes, then `--wait` follows the
  matrix workflow and downloads all three AnyKernel3 packages (plus each
  variant's config) into `artifacts/`;
* refuses to run if the firmware moved to a different kernel series (a port,
  not a re-base) unless `--force` is given.

Only the AnyKernel3 zips are produced for flashing. The script never builds a
boot image, because a locally repacked one cannot reproduce the stock AVB
metadata.

If the release string is unchanged it skips the rebuild and points at the
existing AnyKernel3 packages.

The script cannot fix a patch that no longer applies. If the build stops in
`apply_patch`, re-pin SUSFS / refresh the backend patch as described below,
then re-run the build. The individual steps are listed here in case anything
needs to be done by hand:

1. Read the new release string, either from the running device:

   ```sh
   adb shell uname -r
   ```

   or from the new firmware's `boot.img`:

   ```sh
   python3 tools/bootimg.py extract-kernel new-boot.img new-Image
   strings new-Image | grep -m1 'Linux version'
   ```

2. Compare it to `STOCK_RELEASE` in `scripts/build.sh`:

   * **Identical** - same source, config and vermagic. Nothing to rebuild.
     Restore the stock boot image before the OTA, then re-flash this kernel
     afterwards.
   * **Only `-ab<number>` changed** - same source and config, new build
     number. Update `STOCK_RELEASE` and rebuild.
   * **`6.6.x` changed** - the GKI source moved. The `g<hash>` part of the
     release is the AOSP `kernel_common` commit; resolve it with
     `git -C kernel_common rev-parse <hash>`, update `KERNEL_COMMIT` and
     extract the new config (step 3).
   * **New major/minor or Android GKI branch** (for example
     `android16-6.12`) - this is a port, not a re-base: new source branch,
     new SUSFS branch (`gki-android16-6.12`), possibly a new boot header
     version. Do not reuse any of the 6.6-specific patches without checking
     they apply.

3. Extract the stock config from the new kernel and point
   `configure_kernel()` in `scripts/build.sh` at it:

   ```sh
   python3 tools/bootimg.py extract-config new-Image configs/onyx-<version>-stock.config
   ```

4. Update `sources.lock.json` and the table at the top of this README, run
   the `build` workflow (`workflow_dispatch`) and download the artifacts.

5. Download the AnyKernel3 packages produced by the workflow. There is nothing
   to repack locally, and a hand-built boot image should not be flashed.

6. Restore the stock boot image before taking the OTA, then flash the new
   AnyKernel3 package. Flashing an old kernel onto newer firmware is not
   supported: the vendor modules in `vendor_dlkm` are rebuilt with the new
   release string and the loader rejects a vermagic mismatch, which breaks
   Wi-Fi, camera and other vendor services.

If a patch stops applying after a re-base, re-pin the source it belongs to
instead of forcing the old patch:

| Patch | Belongs to | Refresh from |
| --- | --- | --- |
| `patches/susfs-50.patch`, `susfs.c`, `susfs.h`, `susfs_def.h` | SUSFS kernel side | the matching `gki-android*-6.*` branch of `simonpunk/susfs4ksu` |
| `patches/sukisu-susfs.patch` | SukiSU-Ultra | `Henry-Hilton/Onyx-Wild-Performance` |
| `patches/kernelsu-susfs.patch` | official KernelSU | `kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch` in `simonpunk/susfs4ksu` |

KernelSU and KernelSU-Next are cloned (not downloaded as tarballs) because
their `KSU_VERSION` comes from `git rev-list --count`; a tarball build would
silently report version 16. `scripts/build.sh` verifies the version after the
build and fails if that happened.

## Notes and limitations

* This kernel targets HyperOS 3 EU on `onyx` with the 6.6.118 GKI base. A
  different firmware or kernel base can change the vendor-module ABI. Keep the
  matching stock `boot.img` handy.
* Verified boot will report an unlocked/modified device. This is expected and
  is why the bootloader must be unlocked. Do not relock the bootloader while a
  custom kernel is installed.
* The build uses the stock HyperOS kernel config, so the kernel is large
  (KASAN/CFI/debug info are kept for ABI compatibility). This is intentional.
* Flash only an AnyKernel3 package. A locally repacked boot image drops the
  stock AVB metadata and can leave the device in fastboot.
* The official KernelSU kernel is a commit after the v3.3.1 tag: the tag's
  kernel tree rejects the SUSFS patch. Its reported KSU version is therefore a
  little newer than the v3.3.1 manager (kernel 32716 vs manager 32680); the
  manager works, and newer manager builds work too.
* SUSFS is pinned to v2.4.0 (`ec03d0c`). Compared to v2.3.0 on this branch it
  changes the version string and the rate limiting of two log calls; the
  kernel 50-patch and `susfs_def.h` are identical.
* No proprietary firmware or stock boot image is stored in this repository.

## Credits

* [SukiSU-Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra) - kernel-based root
* [KernelSU](https://github.com/tiann/KernelSU) - official KernelSU
* [KernelSU-Next](https://github.com/KernelSU-Next/KernelSU-Next) - KernelSU fork with a SUSFS guard LKM
* [SUSFS](https://gitlab.com/simonpunk/susfs4ksu) by simonpunk - root hiding
* [WildKernels/AnyKernel3](https://github.com/WildKernels/AnyKernel3) - installer
* [Onyx-Wild-Performance](https://github.com/Henry-Hilton/Onyx-Wild-Performance) -
  SukiSU/SUSFS compatibility patch and onyx AnyKernel scripts
* [AOSP kernel/common](https://github.com/aosp-mirror/kernel_common) - GKI source

See `sources.lock.json` for the exact commits and `patches/LICENSE.susfs` for
the SUSFS license.
