# v4.9 automatic FPS camera and M1 touchscreen fallback

This records code/package checks. **CrossFire acceptance for v4.9 has not yet
been performed.** Earlier v4.8 GPU/Luanti results do not certify this change.
The installed CrossFire investigation before this build was interrupted by the
game's AFK timeout; it is not a successful camera-lock test.

## Artifacts

| Artifact | SHA-256 |
| --- | --- |
| `anvildroid-controller_0.1.3+keymap.v4.9_amd64.deb` | `8dff7873b24008c16e4990bbda0dcc05b676fb5f21124347f4bf191299635344` |
| `anvildroid-controller-0.1.3+keymap.v4.9-1-x86_64.pkg.tar.zst` | `4ebe861ac0c3ac1de96fe229693ec3ff71d86e191448bf7553aec277e6baf63e` |
| Packaged `libanvildroid-window.so` | `4948c892f6446a26352987cd76dc9cfc2c82b4c92ac58083dd2970a18f64087e` |

The package suffix is `0.1.3+keymap.v4.9`; the workspace binary version remains
`0.1.3`. Verification uses the existing package verifier's extraction, payload,
binary and image-tool checks with those respective versions and compares the
packaged bridge and Python keymap module byte-for-byte with the build inputs.

## Host checks

- `scripts/test-runtime-features.sh`: 348 Python tests passed, 14 ARM tests
  skipped for their environment; 396 Node tests passed; 15 native suites passed.
  Log: `target/keymap-redesign/automatic-fps-checks-host.log`. The initial
  sandbox run could not satisfy existing UID/ownership fixtures; the successful
  run used host UID/socket support and temporary fixtures without accessing the
  installed runtime or desktop.
- Native/Python codec parity: 1,606 deterministic V4 profiles, zero divergences,
  plus four valid legacy fixtures. The new mode also has dedicated Python,
  native model/capture, storage round-trip and frontend validation regressions.
- Capture and editor UI tests passed ASan/UBSan; LeakSanitizer was disabled.
  These are fake compositor/Android-sink tests, not desktop or game evidence.
- Production Android bridge compilation and shared GUI/runtime build passed.
  Log: `target/keymap-redesign/automatic-fps-build.log` and
  `target/keymap-redesign/automatic-fps-native-final.log`.
- Debian and Arch payloads passed extraction, dependency/CLI routing, Android
  bytecode, Python modules, GPU payload manifests, packaged image tools and
  overlay compilation/signature checks. Log:
  `target/keymap-redesign/automatic-fps-package-checks-final.log`.

## Real game checks still required

On the installed GPU runtime with libhoudini and a fresh CrossFire session:

1. Select Camera automatic for an existing camera (new cameras already default
   to this), then Try/Save. Focus the game and move the pointer into its canvas.
2. Require an actual compositor lock and camera movement without a held key or
   mouse button. Verify M1 fires at its saved Fire position while captured.
3. Escape must restore the cursor and keep it free. M1 must touch/drag at the
   cursor, never Fire or relock. Holding Alt must provide the same touch access.
4. F5 / releasing Alt must restore capture. Switching windows must release
   contacts and must never send input into another app or the editor.

Loading the new bridge requires restarting the runtime; installing the package
alone does not replace a library already mapped by the running composer.
