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
   RO/RW mounts before boot plus verified Android FUSE reconciliation, status
   and stopped removal.
   Default uses its integrated system-service adapter; managed runtimes use the
   worker's private mount lifecycle.
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
completion work for this branch. The checklist distinguishes implementation
and automated verification from the remaining Android app acceptance gates.
Keep all additions inside the existing Storage, ARM translation, Google Play,
Diagnostics and per-app settings flows.

### 1. Default shared-folder lifecycle — highest priority

Owner: shared-folders agent; primary agent owns shared controller/frontend wiring.

- [x] Trace the system Waydroid startup, LXC mounts and teardown hooks; design a
  Default adapter that works with normal system-service starts as well as GUI
  starts. Do not bypass the existing private-mount guard to enable the UI.
- [x] Reuse explicit owner-scoped grants and path/descriptor checks. Serialize
  share edits against start/stop and verify the active Default owner/session.
- [x] Implement RO/RW mounting, saved/configured/mounted/error reporting, cleanup
  on stop/restart/failure, and recovery after controller/service restart.
- [x] Extend Storage's existing Shared folders panel to supported managed Default
  installations; show a concrete missing prerequisite when integration is absent.
- [x] Verify two real numeric desktop UIDs, host ancestor permissions and POSIX
  ACLs, owner-scoped grants, RO denial, RW writeback and namespace revocation
  for managed and Default. Existing fixtures cover replaced sources, symlinks,
  missing directories and failed boot/cleanup.
- [ ] Verify these lifecycle rules through Android app/media/FUSE access in a
  disposable Default installation; host bind-mount fixtures alone do not cover it.

Exit: an Android app in a disposable Default installation reads the chosen
folder, ordinary writes obey RO/RW, removal remains revoked after restart, and
no mount affects another runtime or the host session unintentionally.

### 2. Live X11/Wayland acceptance matrix

Owner: game-controls agent; shared-folders agent assists mount investigation.

- [ ] Record Default/managed × X11/Wayland results on each actually supported
  backend/image combination. Record explicit unsupported reasons for others.
- [x] Check packaged managed Android 13 raw/FUSE/app access, granted SDK28 storage
  permissions, RO/RW, clean stop and removal/restart. Permission/ACL fixtures use
  two real numeric UIDs. Other images and modern scoped-storage apps remain open.
- [ ] Exercise Basic/WASD/swipe/repeat/skill/macro/aim-fire in Android apps on both
  display backends. Verify editor/save/reload, independent marker visibility,
  focus loss, window resize, app replacement, disconnect and restart cancellation.
- [x] Exercise all ten maintained delivery scenarios on actual managed VANILLA
  Android 13 through native X11, the production bridge and scrcpy Android sink;
  verify finger MotionEvents, finite macros, multiple contacts and blur release.
- [x] Observe the same diagnostic package in two simultaneously running guests
  with independent compatibility settings and real mouse input on private X11
  displays. This does not establish simultaneous gameplay/control-map isolation.

Exit: every supported matrix row has reproducible app-level evidence; no stuck
contacts, unintended cross-window input or false mounted status. Advanced input
must be exercised through the actual Android sink, not only a deterministic one.

### 3. Live app compatibility, ARM and Google Play

Owner: compatibility agent; primary agent reviews existing settings integration.

- [x] Observe Fake Wi-Fi and mouse-as-touch through actual diagnostic app APIs
  and MotionEvents, reset, app/Android restart persistence and the same package
  in two simultaneous runtimes. Mouse source changes; tool type remains MOUSE.
- [ ] Test representative Wi-Fi-only apps and games across supported images;
  the diagnostic probe does not establish their compatibility.
- [ ] Install/disable/switch verified ARM providers through the existing settings;
  execute an ARM probe/app and verify failure recovery. Verify Default's existing
  provider disable/restore separately; Default archive installation is not added
  implicitly by this completion plan.
