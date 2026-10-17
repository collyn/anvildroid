# Experimental NVIDIA payload

AnvilDroid pins `collyn/waydroid-nvidia` commit
`d028cec0904514059227e6cc533ddb2bd563182c` (`fork-d028cec`). The host renderer,
HWC, gralloc and both Venus ABIs come from successful CI run `38244475616`.
ANGLE and the unused SurfaceFlinger payload come from verified upstream v0.1.2
prebuilts. No upstream installer or patched Waydroid Python tools are executed.

All five Actions artifact IDs/digests, the source commit and the three assembled
archive hashes live in `services/runtime-nvidia.py`. The preparer rejects unsafe
archive members, verifies exact inputs and validates 14 x86/x86_64 ELF files.
The installed payload must be root-owned and non-writable by other users;
launch checks provenance and hashes again.

```sh
python3 scripts/prepare-nvidia-fork.py --download
python3 scripts/build-nvidia-payload.py
sudo python3 scripts/install-nvidia-payload.py target/tools/nvidia
```

The preparer requires authenticated GitHub CLI access to download Actions
artifacts. Verified assembled archives are cached under `target/tools/nvidia`
and allow package builds without network/authentication. Actions artifacts have
retention limits; retain the cache until a permanent fork release is pinned.
This development integration does not assume a nonexistent fork release URL.

Release CI prepares the archives once and shares them with all package jobs.
It uses `github.token` by default; if cross-repository artifact access is denied,
set the `NVIDIA_ARTIFACT_TOKEN` repository secret to a token with read-only
Actions access to `collyn/waydroid-nvidia`.

Payload root: `/usr/local/lib/anvildroid-controller/nvidia-fork-d028cec`.
Native package staging copies the path derived from the active backend pin,
rather than hardcoding an older payload version. Guest libraries are bind-mounted
read-only only inside each selected worker. The renderer/socket share that
worker's private namespaces. Existing workers need stop/start to load the new
payload; old immutable payloads are retained for rollback.

No NVIDIA driver installation or global Waydroid changes are made. Runtime
validation layers remain opt-in; CPU-readable capture stays on the udmabuf path.
See `native/nvidia/README.md` for reproduction and
`target/diagnostics/nvidia-fork-20261012/REPORT.md` for live integration evidence.

Managed desktop runtimes also admit SDK 34–36 images with the verified
Waydroid per-app HIDL composer contract. These retain the image's own HWC,
HWC2 adapter and SurfaceFlinger: the pinned Android 13 composer is not ABI
compatible with newer private graphics libraries. ANGLE, Venus and the GBM
wrapper still render on NVIDIA. Missing ANGLE mount filenames are created
only in the worker's private overlay; source images remain unchanged.

For this modern-image path the bridge enables
`waydroid.anvil_gpu_copy_presentation=true`. It intercepts single-plane tiled
NVIDIA DMA-BUF imports and uses the pinned Venus host copy protocol to present
through three LINEAR destinations supported by the desktop compositor. The
producer remains tiled on NVIDIA; presentation uses a GPU copy without EGL CPU
readback. Copy occurs at surface commit, after the native HWC's acquire-fence
wait. A destination is reused only after its Wayland buffer release.

The image-native HWC may clear its buffer cache on geometry changes. A separate
logical proxy lets the bridge retain physical presentation buffers until their
real releases and cache the GPU setup by source device/inode plus full layout
and display connection. Source FDs remain open to prevent allocation identity
reuse. Idle retention is bounded to 32 allocations / 192 MiB; buffers still
owned or read by the compositor are retained until safe to reclaim. Geometry
restores reuse the last copied destination, including when a copy is skipped.

Runtime status records `nvidia_composer` and
`nvidia_presentation=image-native-venus-gpu-copy`. This adapter requires a
single-plane 32-bit format with advertised LINEAR presentation support. Same-GPU
NVIDIA desktops keep direct tiled imports. The Android 13 fork continues to use
its native copy implementation. Live Android 16 measurements are recorded in
`target/diagnostics/nvidia-presentation-20261017/REPORT.md`.
