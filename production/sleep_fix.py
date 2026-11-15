#!/usr/bin/env python3
"""Local G83/HS s2idle quirk: supplement CHRG, mask 6F only across sleep."""
from pathlib import Path
import datetime
import fcntl
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import syslog

GPE = Path('/sys/firmware/acpi/interrupts/gpe6F')
EC = Path('/sys/firmware/acpi/interrupts/gpe6E')
RUN = Path('/run/dynabook-sleep')
STATE = RUN / 'pending.json'
RECEIPT = RUN / 'last-cycle.json'
AML = Path(__file__).resolve().with_name('SSDT-CHRG.aml')
GROUP = Path('/sys/kernel/config/acpi/table/chrg_notify_namespace')
AML_SHA256 = '6122a79984b6f241a869add889f6646c3446d3c22350f8b1121bc0e90f9975db'
PMC = Path('/sys/kernel/debug/pmc_core/slp_s0_residency_usec')


def now():
    return datetime.datetime.now().astimezone().isoformat()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def masked(path):
    return re.search(r'\bmasked\b', path.read_text()) is not None


def pmc():
    try:
        return int(PMC.read_text().strip())
    except (OSError, ValueError):
        return None


def dsdt_hash():
    return hashlib.sha256(Path('/sys/firmware/acpi/tables/DSDT').read_bytes()).hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


def native_info():
    if Path('/sys/class/dmi/id/product_name').read_text().strip() != 'dynabook G83/HS':
        raise RuntimeError('Unsupported machine; quirk not applied')
    if Path('/sys/class/dmi/id/bios_version').read_text().strip() != 'Version 8.90':
        raise RuntimeError('BIOS changed; review this quirk before applying it')
    table = Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
    if (table[:4] != b'DSDT' or sum(table) % 256 or
            struct.unpack_from('<I', table, 24)[0] != 0x20240103 or
            struct.unpack_from('<I', table, 32)[0] != 0x20191018):
        raise RuntimeError('The native firmware DSDT is required')
    if 'invalid' in GPE.read_text() or 'unmasked' not in EC.read_text():
        raise RuntimeError('Unexpected GPE/EC state; quirk not applied')
    return {'boot_id': boot_id(), 'dsdt_sha256': hashlib.sha256(table).hexdigest(),
            'kernel': os.uname().release}


def ensure_notify_target():
    data = AML.read_bytes()
    if hashlib.sha256(data).hexdigest() != AML_SHA256:
        raise RuntimeError('Unexpected address-free SSDT payload')
    mount_type = subprocess.run(['/usr/bin/findmnt', '-n', '-o', 'FSTYPE', '-T', '/sys/kernel/config'],
                                capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if mount_type != 'configfs':
        subprocess.run(['/usr/bin/mount', '-t', 'configfs', 'configfs', '/sys/kernel/config'], check=True, timeout=10)
    subprocess.run(['/usr/bin/modprobe', 'acpi_configfs'], check=True, timeout=10)
    created = not GROUP.exists()
    if created:
        GROUP.mkdir()
    try:
        if created:
            (GROUP / 'aml').write_bytes(data)
        if (GROUP / 'aml').read_bytes() != data:
            raise RuntimeError('Another table occupies the owned CHRG configfs group')
    except Exception:
        if created:
            GROUP.rmdir()
        raise


def prepare():
    # A previous failed/cancelled suspend must never leave our mask behind.
    restore()
    if '[s2idle]' not in Path('/sys/power/mem_sleep').read_text():
        return False
    before = native_info()
    ensure_notify_target()
    if native_info()['dsdt_sha256'] != before['dsdt_sha256']:
        raise RuntimeError('The native DSDT unexpectedly changed')
    state = {**before, 'started_at': now(), 'originally_masked': masked(GPE),
             'gpe_before': GPE.read_text().strip(), 'ec_before': EC.read_text().strip(),
             'pmc_before_us': pmc(), 'mask_applied': False}
    # Save ownership BEFORE writing the mask, so failure cleanup can recover.
    atomic_json(STATE, state)
    try:
        if not state['originally_masked']:
            GPE.write_text('mask\n')
            state['mask_applied'] = True
        if not masked(GPE):
            raise RuntimeError('GPE mask did not take effect')
        state['gpe_during_sleep'] = GPE.read_text().strip()
        atomic_json(STATE, state)
    except Exception:
        restore()
        raise
    syslog.syslog(syslog.LOG_INFO, 's2idle: GPE 0x6F masked; main EC 0x6E unchanged')
    return True


def restore():
    if not STATE.exists():
        return False
    state = json.loads(STATE.read_text())
    if state['boot_id'] != boot_id():
        STATE.unlink()
        return False
    if not state['originally_masked'] and masked(GPE):
        GPE.write_text('unmask\n')
    restored = masked(GPE) == state['originally_masked']
    after = pmc()
    state.update(restored_at=now(), mask_state_restored=restored,
                 gpe_after_restore=GPE.read_text().strip())
    state['ec_after_restore'] = EC.read_text().strip()
    state['pmc_after_us'] = after
    state['slp_s0_residency_delta_us'] = (after - state['pmc_before_us']
                                          if after is not None and state['pmc_before_us'] is not None else None)
    state['original_dsdt_unchanged'] = dsdt_hash() == state['dsdt_sha256']
    atomic_json(RECEIPT, state)
    if not restored:
        raise RuntimeError('Failed to restore GPE mask state')
    STATE.unlink()
    syslog.syslog(syslog.LOG_INFO, 'resume/cleanup: original GPE 0x6F mask state restored')
    return True


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root access required')
    stage = sys.argv[1] if len(sys.argv) > 1 else ''
    action = sys.argv[2] if len(sys.argv) > 2 else ''
    if stage not in ('pre', 'post', 'restore') or action != 'suspend':
        return
    os.umask(0o077)
    RUN.mkdir(parents=True, exist_ok=True)
    RUN.chmod(0o700)
    with (RUN / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if stage == 'pre':
            prepare()
        else:
            restore()


if __name__ == '__main__':
    syslog.openlog('dynabook-sleep')
    try:
        main()
    except Exception as exc:
        syslog.syslog(syslog.LOG_ERR, str(exc))
        raise
