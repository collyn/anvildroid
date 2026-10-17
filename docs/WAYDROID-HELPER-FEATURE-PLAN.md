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
2. Runtime Settings Shared folders dialog, explicit per-runtime grants, enforced
   RO/RW mounts before boot in the worker namespace, status and stopped removal.
   Only managed runtimes own the required mount lifecycle.
3. V2 native profiles/editor with V1 reads, WASD, swipe, repeat, skill, finite
   two-point macros and bounded absolute aim/fire. Relative unlimited FPS mouse
   capture and an arbitrary macro scripting language are outside this delivery.
4. Components dialog over verified ARM and GAPPS workflows, versions/requirements/
   conflicts/digests and separate cache/configuration/readback states. MicroG,
   SmartDock and Magisk remain explicitly unavailable without verified installers.
5. Explicit private JSON export, partial diagnostic sections, optional bounded
   controller logs, best-effort redaction and desktop-user file creation.

Owner authorization precedes paths, requests have exact typed fields, and managed
compatibility writes are generation-bound. Default feature mutations share the
legacy GUI operation guard. All five service modules are in installed packages
and portable/AppImage staging; release preparation runs the focused feature suite
and an isolated privileged share mount check.

### Automated verification (2026-10-17)

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
