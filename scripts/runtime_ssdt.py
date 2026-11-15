#!/usr/bin/env python3
"""Validate/load/remove an address-free CHRG SSDT through ACPI configfs."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()
CONFIG = Path('/sys/kernel/config/acpi/table')
OVERLAY = CONFIG / 'chrg_notify_namespace'
PROBE = CONFIG / 'chrg_notify_probe'
OUTPUT = ROOT / 'local/runtime-ssdt'


def run(arguments):
    return subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=30).stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(name, result):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.chown(OUTPUT, OWNER.st_uid, OWNER.st_gid)
    OUTPUT.chmod(0o700)
    path = OUTPUT / name
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    temporary.chmod(0o600)
    os.chown(temporary, OWNER.st_uid, OWNER.st_gid)
    os.replace(temporary, path)


def native_state():
    hardware = json.loads((ROOT / 'hardware.json').read_text())
    if Path('/sys/class/dmi/id/product_name').read_text().strip() != hardware['product_name']:
        raise RuntimeError('Unexpected machine model')
    if Path('/sys/class/dmi/id/bios_version').read_text().strip() != hardware['bios_version'] or os.uname().release != hardware['kernel']:
        raise RuntimeError('Unexpected BIOS or kernel version')
    table = Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
    if (table[:4] != b'DSDT' or sum(table) % 256 or
            struct.unpack_from('<I', table, 24)[0] != 0x20240103 or
            struct.unpack_from('<I', table, 32)[0] != 0x20191018):
        raise RuntimeError('A native firmware DSDT is required; legacy full-table overrides are forbidden')
    lid = {str(p): p.read_text().strip() for p in Path('/proc/acpi/button/lid').glob('*/state')}
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'dsdt_sha256': sha(table), 'lid': lid,
            'lid_closed': run(['busctl', 'get-property', 'org.freedesktop.login1',
                               '/org/freedesktop/login1', 'org.freedesktop.login1.Manager', 'LidClosed']).strip(),
            'sleep_count': Path('/sys/power/suspend_stats/success').read_text().strip(),
            'mem_sleep': Path('/sys/power/mem_sleep').read_text().strip()}


def assert_unchanged(before):
    after = native_state()
    if after != before:
        raise RuntimeError('Native DSDT, lid state or sleep state changed during namespace validation')
    return after


def compiled(name):
    manifest = json.loads((ROOT / 'ssdt/manifest.json').read_text())
    if not all(manifest['offline_tests_passed'].values()) or manifest['operation_regions_added'] != 0:
        raise RuntimeError('The offline interpreter tests did not pass')
    entry = manifest['files'][name]
    data = (ROOT / 'local/ssdt' / entry['file']).read_bytes()
    source = ROOT / 'ssdt' / (name + '.dsl')
    if sha(data) != entry['sha256'] or sha(source.read_bytes()) != entry['source_sha256']:
        raise RuntimeError('The reviewed SSDT binary or source changed')
    if data[:4] != b'SSDT' or len(data) != int.from_bytes(data[4:8], 'little') or sum(data) % 256:
        raise RuntimeError('Invalid SSDT header')
    return data


def ensure_configfs():
    if run(['findmnt', '-n', '-o', 'FSTYPE', '-T', '/sys/kernel/config']).strip() != 'configfs':
        raise RuntimeError('configfs is not mounted; no tables were changed')
    run(['modprobe', 'acpi_configfs'])
    if not CONFIG.is_dir():
        raise RuntimeError('The ACPI configfs interface is unavailable')


def load(path, name):
    if path.exists():
        raise RuntimeError('The configfs table already exists: ' + path.name)
    data = compiled(name)
    path.mkdir()
    try:
        (path / 'aml').write_bytes(data)
        if (path / 'aml').read_bytes() != data or (path / 'signature').read_text().strip() != 'SSDT':
            raise RuntimeError('Loaded SSDT read-back verification failed')
    except Exception:
        path.rmdir()
        raise


def unload(path):
    if path.exists():
        path.rmdir()


def device_status(hid):
    devices = list(Path('/sys/bus/acpi/devices').glob(hid + ':*'))
    if len(devices) > 1:
        raise RuntimeError('Multiple matching test devices exist')
    if not devices:
        return None
    device = devices[0]
    if (device / 'driver').exists():
        raise RuntimeError('A driver unexpectedly bound to a notification test object')
    return int((device / 'status').read_text().strip())


def probe(expected):
    try:
        load(PROBE, 'SSDT-PROBE')
        observed = None
        for _ in range(20):
            observed = device_status('SYMF0002')
            if observed == expected or expected == 0 and observed is None:
                return 0 if observed is None else observed
            time.sleep(0.1)
        raise RuntimeError(f'CHRG/Q01 probe returned {observed}, expected {expected}')
    finally:
        unload(PROBE)
        for _ in range(20):
            if device_status('SYMF0002') is None:
                break
            time.sleep(0.1)


def validate():
    before = native_state()
    if before['lid_closed'] != 'b false' or not all('open' in state for state in before['lid'].values()):
        raise RuntimeError('Open the lid and restore its correct native state before validating')
    ensure_configfs()
    if OVERLAY.exists() or PROBE.exists():
        raise RuntimeError('An earlier overlay/probe is active; inspect or remove it before a new validation')
    result = {'started_at': datetime.datetime.now().astimezone().isoformat(),
              'before': before, 'state': 'validating', 'persistent_boot_changes': False,
              'physical_suspend_requested': False}
    save('validation.json', result)
    try:
        result['probe_before'] = probe(0)
        assert_unchanged(before)
        load(OVERLAY, 'SSDT-CHRG')
        assert_unchanged(before)
        query_start = datetime.datetime.now().astimezone().isoformat()
        result['probe_with_overlay'] = probe(15)
        log = run(['journalctl', '-b', '-k', '--no-pager', '-o', 'short-iso-precise', '--since', query_start])
        result['Q01_error_lines'] = [line for line in log.splitlines() if 'EC._Q01.CHRG' in line or 'Aborting method \\_SB.PC00.LPCB.EC._Q01' in line]
        if result['Q01_error_lines']:
            raise RuntimeError('The real Q01 invocation still reported a CHRG error')
        if device_status('SYMF0001') not in (None, 0):
            raise RuntimeError('The CHRG placeholder is not disabled')
        assert_unchanged(before)
        unload(OVERLAY)
        result['probe_after_unload'] = probe(0)
        assert_unchanged(before)
        load(OVERLAY, 'SSDT-CHRG')
        result.update(state='validated_and_active', after=assert_unchanged(before),
                      completed_at=datetime.datetime.now().astimezone().isoformat(),
                      original_dsdt_unchanged=True, runtime_only=True,
                      automatic_startup_installed=False, rollback_verified=True)
        save('state.json', result)
    except Exception as exc:
        unload(PROBE)
        unload(OVERLAY)
        result.update(state='error_rolled_back', error=str(exc), after=native_state())
        save('state.json', result)
        raise
    finally:
        save('validation.json', result)
    print(json.dumps(result, indent=2), flush=True)


def remove():
    before = native_state()
    ensure_configfs()
    unload(PROBE)
    unload(OVERLAY)
    after = assert_unchanged(before)
    result = {'state': 'removed', 'before': before, 'after': after,
              'completed_at': datetime.datetime.now().astimezone().isoformat()}
    save('state.json', result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'remove', 'status'])
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root access is required for ACPI configfs')
    if arguments.action == 'status':
        print(json.dumps({'native': native_state(), 'overlay_active': OVERLAY.exists(),
                          'probe_active': PROBE.exists()}, indent=2))
    elif not arguments.worker:
        subprocess.run(['systemd-inhibit', '--what=sleep:idle:handle-lid-switch', '--mode=block',
                        '--who=CHRG namespace validation', '--why=Protecting the native desktop during reversible SSDT validation',
                        '/usr/bin/python3', str(Path(__file__).resolve()), arguments.action, '--worker'], check=True)
    elif arguments.action == 'validate':
        validate()
    else:
        remove()
