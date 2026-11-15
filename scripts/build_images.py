#!/usr/bin/env python3
"""Build paired UKIs by changing only the original image's initrd section."""
from pathlib import Path
import json
import struct
import subprocess

from formats import pe_sections, insert_acpi, sha

ROOT = Path(__file__).resolve().parent.parent


def main():
    directory = ROOT / 'local/images'
    directory.mkdir(parents=True, exist_ok=True)
    capture = json.loads((ROOT / 'local/inputs/capture.json').read_text())
    tables = json.loads((ROOT / 'local/tables/manifest.json').read_text())
    source_path = ROOT / 'local/inputs/original.efi'
    source = source_path.read_bytes()
    if sha(source) != capture['source_sha256']:
        raise ValueError('The captured UKI changed')
    original = pe_sections(source)
    if not {'.linux', '.initrd', '.cmdline'} <= original.keys():
        raise ValueError('The native image lacks required UKI sections')
    # Limine may pass a command line that overrides the embedded one when
    # Secure Boot is disabled. Preserve BOTH independently: this builder keeps
    # .cmdline byte-for-byte, and the installer copies the native menu cmdline.
    initrd = original['.initrd']
    if initrd['characteristics'] != 0x40000040:
        raise ValueError('Unexpected initrd section flags')
    if any(s['address'] > initrd['address'] for name, s in original.items() if name != '.initrd'):
        raise ValueError('The initrd is not the final virtual section; refuse an unsafe in-place growth')
    manifest = {'source_sha256': sha(source), 'original_dsdt_sha256': tables['original_sha256'],
                'cmdline_unchanged': True, 'kernel_unchanged': True,
                'embedded_cmdline_sha256': sha(original['.cmdline']['data']), 'variants': {}}
    # GNU objcopy --update-section leaves PE VirtualSize unchanged when a
    # section grows. Remove and re-add it instead, keeping the original VMA.
    stripped = directory / 'source-without-initrd.efi'
    subprocess.run(['objcopy', '--remove-section', '.initrd', str(source_path), str(stripped)],
                   capture_output=True, text=True, check=True)
    pe_offset = struct.unpack_from('<I', source, 0x3c)[0]
    image_base = struct.unpack_from('<Q', source, pe_offset + 24 + 24)[0]
    for variant in ['A', 'B']:
        table = ROOT / 'local/tables' / tables['variants'][variant]['file']
        aml = table.read_bytes()
        if sha(aml) != tables['variants'][variant]['sha256']:
            raise ValueError('A compiled ACPI table changed')
        updated, validation = insert_acpi(initrd['data'], aml)
        initrd_path = directory / (variant + '.initrd')
        initrd_path.write_bytes(updated)
        image = directory / ('chrg-' + variant + '.efi')
        process = subprocess.run(['objcopy', '--add-section', '.initrd=' + str(initrd_path),
                                  '--change-section-vma', '.initrd=' + hex(initrd['address'] + image_base),
                                  '--set-section-flags', '.initrd=contents,alloc,load,readonly,data',
                                  str(stripped), str(image)], capture_output=True, text=True, check=True)
        (directory / (variant + '-objcopy.log')).write_text(process.stdout + process.stderr)
        actual = pe_sections(image.read_bytes())
        if actual.keys() != original.keys():
            raise ValueError('UKI section names changed')
        for name, section in original.items():
            if name != '.initrd' and actual[name]['data'] != section['data']:
                raise ValueError('An original UKI section changed: ' + name)
            if actual[name]['address'] != section['address'] or actual[name]['characteristics'] != section['characteristics']:
                raise ValueError('An original UKI section address or flags changed: ' + name)
        if actual['.initrd']['data'] != updated:
            raise ValueError('The new UKI initrd does not match the verified archive')
        sorted_sections = sorted(actual.values(), key=lambda item: item['address'])
        for left, right in zip(sorted_sections, sorted_sections[1:]):
            if left['address'] + left['virtual_size'] > right['address']:
                raise ValueError('UKI virtual sections overlap')
        manifest['variants'][variant] = {
            'file': image.name, 'sha256': sha(image.read_bytes()),
            'blake2b': __import__('hashlib').blake2b(image.read_bytes()).hexdigest(),
            'dsdt_sha256': sha(aml), 'unchanged_sections': [n for n in original if n != '.initrd'],
            **validation}
    if (manifest['variants']['A']['original_main_initramfs_sha256'] !=
            manifest['variants']['B']['original_main_initramfs_sha256']):
        raise ValueError('Control and guard initramfs contents differ')
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
