# Keymap editor redesign and acceptance

Started 2026-10-20 from `1cafd1f`, branch `fix/compact-page-headers`.
The user approved parallel implementation with `gpt-6.1-sol` after the
[design research](KEYMAP-DESIGN-RESEARCH-2026-10-20.md).

## Implementation

| Owner | Delivered scope |
| --- | --- |
| editor_ux | Tools palette, named inspector, direct binding, Undo/Redo, Clone, Try; separate draggable native editor window |
| input_engine | V4 model/codec, custom direction keys, walk modifier, capture suspend, drag shot, Skill modes/cancel and mouse Walk; focus/input routing |
| profile_schemes | Multiple schemes in the existing Game Controls library, bounded import/export, revision and editor interlocks; detached-window acceptance |
| Primary | Integration, actual Android/game acceptance, GPU transport, rendering fixes, packages, evidence and PR |

All implementation is original MIT code. The research references upstream
behavior and public documentation; no proprietary/GPL source, artwork or
presets were imported.

The editor now follows Tools → place → bind → inspect → Try → Save. A separate
native window can hold its tools and inspector outside the Android canvas.
Markers stay on that canvas. Detach, Dock and window close retain the same
draft, selection and history; close means Dock. Try accepts mapped gameplay
only while the canvas has focus, and panel/external blur releases contacts.
Save and Cancel use the same transaction in either presentation.

V4 appends twelve integers to the nineteen V3 fields:
`up_key left_key down_key right_key walk_key walk_radius suspend_key drag_shot
skill_mode cancel_key cancel_x cancel_y`. Walk is kind 9; previous kinds keep
their numeric values. V1–V3 remain readable. Changed Save writes V4; unchanged
Save and Cancel preserve exact original bytes, including legacy files.

Scheme operations remain within the existing runtime/app Game Controls
workflow. The backend bounds profiles and metadata, validates scope and keys,
requires a revision derived from the active file and sidecar, interlocks with
native editing, and journals multi-file transactions for rollback/recovery.
Export uses a native Save dialog and creates a private new file without
following links or overwriting an existing file. Default→managed migration
preserves supported profiles and sidecars. No duplicate Settings section was
added. See [usage and format](GAME-CONTROLS.md).

## Maintained checks

- `target/keymap-redesign/integration-gpu-final.log`: PASS; Python 347 passed,
  14 environment-dependent ARM skips; Node 395 passed; native 15 suites;
  packaging checks passed. Controls harness 38 and detached harness 13 are
  included in that Python count.
- Codec differential: 1,606 deterministic V4 profiles (526 accepted,
  1,080 rejected), zero C/Python divergences, plus four valid legacy fixtures.
  Storage/model suites separately cover legacy behavior.
- Rust workspace: 83 passed, one intentionally ignored GTK/session case;
  `cargo-test-final.log`. All-target Clippy with warnings denied passes
  (`clippy-final.log`). A failed sandbox socket attempt remains in its
  separate log; the successful run had host socket support.
- Focused editor/engine/rotation/detached native suites pass with
  `-Wall -Wextra -Werror`. ASan/UBSan runs pass, with leak detection explicitly
  disabled because the test environment does not support LeakSanitizer.
- Schemes exercise real bounded shell transactions in temporary trees,
  including failed rename, SIGKILL recovery, stale/reused PIDs, editor
  exclusion, revision conflicts, malformed imports and path/link refusal.

These are code regressions. Their counts do not establish gameplay or desktop
window-manager usability.

## Defects discovered during actual acceptance

1. Click-to-bind intercepted a subsequent marker drag as M1. Binding now waits
   for release, and motion cancels that click candidate.
2. Android mksh's `hash` alias shadowed the scheme digest helper. A private
   function name fixes the actual shell execution; revision checks remain.
3. After display rotation, stock HWC physical displayFrame coordinates did
   not match logical task bounds. The bridge now uses the active INTERNAL
   viewport from the same atomic task snapshot for rect, crop, buffer-transform,
   damage and upstream-input correction. Unknown viewport data retains the
   previous path. NVIDIA's exported local-frame path and Full UI are excluded.
4. Try from an empty profile retained a held touch on keyboard-only blur.
   Genuine keyboard leave now cancels input regardless of passive hints.