- [x] Exercise production stopped-runtime ARM configuration under its exclusive
  lock: libndk and Houdini execute real ARM32/ARM64 JNI, invalid archive selection
  retains the old working configuration, restart works, and disabling rejects
  fresh ARM-only APK installs. This did not drive the graphical installer route.
- [x] Boot a separate verified GAPPS image, detect Play Store/services/GSF and
  observe the unauthenticated Play Store Activity drawn. GSF ID is uninitialized.
- [ ] Verify online Google account/registration behavior and Default provider
  disable/restore in a separate safely isolated installation.

Exit: configuration readback and observed app behavior are recorded separately;
failed changes preserve a usable prior configuration and app data.

### 4. Diagnostics and regression gates

Owner: primary agent; agents provide feature-specific reproduction fixtures.

- [ ] Export reports from real Default and managed test installations on X11 and
  Wayland, including unavailable sections and opt-in current-generation logs.
  Inspect redaction, stale-session handling, permissions and existing-file refusal.
- [x] Export through the real controller/core API from the managed X11 test guest
  in Starting, Running and Frozen states; verify private files, no overwrite,
  current-generation logs, unavailable fields and live state-change invalidation.
- [x] Export from managed Wayland Running and true Stopped guests, verify log
  opt-in and that stopped reports do not collect previous-session logs. Frozen
  export keeps the runtime asleep and marks live component data unavailable.
- [x] Repair the three known stale frontend test fixtures so the full frontend
  run provides a clean regression signal. Fix any real implementation defects
  exposed by those repairs instead of changing expectations to hide them.
- [x] Run the complete Rust suite with available inotify resources;
  run display/session-dependent checks in the proper test session. Record every
  remaining skip explicitly.
- [x] Add maintained regressions for Default lifecycle failures and findings;
  run feature, native, sanitizer, isolation, Rust and Clippy checks as appropriate.

Exit: required automated checks pass, environment-dependent gates have actual
results, and no skipped live check is represented as successful validation.

### 5. Package acceptance and final delivery

Owner: primary agent; shared-folders agent reviews system-hook packaging.

- [x] Build and verify Debian, Arch and portable artifacts with all new system
  integration pieces. AppImage is deferred by the user's instruction.
- [x] Exercise Debian clean installation, same-version reinstall, fully
  configured 0.1.2 → 0.1.3 upgrade and removal in disposable Ubuntu 24.04 with
  actual systemd PID 1. Verify controller lifecycle and the retained data marker.
- [ ] Confirm installation/start on the user's Arch machine. The user will run
  this acceptance later; no local namespace fixture establishes that result.
- [x] Repeat live controls, shares, compatibility, ARM/GAPPS and diagnostic
  acceptance with pinned package assets and recorded module parity. Latest X11
  controls and Wayland use the final Debian build. Final Debian/Arch extracted
  CLI/controller/GUI smoke and portable manifest/source/native parity pass.
- [x] Update support limitations and instructions with observed results. Default
  support is conditional on verified system integration; app/FUSE acceptance is
  explicitly unverified.
- [x] Review changes and commit/push on `dev/waydroid-helper-features`.

Execution order: Default design/adapter and test-environment preparation first;
app/controls acceptance and regression-fixture repair can proceed in parallel;
final packaging follows integrated acceptance. Continue with `gpt-6.1-sol` for
all delegated work. Installed personal runtimes are not test targets: use a
disposable test installation/VM and named test runtimes for lifecycle changes.

MicroG/SmartDock/Magisk installers, arbitrary macro scripting and unlimited
relative FPS mouse capture remain separate feature scope, not completion gates
for these five agreed recommendations. Publishing a release or merging remains
outside this branch's delivery.

### Completion execution — 2026-10-17

Default now uses integration version 2: a private service baseline, a fresh
per-session namespace, owner/data identity binding, descriptor-pinned mounts,
verified guest mount readback and lifecycle locking through verified teardown.
Hardware-thread remounts and unverified helper cleanup fail closed. Storage
uses the existing inline panel and shows a concrete prerequisite for an old or
missing service integration. See [shared folders](SHARED-FOLDERS.md).

