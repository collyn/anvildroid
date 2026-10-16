# Native X11 app-window adapter

The Android composer, AnvilDroid bridge and runtime application actions are the
same as on Wayland. An embedded protocol endpoint maps each guest xdg_toplevel
to a native X11 window. It does not start Weston. Guest SHM subsurfaces supply
CSD, menus and overlays; X11 does not draw a second header or request SSD.
Default/system Waydroid uses this same adapter through the GUI/CLI session
supervisor on X11; a native Wayland session keeps its existing launch path.
The Default supervisor verifies the installed payload, starts only its private
endpoint before Waydroid, and retains graphics if guest shutdown cannot be
confirmed. Adapter failure never launches a compatibility Weston desktop.

Android GPU surfaces use negotiated LINEAR DMA-BUFs. The existing pinned NVIDIA
composer/renderer makes presentation copies when needed. The adapter snapshots
each committed surface into an owned GPU texture before sending buffer.release,
then draws the committed surface tree into that app window. Android pixels are
never read back to the CPU. Only SHM chrome is uploaded from guest memory.
Synchronized subsurface commits, position and stacking are applied with the
parent transaction; input hit testing uses the same tree and input regions.

X11 readiness shares the protocol event loop, so an idle endpoint sleeps until
input or a guest commit arrives. It drains Xlib's already queued events before
waiting, flushes input before rendering, and uses EGL swap interval zero: Android
paces the producer and the desktop compositor schedules scanout. Stop signals
also wake this wait, including when no clients remain. DMA-BUF snapshot completion
still precedes buffer release; this scheduling change does not bypass GPU fences.

Host XIM is focused only while the focused guest has enabled text-input-v3.
Keymap editing and other non-text input bypass XIM and receive physical keys,
even when Fcitx is active. Remote XIM protocol events are filtered before
native window lookup, preserving UTF-8 commits without duplicate raw keys.

Native windows use a depth-32 ARGB EGL visual and a transparent clear color, so
the desktop compositor receives the bridge's premultiplied rounded CSD pixels.
Committed input regions outside xdg geometry expand the native drawable to
retain all 5 px of transparent resize grips. Rendering and pointer coordinates
share that origin; WM configure sizes exclude the extra input margin. The
adapter advertises this margin through `_GTK_FRAME_EXTENTS`, and removes it
for maximized/fullscreen windows to preserve exact workarea dimensions. It
does not infer input from alpha or draw its own rounded frame.

Start creates only a private endpoint. Windows map after configure acknowledgement
and committed content. App/Full UI close events are sent back to the common guest
bridge, preserving its task and presentation transitions; closing one window
never terminates the endpoint. Worker Stop still flushes Android before graphics
teardown. The old single-framebuffer HAL/input companion and duplicate X11 CSD
implementation have been removed from the production payload and source tree.

Runtime admission accepts SDK 33-36 when both SurfaceFlinger and the composer
expose Waydroid's per-app HIDL contract. Mesa guest rendering uses that image's
composer. The pinned NVIDIA/Venus guest renderer still requires SDK 33; newer
NVIDIA guest ABIs are rejected before boot. SDK 36 with AMD rendering remains
under live validation. The backend remains experimental; admission and synthetic
tests do not establish full Wayland feature parity.

Android init can issue host-global SysRq commands while handling an early boot
failure, even inside a PID namespace. Managed containers mask
`/proc/sysrq-trigger` with `/dev/null`, and installed Waydroid AppArmor profiles
explicitly deny writes to the original control path (including complain mode).
SDK 36 skips host hardware coldboot replay and seeds private `/dev/block/loopN`
nodes for existing kernel loop devices; live ueventd handles newly created loops.
Reusing a free loop device emits no new device event, so it must already have a
guest node. The host-control fix has isolated fixture coverage; SDK 36 boot and
window regression checks must be repeated on a disposable machine/VM before
marking this path validated. Do not run a failing Android shutdown on a working
desktop to test SysRq isolation.

Build and test without connecting to the user's desktop:

