---
name: anvildroid-native-android
description: Use when editing AnvilDroid native code — native/bridge (HWC desktop bridge, CSD, keymap), native/x11 adapter, native/ime, native/compat, native/nvidia, native/input, native/shutdown, or the anvildroid-*.sh/.rc helpers that run inside Android — or when changing the keymap/controls profile format (ANVIL_KEYMAP V1-V4).
---

# AnvilDroid native and Android-side code

## Overview
Three very different targets live in `native/`. Pick the right rules first.

| Code | Runs where | Rules |
|---|---|---|
| `native/bridge`, `native/compat` (C) | inside Android HWC (LD_PRELOAD) | `clang --target=x86_64-linux-android33 -ffreestanding -nostdlib -Werror`. **No libc headers**: new libc calls need explicit `extern` declarations. No NDK, no AOSP. |
| `native/anvildroid-*.sh` | Android `/system/bin/sh` (mksh) | POSIX sh only: no bashisms, no `eval`, no arbitrary command execution, toybox applets only, atomic `mv x.tmp x` writes. `apps.sh` is a narrow queue, never a command interpreter. |
| `native/ime/android`, `native/shutdown` (Java) | Android | `javac --release 8` + pinned R8/D8. APKs must pass `scripts/apk-align.py check` (Android 11+ rejects unaligned resources.arsc). New `.java` → add to `scripts/build-shutdown.sh` list. |
| `native/x11` (C++17) | host, desktop user | `python3 native/x11/build-host.py`; glibc baseline 2.39 (build in ubuntu:24.04 container if host is newer). |

`native/hwc/` is **superseded/experimental**: not shipped. Do not wire the bridge to it or download AOSP/Soong sources. `native/arm-probe`, `touch-probe` are diagnostics.

## Invariants
- **Wayland/X11 parity** (`native/x11/backend-contract.md`): backend choice changes only host presentation and input transport. Policies (CSD, menus, resize, profiles, Full UI, keymaps, IME, scale) live once in the shared bridge — never duplicate them in the X11 adapter. Closing a window ≠ runtime Stop. Never fall back to Weston; never draw X11 decorations.
- **X11 buffer release**: import each DMA-BUF once; GPU copy to recycled texture; send `wl_buffer.release` only after the native fence signals (`x11_adapter_release=fence`). Never block the event loop in `glFinish`, never read pixels back to CPU, don't grow the 4 KiB protocol read batch.
- **DMA-BUF failures**: the Waydroid composer aborts on any dmabuf error — unimportable buffer gets `failed` / black placeholder, never a protocol error. Bounds-check planes.
- **NVIDIA**: payload pins (commits, artifact ids, sha256 in `services/runtime-nvidia.py`) are immutable; never relax manifest verification or overwrite an active payload.
- `scripts/build-native.sh` patches `init.waydroid.rc` by matching the composer service line exactly once — don't loosen it.
- Bridge changes ship in both `libanvildroid-window.so` and `libanvildroid-runtime-window.so` (same source, copied by `build-runtime-desktop.sh`).

## Keymap codec: change all sides together
Format `ANVIL_KEYMAP <ver> <runtime> <package> <count>` + numeric rows (4/9/19/31 fields for V1–V4; ≤64 bindings, ≤16384 bytes). Parsers that must agree:
- C: `native/bridge/keymap-schema.h`, `keymap-model.h`, `keymap-editor-ui.h`
- Python: `parse_keymaps` in `services/runtime-keymap.py`
- Rust: `crates/anvildroid-core/src/keymap_export.rs`
- JS: `src-frontend/keymap-library.js`, `keymap-drafts.js`, `keymap-geometry.js`
- Player: `native/input/mapping-player.c`

`tests/test-keymap-codec-parity.py` checks C vs Python only (corpus size asserted at 1606 — update if corpus changes). Rust/JS are covered only by cargo/node tests.

## Testing
```bash
sh scripts/test-runtime-features.sh         # compiles tests/*.c, keymap + parity tests
sh native/bridge/test-window-state.sh       # bridge host tests
python3 native/x11/host-check.py            # add --software-x11 for Xvfb
python3 tests/test-keymap-codec-parity.py
```
`scripts/test-native.sh` from docs/TECHNICAL.md **does not exist** — use the bridge scripts above.

Host tests use mocks. They do not prove live behavior (gameplay, real compositor, NVIDIA, SDK 36). Say so in your report instead of claiming it works; live checks (`tests/acceptance/*`, `window-regression-check.py`) need a running runtime and the user's desktop.

## Past bug patterns — test these
Input state across focus/blur, held keys, DPI change; CSD origin on maximize/resize/restore; cursor visibility; long Wayland socket paths (AF_UNIX 108 bytes); buffer stride/fences on NVIDIA.
