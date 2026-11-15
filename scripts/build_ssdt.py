#!/usr/bin/env python3
"""Compile and interpret address-free notification supplements offline."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'build/acpi'


def run(command, log):
    process = subprocess.run(command, capture_output=True, text=True, timeout=45)
    text = process.stdout + process.stderr
    log.write_text(text)
    if process.returncode:
        raise RuntimeError('Command failed; inspect ' + str(log))
    return text


def integer(log, path):
    part = log.split('Evaluating ' + path + '\n')[-1].split('Evaluating ')[0]
    match = re.search(r'\[Integer\] = ([0-9A-Fa-f]+)', part)
    if not match:
        raise RuntimeError('Missing interpreter result for ' + path)
    return int(match[1], 16)


def compile_table(source, name):
    prefix = OUTPUT / name
    log = run(['iasl', '-tc', '-p', str(prefix), str(source)], OUTPUT / (name + '-compile.log'))
    if 'Compilation successful. 0 Errors, 0 Warnings' not in log:
        raise RuntimeError('The small SSDT/fixture must compile without warnings')
    return prefix.with_suffix('.aml')


def simulate(name, files, commands):
    return run(['acpiexec', '-b', ';'.join(commands), *map(str, files)], OUTPUT / (name + '.log'))


def main():
    os.umask(0o077)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / 'acpi/manifest.json').read_text())
    source = ROOT / 'acpi/SSDT-CHRG.dsl'
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['ssdt']['source_sha256']:
        raise RuntimeError('The verified SSDT source changed; review it before building')
    overlay = compile_table(source, 'SSDT-CHRG')
    fixture = compile_table(ROOT / 'tests/fixtures/chrg_namespace.dsl', 'fixture')
    base = r'\_SB.PC00.LPCB.EC'
    lid = r'\_SB.LID._LID'
    commands = ['execute ' + base + '._Q01', 'execute ' + base + '.AFTR', 'execute ' + lid]
    baseline = simulate('baseline', [fixture], commands)
    if 'AE_NOT_FOUND' not in baseline or integer(baseline, base + '.AFTR') != 0 or integer(baseline, lid) != 1:
        raise RuntimeError('The fixture did not reproduce the missing-target failure')
    guarded = simulate('overlay', [fixture, overlay], commands + ['execute ' + base + '.CHRG._STA'])
    if ('AE_NOT_FOUND' in guarded or integer(guarded, base + '.AFTR') != 1 or
            integer(guarded, lid) != 1 or integer(guarded, base + '.CHRG._STA') != 0):
        raise RuntimeError('The namespace supplement failed the interpreter test')
    existing_source = (ROOT / 'tests/fixtures/chrg_namespace.dsl').read_text().replace(
        'Name (BEFR, Zero)', 'Device (CHRG) { Name (_HID, "TEST0006") Name (_STA, 0x0F) }\n                    Name (BEFR, Zero)', 1)
    existing = OUTPUT / 'existing.dsl'
    existing.write_text(existing_source)
    existing_aml = compile_table(existing, 'existing')
    known = simulate('existing-device', [existing_aml, overlay], ['execute ' + base + '.CHRG._STA'])
    if 'AE_ALREADY_EXISTS' in known or integer(known, base + '.CHRG._STA') != 15:
        raise RuntimeError('The overlay changed an already-existing device')
    files = {}
    for path in [overlay]:
        data = path.read_bytes()
        if data[:4] != b'SSDT' or int.from_bytes(data[4:8], 'little') != len(data) or sum(data) % 256:
            raise RuntimeError('Invalid compiled SSDT header')
        text = source.read_text()
        if re.search(r'\b(OperationRegion|Field|IndexField|BankField)\s*\(', text):
            raise RuntimeError('The supplement must not introduce hardware address fields')
        files[path.stem] = {'file': path.name, 'sha256': hashlib.sha256(data).hexdigest(),
                            'size': len(data), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    if files['SSDT-CHRG'] != manifest['ssdt']:
        raise RuntimeError('The compiler did not reproduce the verified SSDT payload')
    result = {'files': files, 'compiler_errors': 0, 'compiler_warnings': 0,
              'offline_tests_passed': {'missing_target_reproduced': True,
                                       'notify_completes_with_overlay': True,
                                       'lid_result_unchanged': True,
                                       'new_device_disabled': True,
                                       'existing_device_preserved': True},
              'operation_regions_added': 0, 'dsdt_replaced': False}
    (OUTPUT / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
