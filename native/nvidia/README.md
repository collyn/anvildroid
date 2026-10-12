# Local NVIDIA HWC pitch fix

`hwc-pitch-bytes.patch` is generated from the Android 13 HWC source used by
`devfix-20261010-vkr02726`. It corrects the hybrid 256-byte DMA-BUF pitch
constraint: `buffer_metadata.pixel_stride` counts pixels, so 32-bit RGB needs
64-pixel alignment. The old 256-pixel check sent valid 1920/1088-pixel strides
through `glReadPixels` and CPU conversion every frame. Unknown formats retain
the conservative fallback. Acquire fences and the unsupported-buffer fallback
are unchanged.

Base `hwcomposer/hwcomposer.cpp` SHA-256:
`0169dcbcc355a3e16e763703b0e9037fc15db1bf60e890870379434f7a649593`.
The remaining source and headers are from the existing
`waydroid-nvidia-build/hwc-src` and `wnv` Android 13 build trees. Apply this patch
to an isolated copy of `hwc-src`, never to an active runtime.

The tested build copies `build/hwcomposer` to
`target/nvidia-hwc-pitch/recipe`, applies the patch in
`target/nvidia-hwc-pitch/src`, and runs its standalone `build.sh`. Use
`WNV=<source>/wnv`, `ROOTFS=<source>/wnv/image-rootfs`, NDK `/opt/android-ndk`.
Add `-nostdinc++ -isystem "$WNV/aosp_libcxx/include"` to CXXFLAGS so the
Android 13 libc++ headers match the guest, rather than mixing NDK headers with
the guest ABI config. The build outputs go to `target/nvidia-hwc-pitch/out`.

Reproduce the pinned archives from the built, stripped ELF:

```sh
python scripts/repack-nvidia-hwc.py target/tools/nvidia \
  target/nvidia-hwc-pitch/out/hwcomposer.waydroid.stripped.so \
  target/nvidia-hwc-pitch/repacked
```

The repacker verifies the three base archive hashes, replaces exactly one HWC,
fixes the gzip timestamp, and verifies every output against the new backend pin.
Host renderer and other guest binaries remain byte-for-byte the existing pin.
`install-nvidia-payload.py` stages a new immutable payload; do not overwrite an
active payload or relax manifest verification.

Regression validation:

```sh
python -m unittest discover -s tests -p test_nvidia_hwc_pitch.py
python -m unittest discover -s tests -p test_runtime_nvidia.py
```

The pitch test compiles the predicate from the shipped patch, checking valid
RGB pitches, invalid pitches/insets, zero metadata and unknown formats.
Live measurements and health checks are recorded under
`target/diagnostics/nvidia-performance-20261012/REPORT.md`.
