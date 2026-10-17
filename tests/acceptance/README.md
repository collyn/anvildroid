# Disposable Android acceptance

These opt-in helpers exercise production code and an actual Android guest.
They are separate from `scripts/test-runtime-features.sh` and the privileged
managed/Default/two-UID mount fixtures in CI. A passing host fixture or a worker
in `Starting` state does not establish Android app acceptance.

Use a new task-specific directory under `/tmp`, copied Android images and
fresh userdata. The live harness never contacts the installed controller. It
starts its own bounded systemd scope, private mount/network/PID namespaces,
private display sockets and copied package assets. The worker executes from
the pinned extracted package. User session sockets are masked. The input and
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
two display flags are mutually exclusive. With neither flag, the worker uses
its headless path. Switching backends should use a separate disposable store.
The tested headless Weston advertises no `wl_seat`, virtual keyboard/pointer or
test-input protocol; boot, mapped app content and clean stop do not establish
actual Wayland input acceptance. The controls probe below requires `--x11`.

From another terminal, exercise the actual X11 mapper and Android input sink:

```sh
sudo python3 tests/acceptance/probe-android-controls.py /tmp/anvil-live-local/acceptance.json HOST_WORKER_PID "$PWD/target/live-controls-local"
```

The runner installs only `org.anvildroid.touchprobe` and its guest test profile.
It sends real XTest keys/pointer events and records the APK's MotionEvents.
The scenarios require finger source/tool type, actual movement, multi-contact,
finite macro execution and zero remaining contacts after release/focus loss.
This establishes delivery semantics; it does not certify gameplay feel in an
FPS/MOBA or ARM game.

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
plan. See [Default isolation design](DEFAULT-ISOLATION.md) before attempting
that separate service lifecycle.

The PID1 holder reaps adopted Android child zombies, matching the production
worker's responsibility. Retain worker shutdown status as evidence: an inactive
worker with `Error` or incomplete cleanup is not a clean stop. Recorded package
checksums, backend, generation and PID start/namespace identity bind results to
the actual test guest; do not reuse results after replacing its package.
