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
