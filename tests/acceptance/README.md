# Disposable Android acceptance

These opt-in helpers exercise production code and an actual Android guest.
Live operations are separate from `scripts/test-runtime-features.sh`, which
runs only their inert safety checks, and the privileged managed/Default/two-UID
mount fixtures in CI. A passing host fixture or a worker
in `Starting` state does not establish Android app acceptance.

Use a new task-specific directory under `/tmp`, copied Android images and
fresh userdata. The live harness never contacts the installed controller. It
starts its own bounded systemd scope, private mount/network/PID namespaces,
private display sockets and copied package assets. The worker executes from
the pinned extracted package. Before managed display/worker startup, `/run`,
`/tmp`, `/home` and both installed runtime stores are masked; an inode-checked
FD bind preserves only this fixture root. The environment is whitelisted, so
custom desktop/bus/audio/agent endpoints are not inherited. The input and
sharing probes verify the selected private worker
before changing its diagnostic app or writing inside its selected test share.

Prerequisites include root namespace privileges, systemd/cgroup-v2 CPU and
memory controllers, Binder already available, LXC, Weston, Xvfb, xauth,
xdotool/xwininfo, and the image/build inputs used by this checkout. The harness
does not change persistent sysctls, load kernel modules, install a host package,
or initialize the user's installed Android runtime. Adequate inotify capacity
is required by Android system_server.

Build the diagnostic observer APK first:

```sh
sh scripts/build-touch-probe.sh
```

Prepare and start a new test store (start blocks for the worker lifetime):

```sh
sudo python3 tests/acceptance/live-android.py /tmp/anvil-live-local --prepare --owner-uid "$(id -u)" --package "$PWD/target/releases/anvildroid-controller_0.1.3_amd64.deb"
sudo python3 tests/acceptance/live-android.py /tmp/anvil-live-local --start --x11 --package "$PWD/target/releases/anvildroid-controller_0.1.3_amd64.deb"
```

The harness records runtime ID, generation, image/store paths, chosen owner,
resource scope and the staged package checksum in `acceptance.json`. A new
start gets a new generation and checks the worker lock before updating it.
For another verified image pair, pass `--image-source /path/to/copied-images`
and its actual `--flavor VANILLA` or `--flavor GAPPS` during preparation. The
directory must contain `system.img` and `vendor.img`; only those bytes are
copied into the new test store.
Wait for the production worker to report `Running`; retain its actual host PID
for the probe's namespace identity check. Do not substitute the Android init
PID or a PID from inside the namespace.

For desktop Wayland boot/render acceptance, replace `--x11` with `--wayland` in
the start command. This creates a private software-rendered Weston desktop as
the selected non-root owner, with a private socket and runtime directory. The
display flags are mutually exclusive. With no display flag, the worker uses
its headless path. Switching backends should use a separate disposable store.
The tested headless Weston advertises no `wl_seat`, virtual keyboard/pointer or
test-input protocol; boot, mapped app content and clean stop do not establish
actual Wayland input acceptance.

For actual Wayland input checks, use `--wayland-x11` instead. The product guest
still connects through Wayland. A private software Weston using its X11 backend
runs inside authenticated private Xvfb; XTest injects into that compositor only.
Weston supplies the Wayland seat, keyboard and pointer events to the production
bridge, mapper and Android sink. No host input devices or personal desktop
sockets are used. Verify this display-only fixture before starting Android:

```sh
sudo python3 tests/acceptance/live-android.py /tmp/anvil-live-input-preflight --preflight-wayland-input --owner-uid "$(id -u)"
python3 tests/acceptance/test-controls-harness.py
```

The preflight requires a new disposable directory, starts no Android process,
checks the seat, XTest extension and unique compositor output, then terminates
both display processes. Its JSON and logs remain in the chosen directory.
For the actual test, prepare another new store and start it with
`--start --wayland-x11 --package /path/to/pinned.deb`, then run the same controls
probe below. Preserve the package checksum throughout the lifecycle.

From another terminal, exercise the actual mapper and Android input sink:

```sh
sudo python3 tests/acceptance/probe-android-controls.py /tmp/anvil-live-local/acceptance.json HOST_WORKER_PID "$PWD/target/live-controls-local"
```

