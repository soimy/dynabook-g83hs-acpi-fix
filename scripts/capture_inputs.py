#!/usr/bin/env python3
"""Capture exact current UKI/DSDT for paired images; no boot-setting changes."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root read access is required for the ESP and firmware tables')
    expected = json.loads((ROOT / 'hardware.json').read_text())
    for key, path in [('product_name', '/sys/class/dmi/id/product_name'),
                      ('bios_version', '/sys/class/dmi/id/bios_version')]:
        if Path(path).read_text().strip() != expected[key]:
            raise RuntimeError('Unexpected ' + key)
    if os.uname().release != expected['kernel']:
        raise RuntimeError('The kernel differs from the reviewed baseline')
    variables = list(Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'))
    if len(variables) != 1 or variables[0].read_bytes()[4:] != b'\0':
        raise RuntimeError('This unsigned experimental UKI requires Secure Boot to be disabled')
    dsdt = Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
    if hashlib.sha256(dsdt).hexdigest() != expected['original_dsdt_sha256']:
        raise RuntimeError('The running DSDT is no longer the reviewed original table')
    source = Path('/boot/EFI/Linux/omarchy_linux-omarchy.efi')
    menu = Path('/boot/limine.conf').read_bytes()
    text = menu.decode()
    match = re.search(r'(?m)^\s*path: boot\(\):/EFI/Linux/omarchy_linux-omarchy\.efi(?:#[^\s]+)?\s*\n\s*cmdline: ([^\n]+)', text)
    if not match:
        raise RuntimeError('Cannot identify the native kernel entry and its command line')
    cmdline = match[1].strip()
    if cmdline != Path('/proc/cmdline').read_text().strip():
        raise RuntimeError('The boot entry and running kernel command line differ')
    directory = ROOT / 'local/inputs'
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, OWNER.st_uid, OWNER.st_gid)
    if (directory / 'capture.json').exists():
        raise RuntimeError('Inputs already captured; refusing to replace an experiment baseline')
    original = source.read_bytes()
    files = {'original.efi': original, 'DSDT.aml': dsdt, 'limine.conf': menu}
    for name, data in files.items():
        target = directory / name
        target.write_bytes(data)
        target.chmod(0o600)
        os.chown(target, OWNER.st_uid, OWNER.st_gid)
    report = {'at': datetime.datetime.now().astimezone().isoformat(),
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'bios': expected['bios_version'], 'kernel': expected['kernel'],
              'source_path': str(source), 'source_sha256': hashlib.sha256(original).hexdigest(),
              'original_dsdt_sha256': hashlib.sha256(dsdt).hexdigest(),
              'menu_sha256': hashlib.sha256(menu).hexdigest(), 'cmdline': cmdline,
              'boot_settings_changed': False}
    path = directory / 'capture.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    path.chmod(0o600)
    os.chown(path, OWNER.st_uid, OWNER.st_gid)
    print(json.dumps({k: v for k, v in report.items() if k not in ['boot_id', 'cmdline']}, indent=2), flush=True)


if __name__ == '__main__':
    main()
