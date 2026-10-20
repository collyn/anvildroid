# Native game controls

The native editor and scrcpy touch sink use original MIT-licensed code.
The [2026-10-20 design research](KEYMAP-DESIGN-RESEARCH-2026-10-20.md) records the
behavioral references behind this implementation. The evidence section below
keeps historical acceptance results separate from the current V4 feature set.

F2 opens the editor for the focused, unambiguous app task. Choose **Tools**, pick
a tool, then click the game canvas to place it. The palette has Tap / Hold,
Movement joystick, Swipe, Repeat taps, Skill direction, Macro sequence,
Absolute aim, Mouse touch, Mouse camera (FPS), Right-click walk and FPS starter.
Each tool has a short description. Selecting a tool starts a new placement;
it does not change the selected control's type. A keyboard-triggered control
immediately waits for a keyboard key or mouse button after placement. Joystick
starts with WASD; Mouse touch and Right-click walk already have pointer bindings.

Click an existing key label, double-click its marker, or choose **Binding** in
the inspector to rebind it. The next keyboard press or mouse-button press is the
binding; the click that opens capture is ignored. Drag a label or its outer ring
to move the control without rebinding it. Drag the swipe endpoint, joystick
**R** handle or camera **+** corner to change the corresponding geometry.
The inspector shows named fields for the selected control. Numeric rows have
minus/plus areas at their edges; click the middle, type a value and press Enter
to apply it. Values use the displayed units: percentages, milliseconds or counts.
Backspace edits a number and Escape cancels numeric entry. Invalid input keeps
the previous value. Scroll the inspector/palette with the mouse wheel, its
Scroll up/Scroll down buttons or Page Up/Page Down; **Hide** exposes the covered canvas.

The inspector starts docked over the canvas. Choose **Detach** in the inspector
to open its tools in a separate native window. Drag that window by its title bar
to move it outside the Android window; control markers and placement handles
remain on the game canvas. **Dock** returns the inspector to the canvas.
Closing the detached window also docks it, preserving the current draft,
selection and Undo/Redo history. Both layouts edit the same draft and use the
same **Save** and **Cancel** actions.

**Undo** and **Redo** keep up to 32 states during the edit session. Backspace
also undoes when a numeric field is not being edited. **Clone** offsets a copy
and requests a fresh binding; singleton mouse/camera/walk controls cannot be
cloned. A cloned movement control still needs non-conflicting direction keys.
Delete or the marker's **X** removes the selected control. The editor reports
missing bindings and conflicts; a rejected binding keeps its previous value.

**Try** runs the current valid draft in the app without saving. Entering Try
releases all existing contacts; **Return to editing (F2)** or F2 returns
to editing and releases the test contacts. Escape first releases active input
and leaves Try running so the unlocked mouse can tap the game; another Escape
returns to editing. Try accepts gameplay input only
while the Android canvas has focus. Focusing the detached inspector releases
test contacts; it offers **Return to editing** without sending gameplay input.
**Save** persists and closes;
**Save*** indicates a changed draft. Cancel, Escape or F2 closes an ordinary
edit without saving. Escape first cancels an open binding/numeric capture or
palette. F3 toggles gameplay mapping and F4 toggles markers independently.
Resizing or moving the fitted canvas cancels a drag/button press, requiring a
fresh press. Replacing or closing the Android task discards its unsaved edit;
the next app instance loads the saved profile.

## Authoring controls

- **Tap / Hold:** place the control on a game button and bind a keyboard key or
  mouse button. Release creates a bounded tap (50 ms by default), ending at
  trigger release or its duration limit. Hold lasts until trigger release.
  A fixed mouse binding takes precedence over Mouse touch, so a press creates
  one contact at the authored position.
- **Mouse touch:** left press starts a finger at the pointer, dragging moves it
  and release lifts it. Hover creates no touch. Delete this entry to remove it
  from the profile. This uses the native touch sink. The separate Compatibility
  **Mouse as touch** setting is the Android image's Waydroid FakeTouch property;
  it does not configure these controls.
- **Movement joystick:** place the joystick center, then drag **R** or set Radius
  in the inspector. Radius is a percentage of the app's shorter edge. WASD and
  arrow-key presets are available; Up/Left/Down/Right can each be rebound.
  All four directions share one contact. Diagonals are normalized, opposing
  directions cancel, and releasing every direction lifts the contact.
  Ramp 0 moves instantly; a positive ramp slides from the current position to
  the new target over that many milliseconds. A Walk modifier reduces radius
  while held; its default is 40% of the normal radius. Imported radius-zero
  joysticks retain the old endpoint-derived radius until explicitly changed.