Append `--keymap-v3 --editor-v3 --capture-v3` for the 25-case editor/input
matrix. V3 adds real finger mouse click/drag, fixed-button routing, center-first
joystick, held swipe endpoint, relative hold-look with edge restart, and three
simultaneous movement/look/fire contacts. Editor checks click Add/place/Key/Save,
validate persisted V3 rows and fresh playback, then change a rendered property
and Cancel while requiring exact saved bytes to survive. Geometry includes
centered letterboxing; unknown frame geometry fails closed.

Add `--editor-lifecycle` (requires `--editor-v3`) for the full 45-case run. It
drives select/drag/rebind, rendered duplicate-binding feedback, toolbar Delete,
unassigned Save rejection and marker Delete while binding capture is armed.
It compares actual marker pixels across F4 hide/show, requires saved bytes to
survive Cancel and dirty task replacement, then exercises resize during drag,
maximize/restore, actual Android density and 90-degree rotation while holding
a contact, followed by fresh input. Wayland maximize uses the production CSD;
X11 has no desktop WM and uses an explicit private EWMH/ConfigureNotify fixture.

Readiness waits for the current observer PID's content size and focused,
contact-free state. It collapses the private guest's NotificationShade when
necessary and reacquires mapped native windows after Activity recreation.
The longer run uses a bounded 16 MiB private log buffer and keeps the exact log
prefix oracle. Only density/rotation cases allow Android's framework-generated
terminal CANCEL with touchscreen source, device zero, raw coordinates zero and
UNKNOWN tool type. DOWN/MOVE/UP still require FINGER, and all other cases retain
the strict finger-only oracle. A production-method Java regression also covers
failed CANCEL, stale MOVE/UP rejection and fresh-DOWN recovery after recreation;
the scrcpy builder runs it against the pinned, patched upstream sources.

The separate capture opt-in requires the actual compositor's relative-pointer
and pointer-constraints capabilities. It observes lock-dependent Android DOWN,
relative movement, independent fire, Escape release, blur cancellation and fresh
activation after refocus. Nested Wayland protocol tracing correlates the scoped
app surface, client, pointer and relative/lock objects: a real grant must suppress
that client's absolute pointer motion while delivering relative events, then
restore absolute motion after release. The outer X11 transport cursor may move
independently of this Wayland lock. Native X11 instead requires its host pointer
to remain confined. A missing capability or denied lock fails this requested
test; it cannot silently count as a pass. This bounded test does not certify
unlimited motion, physical mouse feel or sustained gameplay. Keep the flags off
when reproducing the historical 13-case result.

Use a fresh private compositor for each full editor/capture run. Nested Weston's
logical pointer can retain an offset from its outer X11 pointer after relative
capture. Reusing that display after a failed capture can invalidate the next
run's absolute-coordinate editor setup; keep the exact authored-position oracle
and preserve the failed evidence rather than widening its tolerance.

The runner installs only `org.anvildroid.touchprobe` and its guest test profile.
It sends real XTest keys/pointer events and records the APK's MotionEvents.
For guest Wayland, it authenticates the separate compositor's UID, generation,
PID start time and private mount/PID namespaces before sending input. It locates
the exact mapped Android app rectangle using a one-shot `weston-debug
scene-graph` capture with a five-second deadline; timeout cleanup kills only
that capture process group. Summary JSON distinguishes the guest backend from
the compositor's X11 transport and records the capture duration.
The scenarios require finger source/tool type, actual movement, multi-contact,
finite macro execution and zero remaining contacts after release/focus loss.
For nested Weston, held-refocus explicitly clicks the app to restore compositor
keyboard focus: focusing the X11 output alone does not select a Wayland client.
That activation must be exactly one ordinary mouse down/up, saved separately
from the finger oracle. The held key must produce no new contact until release,
then a fresh press must work; the scenario still requires exactly two finger downs.
This establishes delivery semantics; it does not certify gameplay feel in an
FPS/MOBA or ARM game.

Recorded input acceptance on 2026-10-17 used
`target/feature-verification-20261017/completion-wayland-focus.deb`, SHA256
`29700b0b769308e924aa2b4eac269b36df275f2476f5b1e11fa6ca8d633ef36a`.
Both routes passed all ten actual Android sink scenarios: basic tap/hold,
WASD diagonal, swipe, repeat, skill movement, finite macro, aim plus fire,
simultaneous contacts, blur cancellation and held-key refocus.

