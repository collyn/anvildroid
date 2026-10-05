#!/usr/bin/env python3
"""Local native-overlay releases. Never changes images or Android app data."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parent.parent
WAYDROID = Path("/var/lib/waydroid")
BACKUPS = Path("/var/lib/anvildroid/backups")
FILES = {
    "vendor/etc/init/anvildroid-apps.rc": "native/anvildroid-apps.rc",
    "vendor/bin/anvildroid-apps.sh": "native/anvildroid-apps.sh",
    "vendor/lib64/libanvildroid-window.so": "target/native/libanvildroid-window.so",
    "system/etc/init/init.waydroid.rc": "target/native/init.waydroid.rc",
    "vendor/overlay/AnvilDroidCaption/AnvilDroidCaption.apk": "target/native/AnvilDroidCaption.apk",
    "vendor/etc/init/anvildroid-tasks.rc": "native/anvildroid-tasks.rc",
    "vendor/bin/anvildroid-tasks.sh": "native/anvildroid-tasks.sh",
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")
    return path


def target(root, relative):
    if relative not in FILES:
        raise ValueError(f"Unknown overlay path: {relative}")
    path = root / relative
    for part in (root, *path.parents, path):
        if part.is_symlink():
            raise ValueError(f"Refusing symlink: {part}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected file or absent destination: {path}")
    return path


def compatibility(base=WAYDROID):
    cfg = configparser.ConfigParser(interpolation=None)
    with (base / "waydroid.cfg").open() as f:
        cfg.read_file(f)
    section = cfg["waydroid"]
    if platform.machine() != "x86_64" or section.get("arch") != "x86_64":
        raise ValueError("This release supports only x86_64 Waydroid")
    images = Path(section["images_path"])
    return {
        "arch": "x86_64",
        "vendor_type": section.get("vendor_type"),
        "system_image": digest(regular(images / "system.img")),
        "vendor_image": digest(regular(images / "vendor.img")),
        "overrides": {
            name: digest(regular(base / "overlay" / name))
            if (base / "overlay" / name).exists() else None
            for name in ("vendor/lib64/hw/hwcomposer.waydroid.so",
                         "system/framework/framework-res.apk")
        },
    }


def create_release(output):
    if output.exists():
        raise ValueError("Release destination already exists; use a new directory")
    expected = compatibility()
    rootfs = WAYDROID / "rootfs"
    composer = regular(rootfs / "vendor/lib64/hw/hwcomposer.waydroid.so")
    framework = regular(rootfs / "system/framework/framework-res.apk")
    # Ensure the caption was built against the currently mounted framework.
    if digest(framework) != digest(PROJECT / "target/native/framework-res.apk"):
        raise ValueError("Framework changed; rebuild native artifacts first")
    payload = {}
    for name, source in FILES.items():
        payload[name] = digest(regular(PROJECT / source))
    manifest = {
        "format": 1, "created": datetime.now(timezone.utc).isoformat(),
        "compatibility": expected,
        "composer_sha256": digest(composer),
        "framework_sha256": digest(framework),
        "files": payload,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".native-release-", dir=output.parent) as work:
        stage = Path(work) / "release"
        stage.mkdir()
        for name, source in FILES.items():
            dest = stage / "payload" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / source, dest)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verify_release(stage, expected)
        stage.rename(output)
    print(f"Packaged: {output}")


def verify_release(release, expected):
    manifest = json.loads(regular(release / "manifest.json").read_text())
    if manifest.get("format") != 1 or set(manifest.get("files", {})) != set(FILES):
        raise ValueError("Unsupported or incomplete release manifest")
    if manifest.get("compatibility") != expected:
        raise ValueError("Android images/architecture differ from this release; rebuild first")
    for name, checksum in manifest["files"].items():
        path = target(release / "payload", name)
        if digest(regular(path)) != checksum:
            raise ValueError(f"Release checksum mismatch: {name}")
    return manifest


def require_stopped():
    if os.geteuid() != 0:
        raise ValueError("Use sudo for install/rollback")
    result = subprocess.run(["lxc-info", "-P", "/var/lib/waydroid/lxc",
                             "-n", "waydroid", "-sH"],
                            text=True, capture_output=True, check=True)
    if result.stdout.strip() != "STOPPED":
        raise ValueError("Stop the Waydroid session and container before install/rollback")


def replace_file(source, destination, mode=0o644):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".anvildroid-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_record(directory, record):
    path = directory / "record.json"
    temp = directory / "record.tmp"
    with temp.open("w") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def restore_files(backup, record, overlay):
    for name, entry in record["files"].items():
        dest = target(overlay, name)
        if entry["before"] is None:
            dest.unlink(missing_ok=True)
        else:
            replace_file(backup / "before" / name, dest, entry["mode"])


def install_release(release, overlay, backup_root, expected):
    manifest = verify_release(release, expected)
    # Preflight every path before any overlay mutation.
    destinations = {name: target(overlay, name) for name in FILES}
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backup_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            + "-" + uuid.uuid4().hex[:8])
    backup.mkdir(mode=0o700)
    record = {"format": 1, "state": "preparing", "compatibility": expected, "files": {}}
    # Copy payload into the protected transaction directory and verify again.
    shutil.copytree(release, backup / "release", symlinks=True)
    verify_release(backup / "release", expected)
    for name, dest in destinations.items():
        entry = {"before": None, "after": manifest["files"][name], "mode": 0o644}
        if dest.exists():
            entry["before"] = digest(dest)
            entry["mode"] = dest.stat().st_mode & 0o777
            saved = backup / "before" / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, saved)
            if digest(saved) != entry["before"]:
                raise ValueError(f"File changed during backup: {name}")
        record["files"][name] = entry
    record["state"] = "prepared"
    save_record(backup, record)
    try:
        for name, dest in destinations.items():
            replace_file(backup / "release/payload" / name, dest)
        for name, dest in destinations.items():
            if digest(dest) != record["files"][name]["after"]:
                raise ValueError(f"Installed checksum mismatch: {name}")
    except Exception:
        restore_files(backup, record, overlay)
        record["state"] = "install-failed-restored"
        save_record(backup, record)
        raise
    record["state"] = "installed"
    save_record(backup, record)
    return backup


def rollback(backup, overlay, expected):
    record = json.loads(regular(backup / "record.json").read_text())
    if record.get("format") != 1 or record.get("state") not in ("installed", "prepared"):
        raise ValueError("Backup is not an active install transaction")
    if record.get("compatibility") != expected or set(record.get("files", {})) != set(FILES):
        raise ValueError("Backup does not match this runtime or file set")
    for name, entry in record["files"].items():
        dest = target(overlay, name)
        current = digest(regular(dest)) if dest.exists() else None
        # A prepared record may be a power-interrupted partial install.
        allowed = (entry["after"], entry["before"]) if record["state"] == "prepared" else (entry["after"],)
        if current not in allowed:
            raise ValueError(f"File changed since install; refusing to overwrite: {name}")
        if entry["before"] is not None:
            saved = target(backup / "before", name)
            if digest(regular(saved)) != entry["before"]:
                raise ValueError(f"Backup checksum mismatch: {name}")
    # Make an interrupted rollback resumable using the same before/after guard.
    record["state"] = "prepared"
    save_record(backup, record)
    restore_files(backup, record, overlay)
    record["state"] = "rolled-back"
    save_record(backup, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["package", "check", "install", "rollback"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.action == "package":
        create_release(args.path)
    elif args.action == "check":
        manifest = verify_release(args.path, compatibility())
        for name in FILES:
            target(WAYDROID / "overlay", name)
        print(f"PASS: {len(manifest['files'])} artifacts, x86_64, matching Android images")
    else:
        require_stopped()
        expected = compatibility()
        if args.action == "install":
            backup = install_release(args.path, WAYDROID / "overlay", BACKUPS, expected)
            print(f"Installed. Rollback: sudo python3 scripts/native-release.py rollback {backup}")
        else:
            # Only accept backups created in the root-owned backup directory.
            if args.path.is_symlink() or args.path.resolve().parent != BACKUPS.resolve():
                raise ValueError(f"Backup must be a direct child of {BACKUPS}")
            rollback(args.path, WAYDROID / "overlay", expected)
            print("Restored previous overlay files. Android app data unchanged.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        raise SystemExit(f"Native release: {e}")
