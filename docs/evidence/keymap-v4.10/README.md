# KWin pointer capture correction — 2026-10-20

Installed and exercised `0.1.3+keymap.v4.10` on the actual KDE Wayland desktop,
in the user's CrossFire: Legends app (`com.vnggames.cfl.crossfirelegends`).
This was not a private compositor or diagnostic game fixture.

## Reproduced failure

The running v4.9 library was verified by SHA256. F5 created a relative pointer
and one-shot constraint, but capture remained pending at 100 ms and 500 ms,
then timed out after two seconds without a grant. The pointer entered an Android
content subsurface. KWin's `PointerInputRedirection::updatePointerConstraints`
checks the focused window's root surface, so the content constraint was ignored.

## Correction and real desktop observations

- Request the app window first. If still pending after 250 ms, try the focused
  content surface once for compositors requiring exact surface focus, including
  the native X11 backend. Keep the original two-second timeout and release rules.
- In Try, Escape releases active input without returning to editing. Unlocked M1
  can then touch the game. F2, the return button, or another Escape returns to editing.
- The installed guest library matched the newly built library:
  `818e83a51b23256ebdc78982393c491008f6fe5f02ebeaee47381fe72932742a`.
- KWin granted the root constraint. Android logcat recorded `request pointer
  lock: window` and `pointer locked: window` at the same timestamp.
- A bounded relative mouse move without M1 kept the physical cursor at
  `(2698, 513)` and moved the active camera finger. Escape removed capture and
  all active contacts.
- With capture released, M1 created a pointer-source Mouse touch control at
  cursor coordinates (normalized X about 29258), rather than the saved Fire
  location (X 53801). Releasing M1 removed the contact and left capture released.
- SurfaceFlinger reported AMD Radeon Graphics, `radeonsi, navi23, ACO`, OpenGL ES
  3.2 Mesa 26.0.8. The runtime retained `libhoudini.so` and `/dev/dri/renderD128`.
- The saved CrossFire profile was byte-identical before restart and after these
  tests: SHA256 `4c24ed31b79dee4e373ed5e493496af836796df9de535a1032502eff168afad7`.
- The user subsequently confirmed operation, while reporting that camera feel
  still needs sensitivity/smoothness tuning. This evidence does not certify feel.

Local samples, screenshots and logs are in `target/crossfire-live-v49-diagnose/`.
The user was also interacting with the game; the observations establish the
lock lifecycle and touch routing, not isolated measurements of camera angles,
frame times, aiming accuracy or the user's subjective mouse feel.

## Automated validation

Ten affected native suites passed (capture, editor UI/lifecycle, full UI,
advanced mapping, base mapping, detached panel/protocol, overlay, hints).
ASan/UBSan capture checks passed with leak checking disabled for fixture globals.
Regression cases cover root grants with child pointer focus, exact-child fallback,
late callbacks from the first request, focus loss, fallback failure, timeout,
and Escape followed by free touch while Try remains active.

Both Debian and Arch packages passed payload/hash/dependency validation.
The native X11 fallback is covered by state-machine regression tests here;
this run did not perform a new physical X11 desktop acceptance test.
