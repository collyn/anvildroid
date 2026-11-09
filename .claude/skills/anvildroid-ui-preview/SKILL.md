---
name: anvildroid-ui-preview
description: Use when changing anything visible in the AnvilDroid GUI (src-frontend HTML/CSS/JS) and you need to see the result, compare light and dark themes, check layout at the minimum window size, or take before/after screenshots — without building Tauri or running Android.
---

# Previewing the AnvilDroid UI in a browser

## Overview
The real app needs a Rust build, WebKitGTK, the root controller and a running Android. For layout and styling work, none of that is needed: `serve.py` serves `src-frontend/` unchanged and injects a fake `window.__TAURI__` backed by fixture data. **Look at the UI before and after every visual change** — tests do not catch layout bugs.

## Start
```bash
python3 .claude/skills/anvildroid-ui-preview/serve.py --port 1430   # run in background
```
Open with Chrome DevTools MCP (`new_page`) or any browser:

| URL hash | Effect |
|---|---|
| `#theme=dark` / `#theme=light` | theme (stored; **reload** after changing the hash) |
| `#scenario=empty` | first run, **interactive**: Download Android progresses, Create and start runs create → prepare → start, apps appear |
| `#scenario=offline` | controller unreachable (error states) |
| `?view=settings` / `?view=controls` | open those pages at launch (query, not hash) |

Navigate by clicking `.nav-item[data-page="apps|runtime|controls|settings"]`; Images and ADB are sub-tabs: `[data-subtab="images|adb"]`. Resize with `resize_page` (900×600 minimum, 1200×800 default).

## Checklist for a visual change
1. Screenshot the affected page **before** editing (dark + light).
2. Edit; reload with cache ignored (server sends `no-store`).
3. Screenshot again in **both themes**. Compare.
4. Check the minimum window size **900×600** and the default **1200×800**. Use `resize_page`; if it errors with "Restore window to normal state", set the size with `evaluate_script` + an iframe or just check with `document.documentElement.clientWidth`.
5. Check the states your change touches: empty (`#scenario=empty`), error (`#scenario=offline`), busy/long text.
6. Console: `window.__PREVIEW__.missing` lists backend calls with no fixture.
7. Spacing/alignment: run `audit-spacing.js` (pass its contents as `evaluate_script`'s `function`) on every page and settings tab you touched. It must return `[]` — buttons ≥ 8px below the content above them, buttons in one row on the same top. Screenshots miss 0–6px problems; this does not.

## Fixtures
`fixtures.js` maps Tauri command names, or `controller:<op>` / `controller:<op>:<id>` for controller requests, to results (a value or `args => value`). Scenarios override `default`; `undefined` removes a key. A missing fixture rejects, so the UI shows its real error path, and a warning `[preview] no fixture for X` appears in the console.

Working on a panel whose data is missing (e.g. `controller:gpu_info`)? Add a fixture. Get the shape from the Python handler in `services/` or the Rust type in `crates/anvildroid-core`, not by guessing — the frontend validates shapes (`Invalid app inventory`, `Invalid library preferences`).

## Limits — say so in reports
- Chrome (Blink) is not WebKitGTK. Fonts, `<select>` popups, scrollbars and some CSS (`:has`, container queries) can differ slightly. The real window is transparent with a GTK titlebar (`titlebar.rs`), not shown here.
- App icons (`appicon://`) do not load; placeholders show instead.
- Preview proves layout and styling only, not behavior against a real controller. Run `node --test tests/*.cjs` for logic.
