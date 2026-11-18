#!/usr/bin/env python3
"""Control the G83/HS BIOS 8.90 GPP_E4 event workaround."""

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys

LIB = Path(__file__).resolve().parent
RUN = Path('/run/dynabook-gpp-e4')
OWNER = RUN / 'owner'
DEBUG_STATE = RUN / 'aml-debug-output'
DEBUG = Path('/sys/module/acpi/parameters/aml_debug_output')
CONFIG = Path('/sys/kernel/config/acpi/table')
GROUP = CONFIG / 'dynabook_gpp_e4'
IRQ = Path('/sys/firmware/acpi/interrupts')
MASK = 1 << 4
AML_HASHES = {
    'read': '7a88d2f62d748ec66ff4f7ff189da72c8f80f10aa32ffd33137b103772f9f4c9',
    'disable': '21aa4ebd4c6eb7fdd80e5b4781a495a18e957faa05c778064712b6e6d5752523',
    'restore': '10916ab8d7737b5d116fd9396e5ba999abb2ed5de4671586a66903ae37dfa3ee',
}
RECEIPT = re.compile(rb'GPPE4FIX (before|status|after) (0x[0-9a-fA-F]+)')


def verify_machine():
    dmi = Path('/sys/class/dmi/id')
    expected = {'product_name': 'dynabook G83/HS', 'board_name': 'A010D/0001',
                'bios_version': 'Version 8.90'}
    if any((dmi / name).read_text().strip() != value for name, value in expected.items()):
        raise RuntimeError('GPP_E4 workaround requires dynabook G83/HS A010D/0001 BIOS 8.90')
    dsdt = Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
    header = struct.unpack_from('<4sIBB6s8sI4sI', dsdt)
    if (header[0] != b'DSDT' or header[1] != len(dsdt) or sum(dsdt) % 256 or
            header[4:] != (b'TOSHIB', b'A010D\0\0\0', 0x20240103, b'INTL', 0x20191018)):
        raise RuntimeError('Unexpected native DSDT; GPP_E4 workaround refused')
    if not re.search(r'^fd6a0000-fd6affff : INT34C5:00$', Path('/proc/iomem').read_text(), re.M):
        raise RuntimeError('Expected Intel GPIO community 4 resource is missing')
    if 'invalid' in (IRQ / 'gpe6F').read_text() or 'unmasked' not in (IRQ / 'gpe6E').read_text():
        raise RuntimeError('Unexpected ACPI GPE state')


def read_payloads(directory=LIB):
    payloads = {mode: (directory / f'GPPE4-{mode}.aml').read_bytes() for mode in AML_HASHES}
    for mode, data in payloads.items():
        if hashlib.sha256(data).hexdigest() != AML_HASHES[mode]:
            raise RuntimeError(f'Unexpected GPP_E4 {mode} AML')
    return payloads


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def owned():
    boot = Path('/proc/sys/kernel/random/boot_id').read_text()
    if OWNER.exists() and OWNER.read_text() != boot:
        OWNER.unlink()
    return OWNER.exists()


def cleanup_group(payloads):
    if not GROUP.exists():
        return
    try:
        installed = (GROUP / 'aml').read_bytes()
    except OSError as error:
        if error.errno != errno.EINVAL:
            raise
    else:
        if installed not in payloads.values():
            raise RuntimeError('GPP_E4 configfs name is occupied by an unrelated table')
    GROUP.rmdir()


def restore_debug():
    if DEBUG_STATE.exists():
        DEBUG.write_text(DEBUG_STATE.read_text())
        DEBUG_STATE.unlink()


def read_receipt(fd):
    result = {}
    while True:
        try:
            raw = os.read(fd, 16384)
        except BlockingIOError:
            break
        match = RECEIPT.search(raw)
        if match:
            result[match[1].decode()] = int(match[2], 16)
    if set(result) != {'before', 'status', 'after'}:
        raise RuntimeError('GPP_E4 AML did not report a verified register readback')
    return result


def access(mode, payloads):
    fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        os.lseek(fd, 0, os.SEEK_END)
        GROUP.mkdir()
        try:
            original = DEBUG.read_text()
            if original.strip() == '0':
                save(DEBUG_STATE, original)
                DEBUG.write_text('1\n')
            (GROUP / 'aml').write_bytes(payloads[mode])
            result = read_receipt(fd)
        finally:
            try:
                restore_debug()
            finally:
                cleanup_group(payloads)
    finally:
        os.close(fd)
    before = result['before']
    expected = {'read': before, 'disable': before & ~MASK, 'restore': before | MASK}[mode]
    if result['after'] != expected:
        raise RuntimeError(f'GPP_E4 {mode} readback was {result["after"]:#x}, expected {expected:#x}')
    return result


def change(action, payloads):
    if action == 'status':
        return access('read', payloads)
    if action == 'apply':
        if not owned():
            current = access('read', payloads)
            if not current['after'] & MASK:
                return current
            save(OWNER, Path('/proc/sys/kernel/random/boot_id').read_text())
        return access('disable', payloads)
    if not owned():
        return None
    current = access('disable' if action == 'resume' else 'restore', payloads)
    if action == 'restore':
        OWNER.unlink()
    return current


def interrupted(_signum, _frame):
    raise InterruptedError('GPP_E4 operation interrupted')


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in (
            'apply', 'pause', 'resume', 'restore', 'status'):
        raise SystemExit('Run as root with apply, pause, resume, restore or status')
    action = sys.argv[1]
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    os.umask(0o077)
    RUN.mkdir(mode=0o700, exist_ok=True)
    with (RUN / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        restore_debug()
        if action in ('pause', 'resume', 'restore') and not OWNER.exists() and not GROUP.exists():
            return
        verify_machine()
        payloads = read_payloads()
        if not CONFIG.exists():
            subprocess.run(['/usr/bin/modprobe', 'acpi_configfs'], check=True, timeout=10)
        cleanup_group(payloads)
        result = change(action, payloads)
        if result:
            print(json.dumps({'action': action, 'owned': owned(),
                              'e4_enabled': bool(result['after'] & MASK),
                              **{key: f'{value:#010x}' for key, value in result.items()}}))


if __name__ == '__main__':
    main()
