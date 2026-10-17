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

Current recorded result: an uninstrumented real stock Default boot reached
`sys.boot_completed=1` in about 18 seconds and the production desktop-owner
claim succeeded. The following stock Stop correctly failed closed because
persistent service threads named `pool-spawner` and `Thread-3 (servi...)` retained
the session mount namespace. A subsequent package moved the GLib pool spawner
to the baseline namespace; an ordinary rerun still found the second holder.
A separate bounded, read-only GDB diagnostic identified that holder's native
stack as a libgbinder polling thread. Its backtrace and original file metadata
are preserved under `native-gbinder-backtrace/`; the diagnostic detached and
teardown positively confirmed an empty scope and detached task loops. This
instrumented attempt is diagnostic evidence only. A further ordinary package
with unreachable Binder-wrapper collection booted in 12 seconds but still hit
the strict Stop holder deadline. Its post-error snapshot contained only the
service owner thread, which that guard excludes; the exact holder at the
deadline is therefore unresolved, and teardown again confirmed an empty scope
and detached loops. No RO/RW/removal or Default diagnostic gate is
claimed complete until that production lifecycle issue is resolved and the
entire normal run passes. Evidence is under the ignored
`target/feature-verification-20261017/default-shares/` directory.

Earlier failed fixture attempts and bounded syscall traces are retained as
diagnostic evidence. Tracing proved Android's exact kmsg MKNOD requirement;
the later traced run advanced into second stage but inherited diagnostic file
size limits caused SIGXFSZ and cannot establish an acceptance result. Tracing,
wrappers and those limits have been removed from the maintained normal runner.

Every completed attempt has verified an empty task scope, authenticated detach
of only its own copied-image loops, unchanged original device metadata and
unchanged host IPv6 values. Forced namespace disposal is reported as containment
cleanup; it does not substitute for the successful stock Stop acceptance gate.
The launcher never stops/restarts the personal Default runtime, writes host
sysctls, loads/unloads modules, or changes host services. One disposable guest
slot is used at a time. Real audio, gameplay/performance, user desktop/GPU and
provider compatibility acceptance remain separate work.
