#!/usr/bin/env python3
"""Read the workspace version and validate stable/prerelease release tags."""
import argparse
from pathlib import Path
import re
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', help='Require vVERSION to match Cargo.toml')
    parser.add_argument('--debian', action='store_true')
    parser.add_argument('--rpm', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / 'Cargo.toml').read_text())['workspace']['package']['version']
    number = r'(?:0|[1-9][0-9]*)'
    if not re.fullmatch(rf'{number}\.{number}\.{number}(?:-(?:alpha|beta|rc)\.{number})?', version):
        parser.error('Use X.Y.Z or X.Y.Z-alpha.N / beta.N / rc.N')
    if args.tag is not None and args.tag != 'v' + version:
        parser.error(f'Tag {args.tag!r} does not match workspace version v{version}')
    # Debian sorts ~rc.1 before the final version.
    print(version.replace('-', '~', 1) if args.debian or args.rpm else version)


if __name__ == '__main__':
    main()
