#!/usr/bin/env python3
"""Identify the actually loaded table before permitting any suspend test."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def identify(expected=None):
    hardware = json.loads((ROOT / 'hardware.json').read_text())
    tables = json.loads((ROOT / 'local/tables/manifest.json').read_text())
    capture = json.loads((ROOT / 'local/inputs/capture.json').read_text())
    actual = Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
    digest = hashlib.sha256(actual).hexdigest()
    variant = 'native' if digest == hardware['original_dsdt_sha256'] else 'unknown'
    for name, value in tables['variants'].items():
        if digest == value['sha256']:
            variant = name
    if expected and variant != expected:
        raise RuntimeError(f'Expected table {expected}, but the loaded table is {variant}; no suspend will be performed')
    if os.uname().release != hardware['kernel'] or Path('/sys/class/dmi/id/bios_version').read_text().strip() != hardware['bios_version']:
        raise RuntimeError('Kernel or BIOS differs from the baseline')
    if Path('/proc/cmdline').read_text().strip() != capture['cmdline']:
        raise RuntimeError('The active command line differs from the baseline')
    log = subprocess.run(['journalctl', '-b', '-k', '--no-pager', '-o', 'cat'],
                         capture_output=True, text=True, check=True).stdout
    return {'variant': variant, 'dsdt_sha256': digest,
            'oem_revision': hex(int.from_bytes(actual[24:28], 'little')),
            'bios': hardware['bios_version'], 'kernel': os.uname().release,
            'cmdline_matches_baseline': True,
            'mem_sleep': Path('/sys/power/mem_sleep').read_text().strip(),
            'CHRG_error_count_this_boot': sum('EC._Q01.CHRG' in line for line in log.splitlines()),
            'override_log': [line for line in log.splitlines() if 'OVERRIDE' in line or 'DSDT' in line and 'initrd' in line]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', choices=['A', 'B'])
    print(json.dumps(identify(parser.parse_args().expected), indent=2))
