#!/usr/bin/env python3
"""Build paired control/guard DSDTs locally; never install or suspend."""
import argparse
from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def table_header(data):
    if len(data) < 36:
        raise ValueError('Truncated ACPI header')
    sig, size, rev, checksum, oem, table, version, compiler, compiler_version = struct.unpack_from('<4sIBB6s8sI4sI', data)
    if sig != b'DSDT' or size != len(data) or sum(data) % 256:
        raise ValueError('Invalid DSDT signature, size or checksum')
    return {'signature': sig.decode(), 'size': size, 'oem_id': oem.decode().rstrip(' \0'),
            'oem_table_id': table.decode().rstrip(' \0'), 'oem_revision': version,
            'compiler_id': compiler.decode(), 'compiler_version': compiler_version}


def paired_sources(source):
    matches = list(re.finditer(r'Method \(_Q01, 0, Serialized\)\s*\{[^}]*\}', source))
    if len(matches) != 1:
        raise ValueError('Expected one unambiguous _Q01 method')
    method = matches[0]
    pattern = r'(?m)^(\s*)Notify \(CHRG, 0x80\) // Status Change$'
    if len(list(re.finditer(pattern, method.group()))) != 1:
        raise ValueError('Expected exactly one original CHRG notification in _Q01')
    header = re.search(r'(DefinitionBlock\s*\([^\n]+,\s*)(0x[0-9A-Fa-f]+)(\))', source)
    if not header:
        raise ValueError('Missing OEM revision')
    revision = int(header[2], 16)
    if revision == 0xFFFFFFFF:
        raise ValueError('Cannot increment OEM revision')
    control = source[:header.start(2)] + f'0x{revision + 1:08X}' + source[header.end(2):]
    replacement = lambda m: (m[1] + 'If (CondRefOf (CHRG))\n' + m[1] + '{\n' +
                             m[1] + '    Notify (CHRG, 0x80) // Status Change\n' + m[1] + '}')
    guard, count = re.subn(pattern, replacement, control)
    if count != 1:
        raise ValueError('CHRG notification was not unique in the table')
    return control, guard, revision + 1


def run_iasl(arguments, log):
    process = subprocess.run(['iasl', *map(str, arguments)], capture_output=True, text=True, timeout=60)
    output = process.stdout + process.stderr
    log.write_text(output)
    if process.returncode:
        raise RuntimeError('iasl failed; inspect ' + str(log))
    return Counter(f'{kind}:{code}' for kind, code in re.findall(r'(?m)^(Warning|Error|Remark)\s+(\d+)', output))


def build(input_path, output):
    data = input_path.read_bytes()
    header = table_header(data)
    expected = json.loads((ROOT / 'hardware.json').read_text())
    if (digest(data) != expected['original_dsdt_sha256'] or
            header['oem_id'] != expected['oem_id'] or header['oem_table_id'] != expected['oem_table_id']):
        raise ValueError('This DSDT does not match the reviewed G83/HS BIOS 8.90 table')
    output.mkdir(parents=True, exist_ok=True)
    native = output / 'DSDT-native.aml'
    shutil.copyfile(input_path, native)
    run_iasl(['-d', native], output / 'disassembly.log')
    source = native.with_suffix('.dsl').read_text()
    control, guard, revision = paired_sources(source)
    manifest = {'original_header': header, 'original_sha256': digest(data),
                'experimental_oem_revision': revision, 'variants': {}}
    diagnostics = {}
    for variant, text in [('A', control), ('B', guard)]:
        prefix = output / ('DSDT-' + variant)
        prefix.with_suffix('.dsl').write_text(text)
        diagnostics[variant] = run_iasl(['-tc', '-p', prefix, prefix.with_suffix('.dsl')], output / (variant + '-compile.log'))
        aml = prefix.with_suffix('.aml').read_bytes()
        result_header = table_header(aml)
        if result_header['oem_revision'] != revision:
            raise ValueError('Unexpected compiled OEM revision')
        manifest['variants'][variant] = {'file': prefix.with_suffix('.aml').name,
                                         'sha256': digest(aml), 'size': len(aml),
                                         'diagnostics': dict(diagnostics[variant])}
    if diagnostics['A'] != diagnostics['B']:
        raise ValueError('The guard introduced different compiler diagnostics; review before proceeding')
    if manifest['variants']['B']['size'] - manifest['variants']['A']['size'] != 9:
        raise ValueError('Unexpected compiled guard size; review the AML before proceeding')
    diff = ''.join(difflib.unified_diff(control.splitlines(True), guard.splitlines(True),
                                       fromfile='A/DSDT.dsl', tofile='B/DSDT.dsl'))
    (output / 'A-to-B.patch').write_text(diff)
    manifest['compiler_diagnostics_equal'] = True
    manifest['firmware_files_distributed'] = False
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    build(arguments.input, arguments.output)
