# Controls environment and recovery acceptance — 2026-10-19

This follows the [offline game acceptance](GAME-ACCEPTANCE.md) for remaining-work
item 2. Tests use copied images and disposable managed or stock Default
installations. All input enters a private Xvfb/XTest display, the production
native X11 adapter or nested Weston, the shared Android bridge and the patched
scrcpy receiver. No installed personal runtime or physical desktop is a target.

## Managed Android matrix

| Image | Presentation | Editor/input | Held-input recovery |
| --- | --- | ---: | ---: |
| Android 13 Vanilla | Native X11 | 45/45 | 9/9 |
| Android 13 Vanilla | Nested Wayland | 45/45 | 9/9 |
| Android 13 GAPPS | Native X11 | 45/45 | 9/9 |
| Android 13 GAPPS | Nested Wayland | 45/45 | 9/9 |

The 45-case suite retains actual SOURCE_TOUCHSCREEN / TOOL_TYPE_FINGER event
oracles, graphical editor interactions, conflict/error states, Save/reload/
Cancel, visible marker pixels, resize/maximize, Android density and rotation,
task replacement, mouse touch, joystick, swipe, Look and capture checks.

Recovery starts with three actual simultaneous contacts: held Basic, joystick
and free mouse finger. Stopping only the disposable guest's HWC must produce
Android CANCEL and zero remaining contacts. After HWC recovery the app is
reopened; held buttons and stale releases must produce no new contact. Fresh
input must work. A full runtime Stop with three contacts held then requires a
new generation, rejected stale-generation actions, no replay, unchanged saved
profile and fresh three-contact delivery. This certifies recovery after app
reopen; it does not promise preservation of the old app surface.

Evidence is under `target/controls-recovery-20261019/`:

| Row | 45-case summary | Recovery summary |
| --- | --- | --- |
| Vanilla X11 | `vanilla-x11-final/summary.json` | `x11-v4/summary.json` |
| Vanilla Wayland | `vanilla-wayland-final/summary.json` | `wayland-v3/summary.json` |
| GAPPS X11 | `gapps-x11-final/summary.json` | `gapps-x11-recovery/summary.json` |
| GAPPS Wayland | `gapps-wayland-final/summary.json` | `gapps-wayland-recovery/summary.json` |

## Stock Default controls fixture

| Image | Presentation | Scenarios |
| --- | --- | ---: |
| Android 13 Vanilla | Native X11 | 22/22 |
| Android 13 Vanilla | Nested Wayland | 22/22 |
| Android 13 GAPPS | Native X11 | 22/22 |
| Android 13 GAPPS | Nested Wayland | 22/22 |

The Default adapter exercises 22 scenarios per image/backend: all seven V2
control types and simultaneous input; blur/held-key suppression; real editor
Save to V3, app replacement/reload and Cancel; marker pixel visibility without
interrupting playback; maximize/restore cancellation; V3 three-finger input;
HWC disconnect CANCEL and fresh input after reconnect; full stock Stop/restart,
new generation, no held-input replay, unchanged profile and fresh contacts.
The final stock Stop is required before reporting success.

Two fixture conditions limit this evidence. The private delegated cgroup is
mounted writable inside Android so init can create process groups and restart
Zygote after SurfaceFlinger. The inherited stock `cgroup:ro` mount prevented
process-group creation and left Zygote at `stopping`; recovery with that
read-only policy is **not certified**. Only the bounded fixture subtree is
exposed, with host ancestors inaccessible. This harness change does not alter
the installed stock service or product Default configuration.

Software Wayland selects stock
`persist.waydroid.cursor_on_subsurface=true`. The ordinary stock HWC
`wl_cursor` SHM copy crashed in `update_shm_buffer_default` after app replacement;
that upstream software-renderer path is **not fixed or certified** here.
Native X11 uses the compositor cursor. Both routes use private Xvfb/llvmpipe,
not a physical desktop or GPU.

Disconnect requests stock HWC Stop and, if it remains `stopping`, revalidates
and kills only that exact guest HWC process. Actual Android CANCEL is required.
Recovery explicitly starts HWC and restarts SurfaceFlinger because deliberate
Stop disables the automatic init `onrestart` action; init then restarts Zygote.
The observer is reopened after framework restoration. This verifies fresh
input recovery under those conditions, not automatic recovery from every
stock framework crash or preservation of an existing app surface.

Default evidence is archived under `target/controls-recovery-20261019/` in
`default-anvil-default-ctrl-1019-6a9f6a05` (Vanilla Wayland),
`default-anvil-default-x11-van-1019-a6935f7b` (Vanilla X11),
`default-anvil-default-x11-gap-1019-23eb059b` (GAPPS X11) and
`default-anvil-default-gapp-1019-a31120af` (GAPPS Wayland).
Each retains `controls/summary.json`, individual touch logs, marker pixels,
saved profile, reviewed preflight, lifecycle, teardown and cleanup audit.
The preflight pins the exact helpers used for each row. The final helper adds
bounded window rediscovery after X11 replaces a handle during restore; its
inert regression requires refocusing the replacement handle.

