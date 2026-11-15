#!/usr/bin/env python3
"""Install/remove only the paired experimental UKIs and their menu entries."""
from pathlib import Path
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess

from formats import pe_sections, sha

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()
CONFIG = Path('/boot/limine.conf')
ENTRIES = {'A': 'ACPI CHRG - A Control', 'B': 'ACPI CHRG - B Guard'}


def atomic_write(path, data):
    temporary = path.with_name(path.name + '.chrg-new')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def report(name, data):
    path = ROOT / 'local' / name
    path.write_text(json.dumps(data, indent=2) + '\n')
    path.chmod(0o600)
    os.chown(path, OWNER.st_uid, OWNER.st_gid)


def without_entries(text):
    for name in ENTRIES.values():
        text = re.sub(r'(?ms)^/' + re.escape(name) + r'\n.*?(?=^/[^/]|\Z)', '', text)
    return text


def main(remove=False):
    if os.geteuid() != 0:
        raise RuntimeError('Administrator authentication required for ESP writes')
    if not remove and (ROOT / 'EXPERIMENT-HALTED.json').exists():
        raise RuntimeError('Experiment halted after control-boot regression; installation is disabled. See EXPERIMENT-HALTED.json.')
    manifest = json.loads((ROOT / 'local/images/manifest.json').read_text())
    capture = json.loads((ROOT / 'local/inputs/capture.json').read_text())
    destinations = {v: Path('/boot/EFI/Linux') / ('chrg-' + v + '.efi') for v in ENTRIES}
    contents = {}
    if not remove:
        if Path('/sys/class/dmi/id/bios_version').read_text().strip() != 'Version 8.90':
            raise RuntimeError('Unexpected BIOS version')
        secure = list(Path('/sys/firmware/efi/efivars').glob('SecureBoot-*'))
        if len(secure) != 1 or secure[0].read_bytes()[4:] != b'\0':
            raise RuntimeError('Secure Boot must be disabled for these unsigned images')
        if sha(Path(capture['source_path']).read_bytes()) != manifest['source_sha256']:
            raise RuntimeError('The native kernel UKI changed after capture')
        for variant, entry in manifest['variants'].items():
            content = (ROOT / 'local/images' / entry['file']).read_bytes()
            if sha(content) != entry['sha256'] or hashlib.blake2b(content).hexdigest() != entry['blake2b']:
                raise RuntimeError('An experimental image changed')
            original_sections = pe_sections((ROOT / 'local/inputs/original.efi').read_bytes())
            actual = pe_sections(content)
            if any(actual[n]['data'] != original_sections[n]['data'] for n in entry['unchanged_sections']):
                raise RuntimeError('An experimental kernel, stub or embedded command line changed')
            contents[variant] = content
        if shutil.disk_usage('/boot').free < sum(map(len, contents.values())) + 16 * 1024**2:
            raise RuntimeError('Insufficient ESP free space')
    lock = Path('/run/lock/boot-partition.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX)
    original = CONFIG.read_bytes()
    text = original.decode()
    default = next(line for line in text.splitlines() if line.startswith('default_entry:'))
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = Path('/var/backups/limine-chrg-experiment') / stamp
    backup.mkdir(parents=True)
    backup.chmod(0o700)
    (backup / 'limine.conf').write_bytes(original)
    if remove:
        updated = without_entries(text)
        if any(name in default for name in ENTRIES.values()):
            updated = updated.replace(default, 'default_entry: Omarchy/linux-omarchy', 1)
        for variant, path in destinations.items():
            if path.exists() and sha(path.read_bytes()) != manifest['variants'][variant]['sha256']:
                raise RuntimeError('Refusing to remove an experimental file changed by another process')
        atomic_write(CONFIG, updated.encode())
        for path in destinations.values():
            path.unlink(missing_ok=True)
        os.sync()
        report('removal.json', {'at': datetime.datetime.now().astimezone().isoformat(),
                                'backup': str(backup), 'experimental_entries_removed': True})
        return
    if any('/' + name + '\n' in text for name in ENTRIES.values()) or any(p.exists() for p in destinations.values()):
        raise RuntimeError('Experimental entries/files already exist; refusing to overwrite them')
    if default != 'default_entry: Omarchy/linux-omarchy':
        raise RuntimeError('Unexpected default boot entry; review before installing')
    native = re.search(r'(?m)^\s*path: boot\(\):/EFI/Linux/omarchy_linux-omarchy\.efi(?:#[^\s]+)?\s*\n\s*cmdline: ([^\n]+)', text)
    if not native or native[1].strip() != capture['cmdline']:
        raise RuntimeError('The native command line changed after capture')
    blocks = '\n'
    for variant, name in ENTRIES.items():
        blocks += ('/' + name + '\n    comment: Experimental CHRG ' + variant + '; native entry remains default\n'
                   '    protocol: efi\n    path: boot():/EFI/Linux/chrg-' + variant + '.efi#' +
                   manifest['variants'][variant]['blake2b'] + '\n    cmdline: ' + capture['cmdline'] + '\n\n')
    marker = '/BIOS Update - UEFI Shell\n'
    updated = text.replace(marker, blocks + marker, 1) if marker in text else text.rstrip() + '\n' + blocks
    try:
        for variant, content in contents.items():
            atomic_write(destinations[variant], content)
        atomic_write(CONFIG, updated.encode())
        for variant, destination in destinations.items():
            if sha(destination.read_bytes()) != manifest['variants'][variant]['sha256']:
                raise RuntimeError('ESP read-back hash verification failed')
        tree = subprocess.run(['/usr/bin/limine-entry-tool', '--no-hooks', '--no-mutex', '--tree', '2'],
                              capture_output=True, text=True, check=True, timeout=20).stdout
        if not all(name in tree for name in ENTRIES.values()):
            raise RuntimeError('The experimental entries did not parse correctly')
        if CONFIG.read_bytes() != updated.encode() or sha(Path(capture['source_path']).read_bytes()) != manifest['source_sha256']:
            raise RuntimeError('The menu or native UKI changed during installation')
        os.sync()
        report('installation.json', {'at': datetime.datetime.now().astimezone().isoformat(),
                                     'backup': str(backup), 'menu_tree': tree,
                                     'default_entry': default, 'native_uki_unchanged': True,
                                     'global_config_changed': False, 'reboot_performed': False,
                                     'physical_suspend_performed': False,
                                     'installed': {v: str(p) for v, p in destinations.items()}})
        print(tree, flush=True)
    except Exception:
        atomic_write(CONFIG, original)
        for path in destinations.values():
            path.unlink(missing_ok=True)
        os.sync()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    main(parser.parse_args().remove)
