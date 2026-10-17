#!/bin/sh
# Run in a disposable distro container, never on the developer's host.
set -eu
FORMAT=${1:?deb, rpm or pkg required}
PACKAGE_FILE=${2:?absolute package path required}
VERSION=${3:?workspace version required}
case "$FORMAT" in
  deb)
    export DEBIAN_FRONTEND=noninteractive
    # dpkg can preserve permissions of an existing directory. Exercise the
    # legacy 0775 install on both first installation and reinstallation.
    install -d -o root -g root -m 0775 /usr/local/lib/anvildroid-controller
    apt-get update
    apt-get install -y --no-install-recommends "$PACKAGE_FILE" xvfb xauth dbus-x11
    test "$(stat -c %a /usr/local/lib/anvildroid-controller)" = 755
    chmod 0775 /usr/local/lib/anvildroid-controller
    dpkg --install "$PACKAGE_FILE"
    test "$(stat -c %a /usr/local/lib/anvildroid-controller)" = 755
    dpkg-query -W anvildroid-controller
    ;;
  rpm)
    dnf install -y "$PACKAGE_FILE" xorg-x11-server-Xvfb xorg-x11-xauth dbus-daemon
    rpm -V anvildroid-controller
    ;;
  pkg)
    pacman -Syu --noconfirm
    pacman -S --noconfirm xorg-server-xvfb xorg-xauth dbus
    install_log=$(mktemp)
    if pacman -U --noconfirm "$PACKAGE_FILE" >"$install_log" 2>&1; then
      cat "$install_log"
    else
      cat "$install_log"
      rm -f "$install_log"
      exit 1
    fi
    if grep -q 'directory permissions differ' "$install_log"; then
      rm -f "$install_log"
      echo 'Package installation reported mismatched directory permissions' >&2
      exit 1
    fi
    rm -f "$install_log"
    pacman -Qk anvildroid-controller
    ;;
  *) echo "Unknown package format: $FORMAT" >&2; exit 2;;
esac
xvfb-run -a dbus-run-session -- python3 /work/scripts/verify-package.py \
  --installed --gui --version "$VERSION"
# Verify package-manager removal succeeds, including lifecycle hooks.
case "$FORMAT" in
  deb) apt-get remove -y anvildroid-controller;;
  rpm) dnf remove -y anvildroid-controller;;
  pkg) pacman -R --noconfirm anvildroid-controller;;
esac
test ! -e /usr/bin/anvildroid
test ! -e /usr/bin/anvildroid-gui
