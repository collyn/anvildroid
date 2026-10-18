# ARM translation versions

Open a prepared managed runtime's existing **Settings → ARM translation** row,
choose Engine and Version, then Download & install. Running runtimes use the
existing stop/apply/restart flow. Download, checksum, extraction or patch failure
retains the previous saved configuration. App data and earlier staged versions
are retained when switching engines.

The catalog follows waydroid-helper/extensions revision
`a2c9f98548a9f1aa9cf25f1ea56347e1e4930e29`: 13 upstream choices, plus the
previously supported unpatched HPE archive for existing installations.

| Engine | Source / versions | Android target | Upstream CPU target |
| --- | --- | --- | --- |
| libndk_translation | ChromeOS Zork R136-16238.47.0, R125-15853.53.0 | 13 / SDK 33 | Intel / AMD |
| libndk_translation | ChromeOS Guybrush R119-15633.69.0, regular and patched | 11 / SDK 30 | Intel / AMD |
| libhoudini | ChromeOS Octopus 39598, 39489 | 13 / SDK 33 | Intel |
| libhoudini | ChromeOS Brya 38795, regular and patched | 11 / SDK 30 | Intel / AMD |
| libhoudini | WSA11 38765, regular and patched | 11 / SDK 30 | Intel / AMD |
| libhoudini | HPE 14.0.0 GoogleGame com1.3, patched | 13 / SDK 33 | Intel |
| libhoudini | HPE 14.0.0b GoogleGame com1.2, patched | 13 / SDK 33 | Intel |
| libhoudini | HPE 14.0.0b GoogleGame com1.0 | 13 / SDK 33 | Intel |
| libhoudini | HPE 14.0.0 GoogleGame com1.3, legacy unpatched archive | 13 / SDK 33 | Intel |

Board/build names distinguish releases that share the same internal library
version. All listed NDK releases report library version `0.2.3`; that property
is not a ChromeOS firmware version. Unknown custom NDK artifacts no longer
claim `0.2.3`. The UI shows the package target and warns when it differs from
the runtime's detected SDK. Choosing another target remains an experimental
opt-in. Component reports also distinguish cached packages by target SDK.
CPU targets above describe upstream metadata, not a hardware compatibility
certification. Libraries, image versions and individual apps can differ.

## Verified variants

The five patched choices have their own immutable SHA-256 identities. Their
collector first verifies the original upstream ZIP, then replaces only its ZIP
comment with a deterministic AnvilDroid variant marker. Compressed members are
unchanged; output does not depend on the host zlib implementation. The result
must match the catalog's separate variant checksum.

The controller accepts only compiled-in patch recipes associated with those
known checksums. It checks every original byte at its exact file offset, applies
the replacement to `system/lib64/<bridge>`, and verifies the complete resulting
library SHA-256 before publishing a payload. A missing target, unexpected byte
or changed output fails installation. Staged manifests hash the patched bytes;
later verification requires the pinned patched-library hash too.

Guybrush, Brya and WSA11 use the same compatibility byte changes as the helper's
patched variants. HPE com1.2/com1.3 apply its expiration-check fix. The old raw
HPE digest remains explicitly labelled legacy/unpatched; it is never silently
reported as the fixed variant. No downloaded shell installer is executed.

Archive URLs, exact commits, upstream/variant checksums, offsets and patched
library checksums are in `services/runtime-arm-source.py`.

## Custom sources and scope

Use Import another version to open the existing source library. It accepts an
HTTPS ZIP URL with optional publisher SHA-256, local ZIP, or extracted folder.
A payload must provide both `system/lib/<bridge>` and
`system/lib64/<bridge>`, using `libndk_translation.so` or `libhoudini.so`, plus
the supporting ARM libraries and binaries. Upstream `prebuilts/` and bare
`lib/`, `lib64/` layouts are recognized. Mixed or incomplete engines fail.
Unknown artifacts show a checksum revision rather than an invented version.

Collection runs as the desktop user. The controller receives an open archive
FD and independently validates and caches the artifact for that user. Switching
publishes the new configuration only after staging and verification. Disable
clears advertised ARM ABIs without deleting the selected payload.

Support covers JNI app translation. Payload init scripts and binfmt handlers
are excluded; host-global ARM execution handlers are not installed. Default
system Waydroid retains its existing installed-provider disable/restore path;
use a managed copy to select these verified archives.

## Verification

Offline unit tests cover download pinning, separate variant identities,
original/output patch checks, failed-switch rollback, per-user imports and
Settings selection/restart behavior:

```sh
python3 -m unittest discover -s tests -p 'test_runtime_arm*.py'
sudo python3 -m unittest discover -s tests -p 'test_runtime_arm*.py'
sh scripts/test-runtime-features.sh
```

For real-archive installation acceptance, obtain the catalog's upstream ZIPs
as `<revision>.zip` in a local directory, then run:

```sh
sudo python3 tests/acceptance/verify-arm-catalog.py /path/to/archives --output target/arm-catalog.json
```

This uses a temporary controller store and no network, services, Android boot,
mounts or installers. It verifies all catalog choices through collection,
controller import, staging, root-owned file verification and configuration,
then checks corrupt-cache rejection, retained-version selection and
disable/restore. On 2026-10-18 all **14/14** choices passed; the fixture was
removed. Evidence: `target/arm-versions-verification/catalog.json`.
Live acceptance also passed **7/7 Android 13 catalog choices**, each with actual
ARM32 and ARM64 JNI arithmetic execution: both Zork releases, HPE com1.3 patched,
com1.2 patched, com1.0, and both Octopus releases. Each iteration removed the prior
result, force-stopped the probe app and required a fresh JNI result. These used
a copied Vanilla Android 13 image, software rendering, isolated networking and
fresh mount/PID/network namespaces with a 4 GiB / 4 CPU limit. The worker and ARM
configuration helpers ran from Debian package SHA-256
`db28b10cd9c7345bcda8794049f478bb8ee43ff37ba14f53d145fb04e0f03803`.

Evidence is under `target/arm-versions-verification/live/`: `summary.json`,
per-version broadcasts/worker logs, `final-stopped.json`, `fixture-metadata.json`
and `cleanup.json`. Every boot ended with framework flush, `Stopped`, complete
cleanup and no forced shutdown. Final checks found empty scopes, no worker,
no host mount and no attached test image loop before removing the fixture.
The first runner attempt recorded JNI success but checked an asynchronous stop
acknowledgment too early; its failed oracle remains in `first-run-early-stop.json`.
The corrected full seven-version run uses the final worker result.

Android 11 variants have verified installation but require separate live tests
with Android 11 images. Small JNI probes do not certify game compatibility,
performance or a physical desktop/Arch deployment. Default's archive selection
workflow and the graphical managed installer are not covered by this live matrix.

Sources:

- https://github.com/waydroid-helper/extensions/tree/a2c9f98548a9f1aa9cf25f1ea56347e1e4930e29/arm_translation
- https://github.com/supremegamers/vendor_google_proprietary_ndk_translation-prebuilt
- https://github.com/supremegamers/vendor_intel_proprietary_houdini
- https://github.com/waydroid/waydroid/issues/788#issuecomment-2162386712
