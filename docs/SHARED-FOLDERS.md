# Runtime shared folders

Managed runtimes support up to 16 explicitly selected directory grants. Sources
must be owned by the runtime's desktop owner and be non-sensitive subdirectories
of their home. Home itself, symlinked path components and reserved credentials or
application configuration directories are rejected. No folder is shared by default.

Stop the runtime, choose a source, name it, and select read only (the default) or
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

The default system Waydroid runtime is unsupported: AnvilDroid does not own its
mount namespace or startup lifecycle, so it cannot safely isolate or revoke grants.
No system runtime mounts or host mount operations are attempted for that runtime.
The source folder is a directory grant: explicitly choose a folder containing only
files Android should receive, and avoid adding secrets to it later. This retains
Waydroid's privileged guest trust model: directory grants and read-only mounts
do not establish isolation against a hostile rooted Android guest that can
change mounts. The tested read-only guarantee applies to ordinary file writes
through the configured mount.