X11 testing found and fixed held/repeated key replay, stale-window forwarding,
missing release events and unmap cancellation. Review also caught a synthetic
XIM keycode-zero commit regression; real Fcitx CJK composition passes after its
fix. Production mapping handlers were initially exercised through the real
private X11 adapter and a synthetic Android task/sink. The later live follow-up
below adds actual Android delivery evidence; neither fixture certifies gameplay
feel.

Diagnostics pins both the desktop session and Android init PID/start time for
live Default, or the authenticated worker generation for managed runtimes.
Changing identity discards collected sections/log text and marks the report
unavailable. Stopped reports do not reuse previous-session logs.

The Arch mount report prompted explicit OverlayFS options for both Android
layers, with required metacopy/redirect features retained for existing upper
layers. A real migration test caught a mount that succeeded but could not read
metadata-only files; the final policy preserves those reads and directory
redirects. Mount errors retain the original cause, target and options. The
reported Arch failure is still unconfirmed: kernel version, backing filesystem
and dmesg from the failing machine are not available. No upper layer is deleted
or reset as a fallback.

| Check | Observed result |
| --- | --- |
| Focused feature runner | 139 Python tests, 42 dedicated frontend cases, all 19 full frontend files and four native suites pass; isolated module staging/import passes |
| Worker regression suite | 25 pass; compositor-dependent workarea probe reports no compositor |
| Default GPU/shader/provision/NVIDIA image regressions | 28 pass |
| Real managed and Default sharing fixtures | RO denial, RW writeback, userdata recursive bind inheritance, revocation, stale source, symlink and lifecycle cases pass in disposable namespaces |
| Real OverlayFS fixture | Explicit defaults, lower priority, copy-up, whiteout, remount and legacy metacopy/redirect migration pass |
| Private X11/Fcitx suites | Full normal and ASan/UBSan runs pass, including XIM commits and the production-mapper fixture; sanitizer leak detection disabled for external libraries |
| Rust workspace | 79 pass in the ordinary run; the inotify test passes separately under UID 65534 because the desktop UID quota is exhausted; the ignored native panel test passes separately under private Xvfb/D-Bus (81 exercised in total) |
| Clippy | All workspace targets pass with warnings denied |
| Actual WebKit Runtime Settings | Eight scenarios pass at 900×700 and 640×700; Default prerequisite, supported sharing and running-state lock included; screenshots inspected |
| Debian/Arch packages | Built with current Python/native/GUI payloads; metadata, source bytes, manifests, hashes, dependencies, CLI and packaged image tools verified |
| Extracted Debian smoke | GUI reaches its frontend frame; private controller serves list/requirements from a temporary store/socket |
| Portable | Bundle includes verified X11 and Default hook; six packaging fixtures pass; manifest checksums verified; NVIDIA renderer payload remains separately provisioned |

Release preparation now runs the complete maintained frontend set, Default
lifecycle tests, isolated managed/Default/OverlayFS mount tests and a private
software-X11 input suite. The previously ignored frontend fixtures are tracked.
AppImage was not rebuilt. These were the pre-live results; the follow-up below
records subsequent package lifecycle and actual Android acceptance. Initial
device discovery was limited by the sandbox view. Host inspection then found
KVM/GPU devices and a loadable Binder module, enabling disposable acceptance
with copied images and fresh userdata. Installed personal runtimes are not
substitutes for these test installations.

No installed controller/runtime was replaced, restarted or modified, and no
release or merge is part of this branch. The comparison used upstream design
ideas; no GPL-licensed implementation source was copied.

### Live acceptance follow-up — 2026-10-17

All Android observations below use copied images, fresh userdata and private
test stores. The installed controller and personal Android sessions are not
test targets. Native-X11 runs use a private Xvfb server and masked user-session
sockets; software rendering is the tested renderer.

