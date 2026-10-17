# App compatibility

AnvilDroid offers Fake Wi-Fi and Mouse as touch from an installed app's
Compatibility dialog. The selected runtime must be running. Settings belong to
Android user 0 in that runtime; the same package in another runtime is independent.
No host Waydroid configuration is rewritten and no app is stopped automatically.

Fake Wi-Fi writes the package entry in `persist.waydroid.fake_wifi`. Mouse as
touch writes `persist.waydroid.fake_touch`. These are Waydroid's native image
properties, not a replacement input mapper. A successful save verifies property
readback. Android permits persistent properties even if the framework does not
consume them, so the dialog explicitly says that image support and actual app
behavior remain unverified. Unsupported property formats, a rejected write or a
readback mismatch produce an error instead of a success claim.

After saving or resetting, stop and reopen the app to activate its configuration.
The dialog does not infer running-process activation from property values. A
read/no-op reports `current_configuration`; a change reports
`app_restart_required`. This activation field describes that operation; a later
read cannot prove that a previously running app has restarted.

Each setting shows whether the app has an explicit entry and whether an existing
`*` wildcard enables it globally. A global wildcard prevents disabling the setting
for one app. Reset removes **only this package's explicit entries** and retains
unrelated packages, ordering, duplicates and wildcard behavior. Saving an inherited
setting also retains its inherited-only state instead of adding an explicit entry. It is a per-app
reset in the chosen runtime, not a reset of every app in the runtime.

## Safety and concurrency

`services/runtime-compatibility.py` is an original MIT-project implementation. It
accepts an injected argv-only `android(*args)` function; it opens no host paths and
runs only `pm list packages --user 0 PACKAGE`, the two allowlisted `getprop`s and
their corresponding `setprop`s. It validates package syntax and exact installed
package membership before reads or writes. It never accepts a caller-selected
property name, Android command, UID or runtime path. Values have a bounded,
strict comma-delimited package/`*` grammar; unfamiliar formats are left untouched.

An SHA-256 revision binds both property values to the runtime ID and runtime
generation. Set/reset require the revision obtained from a read and refuse stale
configuration or a replaced runtime. A process-local lock serializes reads and
transactions. The worker validates generation again before invoking the helper.
The controller remains responsible for authorizing the runtime owner using the
connection's peer credentials. Default's caller must also verify that its live
session identity remains the same across a request.

Set/reset preflight both desired values, write only changed properties, read back
each write, then verify the complete pair. A failure rolls back every attempted
write, including the write that timed out because it may have taken effect.
Rollback reads verify the previous values; failure explicitly reports uncertainty
and requires refresh. Android has no atomic multi-property compare-and-swap;
external `setprop` users can race this process. The final read detects mismatches,
but cannot guarantee atomicity against unrelated external writers.

## Integration contract

Public operations are `compatibility_get`, `compatibility_set` and
`compatibility_reset`. All require `id` and `package`; set adds `settings` and
`revision`, reset adds `revision`. `settings` has exactly `fake_wifi` and
`mouse_as_touch`, both actual JSON booleans. The result has `runtime_id`,
`generation`, `package`, opaque `revision`, `settings`, `changed`, `activation`,
`support`, `behavior_verified` and `supports_global_override`. Each setting has
boolean `explicit`, `inherited` and `effective` fields.

For managed Android, call
`worker_request(backend, id, action, package, settings=None, revision=None)` with
action `get`, `set` or `reset`. This requires running state and worker capability
`app_compatibility`. The generation-bound worker request is:

```json
{"op":"compatibility","generation":"worker-generation","action":"get","package":"com.example.app"}
```

Set adds `settings` and `revision`; reset adds `revision`. The worker constructs
its bounded `lxc-attach` argv adapter scoped to its own `instance`/`identifier`,
then calls `dispatch(android, request, identifier, generation)`. It puts that
result in `response['compatibility']`; errors go in `response['app_error']`.
The response retains worker state, ID and generation. A generous bounded socket
timeout must allow preflight, writes, readbacks and rollback; adapters with a
2-second command timeout fit within a 30-second request. A command output cap of
64 KiB is sufficient for filtered package queries; property limits remain 8 KiB.

For Default runtime, after owner/session checks, call
`read(android, package, 'default', session_generation)`,
`apply(android, package, 'default', session_generation, settings, revision)` or
`reset(android, package, 'default', session_generation, revision)`. The same
adapter must target Default's trusted LXC directory and require running state.

Load `src-frontend/app-compatibility.js` before `main.js` and create the dialog:

```javascript
const appCompatibility = AnvilAppCompatibility.create({
  request: controllerRequest, document, toast: showAppToast,
});
await appCompatibility.open(runtimeId, packageName, appLabel);
```

`open` also accepts an object with an `id`. The module creates its own modal,
uses `textContent` for all variable text, ignores stale reads and mutations after
close/app/runtime replacement, validates response identity and settings, and
requires refresh after uncertain mutations. No polling or runtime changes occur
until the user opens or acts in the dialog.

## Isolated verification

- `python3 services/compatibility-test.py`: 28 tests, including exact installed
  package validation, injection, settings allowlist, unsupported grammar and
  capacity, wildcard inheritance/reset, revision/generation staleness, two-runtime
  isolation, every write/readback failure, rollback failure and worker identity.
- `node crates/anvildroid-gui/compatibility-check.cjs`: 10 tests covering selected
  runtime/package, XSS-safe rendering, busy serialization, inherited settings,
  reset payloads, stale reads/errors/mutations, close/reopen, malformed reports,
  backend failure/recovery and form submission.

`python3 services/features-test.py` additionally exercises real feature dispatch
with inert stores and Android adapters: owner authorization before paths, rejected
fields/descriptors, runtime/session replacement, busy/deletion gates, compatibility
rollback, component revisions/cache truthfulness and stopped shared-folder gates.

All tests use inert Android and DOM adapters. Live framework behavior, real
Wi-Fi-only app checks and mouse-to-touch behavior on supported images require a
separate manual acceptance session. These tests never alter an installed runtime.
