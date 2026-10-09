# P6 input backend: Waydroid Helper and scrcpy

Source review: 2026-10-08. Decision: prototype scrcpy control-only injection
before implementing a new Android injection backend. Production mapping remains
disabled; this review is not a live input or performance acceptance result.

## Reproducible references

- Waydroid Helper commit `17f5f326de9facdbb3d9f6b02354a4dcb0f628fd`:
  <https://github.com/waydroid-helper/waydroid-helper/tree/17f5f326de9facdbb3d9f6b02354a4dcb0f628fd>
- scrcpy v3.3.1, commit `f01231dff8294fe2c99045a4f9a14b233a71bb86`:
  <https://github.com/Genymobile/scrcpy/tree/f01231dff8294fe2c99045a4f9a14b233a71bb86>

Waydroid Helper is a separate project, not evidence of an official Waydroid
key-mapping API. Its README explicitly credits scrcpy as the backend.

## Verified source path

In Waydroid Helper:

1. `controller/app/window_input_router.py` captures GTK overlay input and routes
   it to mapping handlers in Mapping mode. `docs/KEY_MAPPING.md` describes a
   transparent overlay positioned over the game and F1 Edit/Mapping switching.
2. `controller/core/control_msg.py::InjectTouchEventMsg.pack` scales coordinates
   to Android display pixels and serializes scrcpy's 32-byte touch packet.
3. `controller/core/server.py` queues packets and writes a persistent stream.
4. `controller/app/scrcpy_lifecycle.py` obtains screen resolution, pushes the
   server, creates an ADB reverse tunnel and starts it once per setup.
5. `util/adb_helper.py` pins server version 3.3.1 and launches:

   ```text
   CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / \
     com.genymobile.scrcpy.Server 3.3.1 scid=... \
     log_level=debug video=false audio=false control=true
   ```

There is no per-event shell command. Cage is configurable in
`key_mapping_preference_dialog.py`; it is not required by scrcpy's injection API.

In scrcpy (`server/src/main/java/com/genymobile/scrcpy/`):

- `control/Controller.java::injectTouch` tracks pointer IDs and constructs
  Android MotionEvents. `control/PointersState.java` supports ten contacts and
  translates additional DOWN/UP events into POINTER_DOWN/POINTER_UP indexes.
- `device/Device.java::injectEvent` associates the display and calls
  `wrappers/InputManager.java::injectInputEvent`, using Android's privileged
  input API via reflection. Shell launch supplies injection authority; an
  ordinary installed APK alone does not supply it.
- `control/Controller.java::getEventPointAndDisplayId` explicitly supports
  control without video on an existing display. In that case it uses raw
  Android coordinates, without the video mapper's size validation.
- `device/DesktopConnection.java` supports a local abstract listening socket
  with `tunnel_forward=true`. The name is `scrcpy_%08x` for a supplied SCID.
  A correctly namespaced client can therefore be prototyped without exposing
  ADB TCP or copying Helper's fixed IP/port setup.

## Recommended AnvilDroid integration

```text
focused app's Wayland keyboard events
  -> native profile / mapping-player
  -> verified task-to-display coordinates
  -> bounded per-runtime control channel
  -> scrcpy control-only server in that Android runtime
  -> Android InputManager -> app
```

Keep the existing HWC/Wayland GPU presentation and same-surface editor. This
backend does not need video encoding, a virtual display, a framework image
patch, or an HWC touch FIFO advertised by the desktop seat. Use runtime-worker
to supervise one server per runtime generation; first verify launch under the
Android shell UID and the image's actual SELinux/injection policy. Pin matching
protocol and server versions and verify artifact hashes at packaging time.

Do not copy Helper's fixed `192.168.240.112:5555`, default `0.0.0.0:10721`
listener or `adb reverse --remove-all`: AnvilDroid has multiple isolated runtimes.
Prototype a socket inside the selected runtime network namespace; production
also needs verified peers/session ownership. A random socket name alone does
not authenticate a sender. Run all privileged launch/cleanup through the
existing worker, scoped to its own process and generation.

## Gaps to resolve before enabling production

