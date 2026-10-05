#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p target/native
probe_flag=
bridge_name=libanvildroid-csd-window.so
case "${1:-}" in
  '') ;;
  --display-growth-probe)
    probe_flag=-DANVIL_DISPLAY_GROWTH_PROBE=1
    bridge_name=libanvildroid-display-probe.so ;;
  --auto-boot-canvas)
    probe_flag=-DANVIL_BOOT_CANVAS=1
    bridge_name=libanvildroid-boot-canvas.so ;;
  --keymap-overlay)
    probe_flag='-DANVIL_BOOT_CANVAS=1 -DANVIL_KEYMAP_OVERLAY_PROBE=1'
    bridge_name=libanvildroid-keymap-overlay.so ;;
  *) echo 'Unknown build option' >&2; exit 2 ;;
esac
clang --target=x86_64-linux-android33 -DANVIL_CSD_EXPERIMENT=1 $probe_flag \
  -ffreestanding -fPIC -fno-stack-protector -O2 -Wall -Wextra -Werror \
  -nostdlib -shared -fuse-ld=lld -Wl,-soname,libanvildroid-window.so \
  native/bridge/bridge.c native/ime/text-state.c -o "target/native/$bridge_name"
aapt package -f -M native/overlay/AndroidManifest.xml -S native/overlay/res \
  -I target/native/framework-res.apk -F target/native/csd-caption-unsigned.apk
apksigner sign --ks target/native/overlay-key.p12 --ks-pass pass:anvildroid \
  --out target/native/AnvilDroidCsdCaption.apk target/native/csd-caption-unsigned.apk