| Area | Observed result and practical limit |
| --- | --- |
| Debian lifecycle | Clean install, reinstall, configured 0.1.2 upgrade and removal pass with real systemd; the exact data marker survives. Permission migration and service removal defects found by these tests are fixed. See [Debian acceptance](DEBIAN-ACCEPTANCE.md). |
| Actual Android input | Managed VANILLA Android 13 passes ten XTest → native-X11 → production mapper → scrcpy → APK scenarios: Basic, WASD diagonal, swipe, repeat, skill, bounded macro, aim/fire, simultaneous controls, blur cancellation and held-key suppression after refocus. Final active pointer count is zero. All ten passed again on final Debian `5416f46d…`. This does not certify gameplay feel or Wayland/Default. |
| Actual app compatibility | Fake Wi-Fi changes observed legacy Android network APIs. Mouse-as-touch changes event source to touchscreen but retains tool type MOUSE. Enabled settings and reset persist across separate Android restarts. The same package in two simultaneous guests observes independent settings/input. See [app observations](APP-COMPATIBILITY-ACCEPTANCE.md). |
| ARM and GAPPS | Actual ARM32/ARM64 JNI executes with libndk and Houdini. Invalid archive selection preserves the working configuration; restart works; disable rejects both fresh ARM-only APK installs. A separate GAPPS guest draws the unauthenticated Play Store; GSF ID is null/uninitialized. ARM configuration used production functions under the stopped worker lock, not graphical panel navigation. No online account/Integrity claim. See [ARM/GAPPS acceptance](GAPPS-ARM-ACCEPTANCE.md). |
| Diagnostic export | Real controller/core exports from managed X11 Starting, Running, Frozen, inactive Error and true Stopped states, plus Wayland Running, verify 0600 desktop-owned files, no overwrite, logs opt-in, current-session selection and invalidation on identity change. Frozen collection leaves the runtime asleep and marks live components unavailable. True Stopped reports collect no previous-session log text. |
| Shared-folder identity/ACL fixture | Real numeric UIDs 61001/61002 pass owner/grant separation, ancestor access, named ACL, actual RO denial, RW writeback and revoke for managed and Default adapters. This fixture does not boot Android. |
| Actual Android shared folders | Packaged RO, RW and removal/restart pass through raw data, Android FUSE and an SDK28 app with granted storage permissions. RO writes fail with EROFS; all three RW marker writes reach the chosen host source. Revoked access stays unavailable after restart and the source remains unchanged. All three stops verify Stopped, cleanup complete, quiescence and absent scope. Per-cycle package hashes are retained; all 34 runtime Python modules match the staged package. See [sharing support](SHARED-FOLDERS.md). |
| FUSE reconciliation regression | Vold's nonrecursive backend bind hid preboot shares from apps. A bounded per-generation child now clones only verified grants using pinned mount FDs and preserves RO/nosuid/nodev/noexec. Default reuses the same core with authenticated session state and delayed readiness checks. The privileged fixture proves propagation into an already existing slave namespace and refuses a stale generation. Raw-only binds are not reported as mounted. |
| Headless and Wayland rendering | Canvas reservation is now scoped to desktop runtimes. A separate correction honors strict maximized/fullscreen xdg-shell geometry while retaining Android allocation capacity. Headless boot/clean stop passed on package `aa292460…`; desktop Wayland passed on final `5416f46d…`, including app install/launch, mapped content/CSD, diagnostic export and clean stop without geometry protocol errors. Private Weston exposes no seat/input-injection protocol, so actual Wayland input remains untested. |
| Shutdown harness | Early inactive Error results came from the disposable PID1 holder failing to reap adopted Android zombies. The harness now reaps them; no production shutdown workaround was added. Later X11, sharing, GAPPS, headless and Wayland runs reach true Stopped with verified cleanup. Early Error exits remain recorded as failures. |

### Verification and artifacts before Default window recovery

