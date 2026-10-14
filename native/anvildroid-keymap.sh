#!/system/bin/sh
# The authenticated host adapter supplies a validated boot classpath.
# Control-only receiver; never sources private system files as UID shell.
[ -n "$BOOTCLASSPATH" ] || exit 1
export BOOTCLASSPATH DEX2OATBOOTCLASSPATH
export CLASSPATH=/vendor/framework/anvildroid-scrcpy-server.jar
exec /system/bin/app_process /system/bin --nice-name=anvildroid-keymap \
    com.genymobile.scrcpy.Server 3.3.1 scid=0 tunnel_forward=true \
    video=false audio=false control=true clipboard_autosync=false \
    power_on=false cleanup=false send_device_meta=false send_dummy_byte=true
