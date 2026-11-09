---
name: anvildroid-shipping-new-files
description: Use when adding, renaming or deleting any file in the AnvilDroid repo — a services/*.py module, native/*.sh/.rc helper, scripts/ installer script, built .so/.apk/.jar, Java file, or test file — or when a package, AppImage or installed controller fails with ImportError / missing file / "Missing setup dependency".
---

# Shipping new files in AnvilDroid

## Overview
Nothing ships by glob in the deb/rpm/pkg path. Every new file must be added by hand to several lists, and `.gitignore` silently ignores most new test files. A file that works from the source tree can be missing from the installed controller.

**Always run the checker after adding/renaming/deleting files:**
```bash
python3 .claude/skills/anvildroid-shipping-new-files/check-registration.py
```
It checks module lists and finds gitignored source files. It does NOT check native helpers, build outputs or scripts — use the table below for those.

## Registration table

| New file | Must also edit |
|---|---|
| `services/runtime-X.py` (controller/worker module) | `MODULES` in `scripts/install-runtime-controller.py`; main `for f in ...` loop in `scripts/stage-package.sh` |
| …a feature module (per-runtime feature op) | also `features = {...}` in `scripts/test-runtime-features.sh`, `required` in `scripts/verify-package.py`, `REQUIRED` in `scripts/verify-appimage.py` (`services/` prefix) |
| …loaded by `setup-waydroid.py` | `SETUP_MODULES` in `scripts/setup-waydroid.py` + first (short) loop in `stage-package.sh` |
| `services/X-test.py` | add `X` to the `for suite in ...` line of `scripts/test-runtime-features.sh`, otherwise it never runs. Name it `*-test.py`, never `*_test.py` (ignored) |
| `native/anvildroid-*.sh` / `.rc` | explicit `for f in anvildroid-apps.rc ...` loop in `stage-package.sh`; `scripts/controller-payload.txt` |
| new build output (`.so`, `.apk`, `.jar` under `target/`) | build script; `scripts/runtime-artifacts.txt`; `stage-package.sh` install line; check lists in `scripts/package-portable.sh` and `scripts/package-appimage.sh`; `scripts/controller-payload.txt`; `verify-package.py` if required |
| `native/shutdown/*.java` | explicit file list in `scripts/build-shutdown.sh` |
| `scripts/install-*.py` or other script the controller runs | `stage-package.sh`; `cp` lists in `package-portable.sh` and `package-appimage.sh`; `controller-payload.txt` |
| new top-level install path (outside `/usr/local/lib/anvildroid-controller/`) | hand-written `%files` / `%dir` in `scripts/build-rpm.sh` |
| `tests/test_runtime_*.py` (unittest style) or any `tests/` file except `tests/test_*.cjs` | `!/tests/<exact name>` line in `.gitignore` after `/tests/*` (acceptance: after `/tests/acceptance/*`) |
| `crates/*/tests/*.rs`, `crates/*/examples/*` | `!` exception line in `.gitignore` |
| `scripts/test-*`, `smoke-*`, `probe-*`, `benchmark-*` | ignored on purpose (local experiments). Use a different name or add `!` line |

Verify ignore status: `git check-ignore -v <path>` and confirm `git status` lists the file.

## Test naming
- `services/` tests: `services/<name>-test.py` (hyphen). Not ignored.
- `tests/` Python tests: `tests/test_runtime_<name>.py`. Ignored until whitelisted.
- `tests/` Node tests: `tests/test_<name>.cjs`. Allowed by glob.

## Notes
- `controller-payload.txt` syntax: one path per line, trailing `/` = whole tree, leading `?` = optional. `services/` is already a whole tree.
- The GUI embeds `controller-payload.txt` via `include_str!` — editing it means a GUI rebuild.
- `.claude/`, `/docs/*` (except `TECHNICAL.md`, `images/`), `native/arm-probe/`, `native/csd/` are gitignored.
- Renaming/deleting a module: remove it from every list above, or `check_payload` / verifiers fail.

## Common mistakes
- Adding the module only to `MODULES` — deb/rpm/pkg still miss it (`stage-package.sh`).
- New Python test in `tests/` not whitelisted — passes locally, never committed.
- New `services/*-test.py` not in `test-runtime-features.sh` — never runs in CI.
