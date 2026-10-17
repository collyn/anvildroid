# Waydroid Helper feature implementation plan

Date: 2026-10-17. Branch: `dev/waydroid-helper-features`.

The user requested implementation of all five recommendations, parallel agent
work, thorough testing, and commits on a development branch. Existing installed
Android runtimes and desktop sessions are not deployment targets for this work.
Upstream reference: waydroid-helper commit
`17f5f326de9facdbb3d9f6b02354a4dcb0f628fd`; use design observations and original
implementation, preserving AnvilDroid's MIT source licensing.

## Work allocation

| Owner | Feature | Deliverables |
| --- | --- | --- |
| Compatibility agent | Per-app compatibility | Fake Wi-Fi and mouse-as-touch, installed-package validation, reset, readback and rollback, app dialog, tests |
| Shared folders agent | Runtime folder sharing | Explicit folder selection, per-runtime configuration, read-only/read-write, safe mount lifecycle, status, tests |
| Game controls agent | Advanced mapping | Extend original native mapper/editor, versioned profiles, control types, lifecycle cancellation, deterministic tests |
| Primary agent | Components | Runtime component catalog and installed/available/incompatible state, reuse verified ARM lifecycle and image workflow, tests |
| Primary agent | Diagnostics | User-initiated bounded export, runtime/host/renderer health, log collection, redaction, safe destination handling, tests |
| Primary agent + agents | Integration/review | Controller authorization, typed Rust IPC, frontend integration, packaging, regression checks and commits |

Agents own separate implementation modules. The primary agent owns shared
controller/worker/Rust/frontend entrypoints to prevent concurrent edits. Every
agent supplies its exact integration contract and focused test results. No agent
commits independently.

All three agents used `gpt-6.1-sol`, explicitly selected by the user. The
compatibility agent also reviewed typed IPC, Default's legacy mutation guard and
the sharing/component/diagnostic dialogs. The shared-folder agent integrated
packaging and CI; the controls agent verified the native build and profile UI.

## Delivery sequence

1. Record baseline test results and inspect controller/worker/input boundaries.
2. Agree request/response contracts and feature availability before UI wiring.
3. Implement the five areas independently with failure-path tests.
4. Integrate owner authorization, generation checks, typed requests, GUI and
   package staging. No raw shell commands or caller-selected service sockets.
5. Run focused suites, protocol/integration tests, Rust workspace checks and
   tests, frontend tests, native regressions, sanitizers where applicable.
6. Review diffs for cross-runtime isolation and privilege-boundary regressions.
7. Record exact results, skips and remaining live acceptance gates; commit and
   push the development branch. Do not merge or publish a release.

## Acceptance criteria

### 1. App compatibility

- Settings are scoped to one runtime and one installed package.
- Preserve other packages' values and existing global wildcard behavior.
- Apply only the supported properties, validate/read back results, restore the
  previous value on partial failure, and state when app restart is required.
- Exercise invalid packages, unsupported state, stale responses, failure at
  every write, and two runtimes using the same package.

### 2. Shared folders

- The caller explicitly chooses a source; never grant implicit home access.
- Validate source ownership/access, symlinks, mount targets, nested mounts and
  reserved paths. Apply mounts only inside the intended runtime namespace.
- Read-only mode must be enforced by the mount, not just represented in JSON.
- Persist configuration atomically. Report configured versus mounted state,
  failures and activation requirements. Removal must revoke access safely.
- Exercise path substitution, traversal, cross-user/cross-runtime access,
  interrupted application, mount cleanup, read-only writes and missing sources.

### 3. Game controls

- Keep existing profile compatibility and app/runtime/task scoping.
- Implement D-pad/WASD, swipe, aim/fire, repeated click, skill casting and
  bounded macros in dependency order; only expose controls with a working sink.
- Separate editing, marker visibility and gameplay activation.
- Cancel active contacts/timers/pointer ownership on blur, resize, disconnect,
  editor entry, task replacement and runtime teardown; never replay held keys.
- Test timing, diagonal movement, multiple simultaneous contacts, mouse capture,
  malformed profiles/macros, queue bounds and cancellation/failure paths.
- Native tests do not substitute for real FPS/MOBA/ARM gameplay acceptance.

