# Pinned NVIDIA fork payload

The guest Venus/gralloc base uses `collyn/waydroid-nvidia` commit
`d028cec0904514059227e6cc533ddb2bd563182c`, from the successful
[CI run 38244475616](https://github.com/collyn/waydroid-nvidia/actions/runs/38244475616).
ANGLE is built at fork recipe commit
`fa5044cd1804453a5cbe408191bb19279624e022`
([CI run 38284147292](https://github.com/collyn/waydroid-nvidia/actions/runs/38284147292)).
The host renderer comes from commit `adba12650c099874972f095e24625a4756bb7a22`
([CI run 38338813236](https://github.com/collyn/waydroid-nvidia/actions/runs/38338813236)).
HWC comes from commit `5c5d35d11aa5a513560d30f55bc2c16023008c13`
([CI run 38547350782](https://github.com/collyn/waydroid-nvidia/actions/runs/38547350782));
the guest HWC job succeeded. HWC checks framebuffer-target modifiers as
well as app layers. On AMD/Intel desktops that cannot import NVIDIA tiled
buffers, the host copies them with Vulkan into three release-tracked LINEAR
presentation buffers in system memory. Android renders into tiled NVIDIA
buffers; no CPU frame readback is needed on this supported path. Unsupported
formats or hosts retain the existing wl_shm fallback.
The payload version is `fork-window-transition-5c5d35d`.

The local `hwc-frame-sync.patch` is a candidate on top of that exact pin.
It waits for producer fences before GPU/SHM copies and includes the DMA-BUF
device/inode in the import-cache key, clearing retained raw references when
an allocation changes. These prevent reading an unfinished frame and reusing
an old allocation when Android recycles a native_handle address. The active
payload pin is unchanged until live validation of the candidate.

`check-frame-sync.py <hwcomposer-source>` compiles the actual cache, copy and
metadata functions with a controlled producer/transport. It covers GPU and
SHM copy ordering, fence failure/ownership, recycled CROS/GBM allocations and
retained references. All three cases fail on the original pinned code.
An isolated build can be reproduced from existing pinned source/SDK caches:

```sh
python3 scripts/build-nvidia-hwc-frame-sync.py \
  --fork /path/to/waydroid-nvidia \
  --hwc /path/to/android_hardware_waydroid \
  --sdk /path/to/provisioned-hwc-sdk \
  --rootfs /path/to/unpacked-android13-libraries \
  --ndk /path/to/android-ndk-r29
```

This writes a binary, provenance report and build log under
`target/nvidia-hwc-frame-sync/build`, without installing or restarting anything.
`python3 scripts/stage-nvidia-hwc-frame-sync.py` combines the candidate with
verified cached base archives, stages all 14 ELFs and generates a matching
controller module/manifest under `target/nvidia-hwc-frame-sync/staged`.
The candidate has its own version and records both the local patch and binary
hashes; it never overwrites the active payload or changes the production pin.

Managed and Default runtimes can also use Venus on X11. Startup uses a nested
Weston X11 compositor for guest presentation; the private Venus server still uses the
NVIDIA Vulkan ICD for Android rendering. Weston tries GL, then pixman if GL
cannot start. HWC uses its wl_shm fallback when DMA-BUF import is unavailable;
this can add presentation overhead without changing the Android GPU selection.
The same payload, driver, DRM modeset and udmabuf checks apply on both desktops.
Nested Weston startup/protocols are tested with Xvfb; NVIDIA presentation on a
physical X11 desktop still needs live hardware verification.

`services/runtime-nvidia.py` pins the base source commit, the renderer, HWC and ANGLE recipe/upstream commits, six artifact
IDs and SHA-256 digests, the upstream SurfaceFlinger base archive, and all three
assembled archive hashes. No floating branch or latest-release lookup is used.

The fork includes RGB32 byte-pitch admission, Vulkan buffer/image external
memory declarations, compatible sysmem allocation on hybrid displays, and
Android 13 libc++ compatibility with NDK r29, and a private Wayland configure
queue for safe window construction. Window decorations and runtime
lifecycle are coordinated with AnvilDroid's own bridge/controller.

Before a task resize, the bridge arms HWC's optional window transition ABI.
HWC retains the existing xdg_toplevel, CSD, position and focus while Android
recreates its rendering activity or task. A unique replacement task of the
same package takes over that window. Missing content retains its imported
Wayland buffers without CPU screenshots. Close cancels retention; ordinary
non-resize task removal still closes immediately. An eight-second grace period
can be renewed by a live Android task, bounded to 60 seconds to avoid stale
windows. Simultaneous tasks/windows of the same package are never merged.
Stock HWC without the ABI continues to use the bridge's profile restoration.
The fork documents the ABI in `docs/window-transitions.md`.

The bridge observes the pinned HWC's exported `window::apply_content_frame`
method to translate Android task coordinates into the window's local frame.
This keeps shadow padding outside the content rectangle, preventing a black
strip below the header and clipping of the app's bottom rows. It matches the
window through listener context without inspecting C++ object layouts.

## Reproduce and install

```sh
# Download requires GitHub CLI authentication for Actions artifact access.
python3 scripts/prepare-nvidia-fork.py --download
python3 scripts/build-nvidia-payload.py

# Inspect the staged tree in target/nvidia-fork-window-transition-5c5d35d first.
# Install a new root-owned payload; existing payload directories are retained.
sudo python3 scripts/install-nvidia-payload.py target/tools/nvidia
```

The preparer verifies the actual bytes of each input before reading archive
members, accepts only expected regular files, validates all 14 ELF machines,
and emits deterministic archives. It verifies every output against the backend
pin before publishing. SurfaceFlinger comes from the checksum-pinned upstream
v0.1.2 archive; its older HWC and ANGLE are excluded. ANGLE comes from the
separately pinned dual-ABI fork artifact, applied last so fallback binaries
cannot replace it.

Both Android ANGLE configurations enable `angle_has_astc_encoder = true`.
This is the upstream CPU ASTC-to-RGBA upload fallback; GPU rendering remains
on NVIDIA. Android otherwise omits the decoder, which breaks games shipping
ASTC-only atlases (missing art, stretched text and Cocos Error 3300). Venus
ASTC compute emulation remains disabled. Fork `tests/astc-upload.c` verifies
actual uploads and sampled pixels for all 28 LDR/sRGB formats in both ABIs.

Build/package scripts can reuse the verified assembled archives without
network access or GitHub authentication. Cold builds use the pinned CI inputs;
Actions artifacts have retention limits, so retain this cache until a permanent
fork release is published and pinned. There is currently no fork release URL
for this development payload.

Only a stop/start loads the new payload into an existing worker. Do not overwrite
an active payload or relax manifest verification. Retain the previous immutable
payload and installed module backup for rollback.

```sh
python3 -m unittest discover -s tests -p test_runtime_nvidia.py
python3 -m unittest discover -s tests -p test_nvidia_fork_payload.py
```

Live integration evidence is recorded in
`target/diagnostics/nvidia-fork-20261012/REPORT.md`.
`hwc-pitch-bytes.patch` and `scripts/repack-nvidia-hwc.py` are the frozen
historical recipe for `devfix-20261012-pitchbytes`, not the active fork build.

GPU presentation regression tests live in the fork: `run-present-wire-test.sh`
checks release-ring admission and fd ownership/disconnect; `run-present-copy-test.sh`
checks changing pixels across all slots and three resolutions on NVIDIA plus
AMD/Intel. The latter also accepts `PRESENT_TEST_SOCKET` to exercise the actual
renderer protocol. Local Vulkan validation and both CI component builds passed.
The controlled launcher scroll comparison measured 43.7% jank / 72.6 ms median
before versus 0% / 1.8 ms after; this sample does not guarantee every app's FPS.

## AppArmor Venus IPC

Android's `android_app` complain profile can audit every Venus socket read/write
when `/run/anvil-venus/venus.sock` lacks a file permission rule. This adds work
to Android IPC and auditd even though operations succeed. The controller
installer and Debian post-install hook now run `install-runtime-apparmor.py`.
It preserves existing local policy, adds only this socket's `rwk` access to
`/etc/apparmor.d/local/android_app`, validates the policy, and reloads
`android_app` without restarting Android or changing profile mode. Existing
installations can run `sudo python3 scripts/install-runtime-apparmor.py`.

The helper is idempotent, rejects unsafe policy paths, and restores the previous
local file if validation/loading fails. Hosts without the Android AppArmor
profile need no override. Application data and auditd configuration are untouched.

The per-runtime renderer starts with both `--no-fork` and `--multi-clients`.
All clients are inside that worker's private Android/socket namespace. This
keeps one Vulkan allocator across HWC connections: default per-connection
fork repeatedly created devices during new-window presentation (measured
128-512 ms per setup), stalling Android buffer dequeue even with fast GPU
rendering. Per-runtime isolation, tiled producer buffers, and device-loss
shutdown remain in place. Multi-connection GPU pixel/lifetime checks pass.
