"""Compare production native/Python keymap parsers without Android or a desktop.

Run from any directory: python3 /path/to/tests/test-keymap-codec-parity.py
The compiler output and executable live only in a TemporaryDirectory.
"""
import importlib.util
from pathlib import Path
import random
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('parity_worker', ROOT / 'services/runtime-worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def v4_corpus():
    """Retain the original 1,600 seeded mutations and six lexical cases."""
    rows = []
    for kind in range(10):
        row = [100, 32768, 32768, 0, kind, 49152, 32768, 250, 3] + [0] * 22
        if kind == 1:
            row[0] = 17
        if kind == 7:
            row[0] = 0
            row[9] = 2
        if kind == 8:
            row[13:17] = [8192, 8192, 100, 100]
        if kind == 9:
            row[0] = 273
            row[9] = 1
            row[11] = 8192
        rows.append(row)

    def profile(row):
        return 'ANVIL_KEYMAP 4 r-one com.game.test 1\n' + ' '.join(map(str, row)) + '\n'

    generator = random.Random(2)
    bounds = [0, 1, 2, 10, 17, 30, 60, 62, 63, 100, 256, 272, 273,
              767, 768, 1000, 32767, 32768, 65535, 65536]
    corpus = []
    for _ in range(1600):
        row = generator.choice(rows).copy()
        row[generator.randrange(31)] = generator.choice(bounds)
        corpus.append(profile(row))
    base = profile(rows[0])
    corpus.extend((base,
                   base.replace('100 ', '000000000000100 ', 1),
                   base.replace('100 ', '0000000000000100 ', 1),
                   base.replace('\n', '\r\r\n'),
                   ' ' + base,
                   base.replace('4 r-one', '4\nr-one')))
    return corpus


LEGACY_VALID = (
    'ANVIL_KEYMAP 1 r-one com.game.test 1\n30 100 200 1\n',
    'ANVIL_KEYMAP\t1 r-one com.game.test 1\r\n000000000000030 100 200 1\r\n',
    'ANVIL_KEYMAP 2 r-one com.game.test 1\n30 100 200 0 4 500 500 250 3\n',
    'ANVIL_KEYMAP 3 r-one com.game.test 1\n30 100 200 0 4 500 500 250 3 0 0 0 0 0 0 0 0 0 0\n',
)


def compare(probe, text):
    native = subprocess.run([str(probe)], input=text.encode(), check=False,
                            capture_output=True, timeout=5)
    if native.returncode not in (0, 1):
        raise RuntimeError(f'Native probe failed ({native.returncode}): {native.stderr!r}')
    result = worker.parse_keymaps('BEGIN\n' + text + 'END\n', 'r-one')
    python_accepts = result['invalid_count'] == 0 and len(result['profiles']) == 1
    return native.returncode == 0, python_accepts


def main():
    corpus = v4_corpus()
    if len(corpus) != 1606:
        raise RuntimeError('The deterministic V4 corpus size changed')
    failures = []
    accepted = 0
    with tempfile.TemporaryDirectory(prefix='anvil-keymap-codec-') as directory:
        probe = Path(directory) / 'keymap-codec-probe'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror',
                        str(ROOT / 'tests/keymap-codec-probe.c'),
                        str(ROOT / 'native/ime/text-state.c'), '-ldl', '-pthread',
                        '-o', str(probe)], check=True)
        for index, text in enumerate(corpus):
            native, python = compare(probe, text)
            accepted += int(native)
            if native != python:
                failures.append(f'V4 case {index}: native={native}, python={python}, profile={text!r}')
        print(f'Compared {len(corpus)} deterministic V4 profiles; {len(failures)} divergences '
              f'({accepted} accepted, {len(corpus) - accepted} rejected)')
        for index, text in enumerate(LEGACY_VALID):
            native, python = compare(probe, text)
            if not native or not python:
                failures.append(f'Legacy case {index}: native={native}, python={python}, profile={text!r}')
        print(f'Checked {len(LEGACY_VALID)} valid legacy V1/V2/V3 fixtures')
    for failure in failures[:12]:
        print(failure)
    if failures:
        raise SystemExit(1)
    print('Host keymap codec parity: PASS')


if __name__ == '__main__':
    main()