### 4. Components

- Present version, architecture/Android requirements, conflicts, verification
  and actual runtime installation state; distinguish cached from installed.
- Reuse the existing verified ARM installation and GAPPS image-creation paths.
- Keep incompatible/unavailable components truthful and actionable. Adding a
  catalog item must not enable arbitrary root installer scripts.
- Preserve the current runtime on failed changes, validate package metadata,
  and test stale image/catalog state and cross-runtime independence.

### 5. Diagnostic export

- Export occurs only on explicit user action to a user-chosen file.
- Include schema/version, host summary, selected-runtime state, image/SDK,
  configured versus observed GPU/backend, health and bounded relevant logs.
- Partial collection remains useful and records unavailable sections.
- Redact credentials, identifying paths/network data and sensitive log values;
  do not collect app-private files or upload reports automatically.
- Test huge logs, invalid encoding, symlink/special files, changing files,
  missing services, redaction and destination write failures.

## Verification record

### Implemented scope

1. App context-menu Compatibility dialog, exact package checks, Fake Wi-Fi and
   mouse-as-touch property transactions with readback, revision and rollback.
2. Shared folders embedded in Runtime Settings → Storage, explicit per-runtime grants, enforced
   RO/RW mounts before boot in the worker namespace, status and stopped removal.
   Only managed runtimes own the required mount lifecycle.
3. V2 native profiles/editor with V1 reads, WASD, swipe, repeat, skill, finite
   two-point macros and bounded absolute aim/fire. Relative unlimited FPS mouse
   capture and an arbitrary macro scripting language are outside this delivery.
4. Existing ARM translation settings enriched with versions/requirements/
   conflicts/digests and separate cache/configuration states. Existing Google Play
   retains the GAPPS workflow; diagnostic catalogs keep unavailable extensions truthful.
5. Export report embedded in Diagnostics, partial diagnostic sections, optional bounded
   controller logs, best-effort redaction and desktop-user file creation.

Owner authorization precedes paths, requests have exact typed fields, and managed
compatibility writes are generation-bound. Default feature mutations share the
legacy GUI operation guard. All five service modules are in installed packages
and portable/AppImage staging; release preparation runs the focused feature suite
and an isolated privileged share mount check.

### Initial implementation verification (2026-10-17)

| Command/check | Result |
| --- | --- |
| `sh scripts/test-runtime-features.sh` | 87 Python tests, 60 frontend tests and four native suites pass; complete installer module staging/import passes |
| `sudo -n python3 services/shares-test.py --mount-test` | Pass: actual RO write denial, RW writeback, rollback, cleanup, replaced sources, nested mounts and guest target symlinks in a disposable PID1 mount namespace |
| `cargo test --workspace --locked` outside sandbox | 80 pass, one existing native-panel lifecycle test ignored (requires a live display/session bus) |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Pass |
| `cargo build --workspace --locked` and release build | Pass |
| Local complete `sh scripts/test-native.sh` outside sandbox | 62 native binary invocations and 15 nested Python tests pass; runner remains a local ignored baseline utility |
| Advanced/storage controls with ASan/UBSan | Pass, including post-review macro-UP coordinates and sparse-slot aim/fire regressions |
| `sh scripts/build-native.sh` | Pass: Android shared libraries and signed caption APK; no install |
| `sudo -n python3 -m unittest discover -s tests -p test_runtime_controller.py` | 30 pass, four existing privileged/integration cases skipped |
| `python3 services/runtime-isolation-test.py` | Seven pass, optional mount case skipped |
| Existing worker regressions outside sandbox | 24 pass; one unchanged Binder fixture attempts unmocked `/dev/binderfs` creation and fails without root; same failure reproduced from `main` |
| Full existing frontend regression collection | 16 files pass, three pre-existing files fail: `test_gui_launch.cjs`, `test_lazy_features.cjs`, `test_page_loads.cjs`; no newly failing files |
| Baseline frontend comparison | All 35 failing GUI-launch case names match `main`; lazy/page failures also reproduced on `main` (stale ADB fixture/configuration expectations) |
| Real WebKitGTK rendering in private Xvfb, inert requests | All four new dialogs fit a 900×600 viewport with no horizontal overflow; screenshots inspected; focus/initial scroll corrected |
| Isolated package staging and verifier payload/binaries/image-tools checks | Pass, including all feature modules, native X11/Android payload and notices; no installation |
| Portable/AppImage source staging review | Feature modules included; full AppImage rebuild not run |

