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
The adapter advertises exactly the LINEAR formats the host EGL reports through
`eglQueryDmaBufFormatsEXT` (logged as `x11_adapter_dmabuf_formats=`), including
planar video: YV12/YU12, NV12/NV21 and P010, plus RGB565, 10-bit and FP16 RGB
when listed. Waydroid's composer sends one plane-0 `add` even for planar
buffers, so the chroma planes of a single linear allocation are reconstructed
from the allocator layout: NV12/NV21/P010 at stride × height with the luma
stride; YV12 with `ALIGN(stride / 2, 16)` chroma (gbm_gralloc and
`<system/graphics.h>`); minigbm's private `9997` YV12 fourcc with
`ceil(stride / 2)` and `ceil(height / 2)`. Every explicit or implied plane is
bounds-checked against its fd before import. YUV is sampled through
`samplerExternalOES` with limited-range BT.709 for content at least 1280 wide
or taller than 576 lines, and BT.601 otherwise; the composer conveys no
dataspace. That composer aborts on any dmabuf failure, including the
non-fatal `failed` event, so only structural misuse (params reuse, plane index
or duplicate, missing planes, dimensions) is a protocol error. A well-formed
buffer that cannot be imported (unknown or unadvertised format, non-LINEAR
modifier, flags, short stride, out-of-bounds plane, or an EGL import failure)
receives `failed` for `create` and is shown as an opaque black placeholder for
`create_immed`; its fds are never imported. The composer's SHM path labels
non-RGBA HAL formats `-EINVAL`; that wl_shm format is accepted and also shown
as a placeholder. Each case is logged once (`x11_dmabuf_unsupported`,
`x11_dmabuf_import_failed`).
Synchronized subsurface commits, position and stacking are applied with the
parent transaction; input hit testing uses the same tree and input regions.

X11 readiness shares the protocol event loop, so an idle endpoint sleeps until
input or a guest commit arrives. It drains Xlib's already queued events before
waiting, flushes input before rendering, and uses EGL swap interval zero: Android
paces the producer and the desktop compositor schedules scanout. Stop signals
also wake this wait, including when no clients remain. DMA-BUF snapshot completion
still precedes buffer release; this scheduling change does not bypass GPU fences.

Each guest DMA-BUF is imported once (its EGLImage lives until the guest destroys
the `wl_buffer`), snapshot textures are recycled by size and one FBO is reused,
so a steady frame allocates no GPU memory. With `EGL_ANDROID_native_fence_sync`
(logged `x11_adapter_release=fence`) the snapshot's sync file is watched on the
event loop and `wl_buffer.release` is sent when it signals; the loop never
blocks in `glFinish`. Without the extension, or with `ANVILDROID_X11_SYNC_COPY=1`
(diagnostics), it falls back to `glFinish` before release (`=finish`).
Outputs follow X11 monitors: one wl_output (v4, with name/description) per
RandR 1.5 monitor, so `xrandr --setmonitor` virtual monitors count; without
RandR 1.5, one per active CRTC; without RandR, the whole screen. Each carries
its root position (logical), physical size (EDID mm, else a 96 DPI estimate)
and the current mode in pixels with that monitor's refresh. RandR screen/
CRTC/output notifications and root ConfigureNotify (also raised by
`--setmonitor`) re-enumerate monitors: changed outputs resend geometry/mode/
done, new monitors add a global, removed monitors withdraw theirs (destroyed
3 s later) after `wl_surface.leave`. Toplevels and popups receive
`wl_surface.enter`/`leave` for every monitor their native window intersects,
tracked from (synthetic) ConfigureNotify root positions. The first maximized
configure, used by the composer to calibrate Android's canvas, carries the
logical size of the monitor the window will appear on: the one under the
pointer, where X11 WMs place new windows, else the primary, else the first.
Popups are constrained to the parent's monitor intersected with
`_NET_WORKAREA`. The worker's canvas capacity (`persist.anvildroid.canvas`,
largest connected xrandr output in pixels) is unchanged and covers every
monitor. A monitor's refresh is computed in mHz from its CRTC's dot clock and
total timings (interlace/doublescan, clamped to 23-500 Hz); monitors without a
CRTC (virtual) and Xvfb use the primary-or-fastest CRTC rate, else 60 Hz. The
startup line `x11_output_refresh_mhz=` keeps that whole-screen rate, and
`x11_output_added/changed/removed` lines describe each output. Waydroid's
composer keeps the fastest advertised mode and reads it only at startup, so a
mode change takes effect after the Android runtime restarts. It truncates mHz
to whole Hz before computing its vsync period, so each output advertises its
rate rounded to the nearest whole Hz (59.997 Hz → 60 Hz, 143.98 Hz → 144 Hz).

