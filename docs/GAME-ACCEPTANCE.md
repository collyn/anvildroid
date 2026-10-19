# Real-game controls acceptance — 2026-10-19

This extends managed controls acceptance from the diagnostic MotionEvent APK
to an actual offline game: the official, unmodified Luanti 5.17.0 x86_64
Android APK running Minetest Game 5.8.0. The test uses the existing editor and
V3 controls; it adds no separate runtime setting or game-specific product code.

## Inputs and licensing

| Input | Official source | SHA256 |
| --- | --- | --- |
| Luanti APK | [5.17.0 x86_64 release](https://github.com/luanti-org/luanti/releases/download/5.17.0/luanti-5.17.0-x86_64.apk) | `d0e8bea52462d7e743d411e79c9eaa6b55f382f88ab1644d6ef727b320fae148` |
| Minetest Game | [5.8.0 archive](https://github.com/luanti-org/minetest_game/archive/refs/tags/5.8.0.tar.gz) | `33a3bb43b08497a0bdb2f49f140a2829e582d5c16c0ad52be1595c803f706912` |
| Controller Debian package | `target/releases/anvildroid-controller_0.1.3+editor.lifecycle3_amd64.deb` | `caf0d63bc2abca3a065981df1b7252af9eb7660e960eb23b3e72368b6f1c1b1f` |

The APK is 17,936,990 bytes, package `net.minetest.minetest`, versionCode
5170000, minimum SDK 21, target SDK 35. `apksigner verify --print-certs`
passes; signer certificate SHA256 is
`5323d1a1b4f68be97639eec667edf7e4e1741a8cf5223d1792da5d1f1d6e41a7`.
Controller binaries are unchanged from the editor-lifecycle delivery.

Luanti code is LGPL-2.1-or-later; its media have CC BY-SA and other individual
licenses documented in [the pinned release's LICENSE.txt](https://github.com/luanti-org/luanti/blob/5.17.0/LICENSE.txt).
Minetest Game's [LICENSE.txt](https://github.com/luanti-org/minetest_game/blob/5.8.0/LICENSE.txt)
documents LGPL-2.1 code, CC BY-SA 3.0 media and per-mod attribution.
No APK, upstream game/source tree or media is bundled in AnvilDroid by this
change. Users obtain the original releases; the probe validates their bytes.

## What is measured

The maintained [probe](../tests/acceptance/probe-luanti-game.py) imports the
existing authenticated disposable-runtime endpoint. It verifies the worker's
PID/start time, generation, command, private mount namespace and, on Wayland,
the exact private compositor and output window before executing operations.
No installed controller, personal Android userdata, host input device or
personal desktop/bus is a target. Both guests use Android 13 Vanilla,
software rendering, isolated networking and bounded CPU/memory fixtures.

A world-local Lua mod creates a flat test area, a dirt target and inventory
once on join. During measurement it only observes actual player position,
yaw/pitch, controls, wield index, dig/place callbacks and target-node state.
It never synthesizes touch, moves the player or changes camera state during
the scenarios. A new server epoch and advancing sequence numbers prevent
saved telemetry from passing a fresh run. The runner waits for spawn/camera
state to settle before measuring.

Luanti's supported `touch_controls` and `buttons_crosshair` settings enable
its actual touch joystick/camera and separate dig/place buttons. A supported
custom touch layout puts dig at normalized (0.85, 0.60). Native W/A/S/D are
rebound to arrows so ordinary keyboard forwarding cannot impersonate a mapped
joystick. The negative control disables mapping with F3 and verifies that
ordinary mouse motion/native W leave the player position and camera unchanged
in this touch mode. This does not assert that Luanti lacks native input in
other modes.

All measured controls enter through private XTest, the production X11 or
Wayland bridge, production mapper and Android input sink. Node removal and
hotbar selection confirm actual game actions. Position/camera deltas and
simultaneous game controls confirm more than contact delivery to a test APK.
The existing graphical editor rebinds the single Hold Look to L/Capture and
Saves it; the file must contain that exact result before testing capture.

Thirty seconds of relative motion must change the game camera in all six
five-second batches. After Escape, the oracle drains queued game frames and
requires five fresh stable camera samples. Ordinary pointer movement must
actually change desktop coordinates while leaving the game camera unchanged;
a new toggle must then rotate the camera again. The nested XTest cursor is
distinct from the locked Wayland pointer. Their coordinates drift apart by
the accumulated relative motion during a lock. After release the harness
clamps both private coordinate spaces at opposite output corners, then
re-enters game content before a new lock. The same normalization precedes a
fresh run, so state left by an earlier attempt cannot misroute menu clicks.
These checks establish a bounded 30-second run, not latency, FPS or unlimited
capture certification.

## Evidence and scope

Both final runs pass **11/11** scenarios using the pinned package above:

| Scenario | Native X11 | Nested Wayland |
| --- | --- | --- |
| Mapped action removes a real dirt node | Pass | Pass |
| Mouse-as-finger tap selects hotbar slot 2 | Pass | Pass |
| Mapping-off native mouse/W negative control | No movement/rotation | No movement/rotation |
| Mouse-as-finger drag rotates camera | Pass | Pass |
| Center-first joystick drag/hold, release without drift | Pass | Pass |
| Swipe endpoint holds movement until release | Pass | Pass |
| Hold Look rotates camera | Pass | Pass |
| Simultaneous joystick/look/dig game controls | Pass | Pass |
| Blur cancels movement | Pass | Pass |
| Fresh input after refocus/release | Pass | Pass |
| FPS Capture, Escape/quiescence and fresh toggle | 30.496 seconds | 30.586 seconds |

Evidence:

- [X11 summary](../target/game-acceptance-20261019/x11-v11/summary.json),
  generation `090e83ce1fde4349b29ce96090491ca2`.
- [Wayland summary](../target/game-acceptance-20261019/wayland-v11/summary.json),
  generation `ca4c8e9913ba4d93bc5c9f31e46c0205`.
- [X11 cleanup](../target/game-acceptance-20261019/cleanup-final-x11.json) and
  [Wayland cleanup](../target/game-acceptance-20261019/cleanup-final-wl.json):
  Stopped, cleanup_complete, quiescent, empty scope, no owned process or host
  mount before removing the exact disposable stores.

The 26 inert controls-harness tests pass, including production C validation of
the game and Capture profiles, rejection of a second Look, modified APK bytes,
archive traversal/links, stale telemetry and replaced game epochs. The complete
feature regression script passes, including frontend/backend, controls/Default
harnesses, 12 native suites and packaging checks. Its root-owned trust fixtures
require a root runner and an explicit path to the installed Node binary on
this host; earlier unprivileged/missing-Node attempts are retained separately.
Final log: `target/game-acceptance-20261019/features-final.log`.

Full stage JSON, profiles, screenshots, startup logs and failed attempts are kept
under `target/game-acceptance-20261019/`. Reproduction and teardown steps are
in [the acceptance README](../tests/acceptance/README.md#offline-real-game-controls).

Earlier attempts exposed test-setup errors: saved world/camera state, stale
telemetry, an invalid two-Look profile, root-owned profile files that the
production editor could not Save, queued game frames and the outer XTest
cursor reaching the output edge or retaining an offset after unlock. The
probe now rejects stale epochs/geometry,
validates its profiles with production C code, writes them as Android UID 1000,
and observes camera quiescence and actual post-Escape pointer movement.
An earlier X11 generation also stopped mapping the game and its Android
launch input check timed out; clean Stop and a new generation recovered it.
Its cause is unconfirmed and no product fix is claimed for that occurrence.

One game exercises touch camera, free joystick, independent action buttons
and multi-contact input. Physical GNOME/KDE/CachyOS/Default behavior, other
images, representative commercial FPS/MOBA/ARM games, long gameplay sessions
and GPU/performance measurements remain separate acceptance gates. Runtime
disconnect/restart while a contact is held is also separate from these blur,
Escape and fresh-input checks. AppImage remains deferred.
