# GPU keymap acceptance, 2026-10-20

Candidate: `0.1.3+keymap.v4.7`.
These files are copied from actual opt-in acceptance output under
`target/keymap-redesign/`. No result rows were edited. Runtime generations and
Debian package hashes bind the controls, game, GPU and cleanup records.

## X11

- `x11-controls.json`: 73/73; 22 visible-content checks.
- `x11-game.json`: official Luanti 5.17.0 with Minetest Game 5.8.0, 11/11;
  sustained captured camera interval 30.2036 seconds.
- `x11-gpu.json`: accelerated RX 6600 GLX, Android GLES radeonsi/navi23,
  SurfaceFlinger open `/dev/dri/renderD128`, native X11 GPU presenter.
- `x11-game-gpu.json`: live game process's kernel AMD DRM graphics counters,
  proving GPU submissions by the game itself.
- `x11-cleanup.json`: authenticated stop, complete cleanup and quiescence;
  no owned processes, scope tasks, loop devices or host mounts.
- `x11-detached.png` / `x11-detached-geometry.json`: panel header dragged outside
  Android, unchanged canvas. The private X11 display has no WM and uses an
  exact-window EWMH move fixture. This is not an installed KDE acceptance run.

The X11 controls report has one historical reporting bug:
`input_transport.kind` says `private-xvfb`, the old hardcoded native-X11 label.
The actual transport for its exact generation is GPU Weston/Xwayland, as
recorded independently in `x11-gpu.json` before input acceptance. Android and
presenter renderers and the game's actual AMD DRM submissions also establish
hardware use. The original summary is retained unchanged; the reporting code
now copies the GPU transport metadata for future native-X11 reports. This
metadata-only correction does not change or rerun the 73 measured scenarios.

## Scope

The Android guest, private GPU desktop and production input path are real.
XTest injects through private authenticated displays; personal runtimes,
physical input devices, desktop DBus/audio sockets and installed services are
not used. Results do not certify physical mouse feel, ordinary KWin/GNOME or
CachyOS window behavior, NVIDIA/Intel GPUs, commercial FPS/MOBA/ARM games,
performance or long sessions. Full raw stage logs and failed attempts remain
in the local `target/keymap-redesign/` evidence tree.