- **Swipe:** drag its start and arrow endpoint, then set duration. Release lifts
  at completion; Hold stays at the endpoint until trigger release. Releasing
  the trigger early ends the swipe at its current position.
- **Mouse camera (FPS / Look):** place the camera region away from HUD buttons.
  Drag **+** to resize, or enter its width/height as percentages of the app area.
  Set sensitivity X/Y and optional vertical inversion. New cameras default to
  **Automatic**: Try/Save locks the mouse when the game canvas has keyboard and
  pointer focus; moving the mouse turns the camera without holding any button.
  **F5** is preassigned to lock/unlock and **Left Alt** temporarily shows the
  cursor. **Esc** releases the lock and keeps it released until F5 is pressed.
  While unlocked or Alt is held, M1 taps/drags the screen as an Android finger,
  even if M1 is assigned to Fire and no Mouse touch control was added. While
  looking, the M1 binding fires at its authored position. Unlocking never makes
  the next screen click relock or fire. Other mouse buttons keep their bindings.
  Old profiles retain their Hold/Toggle mode; select **Camera: automatic** in
  the inspector to opt in. Hold follows mouse deltas while its binding is held.
  Toggle capture requests relative mouse input. Automatic and Toggle hide the
  cursor and begin camera touch only after the compositor grants the lock.
  Capture requests the app window first for KWin compatibility. If the
  compositor instead requires the focused Android content subsurface, it tries
  that surface once after 250 ms, within the original two-second deadline.
  The status shows Ready, Pending or Active;
  No FPS/No API means the required compositor protocols are unavailable.
  A Show cursor key temporarily releases the camera touch and pointer lock
  while held. Releasing it requests a fresh lock only if the same app remains
  focused; blur or cancellation prevents automatic resumption. Delete while
  capturing this optional key clears it. At a region edge, Look lifts and
  restarts at the center. Large deltas are bounded to one region traversal per
  event, so sensitivity and touch restarts need tuning in each game.
- **FPS starter:** place the camera, then move the separate M1 fire control onto
  the game's fire button. It creates an Automatic camera, **F5** to lock/unlock, **Left Alt**
  to show the cursor temporarily, and a Basic **M1 Hold** contact. Drag shot is
  off initially. Conflicting F5/Alt/M1 bindings or an existing camera reject the
  addition. This is a layout starting point; it does not know a game's HUD.
- **Drag shot:** enable this optional inspector field on a mouse-bound Basic
  Hold control. With Look already active, pressing fire lifts the camera
  contact and gives its mouse deltas to the fire contact. Fire release lifts
  that contact and starts the camera again at its center. Each delta has one
  owner. Suspend, blur, disable and disconnect release ownership and do not
  replay held fire. One drag-shot control is allowed per profile.
- **Skill direction:** new controls have a radius of 8192/65535 of the shorter
  edge (about 12.5%). Pointer motion chooses a direction and is clamped to that
  physical radius. Release to cast lifts at trigger release. Cast immediately
  sends DOWN, an optional move toward the current cursor and UP on key-down;
  toggle casting remains active across key-up and ends on the next fresh press.
  A Cancel skill key moves the active contact to the authored cancel X/Y and
  lifts it. Set that position to the game's cancel area; the game decides
  whether this cancels the skill. Delete while capturing the optional cancel
  key clears it. Imported radius-zero Skill controls retain their original
  absolute-pointer behavior.
- **Right-click walk:** place its joystick center on the game's movement pad.
  While the right button is held, the cursor direction from that center drives
  one contact at the configured physical radius. Moving the cursor updates its
  direction; releasing the button lifts it. It does not tap the cursor position.
- **Absolute aim:** preserves the older Aim behavior: the trigger holds a
  contact following the absolute pointer, and left mouse adds a separate fire
  contact at the authored endpoint. It does not use Look's relative capture.
  Release its trigger or leave the app to release ownership.
- **Repeat taps / Macro sequence:** Repeat alternates press/release at the
  origin while held. Macro sends a finite sequence of clicks alternating
  origin/endpoint; releasing the trigger cancels it early.

## Saved schemes

In **Game Controls**, expand **Schemes** for the app/runtime. **New** creates an
empty scheme; **Clone** copies the chosen scheme; **Rename** changes its name.
New, Clone and Import create inactive schemes: choose **Use** to activate one.
**Reset** clears the scheme selected in the chooser, which may be inactive.
**Remove** deletes an inactive custom scheme; Default remains available. There
are at most 16 schemes per app/runtime, including Default, with names up to
48 characters. The native editor saves to the currently active scheme.

