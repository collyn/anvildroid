# Proposed disposable Default integration acceptance

Status: design only. Do not invoke stock `do_start` until the launcher and its
isolation checks have been reviewed. Existing managed acceptance is a separate
runtime and must be stopped before this experiment to avoid resource pressure.

1. Validate a new absolute `/tmp/anvil-default-*` directory, root-owned 0700,
   without symlink components. Extract the freshly staged controller package
   here and record its digest. Copy image bytes into a fresh work tree; generate
   new Default configuration and LXC configuration against those copies. Do not
   copy personal userdata, active LXC state, host runtime ownership records, or
   the installed service's live `/run` state.
2. Assign a unique transient systemd resource scope *before* hiding `/run`.
   Use bounded memory/CPU and an explicitly delegated child cgroup. Record the
   scope, launcher PID/starttime and namespace identities. Use a PID1 supervisor
   with `--kill-child`, private mount/network/PID/IPC/UTS namespaces, and make
   `/` recursively private before mounting anything. Verify all identities
   differ from the host and fail before creating a service if they do not.
3. Overlay `/run` with a private tmpfs. This masks host systemd's private
   control socket, host DBus, network/sensor PID files, and personal session
   sockets. Do not bind host `/run/systemd/private` or host system/session DBus
   back into the fixture. Start a private `dbus-daemon` with an explicit local
   configuration and no host service-activation directories. Export its system
   bus address to both service and test clients.
4. Mount a private `/dev` tmpfs. Before covering `/dev`, record only required
   device numbers. Reuse `runtime-worker.private_device_node` to create new
   inodes for ordinary required devices, never bind original device inodes
   that stock Waydroid chmods. Mount a **new BinderFS instance**, allocate only
   fixture binder/vndbinder/hwbinder with the existing binder-control ioctl,
   and give stock configuration those names. Require binder support already
   loaded. Drop `CAP_SYS_MODULE` from the bounding set; do not invoke the managed
   `ensure_binder_filesystem` helper because it runs modprobe. Hide optional GPU,
   framebuffer, camera and DMA-heap devices; use software rendering initially.
   Stock ashmem probing may attempt modprobe, but the dropped capability must
   make it incapable of changing the host. Prefer a private failing modprobe
   executable bind too, with an explicit logged refusal.
5. Hide host `/sys` writable interfaces with recursively read-only mounts.
   Permit only the recorded delegated cgroup subtree to be writable, with no
   host-root cgroup administration. Use private procfs for the new PID namespace;
   mask global `/proc/sys` writes while allowing only private network sysctls
   required by `waydroid-net.sh`. Never write host pid_max or inotify limits.
   Preflight must prove the host `/run` control sockets are unreachable and
   representative original device inode/mode values are unchanged.
6. Bind fresh work, controller store, controller package assets, and service
   state into their conventional paths **inside this namespace only**:
   `/var/lib/waydroid`, `/var/lib/anvildroid-controller`,
   `/usr/local/lib/anvildroid-controller`, `/run/anvildroid-system`. Also provide
   a task-specific desktop home/profile and source grant owned by a numeric
   test UID. Configure a synthetic desktop session on the private DBus. All
   identity/readback functions must see the same private work and bus.
7. Start the repository `runtime-system.py` with the original outer namespace
   token. It should create its own private baseline/session namespaces using
   production hooks. Before sending Start, verify the actual service cgroup,
   bus address, package digest and work/device inode identities. Stock code
   must not see host systemd sockets or host Default work. Reject any escape;
   do not patch out lifecycle checks or replace production start/stop hooks.
8. Exercise management claim, RO grant, full stock Start, actual Android
   raw-data/FUSE/app read and refused write; stock Stop and verify revocation;
   RW reconfigure/restart and verify guest-to-host bytes; remove/restart and
   verify absence. Observe configured-vs-mounted status while Running/Frozen.
   Record Default service marker/session PID/starttime and namespace identity.
   Stop retry after a deliberately retained helper must keep the lifecycle
   lock until that fixture helper exits. Release helper and repeat Stop.
9. Teardown through private DBus, confirm Android STOPPED and service returned
   to baseline, then stop private service/bus. Confirm delegated scope empty
   before PID1 exits. Namespace disposal revokes mounts. Remove only the new
   fixture tree after no holders remain; no host pathname unmounts, recursive
   chmod, module unloads, host service calls, or personal-runtime operations.

Open implementation question: stock LXC's cgroup setup must be shown to stay
within the delegated fixture subtree on this distribution. Merely placing its
parent service in a scope is not proof. The launcher must inspect generated LXC
configuration and actual init PID cgroup before this can count as a safe real
Default acceptance run.
