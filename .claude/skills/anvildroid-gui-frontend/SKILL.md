---
name: anvildroid-gui-frontend
description: Use when editing the AnvilDroid GUI — crates/anvildroid-gui (Tauri 2 Rust commands) or src-frontend HTML/CSS/JS (main.js, theme.css, lazy-features.js) — or when tests/*.cjs or crates/anvildroid-gui/*-check.cjs fail after a frontend refactor, or a Tauri command is "not found" at runtime.
---

# AnvilDroid GUI and frontend

## Overview
Tauri 2 + WebKitGTK. Frontend is plain global-script vanilla JS in `crates/anvildroid-gui/src-frontend/` — **no bundler, no npm, no framework, no ES modules, no i18n**. `tauri::generate_context!()` embeds the folder at compile time, so frontend edits need a Rust rebuild to show up.

## Tests slice main.js by text — rename = break
`tests/*.cjs` and `crates/anvildroid-gui/*-check.cjs` read `main.js` and cut code with `source.indexOf('<anchor>')`, then run it in `vm`. Anchors include comments and exact signatures, e.g. `'function navigatePage('`, `'async function appMenuAction('`, `'let customUrlState ='`, `'// Runtime table selection is independent'`.

Before renaming, moving, reordering or reformatting anything in `main.js`:
```bash
grep -rn "<identifier or comment>" tests/*.cjs crates/anvildroid-gui/*.cjs
```
Update the anchors in the same change. Keep `if (typeof module !== 'undefined') module.exports = X` tails on small modules (Node `require`s them).

## Adding a GUI feature
- **Controller data**: no new Tauri command. Use `controllerRequest({op, ...})`, backed by the generic `runtime_controller` command. Add the op per anvildroid-controller-requests.
- **New Tauri command**: `#[tauri::command]` in `src/main.rs` or `preferences.rs` **and** add it to `tauri::generate_handler![...]` in `main.rs` (missing = silently absent). Blocking work through `blocking(move || ...)`; mutations take `OpGuard`; return `Result<_, String>`. No capability edit needed (only for new plugins).
- `invoke` wrapper in `main.js` auto-injects `runtimeId: 'default'`; Rust params are `runtime_id: String` (camelCase→snake_case by Tauri).
- Rarely used page/feature: register in `lazy-features.js` and load via `lazyFeatures.ensure()`, not a new eager `<script>` (startup time matters).
- The controller is socket-activated and exits when idle: GUI code must tolerate a missing socket.

## UI conventions
- Visual/UX rules (tokens, components, toasts, dialogs, escaping, copy): **REQUIRED SUB-SKILL** anvildroid-ui-design-system.
- Seeing the result in a browser without Tauri: **REQUIRED SUB-SKILL** anvildroid-ui-preview.

## Build caveats
- `crates/anvildroid-gui/build.rs` runs `scripts/build-runtime-desktop.sh` and `build-touch-probe.sh` (needs R8 jar + Android API 23 jar from `scripts/prepare-ci-inputs.sh`) plus GTK/WebKitGTK/Wayland dev libs. If missing, `cargo build/check --workspace` fails — that is environment, not your bug. For core/CLI work use `-p anvildroid-core -p anvildroid-cli`.
- Never edit `crates/anvildroid-gui/gen/` (generated, ignored). Do not add `"version"` to `tauri.conf.json`.

## Checks
```bash
node --test tests/*.cjs
for c in compatibility shares runtime-actions uninstall; do node crates/anvildroid-gui/$c-check.cjs; done
cargo clippy --workspace --all-targets --locked -- -D warnings
```
