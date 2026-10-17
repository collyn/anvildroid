# Components and diagnostic export

Open a runtime's Settings page, then choose Components or Export diagnostics.
These tools stay scoped to that selected runtime. Opening a diagnostic dialog
does not collect anything; collection starts after choosing Save report and a
new destination file. No installed runtime is restarted automatically.

## Components

The catalog separates downloaded archives, saved runtime configuration and
observed Android package state. A cached archive is not an installed translator;
an enabled saved configuration is not proof that an ARM app successfully loaded
it. ARM rows expose architecture, Android SDK requirements, conflicts, versions
and verification policy. Unknown existing Default provider versions stay unknown.

Stop a managed runtime before enabling a cached ARM version or disabling its
translator. The operation reuses the existing verified ARM installation and
rollback path. A revision check rejects stale catalog decisions. Start it after
saving to activate the configuration. Download links open the existing ARM
catalog in GUI Settings; downloaded packages are reverified on installation.
Default uses its installed libndk provider and can restore only a configuration
previously saved when AnvilDroid disabled that provider. It does not install a
selected archive through this view.

Google Play services reports package detection when available. GAPPS requires
creating a separate runtime from a verified GAPPS image in Images. This view
does not switch the image beneath existing app data. MicroG, SmartDock and Magisk
are listed as unavailable until verified, image-specific installers and readback
exist; catalog entries cannot execute privileged installer scripts.

## Diagnostics

Reports include schema/version/time, host kernel/distribution/architecture,
runtime state, image metadata, resource/display configuration, component state
and the existing health/GPU readbacks. Missing sections are marked unavailable;
they do not prevent the remaining report from being useful.

Logs are unchecked by default. Opt-in collection permits only controller-owned
worker, renderer, console and current-generation X11 logs, at most 64 KiB each.
There is a 128 KiB section limit and a 1 MiB service report limit. Symlink,
hardlink, special-file and unsafe-permission log inputs are rejected. The service
does not receive the chosen export path: the desktop Rust process writes the
report with mode 0600, refuses existing files/symlinks and caps serialized output
at 2 MiB. Choose a fresh filename if the native picker selects an existing file.

Credential fields, recognized credential strings, user paths, network addresses
and email addresses are redacted. Redaction is best effort: inspect reports
before sharing, especially when including logs. App-private files, arbitrary
host paths, environment variables and automatic uploads are not collected.
Default's controller-owned runtime logs are unavailable; its other diagnostic
sections can still be exported.

## Verification

Run `sh scripts/test-runtime-features.sh` for isolated service, frontend,
native-control and packaging/import checks. Run `cargo test --workspace --locked`
and `cargo clippy --workspace --all-targets --locked -- -D warnings` for typed
protocol, export-file handling and GUI integration. These tests use temporary
stores/files and inert Android adapters; they do not certify live image/game
behavior. See [the implementation and verification plan](WAYDROID-HELPER-FEATURE-PLAN.md).
