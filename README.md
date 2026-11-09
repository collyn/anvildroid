# AnvilDroid

[![Latest release](https://img.shields.io/github/v/release/anvildroid/anvildroid?display_name=tag)](https://github.com/anvildroid/anvildroid/releases/latest)
[![Release build](https://github.com/anvildroid/anvildroid/actions/workflows/release.yml/badge.svg)](https://github.com/anvildroid/anvildroid/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE-MIT)

Run Android apps on the Linux desktop. AnvilDroid is a GUI and runtime
controller for [Waydroid](https://waydro.id): isolated Android devices, app
windows on Wayland and X11, desktop notifications, game controls and more.

**Website:** [anvildroid.github.io](https://anvildroid.github.io/) ·
**User guide:** [docs/USER-GUIDE.md](docs/USER-GUIDE.md)

> **Experimental, actively developed.** Linux x86_64 only.

![App library with apps from a running device](docs/images/apps.png)

<table>
  <tr>
    <td><img src="docs/images/devices.png" alt="Devices: status, live usage and quick device/GPU choice"></td>
    <td><img src="docs/images/device-settings.png" alt="Device settings with categories in the Simple view"></td>
  </tr>
  <tr>
    <td align="center">Devices with live usage and a quick device/GPU switch</td>
    <td align="center">Device settings, one category at a time</td>
  </tr>
  <tr>
    <td><img src="docs/images/game-controls.png" alt="Game Controls with per-app control schemes, HUD theme"></td>
    <td><img src="docs/images/theme-hud.png" alt="App library in the HUD theme with the Forge accent"></td>
  </tr>
  <tr>
    <td align="center">Game controls with per-app schemes</td>
    <td align="center">Classic or HUD theme, six accent colours</td>
  </tr>
</table>

<sub>Screenshots show the built-in apps of a LineageOS-based Waydroid image.</sub>

## Install

**Debian, Ubuntu, Linux Mint**

```bash
curl -fsSL https://anvildroid.github.io/packages/install.sh | sudo bash
```

**Fedora**

```bash
curl -fsSL https://anvildroid.github.io/packages/install-fedora.sh | sudo bash
```

**openSUSE**

```bash
curl -fsSL https://anvildroid.github.io/packages/install-opensuse.sh | sudo bash
```

**Arch Linux**

```bash
curl -fsSL https://anvildroid.github.io/packages/install-arch.sh | sudo bash
```

**AppImage (any distribution):** download `AnvilDroid-<version>-x86_64.AppImage`
from the [latest release](https://github.com/anvildroid/anvildroid/releases/latest),
make it executable and run it. It installs the runtime controller on first use
(administrator approval required).

Single packages (`.deb`, `.rpm`, `.pkg.tar.zst`) are also attached to each
release.

### Requirements

- Linux x86_64 with a Wayland or X11 desktop session, glibc 2.39+ (Ubuntu 24.04 or newer)
- [Waydroid](https://docs.waydro.id/) installed (AnvilDroid can install the runtime tools for you)
- Intel or AMD GPU with Mesa; NVIDIA is experimental ([details](docs/TECHNICAL.md#compatibility-and-limitations))
- systemd and polkit

## Getting started

1. Open **AnvilDroid** from your applications menu.
2. On first start the **Apps** page shows **Get started**: **Download Android**,
   then **Create and start**. The first start takes a few minutes while the
   image is prepared.
3. **Install APK** or open an app. Each app gets its own desktop window.

Prefer to do it by hand? **Devices → Android images** downloads or imports an
image, then **Devices → + New device** creates a device from it.

Device settings come in **Simple** and **Advanced** views. Change the device
identity or GPU right from the Devices list; a running device restarts to apply
it. The [user guide](docs/USER-GUIDE.md) walks through every page: apps,
devices, images, device settings, game controls, ADB, themes and updates.

## Updates

- **Settings → Updates** checks for new releases, installs the right package
  for your system after you confirm, and restarts AnvilDroid.
- Installed from the repositories above? Your normal system update
  (`apt upgrade`, `dnf upgrade`, `pacman -Syu`) also updates AnvilDroid.

## Features

- Multiple isolated Android devices running side by side, each with its own
  apps, data, storage location and RAM/CPU limits
- System Waydroid management alongside managed devices
- Guided first start: download Android, create a device, install apps
- Android apps as native windows on Wayland and X11, plus the full Android UI
- Desktop notifications from Android apps, with per-app control
- Host input method (e.g. Fcitx), clipboard and shared folders
- Game controls: mouse-to-touch, key mapping, FPS mouse capture, macros and
  per-app control schemes (press **F2** in a game window)
- Official and custom images (folder, ZIP or URL; ext4, EROFS, SquashFS)
- ARM translation (libndk_translation, libhoudini), device identity profiles,
  Google Play registration help
- ADB for Android Studio, diagnostics export
- HUD and Classic themes, light or dark, six accent colours

## Known limitations

- Official Android 13 Waydroid images are the most tested; newer and custom
  images are experimental.
- App and game compatibility depends on the image, ARM translator and GPU.
  Apps that require Play Integrity (banking, some games, DRM video) usually
  do not work.
- Full Android UI and separate app windows cannot be used at the same time on
  one device.

More: [compatibility and limitations](docs/TECHNICAL.md#compatibility-and-limitations).

## Documentation

- [User guide](docs/USER-GUIDE.md): using every page, from the first start to
  game controls, themes and troubleshooting
- [Technical guide](docs/TECHNICAL.md): architecture diagrams, display and
  notification pipelines, building from source, releasing and development
- [Native X11 adapter](native/x11/README.md) · [NVIDIA payload](native/nvidia/README.md)

## Credits

AnvilDroid builds on [Waydroid](https://github.com/waydroid/waydroid),
the [Android Open Source Project](https://source.android.com/) and
[LineageOS](https://github.com/LineageOS),
[quinovax/waydroid-nvidia](https://github.com/quinovax/waydroid-nvidia),
[Mesa](https://gitlab.freedesktop.org/mesa/mesa),
[virglrenderer](https://gitlab.freedesktop.org/virgl/virglrenderer),
[ANGLE](https://github.com/google/angle),
[astc-encoder](https://github.com/ARM-software/astc-encoder),
[scrcpy](https://github.com/Genymobile/scrcpy), [Tauri](https://github.com/tauri-apps/tauri),
the [supremegamers](https://github.com/supremegamers) ARM translation
prebuilts and [Noto Fonts](https://github.com/notofonts/noto-fonts). Thank you
to their maintainers and contributors.

Third-party code, fonts, images and binaries keep their own licenses: see the
[NVIDIA notices](packaging/nvidia/NOTICE),
[NVIDIA component licenses](packaging/nvidia/LICENSE.upstream),
[ASTC decoder license](packaging/nvidia/LICENSE.astc-encoder) and
[font license](native/menu-font-OFL.txt). Bundled AOSP tool notices are
installed in `/usr/local/lib/anvildroid-controller/tools/NOTICE.txt`.

## License

AnvilDroid's source code is [MIT](LICENSE-MIT) licensed. Waydroid,
Android/LineageOS images, Google services, ARM translation packages and
installed apps remain under their own licenses. The ARM translators are
proprietary prebuilts downloaded separately; read the
[ARM translation licensing notes](docs/TECHNICAL.md#arm-translation-licensing)
before redistributing them.
