#!/usr/bin/env bash
# Build one AnyKernel3 package for the POCO F7 (onyx, SM8735).
#
# Two environment variables select the variant:
#
#   ROOT_BACKEND  sukisu | kernelsu | ksunext
#                 sukisu    = SukiSU-Ultra
#                 kernelsu = official KernelSU (tiann/KernelSU)
#                 ksunext   = KernelSU-Next
#
#   SUSFS_MODE    builtin | none
#                 builtin = SUSFS patched into the kernel (CONFIG_KSU_SUSFS=y)
#                 none    = plain root kernel; use the ReSuSFS module, which
#                           loads the SUSFS LKM itself
#
# KernelSU-Next has no built-in SUSFS: it loads SUSFS as a guard LKM from its
# manager.  Both ksunext variants therefore contain the same kernel image; the
# SUSFS one is meant to be used with the manager's SUSFS auto-load, the ReSuSFS
# one with the ReSuSFS module.
#
# The script is intentionally self-contained so it runs the same way on a
# GitHub Actions Ubuntu 24.04 runner and on any Debian/Ubuntu host.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

ROOT_BACKEND=${ROOT_BACKEND:-sukisu}
SUSFS_MODE=${SUSFS_MODE:-builtin}

case "$ROOT_BACKEND" in
  sukisu | kernelsu | ksunext) ;;
  *)
    echo "Unknown ROOT_BACKEND '$ROOT_BACKEND' (expected sukisu, kernelsu or ksunext)" >&2
    exit 2
    ;;
esac
case "$SUSFS_MODE" in
  builtin | none) ;;
  *)
    echo "Unknown SUSFS_MODE '$SUSFS_MODE' (expected builtin or none)" >&2
    exit 2
    ;;
esac

if [ "$SUSFS_MODE" = builtin ]; then
  SUSFS_LABEL=susfs
else
  SUSFS_LABEL=resusfs
fi
VARIANT="${ROOT_BACKEND}-${SUSFS_LABEL}"

WORK=${WORK:-"$ROOT/work/$VARIANT"}
ARTIFACTS=${ARTIFACTS:-"$ROOT/artifacts/$VARIANT"}
KERNEL_SRC=${KERNEL_SRC:-"$WORK/kernel_common"}
KSU_SRC=${KSU_SRC:-"$WORK/$ROOT_BACKEND"}
OUT=${OUT:-"$WORK/out"}
AK3_SRC=${AK3_SRC:-"$WORK/AnyKernel3"}

KERNEL_REPO=${KERNEL_REPO:-aosp-mirror/kernel_common}
KERNEL_COMMIT=${KERNEL_COMMIT:-e56cf6b09cca2151bcee244b3d334fb68685ff57}
SUKISU_COMMIT=${SUKISU_COMMIT:-7e4784ee1282abded0dc3bace873a600ef6d5d3f}
KERNELSU_COMMIT=${KERNELSU_COMMIT:-28db74f6f4692e97006ce9ebd8b19b9cc614b5b7}
KSUNEXT_COMMIT=${KSUNEXT_COMMIT:-4c706b5938665866ebe16944a481a7b91e3396af}
AK3_COMMIT=${AK3_COMMIT:-e1e9dce98430c5c6f231f7094a8c7f4ecaf50948}
RESUSFS_VERSION=${RESUSFS_VERSION:-v26.10.23}

# Exact release string of the stock HyperOS 3 kernel.  Matching it keeps
# vermagic identical, which is what the signed vendor modules check.
STOCK_RELEASE="6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k"

JOBS=${JOBS:-$(nproc)}
LLVM_SUFFIX=${LLVM_SUFFIX:-18}

ZIP_NAME="onyx-${VARIANT}-anykernel3.zip"

case "$ROOT_BACKEND" in
  sukisu) KSU_NAME="SukiSU-Ultra" ;;
  kernelsu) KSU_NAME="KernelSU" ;;
  ksunext) KSU_NAME="KernelSU-Next" ;;
esac

# KernelSU-Next only ever loads SUSFS as a module, so "builtin" is a no-op
# there.  Everything that depends on a compiled-in SUSFS uses this helper.
uses_builtin_susfs() {
  [ "$SUSFS_MODE" = builtin ] && [ "$ROOT_BACKEND" != ksunext ]
}

