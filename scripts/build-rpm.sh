#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VERSION=${1:-$(python3 "$ROOT/scripts/release-version.py" --rpm)}
case "$VERSION" in ''|*[!0-9A-Za-z.+~_-]*) echo "Invalid version: $VERSION" >&2; exit 2;; esac
command -v rpmbuild >/dev/null || { echo 'Missing build tool: rpmbuild' >&2; exit 1; }
OUT="$ROOT/target/releases"
TOP="$ROOT/target/rpm"
STAGE="$TOP/stage"
rm -rf "$TOP" "$OUT/anvildroid-controller-${VERSION}-1.x86_64.rpm"
mkdir -p "$TOP" "$OUT"
sh "$ROOT/scripts/build-runtime.sh"
sh "$ROOT/scripts/stage-package.sh" "$STAGE"
mkdir -p "$STAGE/var/lib/anvildroid-controller" "$STAGE/run/anvildroid"
cat > "$TOP/anvildroid-controller.spec" <<EOF
# Preserve hash-pinned foreign Android ELF payloads and private host libraries.
%global debug_package %{nil}
%global __strip /bin/true
%global __requires_exclude_from ^/usr/local/lib/anvildroid-controller/nvidia-v0[.]1[.]2/.*$
%global __provides_exclude_from ^/usr/local/lib/anvildroid-controller/nvidia-v0[.]1[.]2/.*$
Name: anvildroid-controller
Version: $VERSION
Release: 1
Summary: AnvilDroid Android runtime controller
License: MIT AND BSD-3-Clause AND Apache-2.0
URL: https://github.com/collyn/anvildroid
BuildArch: x86_64
Requires: python3, systemd, patchelf >= 0.18
Requires: glibc >= 2.39, libepoxy, libdrm, mesa-libgbm, libX11, expat, vulkan-loader, util-linux
Requires: webkit2gtk4.1, gtk3, glib2, javascriptcoregtk4.1, libsoup3, pango, cairo, gdk-pixbuf2
Requires(post): systemd
Requires(preun): systemd

%description
GPU accelerated Waydroid runtime controller with BinderFS and Wayland support.

%install
mkdir -p %{buildroot}
cp -a $STAGE/. %{buildroot}/

%post
python3 /usr/local/lib/anvildroid-controller/scripts/patch-waydroid.py >/dev/null 2>&1 || :
# Prefer nftables when this kernel has no legacy iptables tables.
net=/usr/lib/waydroid/data/scripts/waydroid-net.sh
if [ -f "\$net" ] && command -v iptables-nft >/dev/null 2>&1 && command -v iptables-legacy >/dev/null 2>&1 && ! iptables-legacy -t filter -L >/dev/null 2>&1 && grep -q 'command -v iptables-legacy' "\$net"; then
  [ -e "\$net.anvildroid-legacy-backup" ] || cp -p "\$net" "\$net.anvildroid-legacy-backup"
  sed -i 's/command -v iptables-legacy/command -v iptables-nft/g; s/command -v ip6tables-legacy/command -v ip6tables-nft/g' "\$net"
fi
systemctl daemon-reload >/dev/null 2>&1 || :
systemctl enable anvildroid-controller.service >/dev/null 2>&1 || :
systemctl restart anvildroid-controller.service >/dev/null 2>&1 || :

%preun
if [ "\$1" -eq 0 ]; then systemctl disable --now anvildroid-controller.service >/dev/null 2>&1 || :; fi

%files
%license %{_datadir}/licenses/anvildroid-controller/LICENSE-MIT
%{_bindir}/anvildroid-gui
%{_datadir}/applications/org.anvildroid.gui.desktop
%{_datadir}/icons/hicolor/*/apps/org.anvildroid.gui.png
%{_prefix}/local/lib/anvildroid-controller
/usr/lib/systemd/system/anvildroid-controller.service
%dir /var/lib/anvildroid-controller
%dir /run/anvildroid
EOF
rpmbuild --define "_topdir $TOP" --define "_buildrootdir $TOP/BUILDROOT" --define "_rpmdir $OUT" --define "_srcrpmdir $TOP/SRPMS" --define "_specdir $TOP" --define "_sourcedir $TOP" -bb "$TOP/anvildroid-controller.spec" >/dev/null
RPM="$OUT/x86_64/anvildroid-controller-${VERSION}-1.x86_64.rpm"
mv "$RPM" "$OUT/anvildroid-controller-${VERSION}-1.x86_64.rpm"
rm -rf "$OUT/x86_64" "$TOP"
rpm -qip "$OUT/anvildroid-controller-${VERSION}-1.x86_64.rpm"
sha256sum "$OUT/anvildroid-controller-${VERSION}-1.x86_64.rpm"
