# Native game controls

The attached native Wayland editor and scrcpy touch sink implement advanced
controls with original MIT-licensed code. F2 opens the editor for the focused,
unambiguous app task. Choose **Add control**, click its position, then use the
type selector (**Basic >**) to choose a control. Drag its center to move it.
Choose **Key…** or **Mouse…** on the Binding page, then press the desired key or
mouse button. Placing or adjusting a control never assigns a binding. Escape
cancels binding capture. WASD reserves W/A/S/D and Mouse touch needs no binding.

The named page button advances through settings relevant to the selected type;
**< Page** goes back. On a small window, the bottom Tap/Hold button switches the
selected end behavior; Hide temporarily exposes the canvas. **Save** persists
and closes; **Save*** indicates changes. Cancel, Escape or F2 closes the editor
without saving. F3 toggles gameplay mapping and F4 toggles markers independently.
F6–F9 remain shortcuts for type, endpoint, timing and macro count.

## Authoring controls

- **Mouse touch:** add a control and cycle its type to **Touch >**. This enables
  pointer-position finger input for this app profile: left press starts a touch,
  dragging moves it and release lifts it. Hover creates no touch. **Turn off**
  removes this entry; Save applies the change.
- **Fixed mouse fire/button:** place a Basic control on the game's button, choose
  **Mouse…**, then press the mouse button to bind. Choose Release for a bounded
  tap or Hold for a contact lasting until button release. This binding consumes
  the button before Mouse touch, so one press does not create two contacts.
- **WASD:** place the joystick center and select WASD. Drag the **R** handle to set
  radius, or use the Joystick page's radius and ramp controls. Radius is shown as
  a percentage of the app's shorter edge; ramp is in milliseconds. The old
  endpoint-derived radius is shown as Auto until explicitly changed.
- **Swipe:** drag the center and **E** endpoint; set duration on Timing. Release
  lifts at completion; Hold keeps the contact at the endpoint until the trigger
  is released. Releasing the trigger early cancels the swipe.
- **Look:** place the region center and drag its **+** corner to resize, or use
  Region's width/height percentages of the app area. Sensitivity controls X/Y
  percentages; Look controls invert Y and activation. Hold follows pointer motion
  while the binding is held. Capture toggles FPS mouse capture on each new press,
  using relative motion with a hidden cursor. Escape releases capture and active
  contacts. The status shows Ready, Pending or Active; No FPS/No API means this
  compositor lacks the required protocols and capture cannot be enabled. Hold
  mode remains available. Reaching a region edge lifts the touch and starts a
  new contact at the center. Large deltas are capped to one region traversal
  per input event; sensitivity and touch restarts need tuning in each game.

Mouse touch in a profile uses the native touch sink. The separate **Mouse as
touch** compatibility setting (Waydroid FakeTouch) is an Android-image property;
its support depends on the image. It does not configure these control bindings,
joystick, swipe or Look settings.

| Type | Behavior while the trigger is held |
| --- | --- |
| Basic | New Release controls lift at key/button release or the duration limit, whichever comes first (50 ms by default); Hold lasts until release |
| WASD | W/A/S/D share one joystick contact; diagonals retain equal speed and opposing directions cancel |
| Swipe | Touch moves from origin to endpoint over the duration, then releases or holds as selected |
| Repeat | Alternates press/release at the origin every configured interval |
| Skill | Starts at origin, follows pointer within app content, releases at the current position |
| Macro | Finite sequence of clicks alternating origin/endpoint; key-up cancels early |
| Aim | Trigger holds an aim contact following the pointer; left mouse creates a separate fire contact at the authored endpoint |
| Mouse touch | Left press/drag/release sends a finger contact at the pointer position |
| Look | Pointer deltas move a contact inside the authored region, in Hold or supported FPS capture mode |

Aim is bounded absolute pointer aiming inside the app. It does not claim
unlimited relative mouse capture or recenter the desktop pointer. Pointer
movement and left-click are consumed while the aim control owns them; other
input retains the normal bridge path. Release the trigger or leave the app to
release ownership. No profile command can invoke a shell or inject arbitrary
Android key events.

## Profile format and bounds

