# Runtime shared folders

Managed runtimes and an integrated Default runtime support up to 16 explicitly
selected directory grants. Sources
must be owned by the runtime's desktop owner and be non-sensitive subdirectories
of their home. Home itself, symlinked path components and reserved credentials or
application configuration directories are rejected. No folder is shared by default.

Open **Runtime Settings → Storage → Shared folders**. Stop the runtime,
choose a source, name it, and select read only (the default) or
read/write. Start the runtime to activate the grant at
`/sdcard/AnvilDroid/<name>`. Android storage permission and image media/FUSE
behavior still govern whether an app can browse this location. Read/write also
requires the original directory permissions or ACL to allow the Android storage
service to write; AnvilDroid never changes host source ownership or permissions. Stop the runtime
before editing or removing a grant; stopping destroys its private mount namespace
and revokes active access. Removal persists revocation for subsequent boots.
Already copied files cannot be recalled.

The controller authorizes the runtime owner and serializes updates against boot.
Configuration is atomic, private and scoped to that runtime. Startup opens each
source as the desktop user, pins the directory descriptor, and checks its stored
device/inode identity. Moving/replacing a source requires selecting it again.
The worker rejects host/shared mount namespaces and nested source mounts. Before
Android starts, it walks the fixed data/media target without following links,
uses a nonrecursive bind, and remounts with nosuid,nodev,noexec plus actual ro or
rw. Mount status is reported separately from configuration; failures never imply
successful activation. Failure to mount one grant does not grant a broader source.

Android's storage service can create a nonrecursive `/data/media` bind for its
FUSE backend after boot, omitting existing child mounts. A bounded helper pins
the Android process/starttime, runtime generation, userdata and share identities,
then mirrors each verified grant into that backend. It uses Linux's `open_tree`
and `move_mount` APIs (Linux 5.2 or later), with nonrecursive descriptor-based
clones that preserve RO/nosuid/nodev/noexec flags. Unsupported APIs or a missing
backend produce an activation error; no weaker fallback is attempted. Configured
and raw preboot binds are not reported as mounted until backend readback succeeds.

Default requires AnvilDroid management enabled for its desktop owner and the
current system integration (`runtime-system.py`, integration version 2). After
upgrading the package, stop Default and restart its container service before
configuring shares. Storage shows the missing prerequisite when the old service
is still running or integration is absent. Saved entries remain visible but
cannot activate through an unintegrated stock service.

The system integration creates a private service mount namespace and a fresh
namespace for each Default session, including ordinary Waydroid service starts.
Default grants are stored per desktop owner, bound to the verified userdata
device/inode, and locked throughout startup and the active session. Userdata and
share destinations are pinned with descriptors; generated LXC configuration
contains only root-owned staging paths. Actual guest mount identity and flags
are verified before reporting a share mounted. Its service watch waits for the
FUSE backend without blocking stock startup, with bounded reconciliation attempts
and a 300-second readiness deadline. The source is never mounted into
the host session's namespace.

Stopping must verify that Android, hardware callbacks and namespace-holding
helpers have exited before discarding the session namespace and releasing its
configuration lock. Default waits for deferred Binder cleanup through the normal
service event loop and reports success only after that lock is released.
If cleanup cannot verify this within its bounded wait,
changes remain locked; resolve the reported stop failure and retry Stop.
Cross-thread OTA remount callbacks are rejected while this integration owns the
mount lifecycle. Perform image upgrades with Default stopped. Failed mount
verification never turns a configured grant into reported active access.

Revocation removes access to the chosen source. An empty mountpoint directory
may remain in Android's own userdata after removal; it does not retain the grant.
The integration does not recursively delete Android files at that location.

The source folder is a directory grant: explicitly choose a folder containing only
files Android should receive, and avoid adding secrets to it later. This retains
Waydroid's privileged guest trust model: directory grants and read-only mounts
do not establish isolation against a hostile rooted Android guest that can
change mounts. The tested read-only guarantee applies to ordinary file writes
through the configured mount.

Automated tests exercise real isolated RO/RW mounts, recursive userdata bind
inheritance, revocation, two numeric desktop UIDs, actual permission/ACL checks,
source replacement and lifecycle failures. A real nonrecursive backend fixture
also verifies reconciliation reaches an already existing slave mount namespace.
Packaged managed-runtime acceptance on Android 13 verified raw and FUSE reads,
RO writes rejected with EROFS, and RW writes reaching the host source. An SDK28
diagnostic app with granted storage permissions independently verified the same
behavior. Removing the grant while stopped and restarting denied app and FUSE
access without changing the source; every acceptance stop completed cleanup.
Per-cycle package hashes and runtime-module byte parity are recorded with the
acceptance evidence. These results do not establish behavior for other images or
scoped-storage apps.

The complete stock Default lifecycle also passes on copied Android 13 x86_64
Vanilla images and package `f75c7653…`: RO/RW through raw storage, FUSE and a real
SDK28 app UID, removal/restart, and successful stock Stop with baseline namespace
and unlocked configuration. A retained namespace helper correctly rejects Stop
and edits until it exits; retry then completes. Removal leaves only an ordinary
empty userdata directory: no source sentinel or share mount remains, app reads
fail with ENOENT, app writes fail with EACCES, and the source stays unchanged.
The isolated scope is empty and copied-image loops are detached after the run.
See [Default acceptance](../tests/acceptance/DEFAULT-ISOLATION.md) for reproduction
and the exact provenance. Source permissions and ACLs still apply, and
AnvilDroid does not chmod the selected source.
