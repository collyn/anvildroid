# GAPPS and ARM acceptance — 2026-10-17

**PASS for the tested probes:** a separate disposable GAPPS guest launched the Play Store, executed ARM32 and ARM64 JNI code with each cached engine, retained working translation across restart and a rejected archive selection, and rejected ARM-only APKs after ARM was disabled. Its final corrected-harness shutdown reported `Stopped`, no error, no forced shutdown, and cleanup complete.

The guest was `r-dfeb5f745ea248bb973aebcb7146f72b` in `/tmp/anvil-live-compat-20261017`. It used a software ANGLE/Pastel renderer, a private native X11 display/socket/authority, private mount/PID/network namespaces, a separate production resource scope, and no external network. The personal runtime and installed controller were not used. The accepted package was copied into the private store as `accepted-package.deb`; SHA-256 `56dff645cb867b117e3694d0f7b0ff683e386d38a913aed93ac52068f4187ee9`.

| Verified cached input | SHA-256 |
| --- | --- |
| GAPPS system archive, `lineage-20.0-20260927-GAPPS-waydroid_x86_64-system.zip` | `1d79df0b17ab8f79d66fd3b5f94500cc02f1abe149cf8caa391380092ede6e2a` |
| Matching MAINLINE vendor archive | `d911b8353f6c807b94790b1c41c67e863a9f3dcd1cf3ec0232351398235afd7a` |
| Extracted `system.img`, 2,582,528,000 bytes | `be76fbdf8407c5e827a034059daf31f3b847bad296ef8369e9d00278831941f6` |
| Extracted `vendor.img`, 562,388,992 bytes | `5cc4267517ded28f1cf60a5d2b6e8b88b15d8520de1f55a39de7764e7929a877` |
| libndk_translation 0.2.3 archive | `a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0` |
| libhoudini 14.0.0 GoogleGame com1.3 archive | `2e82cdc88ddc4d418f7fb861aeebb7c49c83a91b97f57da10510b5e1146a4ed5` |
| ARM32 diagnostic APK | `3d3e1948ae2b279f70854fadb09c418c532691269b8e9c7800e51f7456e4bb44` |
| ARM64 diagnostic APK | `482a65d012f1d4a527bf54dd8ffcbbd15452c95d5d664e64c35bf3014b15b362` |

Archives were read from existing caches and extracted into new test-owned paths. No downloads or host ARM/binfmt installation were needed. The APKs contain only their respective ARM native library architecture, ELF machine 40 / 183. Their JNI probe loads `armprobe`, calls native code, checks the expected value, and writes/returns `ARM_NATIVE_EXECUTION_PASS`. Restart checks deleted the prior result before executing again.

| Generation | Configuration and actual result |
| --- | --- |
| `eff9a39753884c6fbfa948c12c2569c4` | libndk: ARM32 and ARM64 broadcasts and app result files passed; Play Store Activity visible/resumed/drawn |
| `428717c5ffec43a394c0dea68feda65a` | libndk after restart and rejected invalid archive: fresh ARM32 and ARM64 results passed |
| `ea082eb1f8ae4179a346e0d76da64ee2` | Switched to Houdini: fresh ARM32 and ARM64 results passed |
| `1aa8b189547c431e825e721ad6825fc4` | ARM disabled: both ARM-only APK installs rejected with `INSTALL_FAILED_NO_MATCHING_ABIS`; normal diagnostic app still launched; final clean shutdown passed |

The invalid-selection check supplied a valid ZIP with no translation payload. Production staging rejected it as incomplete; `arm.json` remained byte-identical and the previous payload verified successfully. The next actual boot and fresh JNI execution passed. This proves recovery from a rejected selection, rather than recovery from a running engine crash.

ARM configuration used the production `runtime-arm.configure` and atomic writer while holding the stopped guest's exclusive worker lock. Actual APK installation, app actions, compatibility requests, and worker status used production authenticated APIs. This fixture did not drive the controller's graphical ARM settings panel or its owner-registration route; those are separate request-validation tests.

With ARM disabled, the guest reported bridge `0` and ABIs `x86_64,x86`. Both actual native-only APK installations failed with PackageManager error `INSTALL_FAILED_NO_MATCHING_ABIS`, rather than inferring disable behavior from properties alone. The previously installed JNI probes had been uninstalled before this check.

Google package detection found `com.android.vending`, `com.google.android.gms`, and `com.google.android.gsf`. Launching Play Store produced `com.google.android.finsky.unauthenticated.activity.UnauthenticatedMainActivity`, with an actual process, resumed Activity, visible window and `reportedDrawn=true`. GSF Android ID was `null`, with the service reporting that GSF had not created one. The guest was offline and no account was used, so this does not establish sign-in, registration, Play Integrity, purchasing, downloads, or internet connectivity.

The same guest also passed actual per-app compatibility persistence and two-runtime isolation checks documented in [APP-COMPATIBILITY-ACCEPTANCE.md](APP-COMPATIBILITY-ACCEPTANCE.md). Its generated Android ID stayed `ee06d3f7265326ac` across the recorded boots.

Early shutdowns reported `Error` and `cleanup_complete=false`: the original disposable harness's PID 1 did not reap adopted Android zombies. Those exits are not counted as clean shutdowns. The harness was corrected without changing production shutdown behavior. The final generation then reported `shutdown_forced=false`, `shutdown_method=framework_flush`, `cleanup_complete=true`, and `state=Stopped`. Parent diagnostics acceptance subsequently used that true stopped state.

Generated evidence remains locally under `target/acceptance/2026-10-17-{gapps,arm,compat,isolation}-*`, with the final result in `2026-10-17-gapps-final-stopped-status.json`. The test-only adapters were archived there as well. After acceptance, metadata, final worker status and small logs were archived under `2026-10-17-gapps-cleanup/`. Cleanup checked the exact runtime/root/generation identity, backend quiescence, namespace removal, absent mounted test paths and empty scopes before deleting only the private store and extracted test images. It recovered 9,887,903,744 bytes; cached archives and original `/var/lib` images were preserved. The parent owns restoration of the temporary inotify budget used during Android boot.

These are small JNI and app API probes. They do not certify arbitrary ARM apps, games, DRM/anti-cheat, hardware rendering, or desktop Wayland behavior.
