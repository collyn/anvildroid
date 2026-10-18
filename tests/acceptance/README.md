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
