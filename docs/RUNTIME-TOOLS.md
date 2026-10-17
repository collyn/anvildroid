# Runtime settings and diagnostic export

Open a runtime's Settings page. Features extend the existing groups: ARM
translation, Google Play, Storage and Diagnostics. There is no separate
Components view or duplicate configuration path.

## ARM translation and Google Play

ARM translation shows the saved provider/version, selected archive checksum,
host architecture, Android requirements, cache status and provider conflict.
A cached archive is not an installed translator; saved configuration is not proof
that an ARM app successfully loaded it. Unknown existing Default provider versions
stay unknown. Downloads are verified again on installation.

Use the existing engine/version selectors and install button for a managed
runtime. Applying to a running or frozen runtime uses the existing confirmation,
stop, apply and restart workflow; a stopped runtime activates changes next start.
Import another version opens the existing ARM package catalog in GUI Settings.
Default uses its installed libndk provider and can restore only a configuration
previously saved when AnvilDroid disabled that provider. It does not install a
selected archive through this view.

Google Play keeps its existing package checks, registration help and verified
GAPPS image-creation workflow. Create a separate runtime for a different image;
this does not switch the image beneath existing app data. The backend component
catalog remains available for diagnostics, including truthful unavailable
extension status; it does not expose arbitrary privileged installer scripts.

Shared folders appear inside Storage; see [folder sharing](SHARED-FOLDERS.md).
They load only when Storage is expanded. Drafts and pending actions survive
same-runtime refreshes; changing runtime/state invalidates stale responses.

## Diagnostics

Expand Diagnostics to run the existing health checks or use Export report.
Opening this group does not collect a report. Collection starts only after
choosing Save report and a new destination file. Switching runtime or leaving
the group invalidates pending picker/results; an export already started may
finish writing its captured runtime's report.

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
