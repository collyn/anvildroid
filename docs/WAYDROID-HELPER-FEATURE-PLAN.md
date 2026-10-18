# Waydroid Helper feature implementation plan

Last updated: 2026-10-18. Branch: `dev/waydroid-helper-features`.

The user requested implementation of all five recommendations, parallel agent
work, thorough testing, and commits on a development branch. Existing installed
Android runtimes and desktop sessions are not deployment targets for this work.
Upstream reference: waydroid-helper commit
`17f5f326de9facdbb3d9f6b02354a4dcb0f628fd`; use design observations and original
implementation, preserving AnvilDroid's MIT source licensing.

## Current completion status

All five feature areas are implemented in the existing settings flows. Actual
Android input passes on managed X11 and Wayland; the full disposable stock
Default sharing and diagnostic lifecycle now passes as well. The final Default
results and current package hashes are recorded at the end of this document.

Remaining acceptance requires the user's Arch host, representative apps/games,
physical desktop/editor interactions, Default ARM provider restore, and online
Google account/Integrity checks. These gates remain unchecked below. AppImage
is deferred; this branch is not a merge or published release.

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
- [x] Verify these lifecycle rules through Android app/media/FUSE access in a
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
- [x] Exercise the same ten scenarios through the production Wayland backend,
  with an input-capable private Weston and actual Android MotionEvents. Retain
  failed attempts and distinguish ordinary app activation from mapped fingers.
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

- [x] Export reports from stock Default in a private software/headless fixture
  and managed X11/Wayland, including unavailable sections and opt-in logs.
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
- [x] Confirm installation/start on the user's Arch machine. The CachyOS
  follow-up below records actual package installation, boot, Stop/restart,
  GPU verification and Android Settings activity checks on that host.
- [x] Repeat live controls, shares, compatibility, ARM/GAPPS and diagnostic
  acceptance with pinned package assets and recorded module parity. Latest X11
  controls and Wayland use the final Debian build. Final Debian/Arch extracted
  CLI/controller/GUI smoke and portable manifest/source/native parity pass.
- [x] Update support limitations and instructions with observed results. Default
  support requires verified system integration; stock Android 13 app/FUSE
  acceptance now passes, while other images and scoped-storage apps remain open.
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
reported Arch failure was still unconfirmed at that point. The CachyOS
follow-up below identifies the actual mount API limit and verifies the fix
on the failing machine. No upper layer is deleted or reset as a fallback.

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
| Actual managed Android shared folders | Packaged managed RO, RW and removal/restart pass through raw data, Android FUSE and an SDK28 app with granted storage permissions. RO writes fail with EROFS; all three RW marker writes reach the chosen host source. Revoked access stays unavailable after restart and the source remains unchanged. All three stops verify Stopped, cleanup complete, quiescence and absent scope. Per-cycle package hashes are retained; all 34 runtime Python modules match the staged package. See [sharing support](SHARED-FOLDERS.md). |
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
  failure. Use the latest Arch package at the end of this document; no test here establishes that machine's
  kernel/backing-filesystem result.
- Default ARM provider disable/restore in a separate isolated installation.
- Representative FPS/MOBA/ARM gameplay, including simultaneous windows, capture
  feel, graphical editor lifecycle and recovery beyond the diagnostic scenarios.
- Other Android images, modern scoped-storage apps and representative Wi-Fi-only
  apps; online Google sign-in/registration/Play Integrity and graphical ARM flow.
- Human review of diagnostic redaction before sharing reports with real logs.

These unchecked gates are not recorded as passes. No release or merge is made.

### Completion regression follow-up — 2026-10-17

The full Rust workspace now passes **80 tests** with no filtered test. Its one
normally ignored GTK/WebKit native-panel lifecycle test also passes separately
under private Xvfb/D-Bus, for **81 exercised tests**. This supersedes the earlier
inotify-limited run; the personal Default runtime is not restarted for testing.

