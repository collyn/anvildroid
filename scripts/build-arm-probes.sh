#!/bin/sh
# Disposable diagnostic APKs only. Does not attach to or modify any runtime.
set -eu
cd "$(dirname "$0")/.."
android_jar=/usr/lib/android-sdk/platforms/android-23/android.jar
r8=target/tools/r8-8.3.37.jar
out=target/arm-probe
source=native/arm-probe
test -f "$android_jar" || { echo 'Missing cached Android SDK android.jar' >&2; exit 1; }
printf '%s  %s\n' 59753e70a74f918389cc87f1b7d66b5c0862932559167425708ded159e3de439 "$r8" | sha256sum -c -
mkdir -p "$out"
work=$(mktemp -d "$out/build.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
javac --release 8 -encoding UTF-8 -cp "$android_jar" -d "$work/classes" "$source"/*.java
jar cf "$work/input.jar" -C "$work/classes" .
java -cp "$r8" com.android.tools.r8.D8 --min-api 23 --lib "$android_jar" --output "$work" "$work/input.jar"
if [ ! -f "$out/development.p12" ]; then
    keytool -genkeypair -keystore "$out/development.p12" -storepass anvildroid -keypass anvildroid \
        -alias armprobe -keyalg RSA -keysize 2048 -validity 3650 -dname CN=AnvilDroid-ARM-Probe-development
fi
for bits in 32 64; do
    case "$bits" in
        32) abi=armeabi-v7a; triple=armv7a-linux-androideabi23; machine=ARM;;
        64) abi=arm64-v8a; triple=aarch64-linux-android23; machine=AArch64;;
    esac
    build="$work/arm$bits"
    mkdir -p "$build/lib/$abi"
    library="$build/lib/$abi/libarmprobe.so"
    clang --target="$triple" -ffreestanding -fPIC -fno-stack-protector -O2 -Wall -Wextra -Werror \
        -nostdlib -shared -fuse-ld=lld -Wl,-soname,libarmprobe.so "$source/probe.c" -o "$library"
    readelf -h "$library" | grep -Eq "Machine: +$machine$"
    readelf --dyn-syms --wide "$library" | grep -q 'Java_org_anvildroid_armprobe_MainActivity_run'
    cat > "$build/AndroidManifest.xml" <<XML
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.anvildroid.armprobe$bits" android:versionCode="1" android:versionName="1">
<uses-sdk android:minSdkVersion="23" android:targetSdkVersion="33"/>
<application android:label="ARM$bits acceptance probe" android:hasCode="true" android:extractNativeLibs="true">
<activity android:name="org.anvildroid.armprobe.MainActivity" android:exported="true"><intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter></activity>
<receiver android:name="org.anvildroid.armprobe.ProbeReceiver" android:exported="true"/>
</application></manifest>
XML
    aapt package -f -M "$build/AndroidManifest.xml" -I "$android_jar" -F "$build/unsigned.apk"
    cp "$work/classes.dex" "$build/classes.dex"
    (cd "$build" && aapt add unsigned.apk classes.dex "lib/$abi/libarmprobe.so")
    apksigner sign --ks "$out/development.p12" --ks-pass pass:anvildroid --out "$out/arm$bits.apk" "$build/unsigned.apk"
    apksigner verify "$out/arm$bits.apk"
    aapt dump badging "$out/arm$bits.apk" | grep -F "native-code: '$abi'"
done
sha256sum "$out/arm32.apk" "$out/arm64.apk"