- Final focused runner: **143 Python tests**, **42 dedicated frontend cases**,
  all **19 full frontend files**, five native suites and complete module staging
  pass. The boot-canvas suite covers property scoping, capacity and constrained
  Wayland geometry. Native normal and ASan/UBSan checks pass; leak detection is
  disabled where sandbox/ptrace prevents it.
- Privileged managed/Default/two-UID/FUSE and OverlayFS fixtures pass in disposable
  namespaces. Final independent FUSE lifecycle/FD/owner review found no blocker.
- Final Debian and Arch extracted packages pass payload/dependency/CLI/image-tool
  checks, private controller list/requirements and GUI frontend-frame smoke.
  All runtime modules, CLI/GUI and native bridge bytes match the current build.
  This is distinct from the earlier full Debian install/upgrade/removal run,
  whose accepted hash is retained in [Debian acceptance](DEBIAN-ACCEPTANCE.md).
- Portable manifest and module/native/binary parity pass. NVIDIA renderer
  payload remains separately provisioned. No RPM or AppImage rebuild is claimed.

| Prior local artifact (before Default window recovery) | Bytes | SHA-256 |
| --- | ---: | --- |
| `target/releases/anvildroid-controller_0.1.3_amd64.deb` | 26,256,838 | `5416f46d5490011920c69f958244abcf02d96408ad608b6c320234ddad01b9c7` |
| `target/releases/anvildroid-controller-0.1.3-1-x86_64.pkg.tar.zst` | 26,072,100 | `2ac908a09932c2e08542c306d0906277a8b3ca94737b6af48c5bb7c8d57faac5` |
| `target/releases/waydroid-features-dev-20261017-final/` | directory | Per-file `SHA256SUMS` verified |

Small generated records remain locally in
`target/feature-verification-20261017/{live-shares,live-controls-package-final,wayland-final-small-records,live-diagnostics-*}`
and `target/acceptance/`. Maintained reproduction helpers and their prerequisites
are in [tests/acceptance](../tests/acceptance/README.md). Disposable Android stores,
copied images, share fixtures and Debian containers were removed only after
verified shutdown/quiescence; original images, caches and personal sessions remain.

### Remaining manual acceptance

- User's Arch installation/start, including the originally reported OverlayFS
  failure. Use the Arch package above; no test here establishes that machine's
  kernel/backing-filesystem result.
- Full stock Default Android service lifecycle: app/FUSE RO/RW/removal, provider
  restore and diagnostic export. Implementation and isolated fixtures pass.
  A full stock launcher needs additional device/cgroup/system-bus isolation;
  see [Default isolation design](../tests/acceptance/DEFAULT-ISOLATION.md).
- Actual Wayland keyboard/mouse delivery and representative FPS/MOBA/ARM gameplay,
  including simultaneous windows, capture feel and lifecycle recovery.
- Other Android images, modern scoped-storage apps and representative Wi-Fi-only
  apps; online Google sign-in/registration/Play Integrity and graphical ARM flow.
- Human review of diagnostic redaction before sharing reports with real logs.

These unchecked gates are not recorded as passes. No release or merge is made.

On this host Android system_server initially exhausted the desktop UID's
inotify watch budget. Live tests temporarily increased only the watch limit
under a bounded guardian that restores the original value; no persistent
sysctl configuration or personal desktop process was changed. The acceptance
harness requires adequate resources and never adjusts host sysctls itself.
At the end of disposable acceptance all test-owned guests were stopped and the
original inotify watch limit **65536** was restored and read back. The subsequent
personal-session recovery below required a new temporary increase. No persistent
sysctl file was changed.


### Default boot and full-window recovery — 2026-10-17

This follow-up diagnoses the user's installed Default session after reports of a
stuck boot and an Android window that would not open. It is separate from the
disposable acceptance runs above.

Two actual failures were identified:

- Android system_server exhausted the desktop UID's 65,536 inotify watches. One
  desktop terminal process held 65,144 watches. Raising only the live
  `fs.inotify.max_user_watches` limit to **131072** allowed Android to boot;
  `sys.boot_completed=1` and `dev.bootcomplete=1` were observed. This recovery
  remains active until reboot; no persistent sysctl file was written.