5. The first GPU run exposed passive hints inheriting the editor's 300px
   minimum. A correct 285px contain fit after rotation closed those hints and
   left stale attachment geometry. Passive markers now accept bounded small
   canvases; interactive editor limits remain unchanged. A native regression
   reproduces 704×444→285×448, including the 209px letterbox origin, and 1×1.
   The visual probe crops that actual fitted rectangle while retaining its
   pixel-content and exact-scope checks.
6. GPU Wayland acceptance found that making the detached panel a transient
   child caused Weston to retain its keyboard focus even when clicking the
   Android header or content. Protocol traces showed pointer clicks but no
   keyboard enter into Android. The panel is now an independent top-level;
   its draft and teardown remain owned by the Android overlay. The protocol
   regression supplies a real tracked Android parent and rejects any
   `set_parent` request; restoring the old request makes that regression fail.

The initial `.v4.6` GPU rotation failure and the unsuccessful reuse of that
failed display remain under `target/keymap-redesign/controls-x11-gpu-v4.6/`
and `detached-x11-gpu-v4.6-debug.log`; neither is counted as passing.
The no-WM X11 fixture additionally needs explicit exact-XID focus to model a
window manager when moving between canvas and panel. The duration test holds
its physical key through the automatic 375ms tap release. These harness fixes
retain the finger, contact-balance, exact-byte and timing assertions.
The first `.v4.7` full run then hit a search→geometry BadWindow race while
observing successful task destruction; the wait now rechecks exact scoped
absence, and the complete fresh-generation run passes. Its failed output is
retained as `controls-x11-gpu-v4.7.log`.
The first GPU Wayland run stopped at empty-profile Try refocus. A scoped
protocol trace showed no inner keyboard events after restoring only the outer
X11 Weston window. Activating the actual app produced `wl_keyboard.enter`
and fresh Android DOWN/UP immediately. The fixture now activates the inner app
after proving stationary-pointer blur, requires exactly one ordinary mouse
click without mapped replay, then tests cancelled key-up and a fresh press.
The failed attempt and protocol/Android discriminator remain in
`controls-wayland-gpu-v4.7-final.log` and
`empty-try-wayland-gpu-v4.7-focus-{protocol,android}.log`.
The next Wayland attempt reached rotation and found a valid 704×448 host
surface rejected by the harness's old 480px height minimum. The generic scene
check now uses the editor's 330×420 limit, with a regression for that exact
scene and a 419px negative case. Scope, output bounds, centered fit and visible
pixel thresholds remain enforced; no native change was needed for either
Wayland harness correction.
The third Wayland attempt stopped at detached Undo/Try and exposed defect 6.
Its failed log and header/content protocol discriminators are retained as
`controls-wayland-gpu-v4.7-run3.log` and
`detached-wayland-gpu-v4.7-{focus,canvas}-{protocol,android}.log`.
The final candidate was rebuilt as `.v4.8` after fixing that native defect.

## Packaged candidate

Package revision: `0.1.3+keymap.v4.8`; embedded GUI/CLI version: `0.1.3`.

| Artifact | SHA-256 |
| --- | --- |
| `anvildroid-controller_0.1.3+keymap.v4.8_amd64.deb` | `a0d9a7116cd9db436d2b725dac3c21ad314412010dce74fd555b3e5037579fee` |
| `anvildroid-controller-0.1.3+keymap.v4.8-1-x86_64.pkg.tar.zst` | `c929f51dd04187da204b2b820684bfe1cf7c3224f45ba3983b6b9227d4e44a06` |

Both extracted packages pass metadata, Python/native/NVIDIA/scrcpy manifests,
GUI/CLI dependencies/routing, and packaged image-tool compilation/signature
checks in `verify-packages-v4.8.log`. The verifier uses the package revision
for extraction and 0.1.3 for embedded binary version.

## Final GPU Android acceptance

| Android 13 Vanilla, RX 6600 | Native X11 | Nested Wayland |
| --- | --- | --- |
| Input/editor/lifecycle/schemes/detached/capture | 73/73 | 73/73 |
| Visible app-content checks | 22 | 22 |
| Luanti 5.17.0 + Minetest Game 5.8.0 | 11/11 | 11/11 |
| Sustained captured game camera | 30.2082 seconds | 30.1909 seconds |
| Complete stop/cleanup/process/loop/mount/scope audit | Pass | Pass |

