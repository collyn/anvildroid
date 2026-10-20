# AnvilDroid

[![Stars](https://img.shields.io/github/stars/collyn/anvildroid?style=flat)](https://github.com/collyn/anvildroid/stargazers)
[![Latest release](https://img.shields.io/github/v/release/collyn/anvildroid?display_name=tag)](https://github.com/collyn/anvildroid/releases/latest)
[![Release build](https://github.com/collyn/anvildroid/actions/workflows/release.yml/badge.svg)](https://github.com/collyn/anvildroid/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE-MIT)

Linux GUI and controller for Waydroid. Manage isolated Android runtimes,
existing Waydroid installations, Android apps, custom images, ARM translation,
GPU settings and desktop windows.

> Status: **experimental / actively developed**. The source version is defined
> by `[workspace.package].version` in [`Cargo.toml`](Cargo.toml).

## Current status

As of **2026-10-17**, AnvilDroid has a Tauri 2 desktop GUI, a Python runtime
controller and isolated Android workers, plus native Wayland window and input
integration. Managed runtimes can run concurrently with separate app data,
namespaces, Binder devices and resource limits. Existing Waydroid installations
can also be managed from the GUI.

Live checks have covered official Android 13 images, concurrent runtime
isolation, app management, full Android UI open/close recovery and shutdown data
persistence. NVIDIA/Venus is now integrated as an experimental backend with a
pinned, verified host/guest payload. These checks cover specific configurations;
compatibility across custom images, desktops and games is still being developed.

## Features

- Managed runtimes with independent start, stop, restart, storage and resource
  limits; batch actions and per-runtime settings.
- Runtime Memory/CPU/Disk usage display and optional start on boot.
- Image library for official images and custom Waydroid images from a folder,
  ZIP or ZIP URL; ext4, EROFS, SquashFS and Android sparse image handling.
- App library with runtime filters, install, launch, force-stop, uninstall,
  favorites, recently opened apps and configurable desktop/menu shortcuts.
- Desktop and headless modes, full Android UI, window profiles, resize controls,
  host IME, clipboard and key mapping support.
  App windows and Full Android UI offer **Menu → Size** presets and
  **Landscape / Portrait**. **Custom** follows the user’s window size and
  updates Android after a resize finishes; Full UI fits within the physical
  canvas reserved at startup. Selecting a preset leaves Custom mode. Full UI updates Android’s logical display and fits
  its acknowledged content area to the host window. The boot canvas reserves
  both preset orientations to avoid downsampling the largest presets. Android
  scaling is disabled where supported; images that ignore this setting can
  still soften smaller presets when fitting their physical display.
  **Resize app layout** updates Android task bounds; **Keep resolution** changes
  the host window while preserving the Android rendering resolution.
- ARM translation selection: libndk_translation and libhoudini catalogs;
  new/reinstalled x86_64 runtimes default to libhoudini HPE com1.3.
  These third-party binaries have [separate licensing terms](#optional-arm-translation-binaries).
- Per-app Fake Wi-Fi and mouse-as-touch settings, with runtime-scoped readback,
  reset and rollback. Image support and app behavior still need live validation.
- Explicit shared folders for managed runtimes and integrated Default, with read-only or read/write
  mounts activated at the next start. See [folder sharing](docs/SHARED-FOLDERS.md).
- Native game controls: Mouse touch, fixed keyboard/mouse bindings, WASD joystick,
  swipe, repeated clicks, skill casting, finite macros, aim/fire and Look with
  optional FPS capture. The editor preserves older profiles until Save;
  see [authoring controls and limits](docs/GAME-CONTROLS.md).
- Runtime ARM translation settings show version, checksum, requirements and cache
  status alongside the existing installer. Google Play keeps its image-creation
  workflow. See [runtime settings](docs/RUNTIME-TOOLS.md).
- Local JSON diagnostic export with optional bounded logs and best-effort
  redaction. Reports are saved privately and never uploaded automatically.
- GPU diagnostics and per-runtime RAM/CPU/GPU settings.
- Experimental NVIDIA rendering through ANGLE/Venus, including hybrid display
  presentation and ASTC texture decoding support.
- Google Play device registration help and GSF Android ID copy.
- ADB connections for Default and managed runtimes, compatible with Android Studio.
- GUI login autostart, startup minimized and minimize-to-panel preferences.

## Compatibility and limitations

- Official Android 13 Waydroid images are the main validated path. Custom x86_64
  images require Android SDK 33+ and compatible Waydroid components; newer Android
  builds remain experimental, especially for desktop controls and ARM translation.
- Full Android UI and separate app windows are exclusive within one runtime.
  Opening the full UI closes its app tasks; closing that window restores the
  previous display mode without reopening those apps. Other runtimes can use
  different modes concurrently.
- NVIDIA/Venus currently requires an Android 13 image, driver 535+,
  `nvidia-drm.modeset=1`, desktop-user access to `/dev/udmabuf`, exactly one NVIDIA
  GPU and the matching Vulkan ICD. Buffer sharing and compositor compatibility
  still require live verification. See the [NVIDIA payload notes](native/nvidia/README.md).
- ARM translation and game support depend on the selected image, translator and
  graphics backend. Broad gameplay, mixed-DPI, desktop compatibility and long
  endurance testing remain incomplete.
- The CLI is a smaller experimental Waydroid interface, not equivalent to the GUI.
  Most commands target Default; managed runtime selection currently supports app
  launch through `anvildroid --runtime ID run PACKAGE`.

## Requirements

- Linux x86_64 with Wayland or X11. Automatic mode uses native X11 on Xorg
  and preserves the Wayland backend on Wayland. Unsupported native X11
  configurations fail before starting Android; Weston requires explicitly
  selecting the compatibility backend.
  Native X11 is included as an experimental per-runtime option under Display →
  Desktop for SDK 33-36 images exposing Waydroid's per-app composer, with Mesa
  guest rendering. The pinned NVIDIA/Venus guest renderer requires SDK 33;
  AMD/Intel/NVIDIA can present its windows. SDK 36 remains under validation.
  It uses bridge-drawn CSD directly on Xorg without Weston;
  see [native X11 requirements](native/x11/README.md).
- Waydroid, LXC, Binder/BinderFS, Linux namespaces and cgroups.
  Managed runtimes can prepare downloaded/imported images before host Waydroid
  initialization; they use the installed Waydroid package’s LXC templates.
  Default still requires Waydroid initialization.
- Mesa-compatible Intel or AMD GPU, or an NVIDIA setup meeting the experimental
  backend requirements above.
- Python 3.11+, systemd and the host libraries declared by the selected package.

Building from source additionally requires Rust/Cargo, GTK/WebKitGTK development
packages, Clang/LLD, JDK, Android build tools, `patchelf` and the tools for the
selected package format (`dpkg-deb`, `rpmbuild` or `zstd`). The release workflow
builds on Ubuntu 24.04; package metadata requires glibc 2.39 or newer.

Installed packages also need WebKitGTK 4.1, GTK3, GLib, JavaScriptCoreGTK 4.1,
Soup 3, Pango, Cairo and GDK Pixbuf runtime libraries. Debian/RPM/Arch package
metadata declares these dependencies.
Packages include private AOSP `aapt` and `apksigner` tools for preparing downloaded
images and declare a headless Java runtime dependency. These tools are available
to the controller service without installing a system-wide Android SDK.
The optional panel icon also needs an AppIndicator runtime library, such as
`libayatana-appindicator3-1`, and desktop support for tray icons.

## Build packages

Install the build dependencies and Android SDK APIs 23 and 35, plus build-tools
35.0.0, first. The GUI/helper build expects API 23's `android.jar` at
`/usr/lib/android-sdk/platforms/android-23/android.jar`. Set `ANDROID_HOME` to
the SDK containing API 35 and build-tools 35.0.0 for both the scrcpy server build
and package staging. The distro's `aapt`/`apksigner` packages alone do not provide
the private AOSP tools bundled in release packages. If these tools are missing:

```bash
sdkmanager --sdk_root="/path/to/android-sdk" "build-tools;35.0.0"
ANDROID_HOME="/path/to/android-sdk" ./scripts/build-deb.sh
```

Use your actual SDK path. Package staging also detects the standard
`~/Android/Sdk` location and the project-local `target/tools/android-sdk` cache
when neither `ANDROID_HOME` nor `ANDROID_SDK_ROOT` is set.
Prepare the pinned R8 jar, Android framework inputs and patched scrcpy server,
then download the pinned NVIDIA archives (requires authenticated GitHub CLI access):

```bash
sh scripts/prepare-ci-inputs.sh
sh scripts/prepare-scrcpy.sh
python3 scripts/prepare-nvidia-fork.py --download
```

Native X11 source builds additionally need Wayland server/protocols, EGL/GLES2,
X11/Xcursor and xkbcommon development packages. The adapter uses the existing
Android composer and bridge; no extra guest HAL, Android link libraries or NDK
build is needed for X11. The shared build bundles both display backends.
Release CI builds X11 once and passes the verified payload to package jobs using
`ANVILDROID_X11_PAYLOAD=/absolute/path/to/payload`; local builds compile it by default.

These inputs are cached under `target/tools/`, `target/native/` and
`target/input-diagnostic/`. Verified
NVIDIA archives can be reused without downloading again. Then build the desired
package:

```bash
./scripts/build-deb.sh
./scripts/build-rpm.sh
./scripts/build-pkg.sh
```

Output naming (`DEBIAN_VERSION`, `RPM_VERSION` and `VERSION` are read from
`scripts/release-version.py --debian`, `--rpm` and without flags, respectively):

```text
target/releases/anvildroid-controller_<DEBIAN_VERSION>_amd64.deb
target/releases/anvildroid-controller-<RPM_VERSION>-1.x86_64.rpm
target/releases/anvildroid-controller-<VERSION>-1-x86_64.pkg.tar.zst
```

The version comes from `[workspace.package].version` in `Cargo.toml` and is
shown in the GUI sidebar and CLI help. The `.pkg.tar.zst` file is an Arch Linux
package. Each command builds only its selected package and required shared
payload; no command installs or restarts anything. `scripts/build-runtime.sh`
contains the shared GUI/CLI/native/Android build steps. All three packages
install both `anvildroid-gui` and the `anvildroid` CLI in `/usr/bin`.

## Run

Open `anvildroid-gui`, start the controller from **Runtimes**, and create
a runtime. AnvilDroid prepares its images and starts Android automatically.
Keep AnvilDroid open until this finishes. Preparation and boot progress appear
on the runtime card; failures retain the new runtime with a retry action.
Install and launch APKs from the Apps page while the selected runtime is running.
Use **Images** to download/import images, the runtime Settings button to adjust
its configuration, and **Settings** for GUI preferences, runtime start on boot
and ARM packages. **Open Waydroid** opens the selected runtime's full Android UI.
Runtime Settings includes **Shared folders** inside **Storage** and
**Export report** inside **Diagnostics**. In the app context menu, **Compatibility** opens the
per-app settings; see [app compatibility](docs/APP-COMPATIBILITY.md).

For CLI commands and their supported runtime scope, run `anvildroid --help`.
The CLI's source version follows the workspace version automatically.

## Automated releases

Push a tag matching the workspace version (update `Cargo.toml` before tagging
a new version). GitHub Actions runs separate Debian,
RPM and Arch package jobs, verifies their extracted payloads, and tests actual
installation, controller IPC, CLI execution, GUI rendering and removal in
Ubuntu, Fedora and Arch containers. The release job runs only after all checks
pass, checks the package checksums and creates a GitHub Release:

```bash
release_version=$(python3 scripts/release-version.py)
git tag -a "v${release_version}" -m "Release ${release_version}"
git push origin main
git push origin "v${release_version}"
```

Supported versions: `X.Y.Z`, `X.Y.Z-alpha.N`, `X.Y.Z-beta.N` and `X.Y.Z-rc.N`.
The workflow is defined in `.github/workflows/release.yml`.
It can also be run manually with `workflow_dispatch` to validate packages;
publishing requires a version tag.
It also prepares the pinned NVIDIA payload. If the default GitHub token cannot
read the fork's Actions artifacts, configure `NVIDIA_ARTIFACT_TOKEN` with Actions
read access to `collyn/waydroid-nvidia`. Those artifacts have retention limits;
retain verified archives for reproducible local builds.

## Development

```bash
cargo check --workspace --locked
cargo test --workspace --locked
```

Main directories: `crates/` (Rust GUI, core and CLI), `services/` (controller
and workers), `native/` (Android/Wayland integration), `scripts/` (build and
release) and `packaging/` (package staging, systemd, AppImage and NVIDIA assets).
Debian, RPM and Arch packages are the automated release outputs; portable and
AppImage build scripts are also present. Native integration details are in
[native/hwc](native/hwc/README.md) and [native/nvidia](native/nvidia/README.md).

## Credits

Thank you to the maintainers and contributors of the projects AnvilDroid builds
on, integrates with, and learns from:

- [Waydroid](https://github.com/waydroid/waydroid) and
  [Waydroid Android hardware](https://github.com/waydroid/android_hardware_waydroid)
  — the Android container foundation and Wayland hardware composer integration.
- [Android Open Source Project](https://source.android.com/) and
  [LineageOS](https://github.com/LineageOS) — the Android platform and the base
  for official Waydroid images. The packages also include AOSP `aapt`,
  `apksigner` and the matching C++ runtime for image preparation; their notices
  are installed in `/usr/local/lib/anvildroid-controller/tools/NOTICE.txt`.
- [quinovax/waydroid-nvidia](https://github.com/quinovax/waydroid-nvidia)
  — the upstream NVIDIA/Venus integration and prebuilt payload used by our
  experimental NVIDIA backend. AnvilDroid's pinned adaptations are maintained
  in [collyn/waydroid-nvidia](https://github.com/collyn/waydroid-nvidia).
- [Mesa](https://gitlab.freedesktop.org/mesa/mesa),
  [virglrenderer](https://gitlab.freedesktop.org/virgl/virglrenderer),
  [ANGLE](https://github.com/google/angle) and
  [astc-encoder](https://github.com/ARM-software/astc-encoder)
  — graphics components used by the NVIDIA payload, including Venus rendering
  and ASTC texture decoding.
- [scrcpy](https://github.com/Genymobile/scrcpy)
  — the Android control server and protocol used by the touch/key mapping integration.
- [supremegamers/libndk translation prebuilts](https://github.com/supremegamers/vendor_google_proprietary_ndk_translation-prebuilt)
  and [supremegamers/Houdini prebuilts](https://github.com/supremegamers/vendor_intel_proprietary_houdini)
  — community package sources for the optional ARM translation catalog.
- [Tauri](https://github.com/tauri-apps/tauri)
  — the desktop GUI framework.
- [Noto Fonts](https://github.com/notofonts/noto-fonts)
  — Noto Sans glyphs used in native window menus and key mapping controls.

Third-party code, fonts, images and binary packages retain their respective
licenses and notices. See the [NVIDIA notices](packaging/nvidia/NOTICE),
[NVIDIA component licenses](packaging/nvidia/LICENSE.upstream),
[ASTC decoder license](packaging/nvidia/LICENSE.astc-encoder) and
[font license](native/menu-font-OFL.txt) for bundled asset attributions.

## License

AnvilDroid source code is licensed under the [MIT License](LICENSE-MIT).
Waydroid, Android/LineageOS images, Google services, ARM translation packages,
native libraries and installed applications remain under their own licenses.

### Optional ARM translation binaries

The MIT license above covers AnvilDroid's own source code. The translation
bridges are proprietary Google/Intel prebuilts obtained from community mirrors:

| Component | Source / origin | License information in the pinned archives |
| --- | --- | --- |
| Google's `libndk_translation` | [supremegamers NDK prebuilts](https://github.com/supremegamers/vendor_google_proprietary_ndk_translation-prebuilt); ChromeOS Zork/Guybrush | All three checked revisions include [ARM_TO_AMD_DBT_LICENSE.txt](packaging/arm/ARM_TO_AMD_DBT_LICENSE.txt). This is a limited grant concerning ARM ISA copyrights. |
| Intel's `libhoudini` | [supremegamers Houdini prebuilts](https://github.com/supremegamers/vendor_intel_proprietary_houdini); ChromeOS, Microsoft WSA and Google Play Games for PC | No standalone license notice or redistribution grant was found in the seven checked revisions. Their `Android.mk` files mark the translator modules as proprietary. |

The NDK notice explicitly names `system/lib/libndk_translation.so` and
`system/lib64/libndk_translation.so`. It covers only ARM's copyright rights in
the ARM-v8 and ARM-v9 ISAs, requires retaining/reproducing its notices and
disclaimer, and excludes other intellectual property rights, including patents.
Its clause 3 permits redistribution only in conjunction with the sale of a
semiconductor product containing an AMD-64 ISA compliant CPU core licensed or
sold by AMD, and limits use/execution of that redistributed software to that
core. Read the [complete notice](packaging/arm/ARM_TO_AMD_DBT_LICENSE.txt);
it does not establish a complete license for all rights in the prebuilt package.
The [upstream copy](https://github.com/supremegamers/vendor_google_proprietary_ndk_translation-prebuilt/blob/68734c52556d3d7a6db34c603dd9276915c29f2f/ARM_TO_AMD_DBT_LICENSE.txt)
is pinned to the default catalog revision.

For Houdini, permission to redistribute the catalog binaries has not been
established from those archives. Public availability on GitHub and successful
SHA-256 verification do not themselves grant distribution or modification
rights. Applicable rightsholder terms must be checked before redistributing
translator binaries, modified variants or images containing them. Compatibility
patches do not replace the original licensing terms.

AnvilDroid's package builders include the catalog/downloader code; the ARM
translation archives are fetched separately during runtime preparation or
version installation. The checked revisions, versions and patch variants are
listed in [ARM translation documentation](docs/ARM-TRANSLATION.md), with exact
URLs and checksums in [the catalog](services/runtime-arm-source.py).

See [Waydroid documentation](https://docs.waydro.id/) for Android runtime setup.

## ADB

Start a runtime, open the **ADB** tab, and click **Connect**. Approve USB
debugging in Android if the connection shows **Unauthorized**. Android Studio
and other tools using the same ADB server can then select the runtime.

Each runtime has its own localhost endpoint. **Copy** copies its `adb connect`
command; use `adb -s 127.0.0.1:PORT shell` to target one runtime. The Android SDK
section accepts an absolute `platform-tools/adb` path if automatic detection
finds a different SDK. **Disconnect** closes the endpoint and turns off TCP ADB.
Connections are session-scoped and keep the runtime awake while enabled.