case "$VARIANT" in
  sukisu-susfs)
    KERNEL_STRING="Onyx | SukiSU-Ultra + built-in SUSFS"
    VARIANT_NOTE="SUSFS is compiled into this kernel. Use the SukiSU-Ultra manager (v4.3.0-r2 / 41150 or newer)."
    ;;
  sukisu-resusfs)
    KERNEL_STRING="Onyx | SukiSU-Ultra + ReSuSFS (SUSFS not built in)"
    VARIANT_NOTE="No built-in SUSFS. Boot once, then install the ReSuSFS ${RESUSFS_VERSION} module; it loads the SUSFS LKM."
    ;;
  kernelsu-susfs)
    KERNEL_STRING="Onyx | KernelSU + built-in SUSFS"
    VARIANT_NOTE="SUSFS is compiled into this kernel. Use the official KernelSU manager (v3.3.1 / 32680 or newer)."
    ;;
  kernelsu-resusfs)
    KERNEL_STRING="Onyx | KernelSU + ReSuSFS (SUSFS not built in)"
    VARIANT_NOTE="No built-in SUSFS. Boot once, then install the ReSuSFS ${RESUSFS_VERSION} module; it loads the SUSFS LKM."
    ;;
  ksunext-susfs)
    KERNEL_STRING="Onyx | KernelSU-Next + SUSFS (guard LKM)"
    VARIANT_NOTE="KernelSU-Next has no built-in SUSFS. Enable SUSFS auto-load in the KernelSU-Next manager, which loads its own guard LKM."
    ;;
  ksunext-resusfs)
    KERNEL_STRING="Onyx | KernelSU-Next + ReSuSFS (guard LKM)"
    VARIANT_NOTE="Same kernel as onyx-ksunext-susfs: KernelSU-Next has no built-in SUSFS. Install the ReSuSFS ${RESUSFS_VERSION} module instead of the manager's auto-load."
    ;;
esac

log() { printf '\n[%s] [%s] %s\n' "$(date -u +%H:%M:%S)" "$VARIANT" "$*"; }

install_deps() {
  log "Installing build dependencies"
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends \
    bc bison build-essential cpio curl device-tree-compiler dwarves \
    flex git kmod libelf-dev libssl-dev lz4 python3 rsync unzip zip \
    zstd clang-"$LLVM_SUFFIX" lld-"$LLVM_SUFFIX" llvm-"$LLVM_SUFFIX"
}

fetch_tarball() {
  # fetch_tarball <owner/repo> <commit> <dest>
  local repo=$1 commit=$2 dest=$3 url
  url="https://github.com/${repo}/archive/${commit}.tar.gz"
  log "Fetching ${repo}@${commit:0:12}"
  mkdir -p "$dest"
  curl -fsSL --retry 5 --retry-delay 5 "$url" |
    tar -xz --strip-components=1 -C "$dest"
}

fetch_git_repo() {
  # fetch_git_repo <owner/repo> <commit> <dest>
  #
  # KernelSU and KernelSU-Next derive KSU_VERSION from "git rev-list --count",
  # so they need a real repository, not a tarball.  A blob-less clone keeps it
  # small while preserving the full commit graph.
  local repo=$1 commit=$2 dest=$3
  log "Cloning ${repo}@${commit:0:12}"
  rm -rf "$dest"
  git clone --quiet --filter=blob:none --no-checkout "https://github.com/${repo}.git" "$dest"
  git -C "$dest" checkout --quiet "$commit"
}

apply_patch() {
  # apply_patch <patch-file> <directory> [extra patch args...]
  local patch_file=$1 dir=$2
  shift 2
  log "Applying $(basename "$patch_file")"
  (
    cd "$dir"
    patch -p1 --batch --forward --fuzz=3 "$@" <"$patch_file"
  )
  if find "$dir" -name '*.rej' -print -quit | grep -q .; then
    echo "Patch left rejects:" >&2
    find "$dir" -name '*.rej' -print >&2
    exit 1
  fi
}

