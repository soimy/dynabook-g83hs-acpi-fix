#!/usr/bin/env python3
"""Install, inspect or remove the verified G83/HS s2idle workaround."""
from pathlib import Path
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
LIB = Path('/usr/local/libexec/dynabook-sleep')
HOOK = Path('/usr/lib/systemd/system-sleep/90-dynabook-gpe6f')
DROPIN = Path('/etc/systemd/system/systemd-suspend.service.d/90-dynabook-gpe6f.conf')
RECORD = Path('/var/lib/dynabook-sleep/installation.json')
RUN = Path('/run/dynabook-sleep')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_module(path):
    spec = importlib.util.spec_from_file_location('dynabook_sleep_fix', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payloads():
    manifest = json.loads((ROOT / 'acpi/manifest.json').read_text())
    aml = (ROOT / 'build/acpi/SSDT-CHRG.aml').read_bytes()
    source = (ROOT / 'acpi/SSDT-CHRG.dsl').read_bytes()
    code = (ROOT / 'fix/sleep_fix.py').read_bytes()
    if (sha(aml) != manifest['ssdt']['sha256'] or len(aml) != manifest['ssdt']['size'] or
            sha(source) != manifest['ssdt']['source_sha256'] or
            sha(code) != manifest['sleep_fix_sha256']):
        raise RuntimeError('Source or payload differs from the hardware-verified revision')
    return {
        LIB / 'sleep_fix.py': (code, 0o755),
        LIB / 'SSDT-CHRG.aml': (aml, 0o644),
        HOOK: (b'#!/bin/sh\nexec /usr/bin/python3 /usr/local/libexec/dynabook-sleep/sleep_fix.py "$@"\n', 0o755),
        DROPIN: (b'[Service]\nExecStopPost=/usr/bin/python3 /usr/local/libexec/dynabook-sleep/sleep_fix.py restore suspend\n', 0o644)}


def write_file(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.stat().st_uid != 0 or path.parent.stat().st_mode & 0o022:
        raise RuntimeError('Unprotected privileged parent directory: ' + str(path.parent))
    temporary = path.with_name(path.name + '.new')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    os.chown(temporary, 0, 0)
    os.replace(temporary, path)


def verify_record(report):
    allowed = {str(LIB / 'sleep_fix.py'), str(LIB / 'SSDT-CHRG.aml'), str(HOOK), str(DROPIN)}
    if set(report['files']) != allowed:
        raise RuntimeError('Unexpected paths in the installation record')
    for name, entry in report['files'].items():
        path = Path(name)
        if not path.exists() or sha(path.read_bytes()) != entry['sha256']:
            raise RuntimeError('Installed file is missing or changed: ' + name)
        if path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise RuntimeError('Installed file has unsafe ownership or permissions: ' + name)


def preflight():
    module = load_module(ROOT / 'fix/sleep_fix.py')
    native = module.native_info()
    if '[s2idle]' not in Path('/sys/power/mem_sleep').read_text():
        raise RuntimeError('s2idle must already be selected; no sleep mode was changed')
    return native


def install():
    files = payloads()
    if RECORD.exists():
        report = json.loads(RECORD.read_text())
        verify_record(report)
        if any(report['files'][str(path)]['sha256'] != sha(data) for path, (data, _) in files.items()):
            raise RuntimeError('A different revision is installed; review it before replacing it')
        return {'already_installed': True, 'files_verified': True}
    if any(path.exists() for path in files):
        raise RuntimeError('A target path already exists; no files were changed')
    before = preflight()
    report = {'at': datetime.datetime.now().astimezone().isoformat(), 'files': {},
              'boot_images_changed': False, 'kernel_cmdline_changed': False,
              'dsdt_overridden': False, 'rtc_alarm_installed': False}
    module = None
    written = []
    try:
        for path, (data, mode) in files.items():
            write_file(path, data, mode)
            written.append(path)
            if sha(path.read_bytes()) != sha(data):
                raise RuntimeError('Installed file read-back failed: ' + str(path))
            report['files'][str(path)] = {'sha256': sha(data), 'mode': mode,
                                         'previous_file': None, 'previous_mode': None}
        module = load_module(LIB / 'sleep_fix.py')
        initial_mask = module.masked(module.GPE)
        try:
            module.prepare()
            if not module.masked(module.GPE):
                raise RuntimeError('Pre-hook did not apply the mask')
        finally:
            module.restore()
        if module.masked(module.GPE) != initial_mask or module.native_info()['dsdt_sha256'] != before['dsdt_sha256']:
            raise RuntimeError('Hook self-check did not preserve the native state')
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        report.update(pre_post_selfcheck_passed=True, mask_restored=True, native_dsdt_unchanged=True)
        write_file(RECORD, (json.dumps(report, indent=2) + '\n').encode(), 0o600)
        return report
    except Exception:
        if module is not None:
            module.restore()
        for path in written:
            path.unlink(missing_ok=True)
        RECORD.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        raise


def remove():
    report = json.loads(RECORD.read_text())
    verify_record(report)
    if any(entry.get('previous_file') for entry in report['files'].values()):
        raise RuntimeError('An older installation backed up existing files; review before removal')
    module = load_module(LIB / 'sleep_fix.py')
    module.restore()
    if module.GROUP.exists():
        if sha((module.GROUP / 'aml').read_bytes()) != module.AML_SHA256:
            raise RuntimeError('The configfs group contains a different table; it was not removed')
        module.GROUP.rmdir()
    for name in report['files']:
        Path(name).unlink()
    RECORD.unlink()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    return {'removed': True, 'boot_images_changed': False}


def status():
    if not RECORD.exists():
        return {'installed': False}
    report = json.loads(RECORD.read_text())
    verify_record(report)
    result = {'installed': True, 'files_verified': True,
              'pending_cleanup_exists': (RUN / 'pending.json').exists()}
    for name, path in {'gpe6f': '/sys/firmware/acpi/interrupts/gpe6F',
                       'main_ec6e': '/sys/firmware/acpi/interrupts/gpe6E',
                       'mem_sleep': '/sys/power/mem_sleep',
                       'lid': '/proc/acpi/button/lid/LID/state',
                       'rtc_alarm': '/sys/class/rtc/rtc0/wakealarm'}.items():
        result[name] = Path(path).read_text().strip()
    if (RUN / 'last-cycle.json').exists():
        cycle = json.loads((RUN / 'last-cycle.json').read_text())
        result['last_cycle'] = cycle
        result['last_cycle_is_current_boot'] = cycle['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        result['native_dsdt_matches_last_cycle'] = sha(Path('/sys/firmware/acpi/tables/DSDT').read_bytes()) == cycle['dsdt_sha256']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--remove', action='store_true')
    actions.add_argument('--status', action='store_true', help='Read-only installation and last-sleep status')
    actions.add_argument('--check', action='store_true', help='Read-only payload and hardware preflight')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Use sudo from a terminal, or pkexec from a graphical agent')
    os.umask(0o022)
    if args.check:
        payloads()
        result = {'payloads_verified': True, 'native': preflight()}
    elif args.status:
        result = status()
    elif args.remove:
        result = remove()
    else:
        result = install()
    print(json.dumps(result, indent=2))
