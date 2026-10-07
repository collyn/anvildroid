#!/usr/bin/python3
"""Apply safe Waydroid host fixes that survive AnvilDroid package upgrades."""
from pathlib import Path
import os
import shutil


TARGET = Path('/usr/lib/waydroid/tools/helpers/run_core.py')
MARKER = '# AnvilDroid: close selector and pipe'


def main():
    if os.geteuid() != 0 or not TARGET.is_file():
        return
    text = TARGET.read_text()
    if MARKER in text or ('sel.close()' in text and 'process.stdout.close()' in text):
        return
    start = '    sel.register(process.stdout, selectors.EVENT_READ)\n'
    end = '        return (process.returncode, b"".join(output_buffer).decode("utf-8"))\n'
    begin = text.find(start)
    finish = text.find(end, begin)
    if begin < 0 or finish < 0:
        return
    finish += len(end)
    body = text[begin:finish]
    indented = ''.join('    ' + line if line.strip() else line for line in body.splitlines(True))
    replacement = ('    ' + MARKER + '\n'
                   '    try:\n' + indented +
                   '    finally:\n'
                   '        sel.close()\n'
                   '        process.stdout.close()\n')
    backup = TARGET.with_name(TARGET.name + '.anvildroid-fd-backup')
    if not backup.exists():
        shutil.copy2(TARGET, backup)
    temporary = TARGET.with_suffix('.anvildroid.tmp')
    temporary.write_text(text[:begin] + replacement + text[finish:])
    os.chmod(temporary, TARGET.stat().st_mode & 0o777)
    os.replace(temporary, TARGET)


if __name__ == '__main__':
    main()
