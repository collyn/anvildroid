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
are verified before reporting a share mounted. The source is never mounted into
the host session's namespace.

Stopping must verify that Android, hardware callbacks and namespace-holding
helpers have exited before discarding the session namespace and releasing its
configuration lock. If cleanup cannot verify this within its bounded wait,
changes remain locked; resolve the reported stop failure and retry Stop.
Cross-thread OTA remount callbacks are rejected while this integration owns the
mount lifecycle. Perform image upgrades with Default stopped. Failed mount
verification never turns a configured grant into reported active access.

The source folder is a directory grant: explicitly choose a folder containing only
files Android should receive, and avoid adding secrets to it later. This retains
Waydroid's privileged guest trust model: directory grants and read-only mounts
do not establish isolation against a hostile rooted Android guest that can
change mounts. The tested read-only guarantee applies to ordinary file writes
through the configured mount.

Automated tests exercise real isolated RO/RW mounts, recursive userdata bind
inheritance, revocation, owner checks, source replacement and lifecycle failures.
Android app access through media/FUSE and image-specific permission prompts
still require acceptance on the selected image; these tests do not boot Android.
