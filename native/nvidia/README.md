# Pinned NVIDIA fork payload

AnvilDroid uses `collyn/waydroid-nvidia` commit
`d028cec0904514059227e6cc533ddb2bd563182c`, from the successful
[CI run 38244475616](https://github.com/collyn/waydroid-nvidia/actions/runs/38244475616).
The payload version is `fork-d028cec`.

`services/runtime-nvidia.py` pins the source commit, five artifact IDs and
SHA-256 digests, the upstream ANGLE/SurfaceFlinger base archive, and all three
assembled archive hashes. No floating branch or latest-release lookup is used.

The fork includes RGB32 byte-pitch admission, Vulkan buffer/image external
memory declarations, compatible sysmem allocation on hybrid displays, and
Android 13 libc++ compatibility with NDK r29, and a private Wayland configure
queue for safe window construction. Window decorations and runtime
lifecycle remain in AnvilDroid's own bridge/controller.

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

# Inspect the staged tree in target/nvidia-fork-d028cec first.
# Install a new root-owned payload; existing payload directories are retained.
sudo python3 scripts/install-nvidia-payload.py target/tools/nvidia
```

The preparer verifies the actual bytes of each input before reading archive
members, accepts only expected regular files, validates all 14 ELF machines,
and emits deterministic archives. It verifies every output against the backend
pin before publishing. ANGLE and SurfaceFlinger come from the checksum-pinned
upstream v0.1.2 archive; upstream's older HWC is excluded.

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
