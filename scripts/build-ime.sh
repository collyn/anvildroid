#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
android_jar=/usr/lib/android-sdk/platforms/android-23/android.jar
r8=target/tools/r8-8.3.37.jar
test -f "$android_jar" || { echo "Missing Android SDK android.jar" >&2; exit 1; }
test -f "$r8" || { echo "Missing R8: see README.md" >&2; exit 1; }
printf '%s  %s\n' 59753e70a74f918389cc87f1b7d66b5c0862932559167425708ded159e3de439 "$r8" | sha256sum -c -
out=target/ime
mkdir -p "$out"
work=$(mktemp -d "$out/build.XXXXXX")
# Retain build intermediates for diagnostics; target/ is ignored.
javac --release 8 -Xlint:-options -encoding UTF-8 -cp "$android_jar" -d "$work/classes" \
    native/ime/android/src/org/anvildroid/ime/*.java
jar cf "$work/input.jar" -C "$work/classes" .
java -cp "$r8" com.android.tools.r8.D8 --min-api 23 --lib "$android_jar" \
    --output "$work" "$work/input.jar"
aapt package -f -M native/ime/android/AndroidManifest.xml \
    -S native/ime/android/res -I "$android_jar" -F "$work/unsigned.apk"
(cd "$work" && aapt add unsigned.apk classes.dex)
# Android 11+ rejects an unaligned resources.arsc; apksigner versions differ in
# whether they align, so align first and verify the signed output below.
python3 -I scripts/apk-align.py align "$work/unsigned.apk" "$work/aligned.apk"
if [ ! -f "$out/development.p12" ]; then
    keytool -genkeypair -keystore "$out/development.p12" -storepass anvildroid \
        -keypass anvildroid -alias ime -keyalg RSA -keysize 2048 -validity 3650 \
        -dname CN=AnvilDroid-IME-development
fi
apksigner sign --ks "$out/development.p12" --ks-pass pass:anvildroid \
    --out "$out/AnvilDroidIme.apk" "$work/aligned.apk"
apksigner verify "$out/AnvilDroidIme.apk"
python3 -I scripts/apk-align.py check "$out/AnvilDroidIme.apk"
echo "$out/AnvilDroidIme.apk"