Original final summaries, GPU/device and live game DRM records, cleanup
audits and actual detached-panel screenshots are in
[the `.v4.8` evidence](evidence/keymap-v4.8/README.md). Both guests loaded
bridge SHA-256 `27a6f857ed9b40edc0a7d20912bec429747c92a4d301c136a602707c330b8097`,
matching the package's native sidecar. The package was extracted into private
fixtures; no host package was installed during this acceptance.

Earlier `.v4.7` X11 GPU evidence is retained [separately](evidence/keymap-v4.7/README.md):
73/73 controls, 22 visible-content checks and 11/11 Luanti scenarios, including
30.2036 seconds of captured camera input. Its independent GPU evidence pins
the hardware transport to the same generation; the summary's old hardcoded
`private-xvfb` reporting label is explained there. These results apply to
`.v4.7`, before the independent-panel change. Raw failures and stage logs
remain under `target/keymap-redesign/`.

The final runs use Android 13 x86_64 Vanilla in disposable private namespaces,
with 4 GiB/4 CPUs and no desktop audio or installed-controller access. The selected
render node is `/dev/dri/renderD128`, AMD Radeon RX 6600. The GPU gate requires
accelerated desktop GLX, a hardware Android SurfaceFlinger GLES renderer and
SurfaceFlinger's actual open DRM render device. Native X11's presenter and
nested Wayland's Weston renderer are recorded separately. Host Mesa overrides
are confined to the private display processes; no global drivers are changed.

These private displays exercise actual Android apps and the production
bridge/scrcpy path through XTest. X11 has an explicit scoped EWMH move/resize
fixture because it has no desktop WM. Wayland uses actual Weston moves and
pointer constraints. Neither route represents a person using the installed
KDE/GNOME runtime with a physical mouse.

Earlier candidate results are retained separately: software X11 `.v4.5`
controls 69/69 and Luanti 11/11; software Wayland `.v4.3` controls 66/66, with its
Luanti run failing the small-window editor setup after 10 completed scenarios.
They are not final-package or GPU certifications.

This delivery does not repeat the historical Default/GAPPS recovery matrix or
installed KDE run. Commercial FPS/MOBA/ARM games, physical mouse feel,
end-to-end latency, NVIDIA/Intel GPU acceptance, ordinary KDE/GNOME/CachyOS
window behavior and long sessions remain separate acceptance work. AppImage
is deferred.

## Automatic FPS camera and unlocked M1 touch (v4.9)

New Mouse camera and FPS starter controls default to Automatic capture. They
preassign F5 for lock/unlock and Left Alt for temporary cursor access; placing
the camera does not open a binding prompt. Capture starts when the game canvas
has keyboard and pointer focus, including Try. No Android camera contact starts
before the compositor grants the lock. Escape, compositor release and failed
requests pause automatic capture until an explicit F5 activation; polling does
not fight an unlock. Hold/Toggle remain available and old files retain their
saved mode. Activation value 2 is accepted only for Look, consistently in the
native codec, Python inventory and Game Controls library.

An M1 Fire binding applies while Look is active. After Escape or while Alt is
held, M1 instead injects touchscreen finger DOWN/MOVE/UP at the actual pointer
position, even without a separate Mouse touch control. Clicking the screen
does not relock or fire. Returning to capture releases any free-touch finger
and drains its physical mouse-up. A legacy Look explicitly triggered by M1
keeps that trigger; profiles without Look retain their existing mouse bindings.

Native regressions cover automatic acquisition/grant, relative camera motion,
Escape persistence, ordinary M1 touch/drag versus authored Fire, Alt suspension,
Alt release while M1 is held, compositor unlock, request failure/timeout,
focus/editor/decoration/letterbox exclusion and legacy Hold/Toggle/M1 behavior.
The codec storage regression round-trips Automatic with a suspend key. See
[v4.9 evidence](evidence/keymap-v4.9/README.md) for package hashes and the explicit
distinction between host checks and game acceptance.
