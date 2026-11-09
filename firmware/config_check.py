"""Reject loss of official package selections before expensive compilation."""
from pathlib import Path
import sys

from prepare import config


def symbols(text):
    result = {}
    for line in text.splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key, value = line.split('=', 1)
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            key, value = line[2:-len(' is not set')], 'n'
        else:
            continue
        if key in result:
            raise ValueError(f'Duplicate configuration symbol: {key}')
        result[key] = value
    return result


def differences(official, resolved):
    # Includes the Bluetooth additions; no exceptions for missing official packages.
    required = {k: v for k, v in symbols(config(official)).items()
                if k.startswith('CONFIG_PACKAGE_') and v in ('y', 'm')}
    actual = symbols(resolved)
    return {k: {'expected': required[k], 'actual': actual.get(k, 'absent')}
            for k in sorted(required)
            if actual.get(k) not in (('y',) if required[k] == 'y' else ('m', 'y'))}


def check(official, resolved):
    lost = differences(official, resolved)
    if lost:
        details = '\n'.join(f"  {k}: expected={v['expected']}, actual={v['actual']}"
                            for k, v in lost.items())
        raise ValueError(f'Candidate lost {len(lost)} required package selections:\n'
                         f'{details}\nStop before downloads/compilation; resolve the feed/configuration mismatch.')
    count = sum(k.startswith('CONFIG_PACKAGE_') and v in ('y', 'm')
                for k, v in symbols(config(official)).items())
    return count


if __name__ == '__main__':
    try:
        count = check(Path(sys.argv[1]).read_text(), Path(sys.argv[2]).read_text())
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
    print(f'PASS: all {count} official/candidate package selections preserved (y stays y; m may become y).')
