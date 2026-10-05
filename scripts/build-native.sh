#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
out=target/native
mkdir -p "$out"
python3 - <<'PY'
from pathlib import Path
path = Path('/var/lib/waydroid/rootfs/system/etc/init/init.waydroid.rc')
if not path.exists():
    path = Path('target/native/init.waydroid.rc')
source = path.read_text()
line = '    setenv LD_PRELOAD /vendor/lib64/libanvildroid-window.so\n'
source = source.replace(line, '')
marker = 'service vendor.hwcomposer-2-1 /vendor/bin/hw/android.hardware.graphics.composer@2.1-service --desktop_file_hint=Waydroid.desktop\n'
if source.count(marker) != 1:
    raise SystemExit('Unsupported Waydroid composer service; no files installed')
Path('target/native/init.waydroid.rc').write_text(source.replace(marker, marker + line))
PY
if [ -f /var/lib/waydroid/rootfs/system/framework/framework-res.apk ]; then
    cp /var/lib/waydroid/rootfs/system/framework/framework-res.apk "$out/framework-res.apk"
fi
clang --target=x86_64-linux-android33 -DANVIL_CSD_EXPERIMENT=1 -DANVIL_BOOT_CANVAS=1 -DANVIL_KEYMAP_EDITOR=1 -DANVIL_GAME_FIT=1 -ffreestanding -fPIC -fno-stack-protector -O2 -Wall -Wextra -Werror \
  -nostdlib -shared -fuse-ld=lld -Wl,-soname,libanvildroid-window.so \
  native/bridge/bridge.c native/ime/text-state.c -o "$out/libanvildroid-window.so"
aapt package -f -M native/overlay/AndroidManifest.xml -S native/overlay/res \
  -I "$out/framework-res.apk" -F "$out/caption-unsigned.apk"
if [ ! -f "$out/overlay-key.p12" ]; then
  keytool -genkeypair -keystore "$out/overlay-key.p12" -storepass anvildroid -keypass anvildroid \
    -alias overlay -keyalg RSA -keysize 2048 -validity 3650 -dname CN=AnvilDroid-development
fi
apksigner sign --ks "$out/overlay-key.p12" --ks-pass pass:anvildroid \
  --out "$out/AnvilDroidCaption.apk" "$out/caption-unsigned.apk"