Save or cancel an open editor before changing schemes. Listing and exporting
remain available while editing. Revision checks reject stale changes; corrupt
saved data is preserved and reported. Scheme mutations require a runtime with
the updated native bridge protocol. Profiles remain scoped to their runtime
and installed app, including when imported into a different destination.

**Export** opens the native Save dialog for AnvilDroid scheme JSON version 1.
Choose a new filename; existing files are preserved. **Import** accepts that
format, containing a validated native V1–V4 profile. The JSON limit is 24 KiB and
the enclosed native profile limit is 16 KiB. Proprietary emulator formats are
not supported. Optional reference width/height/orientation are metadata; native
saves currently leave them unknown. Normalized coordinates do not adapt a
scheme to a changed game HUD or automatically rescale from reference metadata.

## Profile format and bounds

V1 (`evdev nx ny hold`), V2 (nine fields) and V3 (nineteen fields) remain
readable. Loading does not rewrite a file; saving a changed profile writes V4.
Cancel, Try and Save after undoing to the original state preserve its bytes.
Newly added fields are zero when reading older
versions. Legacy Basic keeps its key-down/key-up behavior regardless of its old
hold bit; legacy Swipe releases at completion. Explicit Release/Hold changes
replace those legacy behaviors. Look remains relative mouse input; the older
Aim kind keeps its absolute-pointer meaning.

V4 starts with `ANVIL_KEYMAP 4 runtime package count`. Each control has exactly
31 non-negative decimal integers on one complete row, in this order:

```text
evdev nx ny hold kind end_nx end_ny duration_ms repeat_count
source end_mode radius ramp_ms look_width look_height sensitivity_x sensitivity_y invert_y activation
up_key left_key down_key right_key walk_key walk_radius suspend_key drag_shot skill_mode cancel_key cancel_x cancel_y
```

The line breaks above only group the fields for reading; a saved control is
one line. Header and rows end in LF or CRLF. Numbers accept up to 15 digits,
including leading zeros, with values bounded to 65535 before field validation.
Missing/extra fields, unsupported versions and malformed rows are rejected.

| Fields | Meaning and limits |
| --- | --- |
| `kind` | 0 Basic, 1 movement joystick, 2 Swipe, 3 Repeat, 4 Skill, 5 Macro, 6 absolute Aim, 7 Mouse touch, 8 Look, 9 Right-click walk |
| `source`, `evdev` | Keyboard 0, mouse button 1 (evdev 272..276), pointer 2 (Mouse touch only, key zero). Keyboard keys are 2..767 excluding reserved F2/F3/F4 (60..62); Escape is reserved too. |
| `nx ny end_nx end_ny`, `hold` | Coordinates 0..65535; hold is 0 or 1. |
| `duration_ms`, `repeat_count` | Advanced controls require 50..5000 ms and 1..20; Basic permits legacy zero values. Macro requires `2 * duration_ms * repeat_count <= 10000`. |
| `end_mode` | Legacy 0, Release 1, Hold 2. |
| `radius`, `ramp_ms` | Radius zero preserves legacy behavior; an explicit radius is 256..32767 in units of the shorter edge/65535. Ramp is 0..1000 ms. Right-click walk requires a positive radius. |
| `look_width`, `look_height` | Half extents, 0..32767; Look requires at least 256 and its entire region must remain inside the app. The editor displays full width/height percentages. |
| `sensitivity_x`, `sensitivity_y`, `invert_y`, `activation` | Sensitivity 0..1000, with Look requiring 10..1000%; inversion 0/1; activation Hold 0, Toggle capture 1, Automatic capture 2 (Look only). |
| `up_key left_key down_key right_key` | All zero means WASD (17/30/31/32). A custom joystick requires all four distinct keys and `evdev == up_key`. Other kinds leave these zero. |
| `walk_key`, `walk_radius` | Optional keyboard modifier for the movement joystick; radius percentage 10..100, or zero for the 40% default. With no modifier, both fields are zero. |
| `suspend_key` | Optional keyboard Show cursor key on Look Capture. |
| `drag_shot` | 0/1, enabled only on a mouse-bound Basic Hold control. |
| `skill_mode` | Skill release 0, immediate 1, toggle 2. Other kinds leave this zero. |
| `cancel_key`, `cancel_x`, `cancel_y` | Optional Skill cancellation key and destination. Coordinates must remain zero when the key is disabled. |