| Guest backend | Private input transport | Result and retained evidence |
| --- | --- | --- |
| Wayland | XTest → Xvfb → Weston X11 backend → Wayland seat → production bridge/scrcpy | 10/10 PASS; [summary](../../target/feature-verification-20261017/live-controls-wayland-verified/summary.json), [cleanup](../../target/feature-verification-20261017/live-controls-wayland-verified/stopped.json). Scene capture completed in 4 ms within its five-second bound. |
| Native X11 | XTest → authenticated Xvfb app window → production bridge/scrcpy | 10/10 PASS; [summary](../../target/feature-verification-20261017/live-controls-x11-focus-verified/summary.json), [cleanup](../../target/feature-verification-20261017/live-controls-x11-focus-verified/stopped.json). |

Each run used a separate disposable managed runtime with actual Android 13
x86_64 Vanilla, fresh userdata, the TouchProbe observer and a harness-written
profile. Rendering used software ANGLE/pastel; the runtime was limited to
4 GiB/4 CPUs with isolated networking and no desktop audio. The Wayland result
exercises native guest Wayland input through the nested compositor; it does
not establish GNOME/KDE, physical input-device, pointer-lock or relative
mouse-look acceptance. Both runtimes reached `Stopped`, complete cleanup and
quiescence with empty scopes, no owned worker/display processes or host mounts;
their disposable roots were removed after preserving evidence.

These results leave live editor creation/drag/key capture/Save/Cancel and
editor-to-game profile roundtrips open. They also leave resize while contacts
are held, maximize/restore, rotation/DPI changes, app close/replacement/reopen,
two app windows or runtimes, popup/chat/IME interaction and runtime disconnect
unobserved in this fixture. Native regression coverage for those guards is
separate from actual Android acceptance. No game was played here: joystick
tuning, sustained FPS/MOBA gameplay, ARM game compatibility, end-to-end input
latency percentiles and CPU/performance under game load remain open.

The 2026-10-18 follow-up extends both routes to **13/13 actual Android input
scenarios**. F2 editor entry and F3 playback disable cancel held contacts,
suppress mapping until re-enabled, and accept a fresh press afterwards. F4
input leaves an ongoing gesture uninterrupted; the oracle does not observe
marker visibility. Blur/refocus stages now wait for real Android event
acknowledgments and retain failed-stage logs. These results do not close the
editor Save/Cancel, resize or gameplay gates above. Evidence is under
`target/plan-completion-20261018/controls-{x11-synchronized,wayland}/`.

The final keymap-editor follow-up on 2026-10-18 passes **25/25 scenarios on
each route**, using Debian SHA256
`0a5896286b74ed5e5387d8e5fdd17eaf4d3f59c608f565b84ef7287a9e6deb19`.
Both runs enable `--keymap-v3 --editor-v3 --capture-v3` and use the final packaged
native bridge. The visible Add/place/Key/Save/reload and rendered-change/Cancel
subset now passes alongside V3 finger gestures and capture lifecycle.

| Guest backend | Final evidence | Complete cleanup |
| --- | --- | --- |
| Wayland | [25/25 summary](../../target/keymap-v3-verification/controls-wayland-v3-final/summary.json); [scoped protocol trace](../../target/keymap-v3-verification/controls-wayland-v3-final/v3-capture-protocol.log) | [Stopped, empty scope/processes/mounts](../../target/keymap-v3-verification/retired-fixtures/anvil-live-kv3-wl-v4.json) |
| Native X11 | [25/25 summary](../../target/keymap-v3-verification/controls-x11-v3/summary.json), including physical capture confinement | [Stopped, empty scope/processes/mounts](../../target/keymap-v3-verification/retired-fixtures/anvil-live-kv3-x11-v3.json) |

These runs use actual Android 13 Vanilla with software rendering, private
networking and fresh desktop processes. All disposable roots were removed
after authenticated stop and quiescence checks. Neither package was installed
over personal services. At that delivery, full live drag/delete/conflict/resize/
rotation/app replacement and concurrent runtimes remained open; the follow-up
below closes those managed cases. Physical GNOME/KDE/Default input and
representative games remain open. Finite capture tests explicitly record
`unlimited_movement_verified: false`; they do not establish sustained FPS
movement, game feel or physical mouse latency. Earlier failed runs remain in
`target/keymap-v3-verification/` for diagnosis.

