#!/usr/bin/python3
"""Point installed AnvilDroid package sources at the organization's package
repository. Runs from package post-install hooks; only rewrites files that
still name the previous location, and never fails the package transaction."""
import os
from pathlib import Path
import re
import sys

OLD = 'https://collyn.github.io/anvildroid'
NEW = 'https://anvildroid.github.io/packages'
SOURCES = (Path('/etc/apt/sources.list.d/anvildroid.list'),
           Path('/etc/yum.repos.d/anvildroid.repo'),
           Path('/etc/zypp/repos.d/anvildroid.repo'))
PACMAN = Path('/etc/pacman.conf')


def rewrite(path, change):
    try:
        if path.is_symlink() or not path.is_file():
            return False
        text = path.read_text()
        updated = change(text)
        if updated == text:
            return False
        temporary = path.with_name(path.name + '.anvildroid-tmp')
        temporary.write_text(updated)
        os.chmod(temporary, path.stat().st_mode & 0o7777)
        os.replace(temporary, path)
        return True
    except OSError as error:
        print(f'AnvilDroid: could not update {path}: {error}', file=sys.stderr)
        return False


def pacman_section(text):
    # Only the [anvildroid] section's Server lines.
    return re.sub(r'(?ms)^(\[anvildroid\][^\[]*)', lambda match: match.group(1).replace(OLD, NEW), text)


def main(root=Path('/')):
    changed = [path for path in SOURCES if rewrite(root / path.relative_to('/'), lambda text: text.replace(OLD, NEW))]
    if rewrite(root / PACMAN.relative_to('/'), pacman_section):
        changed.append(PACMAN)
    for path in changed:
        print(f'AnvilDroid: package source moved to {NEW} ({path})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
