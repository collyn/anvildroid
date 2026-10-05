#!/bin/sh
# Install only the user-level GUI/CLI. No sudo, runtime, or Android data changes.
set -eu
bundle=${1:-$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)}
bundle=$(CDPATH= cd -- "$bundle" && pwd)
test -x "$bundle/bin/anvildroid-gui" || { echo "Invalid AnvilDroid bundle: $bundle" >&2; exit 1; }
prefix=${ANVILDROID_PREFIX:-$HOME/.local/share/anvildroid}
mkdir -p "$prefix/bin" "$HOME/.local/bin" "$HOME/.local/share/applications"
cp "$bundle/bin/anvildroid-gui" "$prefix/bin/anvildroid-gui"
cp "$bundle/bin/anvildroid" "$prefix/bin/anvildroid"
chmod 755 "$prefix/bin/anvildroid-gui" "$prefix/bin/anvildroid"
cat > "$HOME/.local/share/applications/org.anvildroid.gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AnvilDroid
Comment=Manage isolated Waydroid runtimes
Exec=$prefix/bin/anvildroid-gui
Terminal=false
Categories=System;Utility;
EOF
ln -sfn "$prefix/bin/anvildroid" "$HOME/.local/bin/anvildroid"
echo "Installed GUI and CLI to $prefix"
echo "If Waydroid is absent, the GUI still opens and explains the prerequisite."
echo "Optional controller install (requires root): sudo python3 $bundle/scripts/install-runtime-controller.py"
