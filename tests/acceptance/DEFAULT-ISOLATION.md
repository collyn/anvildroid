# Disposable stock Default acceptance

The maintained launcher is `default-android.py`; its inert safety regressions
are `test-default-harness.py`. `--prepare` copies only official image bytes and
a digest-pinned controller package into a new `/tmp/anvil-default-*` fixture.
`--preflight` starts private support services and exercises stock image/network
setup, but never sends Android Start. `--run` requires the exact reviewed
preflight digest and unchanged harness, package, extracted payload, image and
probe APK identities. See `README.md` for build and invocation commands.

## Isolation actually implemented

- The unsymlinked fixture root is root-owned 0700; work, controller store,
  userdata, synthetic desktop home and sharing source are fresh. Personal
  userdata, configuration, ownership records and live service state are never
  copied. Images are copied and hashed; packaged runtime payload bytes are
  checked against the pinned Debian archive before launch.
- A unique delegated systemd scope bounds memory to 6 GiB, swap to zero, CPU to
  four cores, and tasks to 2048. A private cgroup namespace exposes only that
  scope; its processes move into a supervisor leaf before controller delegation.
  The actual Android init PID/starttime and cgroup are read after every Start.
  `/supervisor/android/lxc.payload.waydroid` has been observed and accepted by
  the strict descendant oracle; merely checking the service scope is insufficient.
- A PID1 supervisor uses private mount, network, PID, IPC and UTS namespaces.
  All inherited mounts become recursively private and read-only. Private tmpfs
  mounts cover `/run`, `/dev`, `/tmp`, `/home`, `/var/lib`, `/var/log` and `/root`.
  Pinned file descriptors restore only the fixture root and delegated cgroup
  subtree; device/inode equality is checked after each bind.
- A fresh read-only sysfs belongs to the private network namespace. Its bridge
  ifindex must match `ip -j link`. A fresh procfs must identify this namespace's
  PID1. Numeric process files remain writable for normal LSM attach; every
  nonnumeric global entry receives a separate recursive read-only bind.
  `self`, `thread-self`, `net -> self/net` and `mounts -> self/mounts` are validated
  process-local aliases. Protected entries are re-enumerated at preflight and
  the final LXC pre-start hook. Global kernel controls remain read-only/masked.
- Writable network settings are limited to the exact private IPv4 forwarding
  file and private `/proc/sys/net/ipv6/conf` subtree. Stock LXC needs the latter
  for dynamically created veth interfaces. A real create/write/read/delete
  veth probe runs before Android. Host all/default/interface `disable_ipv6`
  values are recorded before and after and must match exactly.
- Ordinary devices are new inodes, not host device bind mounts. BinderFS and
  devpts are private new instances. Only copied system/vendor images receive
  task-owned read-only loops. A scope-root cgroup-device BPF filter permits
  exact required device identities, those two loops, the fixture Binder nodes,
  and private devpts. Global loop-control and unrelated block opens must fail.
  Exact character 1:11 permits **MKNOD only** because actual Android init creates
  kmsg nodes; all read/write and combined operation bits remain denied. A real
  private-inode probe proves creation succeeds and RDONLY/WRONLY/RDWR opens fail.
  No host kernel log device is exposed. Capability bounds drop module loading,
  raw I/O, host boot and host time changes; a private failing modprobe bind also
  prevents accidental stock module calls.
- Stock discovery-name aliases point to the same three private Binder devices.
  A real installed `probeBinderDriver` call must leave the BinderFS/control/node
  identities unchanged, preventing stock from stacking another BinderFS mount.
- Private DBus has no activation directories. The actual packaged service and
  controller use conventional paths inside the fixture, including
  `/run/anvildroid/control.sock`. Desktop, bus, SSH, PipeWire and Pulse endpoint
  environment variables are cleared. Weston uses explicit headless/software
  arguments and `--no-config`; actual PipeWire/Pulse use minimal explicit Unix
  socket configurations with no device discovery, session manager or network
  listeners. Their owner UID and processes remain inside the bounded scope.
  A synthetic resolver replaces the conventional resolver symlink's private
  `/run` target; no host resolver socket is restored.