Diagnostic hardening adds maintained cases for escaped and unfinished quoted
credentials, authorization/cookie headers, private-key blocks and truncated
tails, identifying paths/keys, common serialized escapes and terminal controls.
Sensitive dictionary keys are classified before their identifying paths are
masked. Lone serialized surrogates remain literal text so UTF-8/JSON export
stays valid. Bounded log tails discard an incomplete leading line that could
otherwise expose a credential suffix. Operational state, generation, backend,
Binder and GPU device details remain available. **23 diagnostic tests** pass;
redaction remains best effort and exported real logs still need human review.
The maintained feature runner also passes, including all 19 frontend files,
five native suites and complete staged-module imports.

Live Wayland input and disposable stock Default acceptance continue separately;
preflight success is not an Android app acceptance result.

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
app/FUSE RO/RW/removal and diagnostic export were still pending at this stage;
the stock acceptance follow-up below completes them. The user's Arch machine
remains a separate gate. Window recovery alone does not establish those passes.

Local recovery and regression records are retained under
`target/feature-verification-20261017/default-window-*` and
`default-full-ui-token-tests.log`. AppImage remains deferred.


Earlier development artifacts after Default recovery (package-manager lifecycle
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

### Wayland focus and input acceptance — 2026-10-17

The input-capable private compositor exposed a real defect: Weston can transfer
keyboard focus between the root and a content subsurface of the same app just
before a click. Treating that pair as a full blur canceled held aim and let a
legacy mouse click cancel the injected finger gesture. Keyboard leave/enter is
now resolved within the current dispatch batch for the same app owner on
different raw surfaces. True blur, same-surface refocus, app replacement,
pointer leave and connection failure still cancel; held keys require release
before a fresh activation. Input arriving before a pending leave is resolved
is consumed safely. No focus grace timer is used.

All **ten maintained Android input scenarios pass through actual Wayland** on
Debian package `29700b0b…`, including aim/fire, multiple fingers and blur/repeat
suppression. Private Xvfb supplies XTest input to Weston's X11 output; the guest
and production bridge use Wayland. Refocusing that output does not itself
activate a Wayland app, so the final scenario uses an explicit app click. Its
one ordinary mouse down/up pair is verified separately; the mapped-finger
assertions still require zero touches during held-key suppression and exactly
two fresh finger gestures. Earlier failing runs and protocol traces are retained
under `target/feature-verification-20261017/live-controls-wayland-*`.

Native X11 was repeated on the same final `29700b0b…` package and also passes
all ten scenarios. The final summaries are `live-controls-wayland-verified` and
`live-controls-x11-focus-verified` in that verification directory. Both fixtures
reached authenticated Stopped, complete cleanup and quiescence, empty scopes,
and no remaining owned worker/display processes or host mounts before their
disposable roots were removed. Live editor, resize/app replacement, representative
gameplay, GNOME/KDE and physical-input acceptance remain separate gates.

The maintained feature runner passes **169 Python tests**, all 19 frontend
files, dedicated frontend cases, **seven native suites** and staged-module
imports. It now retains the hints and focus-transition regressions. The full
local native suite and focused ASan/UBSan focus tests also pass. Independent
review checked same-owner transitions, actual blur and queue/mutex ordering.
Sixteen inert acceptance-harness checks also pass and are now included in the
maintained runner. The real core diagnostic exporter is retained as the
`acceptance-diagnostics` Cargo example; its build, formatting and Clippy checks
pass. This replaces a dated, generated exporter prerequisite in the Default
harness.

Input-tested artifacts before the Default teardown follow-up (AppImage deferred):

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `target/releases/anvildroid-controller_0.1.3_amd64.deb` | 26,248,678 | `29700b0b769308e924aa2b4eac269b36df275f2476f5b1e11fa6ca8d633ef36a` |
| `target/releases/anvildroid-controller-0.1.3-1-x86_64.pkg.tar.zst` | 26,063,061 | `1a8b147adf2e445068ba7d375f6f95b865c46282019607f2325e354fc7a835a5` |
| `target/releases/waydroid-features-dev-20261017-wayland-focus/` | directory | Per-file `SHA256SUMS` verified |

Both packages pass payload/dependency/CLI/image-tool checks, private controller
and GUI smoke, and exact Python, binary and native-library parity. Portable
manifest/source/native/binary parity passes. These artifacts have not been
installed over the personal controller or Default service; Arch host startup
remains open. The Default app-level gates were completed by the follow-up below.

### Default teardown and full stock acceptance — 2026-10-18

Actual stock Stop exposed two native lifetime issues: GLib's pool spawner
inherited the session namespace, and Binder references needed normal GLib
dispatch to finish cleanup. An empty shared pool now initializes in the private
service baseline before any session. After stock Stop, the adapter joins its
hardware helper, positively checks Android STOPPED, and collects unreachable
Python Binder wrappers. The D-Bus Stop reply waits through bounded GLib timer
callbacks, allowing deferred native cleanup without a nested event loop.

Every attempt retains owner-thread, Android-state and exact namespace-holder
checks. Concurrent Start/Stop and stale hardware callbacks cannot restart or
modify a session during teardown. Success is sent only after namespace return,
session-marker removal and configuration-lock release. Timeout retains the
lock and permits a later Stop retry; no holder is exempted or forcibly killed.

The normal maintained launcher, SHA256
`b34cf225026d0e0d9bb7e985cb81c8c244876fd1b486695bce62886e8f91b0b0`,
completed the full lifecycle with the final Debian package below and fresh
Android 13 x86_64 Vanilla userdata:

- Stock boot, production desktop-owner claim and complete Stop.
- RO and RW readback through raw media, Android FUSE and an actual SDK28 app
  UID. RO writes fail with EROFS; all three RW marker writes reach the source.
- A real retained namespace helper makes Stop and configuration changes fail;
  releasing it permits a successful retry with baseline namespace and lock free.
- Removal/restart leaves no configured grant or raw/backend/FUSE share mount.
  Only empty nonsymlink userdata directories remain. The app probes using the
  original source token: read fails with ENOENT and write with EACCES; exact
  source bytes are unchanged.
- Four real core diagnostic exports in Running/Stopped, logs off/on, verified
  session, desktop-owned mode 0600 and overwrite refusal. Independent inspection
  verifies sections, bounds and redaction. Default log sources report unavailable
  rather than including unverified or previous-session text.
- Final stock Stop, empty delegated scope, authenticated copied-loop detach,
  unchanged host IPv6 and host namespace/device/sysctl/service baseline.

The earlier removal oracle required the whole target directory to disappear.
Production correctly leaves an empty persistent mountpoint. The corrected
oracle verifies source revocation directly and rejects retained mounts, content,
symlinks and unreadable directories. The initial failed run is retained; its
RO/RW results were not substituted for the final complete run.

The final feature runner passes **214 Python tests**, **313 frontend cases in
all 19 files**, **42 dedicated frontend cases**, **seven native suites**, and
complete module staging/import. Nineteen asynchronous Stop tests include real
GLib scheduling/dispatch ownership, actual flock/marker retention, callback
generation guards, concurrent requests, timeouts, recovery and inherited stock
D-Bus Start authorization. The native pool's actual isolated three-session
namespace test also passes. Rust and Android/native input code are unchanged;
exact package comparison against input-tested `29700b0b…` finds only the two
Default Python service modules changed.

Artifacts from this stock Default acceptance (superseded by the CachyOS
delivery below); AppImage remains deferred:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `target/releases/anvildroid-controller_0.1.3_amd64.deb` | 26,250,762 | `f75c765369015cc0d35289ba3e4a84c52e9e9f1ffb9505323cc3d37930359caa` |
| `target/releases/anvildroid-controller-0.1.3-1-x86_64.pkg.tar.zst` | 26,064,303 | `124da722f496e4f4ff90b7c5a584c84036cc68d5bcfafc7850b8f097985d934d` |
| `target/releases/waydroid-features-dev-20261018-default-async/` | directory | Per-file `SHA256SUMS` verified |

Debian and Arch payload/dependency/CLI/image-tool checks, private packaged
controller and UID1000 Xvfb/D-Bus GUI smoke, and Python/native/binary parity all
pass. Portable manifest and source/native/binary parity pass. The exact accepted
Debian copy is retained as `completion-default-async.deb` in the ignored
verification directory. No package was installed over personal services.

Small evidence and original report ownership/mode/hash metadata remain under
`target/feature-verification-20261017/default-shares/final-pass-f75c765/`.
Both final disposable roots were removed only after archive verification,
empty-scope/loop/mount checks and exclusive launch locks. Arch host acceptance,
physical desktop/editor/gameplay, Default ARM restore and online Google checks
remain manual as listed above. No merge or release was performed.

### 2026-10-18: CachyOS startup acceptance

The reported managed-runtime failure was reproduced directly on the user's
CachyOS host: Linux `7.2.9-1-cachyos`, util-linux/libmount `2.42.4`, Btrfs with
`compress=zstd:1`, Intel Iris Xe, and a real Wayland desktop. A simple disposable
Btrfs overlay passed, while the runtime's exact 311-byte `lowerdir` failed at
`fsconfig(FSCONFIG_SET_STRING)` with `EINVAL`. The same layers, upper/work and
feature policy mounted successfully through `mount(2)`. The new mount API's
individual string-value limit was the cause; image corruption and Btrfs upper
support were not implicated by these probes.

Android system/vendor overlay commands now set `LIBMOUNT_FORCE_MOUNT2=always`
only in their child environment. Existing xino/index/metacopy/redirect policy
is retained, with no global environment/kernel changes, reduced-protection
retry, upper reset or image reinstall. A maintained real-mount regression
exercises a three-layer lowerdir longer than 255 bytes, ARM precedence,
copy-up, whiteouts and persistence after remount. It passes on both this
CachyOS kernel and the Ubuntu build host; legacy metacopy migration also passes.

Once overlay mounting worked, startup exposed a second host difference:
CachyOS prepopulates each fresh BinderFS mount with `binder`, `vndbinder` and
`hwbinder`. Unconditional creation returned `EEXIST`. The worker now accepts
that result only for root-owned character nodes on the same BinderFS device
and character-device major as its control node. Other errors, symlinks, regular
files, foreign owners/filesystems and unrelated device majors are rejected.
Two disposable BinderFS mounts on CachyOS passed repeated initialization and
had disjoint Binder device identities, confirming that the worker does not
reuse the host's Binder nodes.

The existing Android 13 GAPPS runtime with libndk translation then booted
successfully. Health confirmed boot completion, private networking and a
non-software Iris Xe renderer. Fourteen launcher apps were listed; Android
Settings launch succeeded and `dumpsys activity` showed its resumed activity.
Normal Stop completed successfully. The rebuilt Arch package was then
reinstalled with `pacman -U`, all 118 installed payload files matched the exact
archive, the controller restarted successfully, and a fresh Start reached
`Running` with a successful lifecycle job. Runtime images and userdata were
preserved; the runtime is left running for the user's testing.

Validation: **28 worker tests plus 214 feature/acceptance Python tests**,
**313 frontend cases**, **42 dedicated frontend cases**, **seven native
suites**, and complete module staging/import pass. Worker Xvfb/Weston and
package GUI checks ran in private mount/network/PID namespaces with host
`/run`, `/tmp`, D-Bus/display sockets and both runtime stores hidden. Both final
package payloads, source/native/binary parity, CLI/image tools, private
controller IPC and UID1000 GUI first-frame checks pass.

The final artifacts are retained in
`target/releases/cachyos-overlay-fix-20261018/` with `SHA256SUMS`:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `anvildroid-controller_0.1.3_amd64.deb` | 26,250,890 | `7ce769516c5c763d531cea74f13f75f7758ebf21f49832ab4b95fde51a7939a3` |
| `anvildroid-controller-0.1.3-1-x86_64.pkg.tar.zst` | 26,069,507 | `5db4af5d5f7297d8e7cbc5cee1221e3034a0eed52a1e48a9036d764b7edcccc1` |

The Arch package was installed on the failing host. The Debian package was
rebuilt and verified; this follow-up did not reinstall it over the build host's
personal services. Evidence is under
`target/cachyos-overlay-fix-20261018-133705/`. This establishes this host's
Wayland startup and app launch, while other Arch kernels, Arch X11, gameplay,
physical input/editor feel, Default ARM restore and online Google checks retain
their separate acceptance scope. AppImage remains deferred.
