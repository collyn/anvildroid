# Native game controls

The attached native Wayland editor and scrcpy touch sink implement advanced
controls with original MIT-licensed code. F2 opens the editor for the focused,
unambiguous app task. Add/place a point and press its trigger key. Select a point
and press F6 to cycle Basic, WASD, Swipe, Repeat, Skill, Macro and Aim. F7 sets the
endpoint at the pointer position in the editor. Dragging moves the origin.
F8 cycles the interval (50/100/250/500/1000 ms); F9 cycles the macro count
(1..5). The selected control shows its type, interval and count. Advanced presets
start at 250 ms and three macro clicks; profile files can specify other validated
values. F3 toggles gameplay mapping and F4 toggles marker
visibility independently. Save persists and closes; Escape cancels edits.

| Type | Behavior while the trigger is held |
| --- | --- |
| Basic | Existing key-down/key-up touch behavior, preserving V1 profiles |
| WASD | W/A/S/D share one joystick contact, radius set by the F7 endpoint distance (1024..16384 normalized units); diagonals retain equal speed; opposing directions cancel |
| Swipe | Touch moves from origin to endpoint over the configured duration, then releases |
| Repeat | Alternates press/release at the origin every configured interval |
| Skill | Starts at origin, follows pointer within app content, releases at the current position |
| Macro | Finite sequence of clicks alternating origin/endpoint; key-up cancels early |
| Aim | Trigger holds an aim contact following the pointer; left mouse creates a separate fire contact at the authored endpoint |

Aim is bounded absolute pointer aiming inside the app. It does not claim
unlimited relative mouse capture or recenter the desktop pointer. Pointer
movement and left-click are consumed while the aim control owns them; other
input retains the normal bridge path. Release the trigger or leave the app to
release ownership. No profile command can invoke a shell or inject arbitrary
Android key events.

## Profile format and bounds

V1 remains readable: `ANVIL_KEYMAP 1 runtime package count` followed by
`evdev nx ny hold`. Saves use V2 with the same header and nine integers per row:
`evdev nx ny hold kind end_nx end_ny duration_ms repeat_count`.
Kinds are 0 Basic, 1 WASD, 2 Swipe, 3 Repeat, 4 Skill, 5 Macro, 6 Aim. Coordinates
are 0..65535, keys 2..767, hold 0 or 1, advanced intervals 50..5000 ms,
repeat counts 1..20. Macros must satisfy `2 * interval * count <= 10000`.
WASD's primary key must be W (17), reserving A/S/D (30/31/32) from other controls.
Profiles are limited to 64 controls and 4096 bytes, with at most ten simultaneous
contacts. There is no queued backlog or catch-up burst after delayed polling.
Advanced controls require the native sink and must not be converted to the
older basic-only offline mapping representation.

## Cancellation and timing

The bridge mutex serializes key, pointer and timer actions. The existing 50 ms
poll services timers using monotonic elapsed time. Timing precision is bounded
by scheduling and sink availability; delayed macro polls cancel/finish safely
without replaying missed clicks. Blur, pointer leave, editor entry, task
replacement, changed task geometry, resize, disable, surface teardown and
disconnect cancel contacts and all pending state. Held inputs are never replayed
on reconnection. Runtime processes and generation-scoped editor requests retain
the existing runtime/package/task isolation; ambiguous duplicate tasks are
rejected.

## Verification and live acceptance

`tests/keymap-advanced.c` exercises diagonal/opposing movement, independent
contacts, swipe timing, repeat cancellation, finite macros, pointer-directed
skill/aim/fire, task/geometry invalidation, unavailable sink, and contact bounds.
Storage and inventory tests cover both versions and malformed advanced bounds.
Build the Android bridge with `ANVIL_KEYMAP_EDITOR=1`; run the native suite and
ASan/UBSan tests before packaging. Tests use a deterministic sink and elapsed-time
advance, and do not install or restart a user's runtime.

Real FPS/MOBA gameplay, Android input compatibility, mouse feel and ARM game
acceptance remain manual release gates. Native deterministic tests cannot certify
those outcomes. Both native Wayland and X11 adapters use the same guest Wayland bridge and
its keymap editor/sink. The X11 adapter forwards its host input through this
shared protocol path; live advanced gameplay acceptance remains unverified on
both backends.
