#!/bin/sh
# Run in a disposable distro container, never on the developer's host.
set -eu
FORMAT=${1:?deb, rpm or pkg required}
PACKAGE_FILE=${2:?absolute package path required}
VERSION=${3:?workspace version required}
UPGRADE_FROM=${4:-}
package_version=$VERSION
if [ -n "$UPGRADE_FROM" ]; then
  test "$FORMAT" = deb || { echo 'Upgrade fixture is currently supported for deb only' >&2; exit 2; }
  test -f "$UPGRADE_FROM"
  test "$(dpkg-deb -f "$UPGRADE_FROM" Package)" = anvildroid-controller
  package_version=$(dpkg-deb -f "$PACKAGE_FILE" Version)
  test "$package_version" = "$(printf '%s' "$VERSION" | sed 's/-/~/')"
  dpkg --compare-versions "$(dpkg-deb -f "$UPGRADE_FROM" Version)" lt "$package_version"
fi
verify_installed() {
  if [ -d /run/systemd/system ]; then
    systemctl is-active --quiet anvildroid-controller.service
    echo 'ACCEPTANCE: real systemd controller service active'
  fi
  xvfb-run -a dbus-run-session -- python3 /work/scripts/verify-package.py \
    --installed --gui --version "$VERSION"
}
case "$FORMAT" in
  deb)
    export DEBIAN_FRONTEND=noninteractive
    # dpkg can preserve permissions of an existing directory. Exercise the
    # legacy 0775 install on both first installation and reinstallation.
    install -d -o root -g root -m 0775 /usr/local/lib/anvildroid-controller
    if [ "${ANVILDROID_APT_UPDATE:-1}" != 0 ]; then apt-get update; fi
    apt-get install -y --no-install-recommends "$PACKAGE_FILE" xvfb xauth dbus-x11
    test "$(stat -c %a /usr/local/lib/anvildroid-controller)" = 755
    verify_installed
    echo "ACCEPTANCE: Debian fresh install GUI/CLI/controller verified ($VERSION)"
    chmod 0775 /usr/local/lib/anvildroid-controller
    dpkg --install "$PACKAGE_FILE"
    test "$(stat -c %a /usr/local/lib/anvildroid-controller)" = 755
    dpkg-query -W anvildroid-controller
    echo "ACCEPTANCE: Debian fresh install and same-version reinstall completed ($VERSION)"
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
verify_installed
echo "ACCEPTANCE: installed GUI/CLI/controller verified ($FORMAT $VERSION)"
if [ -n "$UPGRADE_FROM" ]; then
  # Install a real earlier artifact, then upgrade it in place with the current
  # package. Dependencies were resolved by the initial clean installation.
  apt-get remove -y anvildroid-controller
  test ! -e /usr/bin/anvildroid
  test ! -e /usr/bin/anvildroid-gui
  prior_result=0
  dpkg --install "$UPGRADE_FROM" || prior_result=$?
  prior_version=$(dpkg-deb -f "$UPGRADE_FROM" Version)
  test "$(dpkg-query -W -f='${Version}' anvildroid-controller)" = "$prior_version"
  prior_status=$(dpkg-query -W -f='${Status}' anvildroid-controller)
  upgrade_kind='in-place upgrade'
  if [ "$prior_result" != 0 ]; then
    # Explicit opt-in permits recovery acceptance when a real old maintainer
    # script cannot configure in this container. Never label this a fully
    # configured old-version upgrade, and retain the real error in the log.
    test "${ANVILDROID_ALLOW_HALF_CONFIGURED_RECOVERY:-0}" = 1
    test "$prior_status" = 'install ok half-configured'
    upgrade_kind='half-configured recovery'
    echo "ACCEPTANCE LIMITATION: prior $prior_version install failed (exit $prior_result, $prior_status)"
  else
    test "$prior_status" = 'install ok installed'
    if [ -d /run/systemd/system ]; then
      systemctl is-active --quiet anvildroid-controller.service
    fi
    echo "ACCEPTANCE: prior package fully configured ($prior_version)"
  fi
  marker=/var/lib/anvildroid-controller/.acceptance-preserve
  printf '%s\n' 'disposable acceptance data' > "$marker"
  chmod 0775 /usr/local/lib/anvildroid-controller
  dpkg --install "$PACKAGE_FILE"
  test "$(dpkg-query -W -f='${Version}' anvildroid-controller)" = "$package_version"
  test "$(dpkg-query -W -f='${Status}' anvildroid-controller)" = 'install ok installed'
  test "$(stat -c %a /usr/local/lib/anvildroid-controller)" = 755
  test "$(cat "$marker")" = 'disposable acceptance data'
  verify_installed
  echo "ACCEPTANCE: $upgrade_kind $prior_version -> $package_version verified; data preserved"
fi
# Verify package-manager removal succeeds, including lifecycle hooks.
case "$FORMAT" in
  deb) apt-get remove -y anvildroid-controller;;
  rpm) dnf remove -y anvildroid-controller;;
  pkg) pacman -R --noconfirm anvildroid-controller;;
esac
test ! -e /usr/bin/anvildroid
test ! -e /usr/bin/anvildroid-gui
if [ -d /run/systemd/system ]; then
  if systemctl is-active --quiet anvildroid-controller.service; then
    echo 'Controller service still active after package removal' >&2
    exit 1
  fi
fi

if [ -n "$UPGRADE_FROM" ]; then
  test "$(cat "$marker")" = 'disposable acceptance data'
fi
echo "ACCEPTANCE: package removal verified; GUI/CLI files removed"