prepare_kernel() {
  if [ ! -d "$KERNEL_SRC" ]; then
    fetch_tarball "$KERNEL_REPO" "$KERNEL_COMMIT" "$KERNEL_SRC"
  fi
  [ -f "$KERNEL_SRC/Makefile" ] || { echo "Kernel source missing" >&2; exit 1; }

  if ! uses_builtin_susfs; then
    if [ -e "$KERNEL_SRC/fs/susfs.c" ]; then
      echo "This kernel tree already has SUSFS applied, but ${VARIANT} needs a" >&2
      echo "clean tree.  Remove $WORK or set WORK to a fresh directory." >&2
      exit 1
    fi
    log "SUSFS is not compiled in for this variant"
    return
  fi

  if [ -f "$KERNEL_SRC/.susfs-applied" ]; then
    return
  fi
  if [ -e "$KERNEL_SRC/fs/susfs.c" ]; then
    echo "fs/susfs.c exists without .susfs-applied; use a clean WORK directory" >&2
    exit 1
  fi

  apply_patch "$ROOT/patches/susfs-50.patch" "$KERNEL_SRC"
  install -m 0644 "$ROOT/patches/susfs.c" "$KERNEL_SRC/fs/susfs.c"
  install -m 0644 "$ROOT/patches/susfs.h" "$KERNEL_SRC/include/linux/susfs.h"
  install -m 0644 "$ROOT/patches/susfs_def.h" "$KERNEL_SRC/include/linux/susfs_def.h"

  if [ "$ROOT_BACKEND" = sukisu ]; then
    # The SUSFS patch wires fs/exec.c into the legacy KernelSU preinit API
    # (ksu_setup_preinit_file / ksu_preinit_handle_post_execveat).  The pinned
    # SukiSU-Ultra moved preinit into a single ksu_preinit_prepare() helper and
    # no longer provides those symbols, so keep the legacy call sites inert.
    # Official KernelSU still provides them through the SUSFS KernelSU patch.
    python3 - "$KERNEL_SRC/fs/exec.c" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
pattern = re.compile(
    r"#ifndef KSU_NO_PREINIT\r?\n"
    r"extern int ksu_setup_preinit_file\(.*?"
    r"#endif // #ifndef KSU_NO_PREINIT\r?\n",
    re.S,
)
replacement = """/*
 * The pinned SukiSU-Ultra performs the preinit swap from its syscall bridge
 * (hook/syscall_event_bridge.c), so the legacy SUSFS preinit hooks have no
 * provider.  Keep the call sites inert instead of breaking the link.
 */
static inline int ksu_setup_preinit_file(struct filename **filename_ptr,
					 struct user_arg_ptr *argv_user)
{
	/* Negative means "no preinit session"; SukiSU handles this elsewhere. */
	return -1;
}
static inline int ksu_preinit_handle_post_execveat(int *fd,
						   struct filename **filename_ptr,
						   void *argv_user, void *envp_user,
						   int *flags, int *retval,
						   int *preinit_fd)
{
	return 0;
}
"""
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit("could not rewrite the legacy preinit hooks in fs/exec.c")
path.write_text(text)
print("Replaced legacy SUSFS preinit hooks with inert stubs")
PY
  fi

  touch "$KERNEL_SRC/.susfs-applied"
}

prepare_ksu() {
  case "$ROOT_BACKEND" in
    sukisu) fetch_tarball SukiSU-Ultra/SukiSU-Ultra "$SUKISU_COMMIT" "$KSU_SRC" ;;
    kernelsu) fetch_git_repo tiann/KernelSU "$KERNELSU_COMMIT" "$KSU_SRC" ;;
    ksunext) fetch_git_repo KernelSU-Next/KernelSU-Next "$KSUNEXT_COMMIT" "$KSU_SRC" ;;
  esac

  if ! uses_builtin_susfs; then
    if [ "$ROOT_BACKEND" = ksunext ] && [ "$SUSFS_MODE" = builtin ]; then
      log "KernelSU-Next has no built-in SUSFS; this build uses its guard LKM"
    else
      log "No KernelSU-side SUSFS patch for this variant"
    fi
    return
  fi

  local patch_file="$ROOT/patches/${ROOT_BACKEND}-susfs.patch"
  [ -f "$patch_file" ] || { echo "Missing $patch_file" >&2; exit 1; }
  if [ ! -f "$KSU_SRC/.patch-applied" ]; then
    apply_patch "$patch_file" "$KSU_SRC"
    touch "$KSU_SRC/.patch-applied"
  fi
}

integrate_ksu() {
  log "Integrating ${KSU_NAME} into the kernel tree"
  ln -sfn "$(realpath --relative-to="$KERNEL_SRC/drivers" "$KSU_SRC/kernel")" \
    "$KERNEL_SRC/drivers/kernelsu"

  if ! grep -q 'drivers/kernelsu' "$KERNEL_SRC/drivers/Makefile"; then
    printf '\nobj-$(CONFIG_KSU) += kernelsu/\n' >>"$KERNEL_SRC/drivers/Makefile"
  fi
  if ! grep -q 'drivers/kernelsu/Kconfig' "$KERNEL_SRC/drivers/Kconfig"; then
    printf '\nsource "drivers/kernelsu/Kconfig"\n' >>"$KERNEL_SRC/drivers/Kconfig"
  fi
}

