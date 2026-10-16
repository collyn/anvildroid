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
Native input runs in bounded turns so continuous motion cannot starve guest
requests. Consecutive absolute motion samples are coalesced within each turn,
without reordering buttons, keys, focus or touch events. Duplicate coordinates
are suppressed. The default 4 KiB protocol read batch is retained: enlarging it
also batches DMA-BUF snapshots and can starve XIM or overflow the legacy
composer's outgoing request queue.
The shared bridge requests a bounded 1 MiB client socket send buffer so older
libwayland clients can absorb short multi-layer Android clipboard-overlay
bursts while DMA-BUF snapshots complete. This adds capacity, not a flush delay.

Host XIM is focused only while the focused guest has enabled text-input-v3.
Keymap editing and other non-text input bypass XIM and receive physical keys,
even when Fcitx is active. Remote XIM protocol events are filtered before
native window lookup, preserving UTF-8 commits without duplicate raw keys.
XIM callback preedit edits use Unicode character offsets internally and emit
UTF-8 byte caret positions. Android supplies surrounding text/selection and
cursor-anchor geometry, including Full UI scaling. Password fields retain the
physical-key path without exporting surrounding text. Preedit and commit strings
are bounded to 4,000 UTF-8 bytes per message; surrounding context above that
protocol limit is omitted. Fcitx must enable
`UseOnTheSpot=True` in `~/.config/fcitx5/conf/xim.conf` to expose preedit callbacks;
with off-the-spot XIM, the host input method owns its composition UI. The adapter
does not change or restart the user's input method.

`wl_data_device_manager` bridges X11 CLIPBOARD asynchronously, including TARGETS,
UTF8_STRING/TEXT and INCR transfers. Native MIME transfers are bounded to 32 MiB
and 10 seconds. The authenticated Android IME companion synchronizes plain UTF-8
text up to 1 MiB in chunks, suppresses echoes, and leaves content URIs untouched.
Offers and writes require guest keyboard focus and a delivered input serial.
An initial Android snapshot does not replace the host's clipboard at startup.
This does not provide drag and drop or the X11 PRIMARY selection.

XI2 supplies multitouch with stable contact IDs, surface-local coordinates,
client cancellation and suppression of duplicate touch-emulated pointer events.
Relative-pointer and pointer-constraints globals require XI2, XFixes and Shape.
Locked/confined pointers support one-shot/persistent lifetime, committed regions,
release hints and focus/surface teardown. Confinement shape is refreshed only
when its geometry or input region changes, rather than on each rendered frame.

xdg popups use native override-redirect/transient windows, positioner
flip/slide/resize constraints, desktop workarea, reactive placement, reposition
acknowledgements and nested grabs. Outside clicks and Escape dismiss grabs and
restore parent focus.

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
# Xvfb tests also require xclip and libXtst; XIM tests require Fcitx Pinyin,
# dbus-run-session and xprop. All run privately.
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
calibration and WM close delivery. Dedicated feature checks cover Unicode
preedit/caret replacement, deterministic multitouch/cancellation, native pointer
lock/confinement/relative motion, nested popup grabs/reposition, and bidirectional
1 MiB clipboard INCR. The private Fcitx test composes Pinyin into a CJK commit. A separate chrome fixture checks raw ARGB
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
other guest GPU paths, physical touchscreen devices and broader IME/WM matrices
still need live validation before claiming complete Wayland parity.

Live feature checks on 2026-10-16 passed on the Default Android 13 runtime:
Unicode host clipboard paste/copy-back and new text entry/copy passed before and
after 4,000 targeted X11 motion events. HWC/SurfaceFlinger PIDs stayed unchanged,
with no Wayland marshal errors. The private Fcitx test covers inline Pinyin
preedit/caret/commit and ordered physical Control shortcuts; Android's receiver
covers composing replacement, emoji deletion and stale editor generations.
Live keyboard probes used explicit events targeted at the disposable editor;
XTest delivery on this desktop did not reach the target, while private Xvfb
XTest checks passed. Physical touchscreen and broader IME/WM checks remain open.

For AddressSanitizer/UBSan use a separate build directory:

```sh
ANVILDROID_X11_SANITIZE=1 ANVILDROID_X11_BUILD_DIR=target/x11-host-asan python3 native/x11/build-host.py
ANVILDROID_X11_BUILD_DIR=target/x11-host-asan python3 native/x11/host-check.py
```

The package manifest hashes the executable, runtime adapter and source provenance.
Packaging rejects sanitizer builds, source mismatches and the superseded Full UI
payload format. See [the integration contract](backend-contract.md) for live
acceptance scenarios still to verify.
