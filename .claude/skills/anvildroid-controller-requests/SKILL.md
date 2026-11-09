---
name: anvildroid-controller-requests
description: Use when adding or changing an AnvilDroid controller request/op, editing services/runtime-controller.py, runtime-features.py, runtime-worker.py or other root-side Python services, persisting new controller state, or touching the Rust Request enum in anvildroid-core controller.rs; also when a request fails with INVALID_REQUEST, NOT_FOUND or a serde unknown-field error.
---

# AnvilDroid controller requests (root side)

## Overview
`services/` runs **as root** behind `/run/anvildroid/control.sock` (mode 0666). The only security boundary is the controller: `SO_PEERCRED` uid + record ownership. One op lives in three layers that must match exactly: Python field set, Rust `Request` enum, JS call site.

## Adding an op end to end
1. **Python handler**
   - Per-runtime feature: new module loaded at top of `services/runtime-features.py` with `x = module('runtime-x')`; add op + exact field set to `FIELDS`, branch in its `dispatch`, call `authorize()`. Copy the `camera_get` / `runtime-camera.py` pattern.
   - Core op: `fields[...]` entry + branch in `Store._dispatch` (`runtime-controller.py`).
   - Fields are checked with exact `set(request) == {...}`. Extra or missing key = `INVALID_REQUEST`.
   - Can block for seconds? Add to `SLOW_OPERATIONS`. Long job? Use the job pattern (`active_jobs`, `pool.submit`, `reserve_runtime`, `worker_rpc`) — never hold `self.mutex` during slow I/O.
   - Mutations require `store.android.quiescent(id)` and state in `('Prepared','Stopped')` where relevant.
   - Support `id == 'default'` (System Waydroid) via `store.existing`, or reject it explicitly.
2. **Rust**: variant in `enum Request` (`crates/anvildroid-core/src/controller.rs`). Enum is `tag="op"`, `rename_all="lowercase"`, `deny_unknown_fields` → multi-word ops need `#[serde(rename = "snake_op")]`; field names must equal the Python set. File-carrying ops need a dedicated fd function (see `install`, `import_image`), not `request()`. Add a test in `controller.rs`.
3. **CLI** (optional): only if the op should be scriptable, add it in `crates/anvildroid-cli/src/managed_cli.rs`.
4. **GUI**: generic Tauri command `runtime_controller` already forwards any `Request`. Frontend calls `controllerRequest({op: 'x', id})` in `src-frontend/main.js`. No new Tauri command needed.
5. **Tests**: `services/<name>-test.py` with fakes (temp dirs, mock store) + add to `scripts/test-runtime-features.sh`. See anvildroid-shipping-new-files.
6. If the op changes user-visible state without a registry save, add it to `watch_token(uid)` or the GUI never refreshes.

## Security invariants — never break
- Uid comes only from `SO_PEERCRED`. Never read a uid from the request.
- Every per-runtime op filters `owner_uid == uid`. Ids match `r-[0-9a-f]{32}`; only `default` is special.
- Never accept a client path for privileged reads/writes. Use a caller-opened fd (`runtime-transfer.py: receive`) or `storage.open_directory` (pins components, rejects symlinks and `..`).
- Commands: fixed argv with timeout (`linux.run_bounded`). No shell, no string interpolation.
- State: read with `read_file` (O_NOFOLLOW, root-owned, size cap), write with `storage_module.atomic`. Never `Path.write_text` for state. New state file: handle missing file, prune orphans.
- `notification-relay.py` runs as the desktop user — never add root-side logic to it.
- Diagnostics must redact credentials.

## Code conventions
- Python 3.11+ and 3.12, **stdlib only** (no pip deps).
- Hyphenated modules load via `importlib.util.spec_from_file_location(... Path(__file__).with_name('runtime-x.py'))`. Never `import runtime_x`. Modules share objects (`worker_module.gpu`), not imports.
- Errors: raise `RequestError(code, msg)` (features: `'FEATURE'`). No broad `except Exception: pass`.
- AF_UNIX paths max 108 bytes — check long paths (past bugs).
- Tests: never hardcode uid 1000 or real paths; use the runner's uid (`os.getuid()`).

## Running checks
```bash
python3 services/<name>-test.py
python3 -m unittest discover -s tests -p 'test_runtime_worker.py'
sh scripts/test-runtime-features.sh      # full inert suite
cargo test --locked -p anvildroid-core -p anvildroid-cli
```
Mount tests (`--mount-test`) need sudo; socket/ownership tests fail inside restricted sandboxes — run in a normal shell and say so if skipped.