## Fixes established by live acceptance

Managed Android already cancels its contacts when the framework disconnects,
but its control receiver used to exit permanently. The worker now supervises
one child, reaps exited children and retries with bounded 1/2/4/8-second
backoff. Each launch obtains the current validated Android boot classpath.
Frozen and stopping guests are not woken to launch a receiver; teardown is
bounded. Four focused worker regressions exercise retries, one-child ownership,
frozen/stopping state and forced cleanup.

Default Desktop installation passed pre-serialized bytes to the JSON storage
writer, crashing before bridge installation. It now supplies a dictionary.
A maintained regression checks the real JSON backup manifest, original overlay
backup and installed bridge/keymap assets.

## Image and package provenance

Vanilla is copied from this host's existing stock image pair. GAPPS is the
official Lineage 20.0 Android 13 pair dated 2026-09-27, extracted from the
previously verified image cache. No images or game binaries are redistributed.

| Raw image | SHA256 |
| --- | --- |
| Vanilla system | `d2809f66124f5d99e1165466ebd8aa1dacfb82829937942d81134a0862117103` |
| Vanilla vendor | `5edafb92678c5abec7c8403c6873ce1de583009a57a6cb6de44d99f9b3f5c070` |
| GAPPS system | `be76fbdf8407c5e827a034059daf31f3b847bad296ef8369e9d00278831941f6` |
| GAPPS vendor | `5cc4267517ded28f1cf60a5d2b6e8b88b15d8520de1f55a39de7764e7929a877` |

GAPPS system/vendor archive SHA256 values are
`1d79df0b17ab8f79d66fd3b5f94500cc02f1abe149cf8caa391380092ede6e2a`
and `d911b8353f6c807b94790b1c41c67e863a9f3dcd1cf3ec0232351398235afd7a`.

| Artifact in `target/releases/` | SHA256 |
| --- | --- |
| `anvildroid-controller_0.1.3+controls.recovery2_amd64.deb` | `05384586aa9205f73328d032dc1778b5627b2e7ae7bad06b6951447bc41a7346` |
| `anvildroid-controller-0.1.3+controls.recovery2-1-x86_64.pkg.tar.zst` | `866fb6abd17d7ba5aa8eaa3d72ace172df4c0b8e32be0b49a0151da0a6e5cf38` |

The embedded GUI/CLI application version remains 0.1.3; `controls.recovery2`
identifies the package revision. Both extracted formats pass payload hashes,
Python compilation, native/scrcpy manifests, dependency and CLI-routing checks,
and packaged aapt/apksigner compilation/signing. No host installation is part
of this delivery.

Vanilla managed runs use package `controls.recovery1`, SHA256
`bd2f7dd1bbcddc821739c8905e98a2962cc0a42957781b19c0d05c8365b0e46b`.
Extracted-file comparison with recovery2 permits only the Default-specific
`runtime-existing.py` change; managed worker and native assets are identical.
GAPPS managed runs use recovery2. Exact parity is retained in
`target/controls-package-parity.json`.

## Regression and teardown evidence

The complete `scripts/test-runtime-features.sh` run passes: 311 reported Python
cases (five optional cases skipped), 390 frontend cases and 12 native suites,
including complete isolated package module staging/import. The worker suite
separately passes 33/33, including all four receiver lifecycle regressions.
Final logs are `target/controls-recovery-features-final7.log` and
`target/controls-worker-final2.log`. The feature run includes 32 controls-harness
and 22 Default-harness inert tests, including refusal to kill a replaced guest
HWC process during disconnect injection and rejection of broader automatic
mount policies. The script requires root-owned Default fixture directories;
the successful run uses `sudo` and the installed Node.js binary in PATH.

All four managed stores reached Stopped/cleanup_complete, quiescence and empty
scopes. All four Default stores reached final clean stock Stop and `complete`.
Cleanup verified no remaining owned process, host mount or copied-image loop,
plus unchanged Default host device/IPv6 identities, before archiving records
and removing only the exact disposable stores. The temporary copied GAPPS
source is separately hashed/audited before removal; its provenance is archived.
Cleanup reports and records remain under `target/controls-recovery-20261019/`.

## Supported scope and remaining manual checks

These rows cover Android 13 images with the Waydroid per-app composer contract.
The product also has a native X11 adapter for Default. Custom Android 14–16/CAR
images advertising the per-app contract require their own live image rows;
their SDK alone does not establish input compatibility. Images using the
explicit `android-display` presentation mode currently omit `keymap_editor`.
They must not be reported as passing the per-app editor matrix.

Physical GNOME/KDE/CachyOS input and window-manager behavior, other commercial
FPS/MOBA/ARM games, long gameplay, real GPU behavior and latency under load
remain manual acceptance gates. The previous Luanti result is one actual
offline game with bounded 30-second capture on managed Vanilla; these diagnostic
matrix rows do not extend its game result to every image/runtime. AppImage is
deferred; this branch is not a merged or published release.
