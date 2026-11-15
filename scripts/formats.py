"""Small checked PE/COFF and newc readers for the experiment builder."""
import hashlib
import struct


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(value, boundary):
    return (value + boundary - 1) // boundary * boundary


def pe_sections(blob):
    if len(blob) < 64 or blob[:2] != b'MZ':
        raise ValueError('Not a DOS/PE image')
    pe = struct.unpack_from('<I', blob, 0x3c)[0]
    if pe + 24 > len(blob) or blob[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    machine, count, _, _, _, optional_size, _ = struct.unpack_from('<HHIIIHH', blob, pe + 4)
    optional = pe + 24
    if machine != 0x8664 or not 1 <= count <= 96 or optional_size < 64:
        raise ValueError('Expected a bounded X64 PE image')
    if optional + optional_size + count * 40 > len(blob):
        raise ValueError('Truncated PE section table')
    if struct.unpack_from('<H', blob, optional)[0] != 0x20b:
        raise ValueError('Expected PE32+')
    sections = {}
    for index in range(count):
        start = optional + optional_size + index * 40
        name, virtual_size, address, raw_size, offset = struct.unpack_from('<8sIIII', blob, start)
        name = name.rstrip(b'\0').decode('ascii')
        if name in sections or offset + raw_size > len(blob):
            raise ValueError('Duplicate or out-of-bounds PE section')
        sections[name] = {'virtual_size': virtual_size, 'address': address,
                          'raw_size': raw_size, 'offset': offset,
                          'characteristics': struct.unpack_from('<I', blob, start + 36)[0],
                          'data': blob[offset:offset + min(raw_size, virtual_size)]}
    return sections


def cpio_record(name, data=b'', mode=0o100644, inode=0x43480000):
    name_bytes = name.encode('ascii') + b'\0'
    fields = [inode, mode, 0, 0, 2 if mode & 0o170000 == 0o040000 else 1,
              0, len(data), 0, 0, 0, 0, len(name_bytes), 0]
    record = b'070701' + ''.join(f'{value:08x}' for value in fields).encode() + name_bytes
    record += b'\0' * (-len(record) % 4)
    record += data
    record += b'\0' * (-len(record) % 4)
    return record


def read_cpio(blob):
    offset = 0
    entries = {}
    while True:
        start = offset
        if offset + 110 > len(blob) or blob[offset:offset + 6] not in (b'070701', b'070702'):
            raise ValueError('Malformed or truncated early CPIO')
        try:
            values = [int(blob[offset + 6 + n * 8:offset + 14 + n * 8], 16) for n in range(13)]
        except ValueError as exc:
            raise ValueError('Invalid CPIO integer') from exc
        size, name_size = values[6], values[11]
        if not 1 <= name_size <= 4096 or offset + 110 + name_size > len(blob):
            raise ValueError('Invalid CPIO name bounds')
        name_bytes = blob[offset + 110:offset + 110 + name_size]
        if not name_bytes.endswith(b'\0'):
            raise ValueError('Unterminated CPIO name')
        name = name_bytes[:-1].decode('ascii')
        offset = align(offset + 110 + name_size, 4)
        if offset + size > len(blob):
            raise ValueError('CPIO payload extends past the image')
        data = blob[offset:offset + size]
        offset = align(offset + size, 4)
        if name == 'TRAILER!!!':
            return entries, start, offset
        normalized = name.removeprefix('./')
        if normalized in entries:
            raise ValueError('Duplicate early CPIO path')
        entries[normalized] = {'data': data, 'mode': values[1]}


def insert_acpi(original, aml):
    if original.startswith((b'070701', b'070702')):
        entries, trailer_start, trailer_end = read_cpio(original)
        tail_start = trailer_end
        while tail_start < len(original) and original[tail_start] == 0:
            tail_start += 1
        prefix = original[:trailer_start]
        trailer = original[trailer_start:trailer_end]
        tail = original[tail_start:]
    else:
        entries, prefix, tail = {}, b'', original
        trailer = cpio_record('TRAILER!!!', mode=0)
    if any(name.startswith('kernel/firmware/acpi/') for name in entries):
        raise ValueError('The original initrd already contains an ACPI override')
    addition = b''
    for index, directory in enumerate(['kernel', 'kernel/firmware', 'kernel/firmware/acpi']):
        if directory not in entries:
            addition += cpio_record(directory, mode=0o040755, inode=0x43480000 + index)
        elif entries[directory]['mode'] & 0o170000 != 0o040000:
            raise ValueError('An ACPI parent path is not a directory')
    addition += cpio_record('kernel/firmware/acpi/DSDT.aml', aml, inode=0x43480004)
    early = prefix + addition + trailer
    early += b'\0' * (-len(early) % 512)
    output = early + tail
    verified, _, _ = read_cpio(output)
    if verified['kernel/firmware/acpi/DSDT.aml']['data'] != aml:
        raise ValueError('ACPI CPIO verification failed')
    for name, entry in entries.items():
        if verified[name] != entry:
            raise ValueError('An original early CPIO entry changed')
    if output[len(early):] != tail or not output.startswith(prefix):
        raise ValueError('The original initramfs content changed')
    return output, {'original_early_entries_preserved': len(entries),
                    'original_main_initramfs_sha256': sha(tail),
                    'main_initramfs_unchanged': True, 'early_cpio_bytes': len(early)}