configure_kernel() {
  log "Configuring kernel"
  mkdir -p "$OUT"
  cp "$ROOT/configs/onyx-6.6.118-stock.config" "$OUT/.config"

  # Keep the stock feature set.  Only the pieces that cannot work outside the
  # vendor build are changed:
  #  - WERROR: a newer host clang warns about things the vendor clang did not.
  #  - TRIM_UNUSED_KSYMS: the vendor whitelist (abi_symbollist.raw) is not part
  #    of the public source, and trimming would hide symbols vendor modules use.
  #  - LOCALVERSION_AUTO: the release is pinned via scripts/setlocalversion.
  local args=(
    --disable LOCALVERSION_AUTO
    --disable WERROR
    --disable TRIM_UNUSED_KSYMS
    --disable UNUSED_KSYMS_WHITELIST
    --disable MODULE_SIG_FORCE
    --enable MODULE_SIG_PROTECT
    --enable LTO_NONE
    --disable LTO_CLANG_THIN
    --disable LTO_CLANG_FULL
    --enable KSU
    --disable KSU_DEBUG
  )

  if uses_builtin_susfs; then
    args+=(--enable KSU_SUSFS)
  fi
  if [ "$ROOT_BACKEND" = sukisu ]; then
    # KPM is a SukiSU-only add-on; leave it out of the kernel image.
    args+=(--disable KPM)
  fi

  "$KERNEL_SRC/scripts/config" --file "$OUT/.config" "${args[@]}"

  # SUSFS sub-options default to y in the KernelSU/SUSFS Kconfig; olddefconfig
  # picks them up below.

  # The vendor protected-exports list triggers KMI enforcement that would stop
  # stock HyperOS vendor modules from loading.  Removing it turns the check
  # into a no-op (see kernel/module/gki_module.c).
  rm -f "$KERNEL_SRC/android/abi_gki_protected_exports_aarch64" \
        "$KERNEL_SRC/android/abi_gki_protected_exports_x86_64"

  # Stock vendor modules were linked against the stock genksyms CRCs.  Even
  # though we build the same source with the same config, a different clang can
  # change a CRC.  Accept mismatches like the CLO/GKI vendor kernels do.
  python3 - "$KERNEL_SRC/kernel/module/version.c" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
old = (
    'bad_version:\n'
    '\tpr_warn("%s: disagrees about version of symbol %s\\n", info->name, symname);\n'
    '\treturn 0;'
)
new = (
    'bad_version:\n'
    '\tpr_warn("%s: disagrees about version of symbol %s\\n", info->name, symname);\n'
    '\treturn 1;'
)
if old not in text:
    if new in text:
        print("CRC bypass already present")
    else:
        raise SystemExit("could not find bad_version block in version.c")
else:
    path.write_text(text.replace(old, new, 1))
    print("CRC mismatch bypass applied")
PY

  # Pin the release string.  filechk_kernel.release executes this script and
  # uses whatever it prints, so it must print the complete release.
  cat >"$KERNEL_SRC/scripts/setlocalversion" <<EOF
#!/bin/sh
# Pinned to the stock HyperOS 3 release; see scripts/build.sh.
echo "$STOCK_RELEASE"
EOF
  chmod +x "$KERNEL_SRC/scripts/setlocalversion"

  make -C "$KERNEL_SRC" O="$OUT" ARCH=arm64 LLVM=-"$LLVM_SUFFIX" olddefconfig
}

build_kernel() {
  log "Building kernel (${JOBS} jobs)"
  # Match the stock banner as closely as possible.
  : "${KBUILD_BUILD_USER:=kleaf}"
  : "${KBUILD_BUILD_HOST:=build-host}"
  : "${KBUILD_BUILD_VERSION:=1}"
  : "${KBUILD_BUILD_TIMESTAMP:=Thu May 28 07:48:46 UTC 2026}"
  export KBUILD_BUILD_USER KBUILD_BUILD_HOST KBUILD_BUILD_VERSION KBUILD_BUILD_TIMESTAMP

  make -C "$KERNEL_SRC" O="$OUT" ARCH=arm64 LLVM=-"$LLVM_SUFFIX" \
    -j"$JOBS" Image Image.gz 2>&1 | tee "$ARTIFACTS/build.log"
}