Direction, Walk, Show cursor and Cancel keys participate in conflict validation
within and across controls. A physical key cannot trigger two actions. Mouse
buttons have one explicit owner; absolute Aim reserves M1 for its own fire.
Mouse touch may coexist with a fixed M1 binding, which takes priority. A profile
allows one Mouse touch, one Look and one drag-shot control. It is bounded to
64 controls and 16384 bytes, with at most ten occupied contact slots. There is
no queued backlog or catch-up burst after delayed polling. Advanced controls
require the native sink and cannot be converted to the older basic-only offline
mapping representation. No profile can execute shell commands or arbitrary
Android key events.

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

Android may cancel a gesture itself when density changes recreate the Activity.
If its subsequent CANCEL rejects our now-obsolete contact, the receiver drops
stale MOVE/UP events and accepts only a fresh DOWN to start a new stream with
an empty pointer list. A failed fresh DOWN keeps that recovery guard active.
This avoids permanently disabling input after a configuration change.

The managed worker also supervises the control-only receiver after framework
disconnects. It keeps one child, retries with bounded backoff and refreshes the
validated boot classpath for each attempt. Frozen/stopping guests are not woken
to reconnect. Recovery still requires fresh input; held keys are not replayed.

## Verification and live acceptance

The [installed KDE host check](KEYMAP-HOST-ACCEPTANCE-2026-10-20.md) records the
subsequent usability fixes, real desktop authoring, Calculator response and
seven actual Android finger-input scenarios. It also documents remaining game
and physical-mouse compatibility limits. That report predates the V4 redesign;
it is not evidence that V4 was retested on the installed desktop. Current V4
test scope and package-specific results are recorded in
[the redesign implementation report](KEYMAP-REDESIGN-IMPLEMENTATION.md).

`tests/keymap-advanced.c` exercises diagonal/opposing movement, independent
contacts, swipe timing, repeat cancellation, finite macros, pointer-directed
skill/aim/fire, task/geometry invalidation, unavailable sink, and contact bounds.
Storage and inventory tests cover V1–V4, malformed bounds and shared key
reservations. V4 tests additionally cover custom directions/walk radius, Skill
projection/cast/cancel, right-click walk, fresh capture grants after suspension,
drag-shot handoff and cancellation. These source-level checks do not add new
live acceptance claims to the historical results below.
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

Representative FPS/MOBA/ARM games, physical mouse feel and long capture sessions
remain manual release gates. Diagnostic MotionEvents establish delivery to the
probe app; they cannot certify another game's compatibility or physical
GNOME/KDE/Default input behavior. The 2026-10-19 disposable managed runs pass
45/45 scenarios on each backend, including graphical drag/delete/rebind,
rendered conflict feedback, rejected unassigned Save, marker pixels,
resize/maximize, Android density/rotation and dirty task replacement/reopen.
Wayland maximize uses the production CSD; X11 uses a private EWMH window-manager
fixture. A separate 8/8 two-runtime run verifies different profiles for the same
package, own-profile touch coordinates and peer gameplay while editing, with no
cross-runtime input or saved-byte changes. These checks do not establish physical
desktop window-manager behavior or multiple same-package tasks in one runtime;
ambiguous task owners remain rejected.

The subsequent [real-game acceptance](GAME-ACCEPTANCE.md) uses official Luanti
5.17.0 with Minetest Game 5.8.0 in forced touch mode. It observes actual node
digging, hotbar taps, joystick/swipe movement, mouse/Look camera rotation,
simultaneous movement/look/dig, blur cancellation and fresh recovery. The
existing editor changes Hold Look to FPS Capture for a bounded 30-second
relative-motion run, followed by Escape/quiescence and a fresh toggle. This
is one offline game in disposable managed guests; it does not certify other
games or the physical desktop matrix.

The subsequent [environment and recovery matrix](CONTROLS-MATRIX.md) records
45/45 diagnostic editor/input/capture and 9/9 held-input recovery cases for
each Android 13 Vanilla/GAPPS managed backend. Stock Default has a separate
fixture and explicitly narrower coverage; consult that report for cursor mode,
framework restoration, package/image provenance and remaining manual gates.

The X11 suite additionally uses a private Xvfb/XTest display, the real X11
adapter and Wayland seat, and production keyboard/pointer mapping handlers with
a synthetic Android sink. It covers advanced controls, repeated/held keys,
focus changes, unmap cancellation and unmapped fallback. Real Fcitx Pinyin
composition and normal text input are checked separately through the same
adapter. Normal and ASan/UBSan variants pass; these checks still do not certify
Android app behavior or real GPU presentation.
