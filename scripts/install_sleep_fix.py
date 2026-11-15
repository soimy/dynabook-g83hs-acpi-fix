#!/usr/bin/env python3
"""Install/remove the tested local sleep quirk; never rebuild a boot image."""
from pathlib import Path
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()
LIB = Path('/usr/local/libexec/dynabook-sleep')
HOOK = Path('/usr/lib/systemd/system-sleep/90-dynabook-gpe6f')
DROPIN = Path('/etc/systemd/system/systemd-suspend.service.d/90-dynabook-gpe6f.conf')
RECORD = Path('/var/lib/dynabook-sleep/installation.json')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_file(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.stat().st_uid != 0 or path.parent.stat().st_mode & 0o022:
        raise RuntimeError('Privileged code/config parent is not protected: ' + str(path.parent))
    temporary = path.with_name(path.name + '.new')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    os.chown(temporary, 0, 0)
    os.replace(temporary, path)


def installed_module():
    spec = importlib.util.spec_from_file_location('installed_sleep_fix', LIB / 'sleep_fix.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local_report(name, data):
    path = ROOT / 'local' / name
    path.write_text(json.dumps(data, indent=2) + '\n')
    path.chmod(0o600)
    os.chown(path, OWNER.st_uid, OWNER.st_gid)


def remove():
    report = json.loads(RECORD.read_text())
    allowed = {str(LIB / 'sleep_fix.py'), str(LIB / 'SSDT-CHRG.aml'), str(HOOK), str(DROPIN)}
    if set(report['files']) != allowed:
        raise RuntimeError('Unexpected paths in the installation record')
    for name, entry in report['files'].items():
        path = Path(name)
        if path.exists() and sha(path.read_bytes()) != entry['sha256']:
            raise RuntimeError('Refusing to remove a locally changed file: ' + name)
    module = installed_module()
    module.restore()
    if module.GROUP.exists() and sha((module.GROUP / 'aml').read_bytes()) == module.AML_SHA256:
        module.GROUP.rmdir()
    for name, entry in report['files'].items():
        path = Path(name)
        if entry['previous_file']:
            write_file(path, Path(entry['previous_file']).read_bytes(), entry['previous_mode'])
        else:
            path.unlink(missing_ok=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    RECORD.unlink()
    local_report('sleep-fix-removal.json', {'at': datetime.datetime.now().astimezone().isoformat(),
                                          'removed': True, 'boot_images_changed': False})


def install():
    if RECORD.exists():
        raise RuntimeError('A persistent fix is already installed; inspect its record before replacing it')
    proof_path = next((ROOT / 'local/gpe6f-trial').glob('*-state.json'))
    proof = json.loads(proof_path.read_text())
    if not (proof['state'] == 'completed' and proof['gpe_restored'] and proof['result']['effective_low_power']):
        raise RuntimeError('A successful temporary GPE isolation test is required')
    aml = (ROOT / 'local/ssdt/SSDT-CHRG.aml').read_bytes()
    manifest = json.loads((ROOT / 'ssdt/manifest.json').read_text())
    if sha(aml) != manifest['files']['SSDT-CHRG']['sha256']:
        raise RuntimeError('The validated SSDT changed')
    native_capture = json.loads((ROOT / 'local/inputs/capture.json').read_text())
    native_uki = Path(native_capture['source_path'])
    if sha(native_uki.read_bytes()) != native_capture['source_sha256']:
        raise RuntimeError('The native boot image changed since the reviewed baseline')
    files = {
        LIB / 'sleep_fix.py': ((ROOT / 'production/sleep_fix.py').read_bytes(), 0o755),
        LIB / 'SSDT-CHRG.aml': (aml, 0o644),
        HOOK: (b'#!/bin/sh\nexec /usr/bin/python3 /usr/local/libexec/dynabook-sleep/sleep_fix.py "$@"\n', 0o755),
        DROPIN: (b'[Service]\nExecStopPost=/usr/bin/python3 /usr/local/libexec/dynabook-sleep/sleep_fix.py restore suspend\n', 0o644)}
    # First installation must not overwrite unrelated local customizations.
    if any(path.exists() for path in files):
        raise RuntimeError('A target path already exists; review it before installation')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = Path('/var/backups/dynabook-sleep') / stamp
    backup.mkdir(parents=True)
    backup.chmod(0o700)
    (backup / 'limine.conf').write_bytes(Path('/boot/limine.conf').read_bytes())
    report = {'at': datetime.datetime.now().astimezone().isoformat(), 'backup': str(backup),
              'files': {}, 'boot_images_changed': False, 'kernel_cmdline_changed': False,
              'dsdt_overridden': False, 'rtc_alarm_installed': False}
    module = None
    try:
        for path, (data, mode) in files.items():
            write_file(path, data, mode)
            report['files'][str(path)] = {'sha256': sha(data), 'mode': mode,
                                         'previous_file': None, 'previous_mode': None}
            if sha(path.read_bytes()) != sha(data) or path.stat().st_uid != 0:
                raise RuntimeError('Installed file read-back/ownership verification failed')
        module = installed_module()
        before = module.native_info()
        initial_mask = module.masked(module.GPE)
        try:
            module.prepare()
            if not module.masked(module.GPE):
                raise RuntimeError('Installed pre-hook did not apply the mask')
        finally:
            module.restore()
        if module.masked(module.GPE) != initial_mask or module.native_info()['dsdt_sha256'] != before['dsdt_sha256']:
            raise RuntimeError('Installed hook self-check failed to preserve native state')
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        if sha(native_uki.read_bytes()) != native_capture['source_sha256']:
            raise RuntimeError('The native boot image unexpectedly changed')
        report.update(pre_post_selfcheck_passed=True, mask_restored=True, native_dsdt_unchanged=True)
        write_file(RECORD, (json.dumps(report, indent=2) + '\n').encode(), 0o600)
        local_report('sleep-fix-installation.json', report)
        os.sync()
        print(json.dumps(report, indent=2), flush=True)
    except Exception:
        if module is not None:
            module.restore()
        for path in files:
            path.unlink(missing_ok=True)
        RECORD.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    parser.add_argument('--verify-sleep', action='store_true', help='After installation launch exactly one RTC-backed integration test')
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Administrator authentication required')
    if arguments.remove:
        remove()
    else:
        install()
        if arguments.verify_sleep:
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()[:8]
            subprocess.run(['systemd-run', '--unit=verify-dynabook-sleep-' + boot, '--service-type=exec',
                            '--description=One installed sleep-hook verification', '/usr/bin/python3',
                            str(ROOT / 'scripts/verify_installed_sleep.py')], check=True)
