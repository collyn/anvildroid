#!/bin/sh
# Private AOSP tools used to compile/sign overlays against downloaded images.
set -eu
umask 022
SDK_ROOT=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/usr/lib/android-sdk}}
TOOLS="$SDK_ROOT/build-tools/35.0.0"
DEST=${1:?private tools destination required}
for file in aapt lib64/libc++.so lib/apksigner.jar NOTICE.txt source.properties; do
  test -s "$TOOLS/$file" || { echo "Missing image tool input: $TOOLS/$file (install Android build-tools 35.0.0)" >&2; exit 1; }
done
mkdir -p "$DEST/lib64" "$DEST/lib"
install -m 0755 "$TOOLS/aapt" "$DEST/aapt"
install -m 0644 "$TOOLS/lib64/libc++.so" "$DEST/lib64/libc++.so"
install -m 0644 "$TOOLS/lib/apksigner.jar" "$DEST/lib/apksigner.jar"
install -m 0644 "$TOOLS/NOTICE.txt" "$DEST/NOTICE.txt"
install -m 0644 "$TOOLS/source.properties" "$DEST/source.properties"
cat > "$DEST/apksigner" <<'EOF'
#!/bin/sh
set -eu
exec /usr/bin/java -jar "$(dirname "$0")/lib/apksigner.jar" "$@"
EOF
chmod 0755 "$DEST/apksigner"
