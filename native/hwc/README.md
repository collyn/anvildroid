# Experimental HWC display growth

Superseded for now: user selected stock HWC with a larger boot canvas instead.
Do not download Soong/platform sources or deploy this extension for that path.

User approved source-level HWC work on 2026-09-22. Nothing here is installed
into a normal runtime or included in Cargo release builds.

Source is pinned in `source-lock.json`; the downloaded checkout is
`target/hwc-source`. This is NOT the source revision of the installed binary.
Do not mix its C++ headers/layouts with the installed binary or patch offsets.

## Extension contract

Copy `anvildroid-display.inc` into that source's `hwcomposer/` directory and
include it immediately after `do_hotplug()` and before
`xdg_toplevel_handle_configure()` in `wayland-hwc.cpp`. Build the entire
`hwcomposer.waydroid` module, not an object linked onto the old binary.

The exported C ABI is:

```c
int anvildroid_hwc_grow_display_v1(void *original_window_context,
                                 int android_width, int android_height);
```

The caller uses the live original xdg callback context on the Wayland event
thread, outside the bridge mutex. It must calculate extents including task
offsets. Result `1` only means requested, `0` means HWC already has capacity;
neither confirms SurfaceFlinger. Negative errno means refused. Capacity only
grows, scale/density remain unchanged. Explicit size/padding properties and
missing hotplug callbacks are rejected, allocation has a conservative budget.

The disposable bridge is not connected to this API yet: it still tests the old
callback. Wire it with symbol/capability detection only once the new HWC builds.
Never fall back to the old callback and label it dynamic-display support.

## Build prerequisite (currently missing)

An Android/Lineage 20 compatible platform build tree is required, including
Soong, vendor Waydroid HIDL generated headers, libhardware/libui, Wayland
protocol generation and the corresponding vendor dependencies in Android.bp.
A desktop compiler or Android SDK/NDK alone is not an ABI-correct substitute.
No full platform tree or Soong environment was found in the inspected project,
`/mnt/nvme/android`, `/opt` or the candidate user Android locations.

Before building: select the platform tree/product, verify its manifest against
the runtime image, stage the pinned hardware module and extension in a separate
worktree, then use that tree's configured module build. Do not sync a large
platform tree or modify an existing Android build checkout without agreement.

## Verification status

- Host test `tests/hwc-display.cpp` compiles the actual extension against a
  minimal HWC model: guards, grow-only axes, rounding and unchanged scale.
- Android module compilation/linking, symbol export verification and live SF
  hotplug acceptance: NOT RUN (platform build environment missing).
- Next live experiment must substitute the new HWC only in a disposable
  runtime, record its hash, exercise growth, and confirm SF projections/modes
  and input before integrating task resize. Do not call host tests an ABI test.
