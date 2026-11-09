---
name: anvildroid-ui-design-system
description: Use when designing or restyling AnvilDroid GUI screens, adding a page, panel, dialog, badge, button, toast, empty/error/loading state, or writing user-facing text in src-frontend — anything where new UI must look and behave like the existing app.
---

# AnvilDroid UI design system and UX patterns

## Overview
The app already has a complete, consistent system. **Reuse existing classes and helpers; do not invent new colors, sizes, dialogs or notification mechanisms.** Before writing new CSS, search for an existing class: `grep -n "\.<name>" src-frontend/*.css`.

All paths below are relative to `crates/anvildroid-gui/src-frontend/`.

## Visual style: game HUD, square, flat
- **Two themes (Settings → Appearance → Theme):** Classic (default, rounded) and HUD (square, uppercase). `theme.js` sets `data-skin` on `<html>`. Navigation and layout are identical in both: shared components go in `layout.css`, written in the Classic look with `var(--radius)` / `var(--radius-sm)` (Classic 12/8px, HUD 6/4px). HUD-only styling goes in `skin.css`, where **every selector is scoped with `:where([data-skin="hud"])`** (zero specificity; `tests/test_theme_skin.cjs` enforces it). In HUD, `skin.css` resets every radius to 0 and opts components back in — add a new component's class to those two lists there.
- **Vertical rhythm:** a button or button row that follows text in a stacked block gets 12px above it (`layout.css` rule on `p/label/.hint/… + .btn`). Content after an opened `<details>` summary gets 10px. A wrapper type that isn't covered (e.g. a grid `div` followed by a button) needs its own `+ .btn` rule — verify with `anvildroid-ui-preview/audit-spacing.js`.
- **Buttons that share a row share one size** (36px high, 0 14px padding) — `skin.css` enforces it for `.runtime-actions`, `.page-header`, `.dialog-actions`. Don't add per-button padding/height.
- Palette: **accent presets** chosen in Settings → Appearance, stored as `localStorage['anvildroid.accent']`, applied by `theme.js` as `<html data-accent="…">` before first paint. Default `blue` (original navy surfaces); `forge` (orange), `toxic` (green), `crimson` (red), `synthwave` (purple), `ice` (cyan) use neutral steel surfaces.
- **Never hardcode an accent hue.** Use `--accent`, `--accent-text`, `--accent-strong`, `--accent-bg`, `--bg-selected`, `--on-accent`; every preset defines them for dark and light. A new preset needs: dark + light blocks in `theme.css` (all 7 accent tokens), a swatch rule, an entry in `ACCENTS` in `theme.js`, a picker button in `index.html`; `tests/test_theme_accent.cjs` checks the CSS parts. Check text on `--accent` and on `--accent-bg` stays ≥ 4.5:1 in both themes.
- Labels read like a HUD: page titles, nav, buttons, tags and table headers are UPPERCASE with letter-spacing; page titles get the orange square marker automatically.
- Depth = border color, not shadow: hover/active → `--accent` border; selected → `--accent-bg` / `--bg-selected` fill.
- New shared components live in `layout.css`; their HUD restyle (uppercase, square) goes in `skin.css` under the `:where([data-skin="hud"])` scope. Preview both themes (`AnvilTheme.setSkin('classic'|'hud')`).

## Tokens (`theme.css`) — the only source of color
- Every color in other CSS is `var(--token)`. **No hex/rgb literals outside `theme.css`** (current count: 0 — keep it 0). New color need? Add a token in both `:root` (dark) and `:root[data-theme="light"]`.
- Surfaces: `--bg-app`, `--bg-sidebar`, `--bg-panel`, `--bg-surface`, `--bg-raised`, `--bg-hover`, `--bg-selected`
- Borders: `--border-subtle`, `--border`, `--border-strong`
- Text: `--text-primary`, `--text-soft`, `--text-secondary`, `--text-muted`
- Accent: `--accent`, `--accent-text`, `--accent-strong`, `--accent-bg`, `--on-accent`
- Status: `--success|warning|danger` + `-bg`, `-border` (`--success-text`)
- Shadows: `color-mix(in srgb, var(--shadow) N%, transparent)`
- Type: `--font-xs/sm/md/lg/xl` = 11/12/13/15/22px. Radius: `--radius-sm` 4px, `--radius` 6px.
- Spacing in use: 4, 6, 8, 10, 12, 16, 24px. Stay on that scale.
- Light-theme-only tweaks go at the bottom of `theme.css` under `:root[data-theme="light"] …`.