The 2026-10-19 editor-lifecycle follow-up passes **45/45 on each route** with
`--keymap-v3 --editor-v3 --editor-lifecycle --capture-v3`. The pinned package is
`target/releases/anvildroid-controller_0.1.3+editor.lifecycle3_amd64.deb`, SHA256
`caf0d63bc2abca3a065981df1b7252af9eb7660e960eb23b3e72368b6f1c1b1f`.
Its patched scrcpy JAR SHA256 is
`855be209a8719a964827cc990ed84a406119f4110c77f2074d215bba20cb2ca7`.

| Guest backend | Final lifecycle evidence | Cleanup before fresh isolation desktops |
| --- | --- | --- |
| Native X11 | [45/45 summary](../../target/editor-lifecycle-20261019/recovery-x11-v2/summary.json) | [Stopped and quiescent](../../target/editor-lifecycle-20261019/cleanup-before-isolation-x11.json) |
| Wayland | [45/45 summary](../../target/editor-lifecycle-20261019/recovery-wayland-v5/summary.json) | [Stopped and quiescent](../../target/editor-lifecycle-20261019/cleanup-before-isolation-wl.json) |

For simultaneous-runtime isolation, first complete the normal probe on two
different disposable stores so the observer is installed in each guest. Stop
them cleanly and start fresh private desktop processes with the same pinned
package (relative capture can leave the outer pointer offset). Wait for both
workers to be Running and rediscover their exact host PIDs/new generations:

```sh
sudo python3 tests/acceptance/probe-editor-isolation.py /tmp/anvil-live-a/acceptance.json HOST_WORKER_PID_A /tmp/anvil-live-b/acceptance.json HOST_WORKER_PID_B "$PWD/target/live-editor-isolation"
```

This probe validates private worker/compositor identities before each operation.
It writes different scoped profiles for the same observer package, alternates
real mapped input and checks own-profile DOWN coordinates within four pixels.
It then edits/rebinds/Saves each through the visible editor while playing the
peer, requiring no leaked contact or peer saved-byte change. The final mixed
native-X11/Wayland run passes **8/8**, with actual DOWN x=109/334 matching the
two authored positions. See [isolation summary](../../target/editor-lifecycle-20261019/isolation-v2/summary.json).
Both final generations reached Stopped/cleanup_complete and quiescence, with
empty scopes, no owned worker/display processes and no host fixture mounts:
[X11 cleanup](../../target/editor-lifecycle-20261019/cleanup-final-x11.json),
[Wayland cleanup](../../target/editor-lifecycle-20261019/cleanup-final-wl.json).
Their disposable roots were removed after preserving metadata and evidence.

These results close the managed graphical editor lifecycle and same-package
two-runtime isolation gates above. Multiple ambiguous same-package tasks in
one runtime remain rejected. Physical GNOME/KDE/CachyOS/Default input, popup/chat/
IME gameplay, sustained capture, representative games and latency under load
remain separate gates. No package from this follow-up was installed over the
personal controller/runtime or remote desktop session.

Shared-folder checks require a test-owned source with the sentinel
`acceptance-read` containing TOKEN, configured through production shared-folder
APIs while stopped. Use a new runtime generation after each RO/RW/remove change:

```sh
sudo python3 tests/acceptance/probe-android-shares.py /tmp/anvil-live-local/acceptance.json HOST_WORKER_PID Acceptance TOKEN --expect ro
```

Use `--expect rw` or `--expect removed` for the corresponding lifecycle stage.
The helper reports root raw-data and Android FUSE paths separately. Neither
root probe establishes ordinary app access: retain the ShareProbe observations
from the diagnostic APK, including Android storage permission state and host
Unix permissions/ACLs. Tests may adjust only their own fixture directory; the
product does not change the selected source's ownership or permission bits.
The observer targets SDK 28 and the storage probe requires its Android
READ/WRITE_EXTERNAL_STORAGE permissions. A passing result covers that app and
image, and does not establish unrestricted access for modern scoped-storage
apps.

Stop only the test runtime using its authenticated worker API:

