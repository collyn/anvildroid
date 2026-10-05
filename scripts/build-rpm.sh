#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VERSION=${1:-$(python3 "$ROOT/scripts/release-version.py" --rpm)}
case "$VERSION" in ''|*[!0-9A-Za-z.+~_-]*) echo "Invalid version: $VERSION" >&2; exit 2;; esac
command -v rpmbuild >/dev/null || { echo 'Missing build tool: rpmbuild' >&2; exit 1; }
OUT="$ROOT/target/releases"
TOP="$ROOT/target/rpm"
BUILDROOT="$TOP/BUILDROOT/anvildroid-controller-${VERSION}-1.x86_64"
rm -rf "$TOP" "$OUT/anvildroid-controller-${VERSION}-1.x86_64.rpm"
mkdir -p "$TOP" "$OUT"
sh "$ROOT/scripts/stage-package.sh" "$BUILDROOT"
mkdir -p "$BUILDROOT/var/lib/anvildroid-controller" "$BUILDROOT/run/anvildroid"
cat > "$TOP/anvildroid-controller.spec" <<EOF
Name: anvildroid-controller
Version: $VERSION
Release: 1
Summary: AnvilDroid Android runtime controller
License: MIT
URL: https://github.com/collyn/anvildroid
BuildArch: x86_64
Requires: python3, systemd, patchelf >= 0.18
Requires(post): systemd
Requires(preun): systemd

%description
GPU accelerated Waydroid runtime controller with BinderFS and Wayland support.

%install
mkdir -p %{buildroot}
cp -a $BUILDROOT/. %{buildroot}/

%post
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
