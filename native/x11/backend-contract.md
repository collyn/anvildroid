# X11 backend integration contract

X11 must preserve the existing Wayland product behavior. Backend selection must
only change host window presentation and input transport. A full-display
framebuffer viewer does not meet this contract, even if it lists Android apps.

## Shared behavior

The controller/worker owns Start, Stop, Restart, inventory, launcher resolution,
install/uninstall, force-stop, runtime isolation, startup diagnostics and idle
suspension. Start boots Android without opening a desktop window. Closing an app
closes that app's window; closing Full UI returns to app-window mode. Neither
action is a runtime Stop. Only explicit app/Full UI actions create or activate
windows. Inventory must preserve package identity, labels and icons.

The existing bridge owns CSD, menu actions, resize modes, saved window profiles,
Full UI transitions, game fitting, keymap overlays, IME, scale, activation and
window feedback. X11 must reuse those policies. App windows carry the same
per-runtime/per-package desktop identity as their shortcuts. The bridge draws
the header; the X11 window manager does not add server decorations.

## Implemented transport boundary

The worker now retains Waydroid's per-task composer and the existing AnvilDroid
bridge. `wayland-host.cpp` provides their protocol endpoint. `surface-tree.hpp`
owns committed/cached surface state, subsurface transactions, ordering and hit
regions; `x11-platform.hpp` owns native X11 windows, EGL imports and GPU snapshots.
No per-app policy is duplicated in the worker or a second CSD implementation.
The old framebuffer-only guest HAL and input companion have been removed.

The endpoint supports xdg toplevel lifecycle/configure acknowledgement,
subsurface commits/stacking, SHM, LINEAR RGB DMA-BUF imports, viewporter, output,
pointer/keyboard/touch focus, guest cursors, text-input-v3 with XIM preedit/caret,
relative pointers and pointer confinement/lock, clipboard and activation. xdg
positioners/popups support constrained placement, reposition and nested grabs;
Waydroid's existing CSD menus remain subsurfaces. Clipboard text reaches Android
through the authenticated IME companion. See README for transfer bounds and the
host XIM callback configuration. Physical touchscreen and broader IME/WM hardware
matrices still require live acceptance. There is no automatic Weston fallback.

## Acceptance checks before deployment as the integrated backend

Run the same scenario suite for Wayland and X11 with backend-specific window
inspection adapters. Verify Start maps no window; real inventory matches;
launching two packages creates two independently actionable windows; shortcuts
activate/group the correct package; force-stop/uninstall affect the target only;
closing a window leaves its runtime running; Full UI enter/leave restores app
windows; resize/maximize/profile restore, CSD menus, game fitting, keymaps and
Unicode IME match the established Wayland behavior. Test two runtimes concurrently.

Backend switching must recover Android display settings and preserve guest-first
GPU teardown. X11 must never launch Weston implicitly. Wayland builds and
behavior must continue to pass unchanged. Host mock tests are necessary for
synchronization but do not establish live feature parity.