Sandbox socket denials initially failed CLI/native/Weston fixtures; affected suites
were rerun outside the sandbox. No source changes were made to hide those failures.
Local detailed logs/screenshots are in `target/feature-verification-20261017/`
(ignored). The focused suite and its required tests are committed for CI;
historical ignored experiments are not imported wholesale.

### Settings consolidation follow-up (2026-10-17)

User feedback: extend existing settings instead of adding duplicate tools.
The Components toolbar/dialog was removed. ARM package details now extend the
existing ARM translation section and installer; Google Play keeps its existing
flow. Shared folders is inline in Storage and Export report is inline in
Diagnostics. Default's ARM section only toggles/restores installed libraries;
its report now explicitly indicates whether saved restore properties exist.

The primary agent handled modules, integration, metadata, documentation and real
WebKit checks. The compatibility agent adapted sharing tests, the controls agent
adapted diagnostic/ARM/integration tests, and the sharing agent independently
reviewed lifecycle behavior. Panels retain stable DOM nodes and pending actions
on same-runtime rerenders, load only while their section is visible, and
invalidate late replies on target/state changes, collapse or navigation.

| Follow-up check | Result |
| --- | --- |
| `sh scripts/test-runtime-features.sh` | 88 Python tests, 70 frontend tests and four native suites pass; complete module staging/import passes |
| Existing `tests/test_runtime_settings.cjs` | Pass; retained stop/apply/restart and failure handling |
| Real WebKitGTK, actual index/main with inert IPC | Seven scenarios pass at 900×700 and 640×700: collapsed/inert groups, inline Storage, drafts/focus/reparent, running-state lock, diagnostic picker/save, navigation cancellation, and Default restore/export; screenshots inspected |
| `cargo test --workspace --locked` outside sandbox | One unrelated shortcut monitor test fails because host inotify quota is exhausted (`No space left on device`); no source changes to bypass it |
| Remaining Rust workspace tests, with that exact test filtered | 79 pass, one existing live native-panel test ignored, one filtered |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Pass |
| Independent lifecycle review and `git diff --check` | Pass |

Detailed follow-up logs and screenshots use the `settings-*` prefix in the local
ignored verification directory. No installed runtime or controller was modified.

## Remaining completion plan

The user confirmed that Default folder sharing and actual X11 validation are
missing. Treat them as completion work for this branch. The implementation
above remains the current state; the checklist below is pending, not verified.
Keep all additions inside the existing Storage, ARM translation, Google Play,
Diagnostics and per-app settings flows.

### 1. Default shared-folder lifecycle — highest priority

Owner: shared-folders agent; primary agent owns shared controller/frontend wiring.

- [ ] Trace the system Waydroid startup, LXC mounts and teardown hooks; design a
  Default adapter that works with normal system-service starts as well as GUI
  starts. Do not bypass the existing private-mount guard to enable the UI.
- [ ] Reuse explicit owner-scoped grants and path/descriptor checks. Serialize
  share edits against start/stop and verify the active Default owner/session.
- [ ] Implement RO/RW mounting, saved/configured/mounted/error reporting, cleanup
  on stop/restart/failure, and recovery after controller/service restart.
- [ ] Extend Storage's existing Shared folders panel to supported managed Default
  installations; show a concrete missing prerequisite when integration is absent.
- [ ] Verify two desktop users, replaced sources, symlinks, missing directories,
  read-only write denial, read/write writeback, revocation and failed boot/cleanup.

Exit: an Android app in a disposable Default installation reads the chosen
folder, ordinary writes obey RO/RW, removal remains revoked after restart, and
no mount affects another runtime or the host session unintentionally.

### 2. Live X11/Wayland acceptance matrix

Owner: game-controls agent; shared-folders agent assists mount investigation.

- [ ] Record Default/managed × X11/Wayland results on each actually supported
  backend/image combination. Record explicit unsupported reasons for others.