V1 (`evdev nx ny hold`) and V2 (nine fields) remain readable. Opening a profile
does not migrate it; only explicit Save writes V3. Cancel leaves the saved file
unchanged. Legacy Basic controls retain their original key-down/key-up behavior,
regardless of the old hold flag; legacy Swipe controls release at completion.
Choosing Release or Hold explicitly replaces that legacy behavior.

V3 uses `ANVIL_KEYMAP 3 runtime package count` and nineteen integers per row:
`evdev nx ny hold kind end_nx end_ny duration_ms repeat_count source end_mode radius ramp_ms look_width look_height sensitivity_x sensitivity_y invert_y activation`.
Kinds are 0 Basic, 1 WASD, 2 Swipe, 3 Repeat, 4 Skill, 5 Macro, 6 Aim,
7 Mouse touch and 8 Look. Sources are keyboard (0), mouse button (1) and pointer
(2); mouse buttons use evdev 272..276, while Mouse touch uses pointer source and
key zero. End modes are legacy (0), Release (1) and Hold (2). Coordinates
are 0..65535, keys 2..767, hold 0 or 1, advanced intervals 50..5000 ms,
repeat counts 1..20. Macros must satisfy `2 * interval * count <= 10000`.
WASD's primary key must be W (17), reserving A/S/D (30/31/32) from other controls.
Each profile allows one Mouse touch and one Look control. Profiles are limited to
64 controls and 16384 bytes, with at most ten simultaneous
contacts. There is no queued backlog or catch-up burst after delayed polling.
Advanced controls require the native sink and must not be converted to the
older basic-only offline mapping representation.

## Cancellation and timing

The bridge mutex serializes key, pointer and timer actions. The existing 50 ms
poll services timers using monotonic elapsed time. Timing precision is bounded
by scheduling and sink availability; delayed macro polls cancel/finish safely
without replaying missed clicks. Blur, a pointer leave that does not transfer to
another surface of the same app, editor entry, task
replacement, changed task geometry, resize, disable, surface teardown and
disconnect cancel contacts and all pending state. Held inputs are never replayed
on reconnection. Runtime processes and generation-scoped editor requests retain
the existing runtime/package/task isolation; ambiguous duplicate tasks are
rejected.

## Verification and live acceptance

`tests/keymap-advanced.c` exercises diagonal/opposing movement, independent
contacts, swipe timing, repeat cancellation, finite macros, pointer-directed
skill/aim/fire, task/geometry invalidation, unavailable sink, and contact bounds.
Storage and inventory tests cover all three versions and malformed bounds.
Build the Android bridge with `ANVIL_KEYMAP_EDITOR=1`; run the native suite and
ASan/UBSan tests before packaging. Tests use a deterministic sink and elapsed-time
advance, and do not install or restart a user's runtime.

Both native Wayland and X11 adapters use the same guest Wayland bridge and its
keymap editor/sink. The maintained disposable Android probe exercises actual
SOURCE_TOUCHSCREEN / TOOL_TYPE_FINGER input, simultaneous joystick/look/fire,
visible editor creation/binding/Save/reload/Cancel and FPS capture cancellation.
On nested Wayland, protocol tracing observes the scoped app's lock grant,
relative events, suppression of its absolute pointer motion while locked, and
restored motion after release. The outer X11 transport cursor is a separate
pointer and may still move. Native X11 additionally checks its host confinement.
Recorded results and package checksums are in
[`tests/acceptance/README.md`](../tests/acceptance/README.md).

Real FPS/MOBA gameplay, mouse feel, sustained capture and ARM game acceptance
remain manual release gates. Diagnostic MotionEvents establish delivery to the
probe app; they cannot certify another game's compatibility or physical
GNOME/KDE/Default input behavior. The full live editor drag/delete/conflict,
resize/rotation/replacement and concurrent-window matrix also remains open;
deterministic native coverage of those guards is separate from live acceptance.

The X11 suite additionally uses a private Xvfb/XTest display, the real X11
adapter and Wayland seat, and production keyboard/pointer mapping handlers with
a synthetic Android sink. It covers advanced controls, repeated/held keys,
focus changes, unmap cancellation and unmapped fallback. Real Fcitx Pinyin
composition and normal text input are checked separately through the same
adapter. Normal and ASan/UBSan variants pass; these checks still do not certify
Android app behavior or real GPU presentation.