## Components
| Need | Use |
|---|---|
| Page | `<section class="page" id="page-x">` → `.page-header` with `<h1 class="page-title">` + `.runtime-actions` button group |
| Buttons | `.btn` (default), `.btn-primary` (one per area), `.btn-quiet`, `.btn-danger` (destructive) |
| Runtime/item state | `.runtime-status.info|success|warning|error` |
| Connection badge | `.status-badge.connected|disconnected` |
| Inline message | `<p class="runtime-feedback error|warning|success">`; errors get `role="alert"` |
| Secondary text | `.hint` |
| Nothing to show | `<div class="empty-state"><p>What is empty.</p><p class="hint">What to do next.</p></div>` |
| Sections of one area (Devices · Android images · ADB) | `.subtabs` + `button.subtab[data-subtab="<page>"][aria-selected]` in the page header; one sidebar entry |
| First-run guide | `quick-start.js` (`AnvilQuickStart.render(state, escapeHtml)`), shown on Apps while no device runs |
| Device with a quick action | `.device-chip[data-state]` + `button[data-library-start]` (Apps page) |
| Long settings page | game-style menu from `settings-tabs.js`: sections are `details.runtime-settings-group` / `.runtime-setting-row` / `.runtime-details`; the module adds the left category nav (top tab row under 760px container width) and shows one section. New section = new `<details>` of one of those classes; add its title to `ORDER` in `settings-tabs.js` |
| Advanced-only control | `data-level="advanced"` (hidden in Simple settings view) |
| `<select>` | plain `<select>`; `select-menu.js` themes the popup automatically |
| Icons | inline SVG 24×24, `stroke="currentColor"`, stroke-width ~1.75, round caps, `aria-hidden="true"` |

## Feedback and dialogs — use the helpers
- Short result: `showAppToast(message, 'info'|'success'|'error')`. Duration scales with length; errors stay 10 s.
- Long task: `showProgressToast(key, {title, detail, percent, cancel})` → `clearProgressToast(key)`, then a normal toast for the result.
- Confirm / prompt: `await AppDialog.confirm(message, {title, okLabel, cancelLabel})` / `AppDialog.prompt(...)`. **Never `window.confirm`/`alert`** (blocks, unthemed). It queues dialogs and focuses Cancel, so Enter does not confirm destructive actions.
- Custom dialog: native `<dialog>` in `index.html` with `aria-labelledby` (+ `aria-describedby`), opened with `showModal()`, buttons in `.dialog-actions` (Cancel first, primary last).
- Busy state: disable the button and change its label (`'Refreshing…'`), restore in `finally`.

## Safety and accessibility
- **Every interpolated value in `innerHTML` goes through `escapeHtml()`** — app labels, package names, runtime names and error messages come from Android or users. Prefer `textContent` when not building markup.
- Status text that updates: `role="status"` / `aria-live="polite"`. Focus ring is global (`:focus-visible`); never remove outlines.
- Clickable non-buttons need `role="button"`, `tabindex="0"` and keyboard handling. `prefers-reduced-motion` is already honored globally.
- Window minimum is 900×600 (`tauri.conf.json`). Long names truncate with ellipsis (`min-width: 0` on flex/grid children). Tight areas use container queries (see `.runtime-quick`).

## Information architecture (beginner first)
- Sidebar has 4 entries: **Apps · Devices · Game Controls · Settings**. Images and ADB are sub-tabs of Devices (`navigatePage` maps them to the Devices nav item). Don't add sidebar entries for expert tools — add a sub-tab or an Advanced section.
- Apps is the home screen: a user must be able to go from nothing to a running app without leaving it (Get started guide, device chips with Start).
- Expert/maintenance controls (restart controller, diagnostics) live behind **Troubleshoot** or `data-level="advanced"`, never in the main header.
- Primary actions show a text label; icon-only buttons are for secondary actions and need `title` + `aria-label`.

## Writing UI text
Plain English (no i18n layer). Users see **device** (not runtime) and **Android image**. Sentence case in markup ("Install APK", "New device"); CSS uppercases HUD labels. Errors say what failed, then what to do ("… Use Refresh to retry."). Ellipsis `…` for in-progress labels. Names: "System Waydroid", "runtime", "image", "controller".

## Common mistakes
- Hardcoding a color or px font size that a token covers.
- New toast/modal implementation instead of `showAppToast` / `AppDialog`.
- Checking only the dark theme. Verify both with anvildroid-ui-preview.
- Adding page-specific overrides for header/filters spacing instead of fixing shared rules in `shell.css` / `management.css`.
