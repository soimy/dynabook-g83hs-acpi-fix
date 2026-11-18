#!/usr/bin/env python3
"""Build, install, inspect or remove the G83/HS awake GPP_E4 repair."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from install_sleep_fix import ROOT, load_module, sha, write_file

LIB = Path('/usr/local/libexec/dynabook-gpp-e4')
UNITS = Path('/etc/systemd/system')
RECORD = Path('/var/lib/dynabook-gpp-e4/installation.json')
RUNTIME = LIB / 'gpp_e4_fix.py'
AWAKE = 'dynabook-gpp-e4.service'
SLEEP = 'dynabook-gpp-e4-sleep.service'
BUILD = ROOT / 'build/acpi'
SYSTEMCTL = '/usr/bin/systemctl'
PYTHON = '/usr/bin/python3'
SOURCE = ROOT / 'fix/gpp_e4_fix.py'


def build():
    BUILD.mkdir(parents=True, exist_ok=True)
    for mode in ('read', 'disable', 'restore'):
        defines = [] if mode == 'read' else ['-D', 'E4_' + mode.upper()]
        subprocess.run(['/usr/bin/iasl', '-ve', *defines, '-p', str(BUILD / f'GPPE4-{mode}'),
                        str(ROOT / 'acpi/SSDT-GPPE4.dsl')], check=True, timeout=30)
    load_module(SOURCE).read_payloads(BUILD)


def install_files(aml):
    files = {RUNTIME: SOURCE.read_bytes()}
    files.update({LIB / f'GPPE4-{mode}.aml': data for mode, data in aml.items()})
    files.update({UNITS / name: (ROOT / 'fix' / name).read_bytes() for name in (AWAKE, SLEEP)})
    return files


def verify_record():
    entries = json.loads(RECORD.read_text())
    expected = {RUNTIME, UNITS / AWAKE, UNITS / SLEEP,
                *(LIB / f'GPPE4-{mode}.aml' for mode in ('read', 'disable', 'restore'))}
    if set(entries) != {str(path) for path in expected}:
        raise RuntimeError('Unexpected paths in the GPP_E4 installation record')
    for path in expected:
        if (path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022 or
                sha(path.read_bytes()) != entries[str(path)]):
            raise RuntimeError(f'Installed file was changed: {path}')
    return expected


def systemctl(*args, check=True):
    return subprocess.run([SYSTEMCTL, *args], check=check, timeout=45)


def runtime(action, *, check=True):
    return subprocess.run([PYTHON, str(RUNTIME), action], check=check, timeout=30)


def install():
    module = load_module(SOURCE)
    module.verify_machine()
    files = install_files(module.read_payloads(BUILD))
    if RECORD.exists():
        verify_record()
        if any(path.read_bytes() != data for path, data in files.items()):
            raise RuntimeError('A different GPP_E4 repair is installed')
        systemctl('enable', SLEEP, AWAKE)
        systemctl('start', AWAKE)
        runtime('status')
        return
    if any(os.path.lexists(path) for path in files):
        raise RuntimeError('A GPP_E4 install path already exists')

    written = []
    try:
        for path, data in files.items():
            write_file(path, data, 0o644)
            written.append(path)
        entries = {str(path): sha(data) for path, data in files.items()}
        write_file(RECORD, (json.dumps(entries, indent=2) + '\n').encode(), 0o600)
        verify_record()
        systemctl('daemon-reload')
        systemctl('enable', SLEEP, AWAKE)
        systemctl('start', AWAKE)
        runtime('status')
    except BaseException:
        if RECORD.exists():
            remove()
        else:
            for path in written:
                path.unlink()
        raise


def remove():
    files = verify_record()
    systemctl('disable', SLEEP, AWAKE)
    systemctl('stop', SLEEP, AWAKE)
    runtime('restore')
    for path in files:
        path.unlink()
    RECORD.unlink()
    systemctl('daemon-reload')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'install', 'status', 'remove'))
    args = parser.parse_args()
    if args.action == 'build':
        build()
        return
    if os.geteuid() != 0:
        parser.error('Run install, status or remove as root')
    if args.action == 'install':
        install()
    elif args.action == 'remove':
        remove()
    else:
        verify_record()
        for name in (AWAKE, SLEEP):
            systemctl('show', '--property=Id,LoadState,UnitFileState,ActiveState,SubState', name)
        runtime('status')


if __name__ == '__main__':
    main()