- The installed stock generators create fresh MAINLINE properties/configuration;
  explicit fixture software properties select the image's ANGLE/Pastel pair.
  Original stock lifecycle hooks and actual packaged integration remain in use.
  A final pre-start audit rejects external includes/hooks/namespaces, escaping
  cgroup paths and unapproved mount sources/targets after production rewrites.

## Lifecycle and evidence

The normal runner boots without a grant, claims Default through the production
controller as the actual desktop UID, and requires a complete stock Stop before
configuring shares. It then exercises RO, RW and removal across full stock
restarts. Each active grant requires production `mounted` readback, raw-data and
Android FUSE probes, and the maintained SDK28 Android Activity running under a
real app UID with granted storage permissions. RW writes must match exact host
fixture bytes; RO/removal must leave the fixture source unchanged.

A real helper retaining the production session mount namespace is used to
verify Stop/configuration stay locked until that helper exits, followed by an
actual retry. The maintained Rust core diagnostic exporter runs as the desktop
UID while Stopped and Running and checks opt-in logs, mode 0600 and overwrite
refusal. These are acceptance gates, not claims inferred from root shell probes.

Boot polling has a fixed 180-second deadline and tolerates early attach/mount
readiness errors only while init PID/starttime, private mount namespace, cgroup
and stock RUNNING status remain authenticated. Timeout captures bounded actual
properties, logcat and process state before teardown. Stop exceptions capture
matching process and service-thread namespace identities without exempting or
killing unknown holders.

Recorded final result: the normal full lifecycle passed on 2026-10-18 with
launcher SHA256 `b34cf225026d0e0d9bb7e985cb81c8c244876fd1b486695bce62886e8f91b0b0`
and Debian SHA256 `f75c765369015cc0d35289ba3e4a84c52e9e9f1ffb9505323cc3d37930359caa`.
The production desktop-owner claim, RO and RW raw/FUSE/app probes, retained
helper rejection/configuration lock, recovery, removal and every final stock
Stop passed. All four real core exports passed independent content inspection,
including Running/Stopped sections, log opt-in, redaction, owner UID, mode 0600
and overwrite refusal. Final teardown positively confirmed an empty delegated
scope and detached task loops. Small evidence, original ownership/mode/hash
metadata and the exact launcher are archived under the ignored
`target/feature-verification-20261017/default-shares/final-pass-f75c765/`.

Earlier real runs exposed GLib/Binder threads retaining a session namespace.
The production fix releases unreachable Binder wrappers after stock Stop and
uses asynchronous DBus completion to let the ordinary GLib loop finish native
cleanup. It preserves the strict namespace-holder guard: the real retained
helper still caused Stop and configuration changes to fail until it exited.
No nested event loop or holder exemption was used.

Removal revokes source access; an ordinary empty target directory can persist
in Android userdata. The final oracle requires an empty configured-share list,
no exact raw/backend/FUSE target mounts, no sentinel, and targets absent or
nonsymlink empty directories before the app probe. The app uses the original
source token, preventing a false negative from a changed comparison token. Its
real UID 10125 received ENOENT when reading the removed sentinel and EACCES
when writing; exact source bytes remained unchanged. The initial stricter
whole-directory-absence failure and its evidence remain archived separately.

This verifies disposable stock Default sharing and diagnostic export with
software rendering. It does not establish physical desktop input behavior or
gameplay on Default.

## Optional installed ARM fixture

When all three ARM preparation inputs are supplied, only a production-pinned
libndk archive and the two diagnostic APKs are additionally copied. Their exact
hashes and the verified fixture overlay manifest bind the reviewed preflight to
the live run. The fixture provisions its own installed libraries/properties;
it never copies the personal Default overlay or userdata and does not extend
the product's Default installer API. Pinned helper imports disable Python
bytecode creation to keep extracted-package provenance exact.

The optional lifecycle replaces share/diagnostic phases with actual controller
disable/restore while stopped. ARM32/64 JNI passes before/after, fresh ARM-only
install fails while disabled, the test-app marker survives, and all ARM and
unrelated properties/configuration match after the final restored boot/Stop.
App-private test values are checked in-guest and are not exported. The run
passed on 2026-10-18; its evidence is under
`target/plan-completion-20261018/default-arm/`, with an empty scope and positively
detached task loops. See `README.md` for the maintained probe build and commands.
