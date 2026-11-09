#!/system/bin/sh
# Resolve a named boot partition; never guess a raw disk partition number.
onyx_slot() {
  value=$(getprop ro.boot.slot_suffix 2>/dev/null)
  [ -n "$value" ] || value=$(getprop ro.boot.slot 2>/dev/null)
  if [ -z "$value" ] && [ -r "${ONYX_PROC_ROOT:-/proc}/bootconfig" ]; then
    value=$(awk -F= '/^[[:space:]]*androidboot.slot(_suffix)?[[:space:]]*=/ {gsub(/[[:space:]" ]/, "", $2); print $2; exit}' "${ONYX_PROC_ROOT:-/proc}/bootconfig")
  fi
  if [ -z "$value" ] && [ -r "${ONYX_PROC_ROOT:-/proc}/cmdline" ]; then
    for word in $(cat "${ONYX_PROC_ROOT:-/proc}/cmdline"); do
      case "$word" in androidboot.slot_suffix=*|androidboot.slot=*) value=${word#*=};; esac
    done
  fi
  case "$value" in a|_a|0) echo _a;; b|_b|1) echo _b;; esac
}
onyx_resolve_boot() {
  ONYX_BOOT_SLOT=$(onyx_slot)
  if [ -n "$onyx_slot_override" ]; then
    case "$onyx_slot_override" in _a|_b) ;; *) echo 'Invalid boot slot override' >&2; return 1;; esac
    if [ -n "$ONYX_BOOT_SLOT" ] && [ "$ONYX_BOOT_SLOT" != "$onyx_slot_override" ]; then
      echo 'Selected ZIP slot differs from the detected active slot; aborting' >&2; return 1
    fi
    ONYX_BOOT_SLOT=$onyx_slot_override
  fi
  [ -n "$ONYX_BOOT_SLOT" ] || { echo 'Active slot unavailable. Use the explicitly labelled ZIP for your confirmed slot.' >&2; return 1; }
  part=boot$ONYX_BOOT_SLOT
  dev=${ONYX_DEV_ROOT:-/dev}; sys=${ONYX_SYS_ROOT:-/sys}
  for node in "$dev/block/by-name/$part" "$dev/block/bootdevice/by-name/$part" $(find "$dev/block" -name "$part" 2>/dev/null); do
    if [ -b "$node" ]; then ONYX_BOOT_BLOCK=$node; return 0; fi
  done
  for entry in "$sys"/class/block/*/uevent; do
    [ -r "$entry" ] || continue
    grep -qx "PARTNAME=$part" "$entry" || continue
    name=$(sed -n 's/^DEVNAME=//p' "$entry")
    case "$name" in ''|*/*) continue;; esac
    node=$dev/block/$name
    if [ -b "$node" ]; then ONYX_BOOT_BLOCK=$node; return 0; fi
  done
  echo "No block device found for $part" >&2; return 1
}