- **App routing:** stock touch packets identify a display and coordinates, not
  a package, task or surface epoch. Convert normalized app points using verified
  Android task bounds, excluding decorations and letterboxes. Host focus alone
  does not prove Android's hit-test target. Verify Android focus, overlays and
  task lifetime; stale/ambiguous routing must suspend mapping. Multiwindow may
  require a narrow server extension for target validation. Polling dumpsys alone
  is not an atomic guarantee that a different window cannot receive a touch.
- **Cancellation:** stock 3.3.1 marks pointers removed only on ACTION_UP in
  `injectTouch`; ACTION_CANCEL does not clear `PointersState`. Controller EOF
  and stop also have no explicit touch-cancel/reset in the inspected path.
  Do not translate our TOUCH_CANCEL to one packet and assume reset succeeded.
  Verify cancellation in the probe and add a small server reset/watchdog path
  if necessary. Releasing every pointer with UP can activate controls, so it is
  not equivalent to canceling a gesture.
- **Delivery:** `handleEvent` does not send a per-touch success acknowledgement.
  Socket write success proves transport only. Our receiver-reset barrier needs
  explicit evidence/acknowledgement, potentially a narrow protocol extension.
- **IME and focus:** cancel on blur, editor activation, Android text focus,
  resize, disconnect and runtime replacement; resume only after fresh key-down.
  Continue using BridgeIme for Vietnamese composition. Scrcpy text injection
  is not a replacement for the existing text-input-v3 integration.
- **Backpressure:** bounded queue; never discard DOWN/UP/CANCEL to make room.
  Coalesce only compatible MOVE updates. A receiver watchdog is needed when
  the producer dies with a finger down.

## Next executable milestone

1. Run pinned stock scrcpy control-only in the selected test runtime and record
   launch identity, Android SDK, display bounds and connection handshake.
2. Deliver tap, hold, move and two simultaneous contacts to Touch Probe. Record
   Android-received pointer IDs/actions/coordinates, not just transmitted bytes.
3. Test CANCEL then a new gesture, producer disconnect while holding, app blur,
   resize and two runtimes. Use these results to choose the minimal scrcpy
   extension needed for routing, cancel/reset and watchdog acknowledgements.
4. Connect the existing editor/player to that verified backend, then add WASD
   with normalized diagonals and action buttons held concurrently.

Expected performance benefit: persistent binary input IPC and no video encode
or decode. No latency or CPU numbers are claimed until measured live.

This review copies no implementation. Helper carries GPLv3; scrcpy carries
Apache-2.0. Bundle the appropriate notices with any reused source/server artifact.

## First implementation and live checkpoint, 2026-10-08

Added `scrcpy_control.py` (pinned launch flags, display-space touch packet encoder,
peer identity and handshake validation) and `scrcpy-handshake.py` (disposable
runtime-scoped diagnostic). They are not wired into production keyboard capture.
The encoder refuses CANCEL rather than claiming stock scrcpy resets pointers.

Run the diagnostic with an independently obtained reviewed artifact:

```sh
python3 -B native/input/scrcpy-control-test.py
sudo python3 -B native/input/scrcpy-handshake.py RUNTIME_ID /path/to/scrcpy-server
```

Expected server SHA256:
`a0f70b20aa4998fbf658c94118cd6c8dab6abbb0647a3bdab344d70bc1ebcbb8`.
No downloaded server binary is bundled by this change. Five protocol tests pass:
byte layout/pressure, invalid coordinates/actions/IDs, control-only flags and
socket naming, wrong process rejection, and handshake EOF handling.

Live result on `r-8527d7d7a3a346bd9cc68a9460380dad`, generation
`ef519ecc0a954f10be707b66be95697a`, official GAPPS Android 13 / SDK 33:

- Reused `runtime-worker.shutdown_environment` to provide Android's actual
  boot classpath. Bare `app_process` inherited from host attach had exited before
  server main. Correct environment successfully started scrcpy under UID/GID 2000.
- The worker and Android container have DIFFERENT network namespaces. Connecting
  in the worker namespace failed. Pinning Android init's network namespace via
  the worker's proc mount fixed it; no ADB/TCP listener was needed.
- The repeatable diagnostic verified the peer was a descendant of its launch
  process, UID 2000, received `00`, then closed the socket. scrcpy logged
  `Controller stopped` and `Device message sender stopped`, exited successfully,
  and the diagnostic removed its unique guest JAR. A guest timeout bounds an
  unsuccessful handshake to 15 seconds.