verify_kernel() {
  local release
  release=$(make -s -C "$KERNEL_SRC" O="$OUT" ARCH=arm64 kernelrelease)
  log "kernelrelease: $release"
  [ "$release" = "$STOCK_RELEASE" ] || {
    echo "Release mismatch: expected $STOCK_RELEASE, got $release" >&2
    exit 1
  }
  [ -f "$OUT/arch/arm64/boot/Image" ] || { echo "Image missing" >&2; exit 1; }

  grep -q '^CONFIG_KSU=y$' "$OUT/.config" || { echo "CONFIG_KSU not enabled" >&2; exit 1; }

  if uses_builtin_susfs; then
    grep -q '^CONFIG_KSU_SUSFS=y$' "$OUT/.config" || {
      echo "CONFIG_KSU_SUSFS not enabled" >&2
      exit 1
    }
    [ -f "$KERNEL_SRC/fs/susfs.c" ] || { echo "fs/susfs.c missing" >&2; exit 1; }
  else
    if grep -q '^CONFIG_KSU_SUSFS=y$' "$OUT/.config"; then
      echo "CONFIG_KSU_SUSFS must not be enabled for ${VARIANT}" >&2
      exit 1
    fi
  fi

  # KernelSU / KernelSU-Next derive their version from the git history.  A
  # tarball build silently falls back to version 16, which the managers reject,
  # so make sure the backend really reported a modern version.
  local version_pattern
  case "$ROOT_BACKEND" in
    sukisu) version_pattern='SukiSU.*version: [0-9]+' ;;
    kernelsu) version_pattern='KernelSU version: [0-9]+' ;;
    ksunext) version_pattern='KernelSU-Next version: [0-9]+' ;;
  esac
  local version
  version=$(grep -oE "$version_pattern" "$ARTIFACTS/build.log" | tail -n1 | grep -oE '[0-9]+$' || true)
  [ -n "$version" ] || {
    echo "Could not find a ${KSU_NAME} version in the build log" >&2
    exit 1
  }
  [ "$version" -ge 30000 ] || {
    echo "${KSU_NAME} reported version $version; the source tree is missing its git history" >&2
    exit 1
  }
  log "${KSU_NAME} version: $version"

  if strings "$OUT/arch/arm64/boot/Image" | grep -q "$STOCK_RELEASE"; then
    log "Image contains the pinned release string"
  else
    echo "Release string not found in Image" >&2
    exit 1
  fi
}

package_anykernel() {
  log "Packaging AnyKernel3"
  if [ ! -d "$AK3_SRC" ]; then
    fetch_tarball WildKernels/AnyKernel3 "$AK3_COMMIT" "$AK3_SRC"
  fi
  cp "$ROOT/anykernel/onyx-boot.sh" "$AK3_SRC/"
  cp "$OUT/arch/arm64/boot/Image" "$AK3_SRC/Image"

  python3 - "$ROOT/anykernel/anykernel.sh" "$AK3_SRC/anykernel.sh" \
    "$VARIANT" "$KERNEL_STRING" "$VARIANT_NOTE" <<'PY'
import pathlib
import sys

src, dst, variant, kernel_string, note = sys.argv[1:6]
text = pathlib.Path(src).read_text()
for token, value in (("@VARIANT@", variant),
                     ("@KERNEL_STRING@", kernel_string),
                     ("@VARIANT_NOTE@", note)):
    if token not in text:
        raise SystemExit(f"{token} missing from the AnyKernel3 template")
    text = text.replace(token, value)
pathlib.Path(dst).write_text(text)
PY

  cat >"$AK3_SRC/onyx-variant.txt" <<EOF
variant:        $VARIANT
root backend:   $KSU_NAME
SUSFS:          $([ "$SUSFS_MODE" = builtin ] && echo "built-in (CONFIG_KSU_SUSFS=y)" || echo "not built in")
kernel release: $STOCK_RELEASE
note:           $VARIANT_NOTE
EOF

  local zip="$ARTIFACTS/$ZIP_NAME"
  rm -f "$zip"
  (
    cd "$AK3_SRC"
    zip -r9 "$zip" . -x '.git/*' -x '*/.git/*'
  )
  log "Created $zip"
}

main() {
  mkdir -p "$ARTIFACTS"
  if [ "${INSTALL_DEPS:-0}" = "1" ]; then
    install_deps
  fi

  log "Variant ${VARIANT}: ${KSU_NAME}, SUSFS mode ${SUSFS_MODE}"

  prepare_kernel
  prepare_ksu
  integrate_ksu
  configure_kernel
  build_kernel
  verify_kernel

  cp "$OUT/arch/arm64/boot/Image" "$ARTIFACTS/Image"
  cp "$OUT/arch/arm64/boot/Image.gz" "$ARTIFACTS/Image.gz"
  cp "$OUT/.config" "$ARTIFACTS/${VARIANT}.config"
  cp "$OUT/include/config/kernel.release" "$ARTIFACTS/kernel.release"

  if [ "${SKIP_ANYKEY:-0}" != "1" ]; then
    package_anykernel
  fi

  log "Artifacts:"
  ls -lh "$ARTIFACTS"
}

main "$@"