```sh
sudo python3 tests/acceptance/live-android.py /tmp/anvil-live-local --stop
```

Wait for `Stopped`/cleanup acknowledgement and scope exit before removing its
test-owned directory. Test userdata and logs are retained by default. Do not
use installed personal runtimes as acceptance fixtures. Default's stock
Waydroid service needs additional device/system-bus isolation beyond this
managed harness; its real app matrix is recorded separately in the feature
plan. See [Default isolation](DEFAULT-ISOLATION.md) before attempting
that separate service lifecycle.

The PID1 holder reaps adopted Android child zombies, matching the production
worker's responsibility. Retain worker shutdown status as evidence: an inactive
worker with `Error` or incomplete cleanup is not a clean stop. Recorded package
checksums, backend, generation and PID start/namespace identity bind results to
the actual test guest; do not reuse results after replacing its package.

## Disposable stock Default service

`default-android.py` uses the installed stock Waydroid tools inside a separate
bounded scope, private namespaces, fresh BinderFS, an exact cgroup device
allowlist, copied read-only image loops, private system bus and fresh desktop
home/userdata. Its controller and system integration execute from the pinned
package. The installed personal services and userdata are not test targets.
The launcher currently requires amd64, cgroup v2 delegation, the stock network
tools (including dnsmasq/iptables), PipeWire with `pipewire-pulse`, and a
conventional `/home` desktop account. Its audio servers use explicit minimal
configs and private sockets; no host audio device or session manager is used.

Build the observer APK and the maintained production-core export probe first:

```sh
sh scripts/build-touch-probe.sh
cargo build --locked -p anvildroid-core --example acceptance-diagnostics
python3 tests/acceptance/test-default-harness.py
```

Prepare a new root with the actual checksum of the selected package, then run
Android-disabled preflight:

```sh
sudo python3 tests/acceptance/default-android.py /tmp/anvil-default-local --prepare --owner-uid "$(id -u)" --package /absolute/package.deb --expected-package-sha256 PACKAGE_SHA256
sudo python3 tests/acceptance/default-android.py /tmp/anvil-default-local --preflight
```

Review `preflight.json`, `stock-network-preflight.log` and `teardown.json` in
that root: no Android Start, private service/bus, denied unrelated devices and
loop-control, exact copied-image provenance, read-only stock image mounts,
working private stock network Start/Stop, and empty scope/detached task loops.
Record the digest of that exact preflight after reviewing the launcher source.
The live run refuses changed launcher/package/image/APK identities:

```sh
sudo sha256sum /tmp/anvil-default-local/preflight.json
sudo python3 tests/acceptance/default-android.py /tmp/anvil-default-local --run --reviewed-preflight PREFLIGHT_SHA256
```

The live lifecycle claims management, tests RO then RW then removal across
stock Start/Stop cycles, verifies the actual Android init cgroup, probes raw
media/FUSE and the SDK28 Activity, and checks exact source bytes. It also
retains a real namespace helper to verify Stop fails and configuration stays
locked until that helper exits; retry must restore the service baseline. Core
diagnostic exports run as the desktop UID with logs off/on, mode 0600 and
overwrite refusal in Running and Stopped states. Inspect the report content
separately; successful export alone does not establish valid live sections.
`lifecycle.json` records each passed phase and emits `complete` only after all
stock stops succeed. Teardown must show an empty scope and authenticated task
loop detach even on failure. Preserve evidence before removing the test root.

The full disposable Default lifecycle passed on 2026-10-18 using launcher
`b34cf225026d0e0d9bb7e985cb81c8c244876fd1b486695bce62886e8f91b0b0`
and Debian package
`f75c765369015cc0d35289ba3e4a84c52e9e9f1ffb9505323cc3d37930359caa`.
RO/RW app probes, strict retained-helper rejection/recovery, removal, all stock
stops and four independently inspected core exports passed. Removal permits
an empty persistent directory but requires no share mount or source sentinel;
the app probes with the original token and must still fail both read and write.
See [Default isolation](DEFAULT-ISOLATION.md) for evidence and scope. These
checks do not establish physical desktop input or gameplay on Default.

## Default installed ARM provider disable/restore

Build the maintained ARM-only arithmetic JNI probes from source (cached Android
SDK, pinned R8, Java, clang/lld, binutils, aapt and apksigner are required):