- No touch was sent. Touch Probe installation through the normal controller
  failed with `INSTALL_FAILED_VERIFICATION_FAILURE: Install not allowed`, job
  `a9182460602a4dff9b28235e718329d6`. Android verification was not disabled.

Tap/hold, multitouch, cancellation, focus/geometry routing, producer-crash
recovery, latency and cross-runtime input isolation remain unverified live.
The successful control handshake proves startup/connectivity, not injection
permission or delivery. Resume those gates after the user resolves Android's
installation verification for Touch Probe.

## Live touch and cancellation checkpoint, 2026-10-08

User approved Android's Touch Probe verification. A subsequent normal controller
install and launch succeeded; no verifier policy was changed. This supersedes
the installation blocker above.

Added `scrcpy-touch-smoke.py`: a diagnostic restricted to the focused, visible
Touch Probe window on display 0. It reads verbose window geometry and returns
sent packets separately from Android log observations. It is deliberately not
a production focus/route authority. It reuses the bounded server lifecycle and
peer checks in `scrcpy-handshake.py`.

On the same runtime/generation, Android display was 1984x1164 and probe window
frame [760,154][1224,978]. Sending display (914,566) produced content (154,411):
the content has a one-pixel top inset. Future production mapping must account
for content insets, not blindly equate task/window bounds with content bounds.

Stock 3.3.1 results:

- Tap: DOWN/UP delivered with activeAfter=0.
- Hold/drag: DOWN, MOVE after about 300ms, UP delivered.
- Two contacts: DOWN, POINTER_DOWN(1), MOVE, POINTER_UP(1), UP; IDs 0/1
  preserved and active counts 1,2,2,1,0.
- CANCEL: delivered and app cleared its contacts, but the immediately following
  new contact was not delivered until the old server pointers were cleared by
  UP. Confirms the source finding about stale PointersState.
- Client disconnect while holding: server exited, but no CANCEL reached the
  probe during the observation interval; last event remained activeAfter=1.
  Probe alone was force-stopped/reopened before testing the fix.

Added `scrcpy-3.3.1-cancel.patch` and `build-scrcpy-diagnostic.py`. The builder
exports the pinned source commit without modifying the reference checkout,
applies the patch, builds Java/AIDL/DEX, and includes upstream Apache-2.0 license
beside the artifact. The patch executes CANCEL against the old display with
WAIT_FOR_RESULT, clears pointer state only on success, blocks further touch on
failed reset, and attempts cancellation in the control thread's finally path.
It is an experimental server patch, not an Android framework/image patch.

Build inputs: Google Android platform-35_r02.zip SHA256
`0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0`,
build-tools_r35_linux.zip SHA256
`bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88`.
Artifact `target/input-diagnostic/scrcpy-server-cancel.jar` SHA256
`da318a3948eecddf5d7780a84b27b3cd0c2d41f4266963c81203d3426eb691d3`.
The diagnostic accepts only the pinned stock and this reviewed build hash.

Patched server live results:

- Two-finger CANCEL: probe log seq64 ACTION_CANCEL activeAfter=0; seq65/66
  immediately following fresh DOWN/UP delivered; seq67/68 next tap delivered.
  Server: `AnvilDroid touch reset applied=true pointers=2`.
- Disconnect while holding: seq79 DOWN activeAfter=1 then seq80 ACTION_CANCEL
  activeAfter=0. Server: `touch reset applied=true pointers=1`, clean exit.
- Regular tap/drag/multitouch sequences also passed on the patched build.
  Events report SOURCE_TOUCHSCREEN (0x1002), device=-1, distinguishing them
  from the user's mouse events (0x2002).

No production deployment or DE/IME integration yet. This patch does NOT add
wire reset acknowledgements, sender authentication, receiver heartbeat/watchdog,
atomic task routing, or recovery from server SIGKILL. WAIT_FOR_RESULT is a
server-side injection result, not a client acknowledgement or delivery latency.
The existing encoder continues to reject CANCEL for generic stock use; only
the explicit probe diagnostic constructs it. Next gate is a supervised,
authenticated receiver with those lifecycle/route guarantees, then connect the
native tap/hold player and WASD to it. Game, blur/resize and cross-runtime
acceptance remain open. No latency percentile is claimed from log event age.
