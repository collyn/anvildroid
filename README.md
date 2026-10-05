# AnvilDroid

[![Stars](https://img.shields.io/github/stars/collyn/anvildroid?style=flat)](https://github.com/collyn/anvildroid/stargazers)
[![Latest release](https://img.shields.io/github/v/release/collyn/anvildroid?display_name=tag)](https://github.com/collyn/anvildroid/releases/latest)
[![Release build](https://github.com/collyn/anvildroid/actions/workflows/release.yml/badge.svg)](https://github.com/collyn/anvildroid/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE-MIT)

Linux GUI and controller for Waydroid. Manage isolated Android runtimes,
existing Waydroid installations, Android apps, custom images, ARM translation,
GPU settings and desktop windows.

> Current version: **0.1.0**

## Features

- Managed runtimes with independent start, stop, storage and resource limits.
- Official or custom Waydroid images from a folder or ZIP.
- App install, launch, force-stop, uninstall, favorites and desktop shortcuts.
- Desktop and headless modes with resize, IME, clipboard and key mapping support.
- ARM translation selection: libndk_translation and libhoudini catalogs.
- GPU diagnostics and per-runtime RAM/CPU/GPU settings.
- Google Play device registration help and GSF Android ID copy.

## Requirements

- Linux x86_64, preferably Wayland.
- Waydroid, LXC, Binder/BinderFS, Linux namespaces and cgroups.
- Mesa-compatible Intel or AMD GPU. NVIDIA/Venus support is not integrated.
- Rust/Cargo, Python 3.11+, GTK/WebKitGTK development packages, Clang/LLD,
  JDK, Android build tools, `patchelf`, `rpmbuild`, `zstd` and Debian packaging
  tools.

Installed packages also need WebKitGTK 4.1, GTK3, GLib, JavaScriptCoreGTK 4.1,
Soup 3, Pango, Cairo and GDK Pixbuf runtime libraries. Debian/RPM/Arch package
metadata declares these dependencies.

## Build packages

Prepare Android SDK API 23 and the pinned R8 jar, then run:

```bash
./scripts/build-deb.sh
./scripts/build-rpm.sh
./scripts/build-pkg.sh
```

Output:

```text
target/releases/anvildroid-controller_0.1.0_amd64.deb
target/releases/anvildroid-controller-0.1.0-1.x86_64.rpm
target/releases/anvildroid-controller-0.1.0-1-x86_64.pkg.tar.zst
```

The version comes from `[workspace.package].version` in `Cargo.toml` and is
shown in the GUI sidebar and CLI help. The `.pkg.tar.zst` file is an Arch Linux
package. Each command builds only its selected package and required shared
payload; no command installs or restarts anything. `scripts/build-runtime.sh`
contains the shared GUI/native/Android build steps.

## Run

Open `anvildroid-gui`, start the controller from **Runtimes**, create or import
a runtime, prepare its images, then start it. Install and launch APKs from the
Apps page while the selected runtime is running.

## Automated releases

Push a tag matching the workspace version. GitHub Actions runs separate Debian,
RPM and Arch package jobs, then a separate release job checks their checksums
and creates a GitHub Release:

```bash
git tag -a v0.1.1 -m "Release 0.1.1"
git push origin main
git push origin v0.1.1
```

Supported versions: `X.Y.Z`, `X.Y.Z-alpha.N`, `X.Y.Z-beta.N` and `X.Y.Z-rc.N`.
The workflow is defined in `.github/workflows/release.yml`.

## Development

```bash
cargo check --workspace --locked
cargo test --workspace --locked
```

Main directories: `crates/` (Rust GUI, core and CLI), `services/` (controller
and workers), `native/` (Android/Wayland integration), `scripts/` (build and
release) and `packaging/` (Debian/AppImage).

## License

AnvilDroid source code is licensed under the [MIT License](LICENSE-MIT).
Waydroid, Android/LineageOS images, Google services, ARM translation packages,
native libraries and installed applications remain under their own licenses.

See [Waydroid documentation](https://docs.waydro.id/) for Android runtime setup.
