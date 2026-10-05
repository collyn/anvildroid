#!/usr/bin/env python3
"""Install the IME APK and one KDE window rule; keep native overlay separate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

PACKAGE = "org.anvildroid.ime"
IME = PACKAGE + "/.BridgeIme"
RULE = "anvildroid-native-titlebar"
RULE_BODY = """Description=AnvilDroid native desktop titlebar
wmclass=^waydroid\\..*
wmclassmatch=3
wmclasscomplete=false
noborder=false
noborderrule=2
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def android(*args):
    return run("sudo", "-n", "waydroid", "shell", "--", *args)


def sections(text):
    return list(re.finditer(r"(?m)^\[([^\]\n]+)\][ \t]*\n", text))


def group(text, name):
    groups = sections(text)
    matches = [(m.end(), groups[i + 1].start() if i + 1 < len(groups) else len(text))
               for i, m in enumerate(groups) if m[1] == name]
    if len(matches) > 1:
        raise ValueError("Duplicate KWin section: " + name)
    return text[slice(*matches[0])] if matches else None


def put_group(text, name, body):
    groups = sections(text)
    for i, m in enumerate(groups):
        if m[1] == name:
            end = groups[i + 1].start() if i + 1 < len(groups) else len(text)
            return text[:m.end()] + body.rstrip() + "\n\n" + text[end:]
    return text.rstrip() + "\n\n[" + name + "]\n" + body.rstrip() + "\n"


def set_key(body, key, value):
    pattern = r"(?m)^" + re.escape(key) + r"=.*$"
    if len(re.findall(pattern, body)) > 1:
        raise ValueError("Duplicate KWin key: " + key)
    if re.search(pattern, body):
        return re.sub(pattern, lambda _: key + "=" + value, body)
    return body.rstrip() + "\n" + key + "=" + value + "\n"


def configure_rules(text, rule=RULE, body=RULE_BODY):
    existing = group(text, rule)
    if existing is not None:
        entries = lambda body: dict(line.split("=", 1) for line in body.splitlines()
                                    if "=" in line and not line.lstrip().startswith("#"))
        if entries(existing) != entries(body):
            raise ValueError("Existing AnvilDroid rule differs; refusing to overwrite")
    general = group(text, "General") or ""
    match = re.search(r"(?m)^rules=(.*)$", general)
    rules = [r for r in (match[1].split(",") if match else []) if r]
    # Preserve legacy numbered rules by registering their section names.
    if not match:
        rules = [m[1] for m in sections(text) if m[1].isdigit()]
    if rule not in rules:
        rules.append(rule)
    general = set_key(general, "rules", ",".join(rules))
    general = set_key(general, "count", str(len(rules)))
    result = put_group(text, "General", general)
    if existing is None:
        result = put_group(result, rule, body)
    return result


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing symlink: " + str(path))
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".anvildroid-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def validate_bundle(bundle):
    data = json.loads((bundle / "desktop.json").read_text())
    if data.get("format") != 1 or data.get("package") != PACKAGE or data.get("rule") != RULE_BODY:
        raise ValueError("Unsupported desktop bundle")
    apk = bundle / "AnvilDroidIme.apk"
    if apk.is_symlink() or sha(apk.read_bytes()) != data["apk_sha256"]:
        raise ValueError("IME APK checksum mismatch")
    run("apksigner", "verify", str(apk))
    badging = run("aapt", "dump", "badging", str(apk))
    if not badging.startswith("package: name='" + PACKAGE + "'"):
        raise ValueError("Wrong APK package")
    return apk


def snapshot_apk():
    lines = android("pm", "path", PACKAGE).splitlines()
    if not lines:
        return None
    if len(lines) != 1 or not lines[0].startswith("package:/data/app/"):
        raise ValueError("Expected one installed IME APK under /data/app")
    return subprocess.check_output(
        ["sudo", "-n", "waydroid", "shell", "--", "cat", lines[0][8:]])


def check(bundle):
    validate_bundle(bundle)
    run("qdbus6", "org.kde.KWin", "/KWin", "org.freedesktop.DBus.Peer.Ping")
    if android("getprop", "sys.boot_completed") != "1":
        raise ValueError("Android must be running and booted")