```sh
sh scripts/build-arm-probes.sh
```

Use a fresh Default fixture and supply all three optional inputs during
preparation. The archive must match the packaged production libndk checksum;
no archive installer is added to Default's product API.

```sh
sudo python3 tests/acceptance/default-android.py /tmp/anvil-default-arm-local --prepare --owner-uid "$(id -u)" --package /absolute/package.deb --expected-package-sha256 PACKAGE_SHA256 --arm-archive /absolute/verified-libndk.zip --arm-probe32 "$PWD/target/arm-probe/arm32.apk" --arm-probe64 "$PWD/target/arm-probe/arm64.apk"
sudo python3 tests/acceptance/default-android.py /tmp/anvil-default-arm-local --preflight
```

Review that preflight and teardown exactly as for the stock share fixture,
including ARM input hashes and the overlay manifest, then pass its exact digest
to `--run --reviewed-preflight PREFLIGHT_SHA256`. This optional mode performs
the owner claim and ARM lifecycle instead of the share/diagnostic lifecycle.
It observes actual ARM32/64 JNI before/after stopped-controller disable/restore,
no advertised ARM ABI and rejected fresh ARM APK while disabled, exact ARM and
unrelated configuration after the final restored boot/Stop, and a retained
fixture-app marker checked only inside Android. APK/archive/payload changes
invalidate the reviewed preflight. It does not drive the graphical installer.

This lifecycle passed on 2026-10-18 using launcher SHA256
`650deb81c1fb96b6c3bdb29414c68fbbbcdf53a87cd98e1898d266e59ac066c1`.
Evidence and positive scope/loop teardown are retained in
`target/plan-completion-20261018/default-arm/`. The probes' JNI results establish
actual ARM code execution, not representative ARM game compatibility.

## Offline real-game controls

`probe-luanti-game.py` uses the official unmodified Luanti 5.17.0 x86_64 APK
and Minetest Game 5.8.0 in a managed disposable Android guest. Obtain the two
release files from the pinned sources in [game acceptance](../../docs/GAME-ACCEPTANCE.md).
The helper checks their SHA256 before installation. It downloads nothing and
uses the same worker/generation/namespace and private display guards as the
two-runtime controls probe. It never connects to the installed controller.

Prepare/start separate stores with `live-android.py` as above, using `--x11`
or `--wayland-x11`. From a second terminal, pass that store's actual host worker
PID, absolute output directory and downloaded input paths:

```sh
sudo python3 tests/acceptance/probe-luanti-game.py /tmp/anvil-live-game-local/acceptance.json HOST_WORKER_PID "$PWD/target/game-local" --prepare --apk /absolute/luanti-5.17.0-x86_64.apk --game /absolute/minetest_game-5.8.0.tar.gz --normalize-window --enter --run
```

`--prepare` resets only the probe's `Acceptance` world inside that authenticated
disposable guest. It installs a world-local scene/telemetry mod, enables the
game's touch controls, remaps native W/A/S/D away from movement, and creates
the package's V3 profile as Android UID 1000 so the production editor can Save.
The observer sets the initial scene once, then records actual player state;
it does not move the player or synthesize input during measurement.

Window normalization uses the production CSD on private Weston; native X11
uses its private Xvfb window. The probe rejects invisible/ambiguous surfaces
and stale fitted geometry. A new server epoch and advancing sequence numbers
are required. Tests observe node removal, hotbar selection, position, camera
and simultaneous game controls through real XTest → bridge → mapper → Android
input. Hold Look is changed to FPS Capture using the existing graphical editor.
Capture receives relative motion for at least 30 seconds; Escape, camera
quiescence and a fresh toggle are checked separately. Final/failed runs blur
the private client and release inputs, including a toggle that survives key-up.

Run `python3 tests/acceptance/test-controls-harness.py` for inert archive,
pinning, stale-oracle, endpoint and profile checks. The actual game profile and
its Capture variant are checked by the production C validator. The ordinary
feature script runs those checks without starting Android or a game.
After live testing, stop each store with `live-android.py --stop`, verify
Stopped/cleanup_complete, quiescence, empty scope and no owned process/mount,
then remove only the exact disposable stores. Retain evidence outside them.