```sh
python3 native/x11/build-host.py
python3 native/x11/host-check.py
python3 native/x11/host-check.py --software-x11
# Optional: requires fcitx5, dbus-run-session and xprop; all run privately.
python3 native/x11/host-check.py --xim
python3 services/x11-integration-test.py
python3 services/x11-lifecycle-test.py
python3 services/x11-display-test.py
python3 services/x11-payload-test.py
python3 services/runtime-isolation-test.py
python3 services/default-x11-test.py
```

The default host check uses fake window/GPU operations and real private protocol
sockets. The software check starts its own Xvfb with llvmpipe, verifies two native
window identities, CSD/content pixels, maximize/restore (including deletion of
WM state), keyboard focus/modifiers, UTF-8 XIM commits without duplicate raw keys,
CSD hit testing, move/resize serial ownership and a WM consuming button releases
before the next app is clicked, unchanged accepted geometry after a WM resize,
deferred resize requests without stale drawable dimensions, CSD/content edge
pixels after the grab ends, cursor reuse, canvas
calibration and WM close delivery. A separate chrome fixture checks raw ARGB
corner/antialias pixels from the shared bridge style, every pixel of all four
5 px resize edges, diagonal corners, padded configure/accepted sizes and frame
extents across maximize/fullscreen/restore. Raw alpha checks on Xvfb do not
substitute for visual validation with the user's desktop compositor. Neither test
starts Android or Weston, changes the desktop display mode, or connects to its X
server. Local socket permissions are required. Test processes and displays are
owned by the harness and cleaned up afterwards.
The Default fixtures pin Waydroid to an inert executable. For the additional
real-adapter check on a private Xvfb with llvmpipe (still no Android), run
`ANVILDROID_DEFAULT_X11_SOFTWARE=1 python3 services/default-x11-test.py`.

For the reported Music/Settings regressions on an already running test runtime:

```sh
python3 native/x11/window-regression-check.py RUNTIME_ID
```

This checks the initial content edges before a manual resize can mask a stale
open-size request, performs six alternating real CSD/WM drags while asserting
the other app stays stationary, and captures resize/maximize/restore. It leaves
both windows open. Reports and window-only captures go under
`target/x11-window-regression`.

Layout mode fills the accepted frame after Android reaches the requested task
bounds, absorbing NVIDIA pitch alignment even when maximized. The temporary
old task image and Keep resolution retain their aspect-preserving fit. The
bridge owns both the presentation and inverse pointer mapping on both backends.

For a deliberately selected, disposable Android 13 NVIDIA test runtime on the
current X11 desktop:

```sh
python3 scripts/test-native-x11-session.py RUNTIME_ID --start --full-ui
```

This starts that stopped runtime, opens Settings and Calculator, exercises
native resize/maximize/restore and CSD menu, closes/reopens an app and enters/
closes Full UI. It leaves the runtime running for inspection. Only the selected
runtime's windows are controlled; screenshots and a report go under
`target/x11-session-check`. Review the screenshots too: a successful X11 resize
alone does not prove Android relayout.

Live checks on 2026-10-15 passed on Android 13 GAPPS, NVIDIA RTX 2060/Venus
rendering and AMD RX 6600 presentation: Start leaves no app window, inventory
contains 14 apps with icons, separate app close/reopen preserves the runtime,
resize/maximize/restore and the bridge CSD menu render correctly, and closing
Full UI allows launching a separate app again. The shared keymap editor also
rendered in the app window and accepted Escape. Xorg and the RustDesk service
kept their original PIDs; Weston was never started. Multi-runtime hardware,
other guest GPU paths, clipboard and rich IME composition still need validation
or adapter support before claiming complete Wayland parity.

For AddressSanitizer/UBSan use a separate build directory:

```sh
ANVILDROID_X11_SANITIZE=1 ANVILDROID_X11_BUILD_DIR=target/x11-host-asan python3 native/x11/build-host.py
ANVILDROID_X11_BUILD_DIR=target/x11-host-asan python3 native/x11/host-check.py
```

The package manifest hashes the executable, runtime adapter and source provenance.
Packaging rejects sanitizer builds, source mismatches and the superseded Full UI
payload format. See [the integration contract](backend-contract.md) for live
acceptance scenarios still to verify.