- [ ] Check Android file-manager/app access to shares, media/FUSE discovery,
  storage permission prompts, host Unix permissions, stop/restart and removal.
- [ ] Exercise Basic/WASD/swipe/repeat/skill/macro/aim-fire in Android apps on both
  display backends. Verify editor/save/reload, independent marker visibility,
  focus loss, window resize, app replacement, disconnect and restart cancellation.
- [ ] Verify runtime/package isolation with simultaneous windows and runtimes.

Exit: every supported matrix row has reproducible app-level evidence; no stuck
contacts, unintended cross-window input or false mounted status. Advanced input
must be exercised through the actual Android sink, not only a deterministic one.

### 3. Live app compatibility, ARM and Google Play

Owner: compatibility agent; primary agent reviews existing settings integration.

- [ ] Test Fake Wi-Fi and mouse-as-touch with representative Android apps after
  app restart; verify reset and independent settings for the same package in two
  runtimes. Record unsupported image behavior without claiming activation.
- [ ] Install/disable/switch verified ARM providers through the existing settings;
  execute an ARM probe/app and verify failure recovery. Verify Default's existing
  provider disable/restore separately; Default archive installation is not added
  implicitly by this completion plan.
- [ ] Create a separate GAPPS test runtime, check Play Store/services/GSF ID and
  registration help. Keep user confirmation separate from verified readback.

Exit: configuration readback and observed app behavior are recorded separately;
failed changes preserve a usable prior configuration and app data.

### 4. Diagnostics and regression gates

Owner: primary agent; agents provide feature-specific reproduction fixtures.

- [ ] Export reports from real Default and managed test installations on X11 and
  Wayland, including unavailable sections and opt-in current-generation logs.
  Inspect redaction, stale-session handling, permissions and existing-file refusal.
- [ ] Repair the three known stale frontend test fixtures so the full frontend
  run provides a clean regression signal. Fix any real implementation defects
  exposed by those repairs instead of changing expectations to hide them.
- [ ] Run the complete Rust suite on a host with available inotify resources;
  run display/session-dependent checks in the proper test session. Record every
  remaining skip explicitly.
- [ ] Add maintained regressions for Default lifecycle failures and live findings;
  run feature, native, sanitizer, isolation, Rust and Clippy checks as appropriate.

Exit: required automated checks pass, environment-dependent gates have actual
results, and no skipped live check is represented as successful validation.

### 5. Package acceptance and final delivery

Owner: primary agent; shared-folders agent reviews system-hook packaging.

- [ ] Build and verify Debian, portable and AppImage artifacts with all new system
  integration pieces. Exercise clean installation and upgrade in disposable
  supported test environments, including hook availability and runtime ownership.
- [ ] Repeat a short app/share/settings/export smoke test using packaged binaries.
- [ ] Update support limitations, instructions and the matrix with observed
  results; remove Default's unsupported statement only once its acceptance passes.
- [ ] Review changes and commit/push on `dev/waydroid-helper-features`.

Execution order: Default design/adapter and test-environment preparation first;
app/controls acceptance and regression-fixture repair can proceed in parallel;
final packaging follows integrated acceptance. Continue with `gpt-6.1-sol` for
all delegated work. Installed personal runtimes are not test targets: use a
disposable test installation/VM and named test runtimes for lifecycle changes.

MicroG/SmartDock/Magisk installers, arbitrary macro scripting and unlimited
relative FPS mouse capture remain separate feature scope, not completion gates
for these five agreed recommendations. Publishing a release or merging remains
outside this branch's delivery.

### Remaining live acceptance gates

- Wi-Fi-only apps and mouse-as-touch behavior on actual supported Android images.
- Android media/FUSE discovery, app permissions and host Unix permission/ACL
  access for shared folders. No ownership or permission changes are applied.
- Real FPS/MOBA/ARM gameplay, aim feel and lifecycle recovery on Wayland and X11.
- Actual ARM activation and GAPPS package behavior after a deliberately chosen
  test runtime restart; saved configuration is not treated as observed execution.
- Human review of diagnostic redaction before sharing reports with real logs.

No installed controller/runtime was replaced, restarted or modified, and no
release or merge is part of this branch. The comparison used upstream design
ideas; no GPL-licensed implementation source was copied.
