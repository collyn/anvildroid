# Device identity profiles

Open a runtime's existing **Settings → Device identity**. Choose one of 26
device presets, or choose **Custom device** to edit brand, manufacturer, model,
device codename, product name and an optional Android build fingerprint. Custom
starts with the selected preset's values. Selections and drafts belong to one
runtime. Both managed runtimes and managed Default expose this setting.

The catalog covers Google Pixel 5–9, Samsung Galaxy S20+–S24 Ultra, OnePlus,
Xiaomi, Redmi, POCO, Huawei, vivo, OPPO and ASUS ROG. These are identity presets,
not device firmware packages. Pixel 7 uses `panther`; Pixel 7 Pro uses `cheetah`.
Preset metadata and this implementation are maintained independently of
[waydroid-helper](https://github.com/waydroid-helper/waydroid-helper), whose
device selector informed the feature.

Saving a running or suspended runtime follows the existing stopped-only
settings flow: stop Android, save, start and wait for boot completion. A stopped
runtime stays stopped. Keep the GUI open during this sequence. Read-only Android
properties cannot reliably change within an already booted guest.

**Restore original** selects the managed image's native identity. For Default,
it restores the identity configuration that existed before AnvilDroid first
applied spoofing, including an earlier user configuration. Each Waydroid file
has its own saved originals; switching profiles does not replace that baseline.
Removing a profile fingerprint restores the corresponding original values.
ARM translation, ABI lists, graphics, app data and unrelated settings are kept.

Model spoofing changes the information apps read through Android properties.
It does not install OEM services, emulate hardware, change Android SDK level,
add ARM support or grant Play certification/Play Integrity. Presets without a
pinned fingerprint let Android generate it from the image and selected
identity; leaving this field empty does not guarantee the same fingerprint
after changing the model. Pixel 5 and Pixel 7 include pinned build fingerprints;
Custom allows editing or clearing them.
Apps can also identify hardware through mechanisms beyond these properties.

## Storage and integration

Managed instances save a root-owned `device-profile.json` alongside their
other boot settings. The common worker boot path merges identity properties
into its private `/vendor/waydroid.prop` for both Wayland and native X11. It
filters host-inherited identity properties, so a Default spoof cannot silently
become another runtime's device. Shared support files and Android image files
are not edited. A profile can be saved before Prepare and survives support
regeneration.

Default updates existing `waydroid.cfg`, `waydroid.prop` and
`waydroid_base.prop`, where present. Original values and the selection live in
the existing management metadata and participate in the same stopped-only
rollback transaction as the property files. Default must have management
enabled; changes are rejected while an import copies its configuration.

The owner-authorized IPC operations are `device_info {id}` and
`device_set {id, profile, custom, revision}`. Built-in/default requests use
`custom: null`. Custom accepts exactly the six fields above. Revisions include
runtime identity and relevant configuration; stale or cross-runtime writes,
extra fields/descriptors and active managed workers are rejected. Custom values
cannot specify arbitrary Android property keys, commands or paths.

## Verification

`python3 services/device-test.py` tests every preset, custom validation, boot
property generation, runtime isolation, pre-Prepare saving, ownership, stale
revisions, Default switching/restoration and rollback after a failed write.
Filesystem trust checks require real UID ownership rather than sandbox-mapped
root directories. Tests use temporary files and inert session/state adapters.

`sh scripts/test-runtime-features.sh` includes these checks, GUI regression
tests, boot settings lifecycle tests and package staging/import verification.
Rust controller and GUI tests cover typed requests and Default's operation
guard. Live evidence is recorded separately; passing inert tests does not
establish game compatibility or Play certification.

### Live check, 2026-10-18

A copied official Android 13 VANILLA image in the maintained disposable
namespace harness completed three clean boots: Pixel 7, Custom with no explicit
fingerprint, then original image. All 76 property checks passed. SDK 33, Android
release, ABI list and renderer stayed unchanged. The final stop acknowledged
framework flush and complete cleanup, and the worker lock was released.

Evidence is under `target/device-profile-live-{pixel7,custom,reset}.json`.
The pinned test package SHA-256 was
`c7a2a1a1e878d2c9e80e6f070d88daa1d2b52d4d01b1c91fb4e12d77e8c06195`.
This checks Android property behavior on the common managed boot path. Default
file transactions have inert coverage; this run did not restart the personal
Default runtime or verify a particular game/device-specific OEM feature.