Desktop scaling: X11 has no compositor scaling, so the adapter takes the
desktop scale from `Xft.dpi` in the root `RESOURCE_MANAGER` (dpi/96, in 1/120
steps, 1x-4x; GNOME, Cinnamon, MATE and KDE write 96 × scale there, and the
accessibility text scaling factor multiplies it). It is exposed as a
fractional Wayland compositor does: `wl_output.scale` = ceil(scale) and, only
when the scale is not 1, `wp_fractional_scale_manager_v1` with
`preferred_scale` = scale × 120. The composer and the shared bridge then
calibrate exactly as on KWin/Mutter: Android's display is the physical pixel
size and its default density 180 × scale. Every Wayland coordinate stays
logical and X11 pixels are logical × scale: native window and size-hint
sizes (logical → pixels rounds up, pixels → logical down, so sizes round-trip
without resize loops), `_GTK_FRAME_EXTENTS`, rendering (Android's viewported
physical buffers are sampled 1:1, so Android is crisp; 1x guest SHM chrome
and SHM cursors are enlarged), pointer, touch and relative motion,
confinement/lock regions and release warps, popup placement, XIM spot and the
cursor hotspot. At 1x every path is unchanged. Xft.dpi is screen-wide, so
all monitors share one scale; per-monitor scale is unavailable on X11. XFCE's
integer window scaling (XSETTINGS only, Xft.dpi left at 96) is not detected.
The scale is latched once a client can observe it (first wl_output,
fractional-scale or window); Waydroid's composer never recalibrates, so a
later `Xft.dpi` change is logged as `x11_desktop_scale=… restart_required=1`
and applies after the runtime restarts; earlier changes apply immediately.
A client that already received a fractional `preferred_scale` is not sent an
integer `wl_output.scale` for hotplugged outputs at fractional scales,
because the composer would replace its fractional scale with it.

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
does not change or restart the user's input method. When the XIM server exits,
its destroy callback drops every XIC and clears guest preedit; enabled text
input falls back to physical keys. An instantiate callback reopens the IM under
the same pinned `XMODIFIERS` when a server appears, including one started after
the adapter, and restores XIC focus and spot for the enabled text input.

`wl_data_device_manager` bridges X11 CLIPBOARD asynchronously, including TARGETS,
UTF8_STRING/TEXT and INCR transfers. Native MIME transfers are bounded to 32 MiB
and 10 seconds. The authenticated Android IME companion synchronizes plain UTF-8
text up to 1 MiB in chunks, suppresses echoes, and leaves content URIs untouched.
Offers and writes require guest keyboard focus and a delivered input serial.
An initial Android snapshot does not replace the host's clipboard at startup.
This does not provide drag and drop or the X11 PRIMARY selection.

XI2 supplies multitouch with stable contact IDs, surface-local coordinates,
client cancellation and suppression of duplicate touch-emulated pointer events.
Wheel buttons synthesized by XI2 scroll valuators retain vertical/horizontal
scroll events; they are not discarded with touch-emulated motion and clicks.
Relative-pointer and pointer-constraints globals require XI2, XFixes and Shape.
Locked/confined pointers support one-shot/persistent lifetime, committed regions,
release hints and focus/surface teardown. Confinement shape is refreshed only
when its geometry or input region changes, rather than on each rendered frame.

`zwp_idle_inhibit_manager_v1` inhibitors take effect only while their surface
belongs to a mapped toplevel/popup that the WM has not unmapped (iconified) or
marked `_NET_WM_STATE_HIDDEN`. While one is effective the adapter calls
XScreenSaverSuspend (MIT-SCREEN-SAVER 1.1, also suspending DPMS) and resets
the server idle time every 30 s from an event-loop timer, as `xdg-screensaver`
does on generic X11; desktop idle monitors read that idle time. The timer is
disarmed again when the last inhibitor stops applying, and the X server drops
the suspension if the adapter exits. No D-Bus session inhibit is used: the
adapter does not receive the desktop session bus.

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
calibration, WM close delivery and idle inhibit (saver suspension/idle reset,
guest unmap, WM unmap/hidden state, surface destruction, client teardown and
an idle loop while inhibited). Dedicated feature checks cover Unicode
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

The software run also sends real XTest keys/motion/buttons through the X11 seat
into production guest bridge input handlers and a deterministic fake scrcpy
sink. It checks WASD, swipe, repeated click, skill, finite macros, aim/fire,
normal input fallback, blur/refocus and held-key repeat suppression. The native
adapter drains missing button releases after a WM grab, blocks held keys until
release after focus loss, rejects events for stale windows, and revokes input on
unmap. Physical key repeat is handled by Wayland clients; opted-in XIM text
continues receiving repeats. This fixture mocks Android/task inventory and the
sink, so it does not establish Android input compatibility or FPS/MOBA gameplay
acceptance. That requires a disposable booted Android 13 x86_64 runtime with the
production guest bridge, shell-owned scrcpy control server, a visible unambiguous
app task and a touch-test app/game, on each intended GPU/backend.
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
preedit/caret/commit, ordered physical Control shortcuts and a Fcitx
kill/restart under the same adapter; Android's receiver
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

Window placement matches the Wayland KWin placement script. Before an app
window is first mapped, the adapter asks the window manager (`USPosition`) to
put it at the position remembered for that app, or, without one, centered on
the workarea of the monitor under the pointer. A remembered position is reused
only while at least min(160, width) pixels of the header stay visible and the
top min(64, height) rows lie inside a current workarea, so an unplugged monitor
never hides the window. Only an app's single window restores or records its
position; maximized/fullscreen positions are never recorded (a WM-initiated
maximize rolls back to the previous normal position). Positions are saved when
a window is hidden or closed and when the adapter stops, in
`$XDG_STATE_HOME/anvildroid/x11-window-positions-<runtime>.tsv` (default
`~/.local/state`), written atomically with mode 0600 by the desktop user. Log
lines: `x11_window_positions=`, `x11_toplevel_position_centered=`,
`x11_toplevel_position_restored=`.
