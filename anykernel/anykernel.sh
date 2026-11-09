### AnyKernel3 Ramdisk Mod Script
## Based on osm0sis AnyKernel3 and WildKernels GKI installer.
properties() { '
kernel.string=Onyx Wild Performance (experimental)
do.devicecheck=1
do.modules=0
do.systemless=0
do.cleanup=1
do.cleanuponabort=0
do.check_boot_version=0
device.name1=onyx
device.name2=onyx_global
device.name3=onyx_eea
supported.versions=
supported.patchlevels=
supported.vendorpatchlevels=
'; }

onyx_slot_override=
. ./onyx-boot.sh
onyx_resolve_boot || exit 1
block=$ONYX_BOOT_BLOCK
SLOT=$ONYX_BOOT_SLOT
is_slot_device=0
ramdisk_compression=auto
patch_vbmeta_flag=auto
no_magisk_check=1
. tools/ak3-core.sh

ui_print "Onyx Wild Performance: experimental device-validation build"
ui_print "Module symbol CRC mismatch bypass enabled (gki-clo behavior)"
ui_print "Preserving installed ROM ramdisk, bootconfig and device trees"
split_boot
if [ -f "$SPLITIMG/ramdisk.cpio" ]; then
  unpack_ramdisk
  write_boot
else
  flash_boot
fi