- The Default service's private mount namespace contained Binderfs while the
  desktop's `/dev/anbox-binder` alias had no visible target. Desktop clients
  repeatedly logged `Can't open /dev/anbox-binder`, although Android's Binder
  services were alive. The same running Binderfs mount was imported into the
  host namespace using a detached mount FD, preserving Android's exact device,
  inode and Binder context. No service/container restart or image/userdata
  change was needed. A fresh `waydroid show-full-ui` completed, an immediate
  property read returned `waydroid.active_apps=Waydroid`, and KDE's window list
  reported a Waydroid window with nonzero geometry, `hidden=false` and
  `minimized=false`.

The durable system helper now prepares host-visible Binder before isolating
mounts, using the installed Waydroid allocation API. It preserves existing
character devices and never stacks a Binderfs mount over an existing context.
Missing-node preparation requires a positive LXC STOPPED result; first
initialization uses stock supported device candidates before names are saved.
Invalid names, unrelated paths/mounts and unknown/running state fail clearly.
A later start refuses missing host devices instead of creating a private-only
context. The actual CLI ran idempotently against the recovered Default session:
all three device identities and Android init PID **58409** remained unchanged.

The GUI's existing Open Waydroid action now checks session readiness and captures
the full-window command's output with a 15-second command timeout. Binder and
IPlatform failures propagate to the GUI even when stock Waydroid exits zero.
Readiness retains its separate 90-second boot deadline. A successful response
confirms the request; window visibility is established separately. Inherited
activation tokens are cleared before accepting a validated fresh token.

Validation for this correction:

- **17 Binder**, **10 Default GPU**, **27 Default sharing** regression tests pass.
  The maintained feature runner's existing Python/frontend/native checks pass.
- A disposable mount test uses actual installed `tools.prep_args`, cold config
  loading, LXC STOPPED lookup and Binder allocation with private `/dev` and
  `/run`. It proves cold preparation and identical device/inode/rdev across
  service namespace isolation. It does not restart a personal runtime.
- Process-isolated Rust launch scenarios pass, including zero-exit Binder and
  IPlatform errors, nonzero stderr, readiness refusal, an actual command timeout,
  and absent/invalid/valid fresh tokens with a stale inherited token present.
  GUI compilation, release build and Clippy with warnings denied pass.

The repaired live session is running the previously installed service/GUI.
The durable changes are included in the rebuilt development packages below;
installation and the next safe service start load them. Full stock Default
app/FUSE RO/RW/removal, diagnostic export and the user's Arch machine remain
manual gates. Window recovery alone does not establish those passes.

Local recovery and regression records are retained under
`target/feature-verification-20261017/default-window-*` and
`default-full-ui-token-tests.log`. AppImage remains deferred.


Current development artifacts after Default recovery (package-manager lifecycle
was not repeated; package scripts are unchanged):

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `target/releases/anvildroid-controller_0.1.3_amd64.deb` | 26,247,478 | `38b2af777c78fd5eabbd870cfba72713b3732515ebe38c66057b2e48c965e8f8` |
| `target/releases/anvildroid-controller-0.1.3-1-x86_64.pkg.tar.zst` | 26,059,177 | `09522f45496038f72c5a9d9f7da29c6f7175a94d7eec5ea166bc8e0674fb2359` |
| `target/releases/waydroid-features-dev-20261017-default-window-final/` | directory | Per-file `SHA256SUMS` verified |

Both extracted packages pass payload/dependency/CLI/image-tool checks and a
private controller list/requirements plus GUI first-frame smoke on an isolated
Xvfb/D-Bus session. All 34 runtime modules and both binaries match current
source/build bytes; the final portable bundle passes manifest and source/binary
parity. These checks do not establish Arch host startup or a full new Default
service lifecycle. No package was installed and no live service was restarted
for this follow-up.
