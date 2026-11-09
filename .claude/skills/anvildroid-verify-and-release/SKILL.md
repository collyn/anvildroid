---
name: anvildroid-verify-and-release
description: Use before claiming AnvilDroid work is done, before committing, when bumping the version, tagging a release, or editing .github/workflows/release.yml, packaging/ or systemd units; also when cargo fails with --locked / stale Cargo.lock or a release tag job fails.
---

# AnvilDroid verification and release

## Overview
There is **no per-PR CI**. The only workflow, `.github/workflows/release.yml`, runs on `v*` tags (and `workflow_dispatch`). Anything you break is found only at release time — so run the matching checks locally before saying "done".

## Checks by area changed

| Changed | Run |
|---|---|
| any Rust | `cargo clippy --workspace --all-targets --locked -- -D warnings` and `cargo test --workspace --locked` (warnings fail CI). Core/CLI only: `cargo test --locked -p anvildroid-core -p anvildroid-cli` |
| `services/` | `python3 services/<x>-test.py`, then `sh scripts/test-runtime-features.sh` |
| frontend | `node --test tests/*.cjs` + `crates/anvildroid-gui/*-check.cjs` |
| native | see anvildroid-native-android |
| added/renamed files | `python3 .claude/skills/anvildroid-shipping-new-files/check-registration.py` |

Always pass `--locked`. If a check cannot run here (missing Android inputs, GTK libs, sudo, sandboxed sockets), say exactly which one and why. Do not report it as passing.

## Version bump
- Edit only `version` in root `Cargo.toml` `[workspace.package]`, then `cargo check --workspace` (or `cargo update -w`) to refresh `Cargo.lock`. Commit both. Crates use `version.workspace = true`. Never hand-edit `Cargo.lock`. Don't add a version to `tauri.conf.json`.
- Format: `X.Y.Z` or `X.Y.Z-(alpha|beta|rc).N` (`scripts/release-version.py` rejects others). Prereleases skip repo publishing.
- Commit style: `bump: version X.Y.Z`.

## Release (only when the user asks — tag push publishes to users)
```bash
release_version=$(python3 scripts/release-version.py)
git tag -a "v${release_version}" -m "Release ${release_version}"
git push origin main
git push origin "v${release_version}"
```
Tag must equal `v<Cargo version>`, otherwise the `prepare` job fails.

## Packaging invariants
- Controller installs to `/usr/local/lib/anvildroid-controller/`, state in `/var/lib/anvildroid-controller` (0700).
- Install dirs must end up 0755 regardless of umask: `mkdir` then explicit `os.chmod`. Destination root-owned, not a symlink, not group/world-writable.
- systemd: enable `anvildroid-controller.socket`, not the service (no `[Install]`). A controller started outside systemd must not block the socket unit, and it unlinks only a socket it bound itself (`services/controller-takeover-test.py`).
- Pinned inputs (NVIDIA artifacts, scrcpy, linuxdeploy, R8) carry sha256 pins. Change the file and the pin together, never drop verification.
- Verification scripts must set `ANVILDROID_SKIP_BOOTSTRAP=1` so they never run pkexec on the build host.
- Generated, don't hand-edit: `Cargo.lock`, `native/bridge/menu-font-data.h` (`scripts/build-menu-font.py`), `crates/anvildroid-gui/icons` (`scripts/build-app-icons.py`), `crates/anvildroid-gui/gen/`.

## Docs drift
`docs/TECHNICAL.md` is the public guide. If you change build steps, requirements or architecture, update it. Known stale references: `scripts/test-native.sh` (missing), `tests/hwc-display.cpp` (missing).