def install(bundle, config, state):
    if config.is_symlink():
        raise ValueError("Refusing symlink KWin config")
    check(bundle)
    before = config.read_bytes() if config.exists() else None
    after = configure_rules((before or b"").decode()).encode()
    previous = android("settings", "get", "secure", "default_input_method")
    if previous in ("", "null"):
        raise ValueError("No previous IME; cannot prepare restoration")
    enabled = android("settings", "get", "secure", "enabled_input_methods")
    old_apk = snapshot_apk()
    new_apk = validate_bundle(bundle).read_bytes()
    backup = state / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700)
    if before is not None:
        (backup / "kwinrulesrc.before").write_bytes(before)
    if old_apk is not None:
        (backup / "previous.apk").write_bytes(old_apk)
    (backup / "installed.apk").write_bytes(new_apk)
    record = {
        "format": 1, "status": "prepared", "config": str(config),
        "before": sha(before) if before is not None else None,
        "after": sha(after), "config_mode": config.stat().st_mode & 0o777 if before is not None else 0o600,
        "previous_ime": previous, "enabled_before": enabled,
        "apk_before": sha(old_apk) if old_apk is not None else None,
        "apk_after": sha(new_apk),
    }
    atomic(backup / "record.json", json.dumps(record, indent=2).encode())
    print("Recovery record: " + str(backup), flush=True)
    if old_apk != new_apk:
        run("waydroid", "app", "install", str((backup / "installed.apk").resolve()))
        if snapshot_apk() != new_apk:
            raise ValueError("APK install did not produce expected bytes; use recovery record")
    if before != after:
        atomic(config, after, record["config_mode"])
        run("qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure")
    android("ime", "enable", IME)
    android("ime", "set", IME)
    if android("settings", "get", "secure", "default_input_method") != IME:
        raise ValueError("IME selection failed; use recovery record")
    record["status"] = "installed"
    atomic(backup / "record.json", json.dumps(record, indent=2).encode())
    print("Desktop components installed. Rollback: python3 scripts/desktop-setup.py rollback " + str(backup))


def rollback(backup, config):
    if config.is_symlink():
        raise ValueError("Refusing symlink KWin config")
    record = json.loads((backup / "record.json").read_text())
    if record.get("format") != 1 or record.get("status") not in ("prepared", "installed"):
        raise ValueError("Inactive recovery record")
    if record["config"] != str(config):
        raise ValueError("Recovery record belongs to another desktop config")
    before_file = backup / "kwinrulesrc.before"
    before = before_file.read_bytes() if record["before"] is not None else None
    if before is not None and sha(before) != record["before"]:
        raise ValueError("KWin backup checksum mismatch")
    current = config.read_bytes() if config.exists() else None
    current_hash = sha(current) if current is not None else None
    if current_hash not in (record["before"], record["after"]):
        raise ValueError("KWin config changed after install; refusing whole-file restoration")
    old = (backup / "previous.apk").read_bytes() if record["apk_before"] else None
    if old is not None and sha(old) != record["apk_before"]:
        raise ValueError("APK backup checksum mismatch")
    current_apk = snapshot_apk()
    if (sha(current_apk) if current_apk else None) not in (record["apk_before"], record["apk_after"]):
        raise ValueError("IME APK changed since install")
    selected = android("settings", "get", "secure", "default_input_method")
    if selected not in (IME, record["previous_ime"]):
        raise ValueError("IME selection changed since install")
    if old is not None and current_apk != old:
        run("waydroid", "app", "install", str((backup / "previous.apk").resolve()))
        if snapshot_apk() != old:
            raise ValueError("APK restoration refused (e.g. downgrade); no uninstall attempted")
    android("ime", "set", record["previous_ime"])
    if android("settings", "get", "secure", "default_input_method") != record["previous_ime"]:
        raise ValueError("Previous IME could not be selected")
    enabled_before = [v.split(";")[0] for v in record["enabled_before"].split(":")]
    if IME not in enabled_before:
        android("ime", "disable", IME)
    # A newly installed APK is retained disabled, never silently uninstall app data.
    if current_hash != record["before"]:
        if before is None:
            config.unlink()
        else:
            atomic(config, before, record["config_mode"])
        run("qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure")
    record["status"] = "rolled-back"
    atomic(backup / "record.json", json.dumps(record, indent=2).encode())
    print("Previous desktop settings restored; app data retained")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["package", "check", "install", "rollback"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise ValueError("Run as the desktop user, not sudo; Android calls use sudo -n internally")
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kwinrulesrc"
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "anvildroid/desktop"
    if args.action == "package":
        if args.path.exists():
            raise ValueError("Bundle destination exists; use a new directory")
        apk = Path("target/ime/AnvilDroidIme.apk")
        run("apksigner", "verify", str(apk))
        args.path.mkdir(parents=True)
        atomic(args.path / "AnvilDroidIme.apk", apk.read_bytes(), 0o644)
        atomic(args.path / "desktop.json", json.dumps({
            "format": 1, "package": PACKAGE, "apk_sha256": sha(apk.read_bytes()), "rule": RULE_BODY,
        }, indent=2).encode(), 0o644)
        validate_bundle(args.path)
        print("Packaged desktop components: " + str(args.path))
    elif args.action == "check":
        check(args.path)
        configure_rules(config.read_text() if config.exists() else "")
        print("PASS APK signature/checksum, KWin rules and booted Android")
    elif args.action == "install":
        install(args.path, config, state)
    else:
        if args.path.is_symlink() or args.path.resolve().parent != state.resolve():
            raise ValueError("Rollback path must be in the desktop state directory")
        rollback(args.path, config)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        raise SystemExit("Desktop setup: " + str(e))
