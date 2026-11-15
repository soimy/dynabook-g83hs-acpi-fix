#!/usr/bin/env python3
"""Verify one actual systemctl suspend cycle using the installed hook."""
from pathlib import Path
import json
import os
import re

import runtime_ssdt
from ssdt_sleep_pair import phase, save

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()


def main():
    if os.geteuid() != 0 or '/system.slice/' not in Path('/proc/self/cgroup').read_text():
        raise RuntimeError('Run as a root system service outside the user freezer')
    native = runtime_ssdt.native_state()
    if native['lid_closed'] != 'b false' or '[s2idle]' not in native['mem_sleep']:
        raise RuntimeError('Native open-lid s2idle state is required')
    ac_online = Path('/sys/class/power_supply/ADP1/online').read_text().strip()
    if ac_online not in ('0', '1'):
        raise RuntimeError('Cannot establish the current power source')
    directory = ROOT / 'local/installed-fix-trial'
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, OWNER.st_uid, OWNER.st_gid)
    path = directory / (native['boot_id'] + '.json')
    if path.exists():
        raise RuntimeError('The installed-hook trial was already attempted; no automatic repeat')
    if re.search(r'\bmasked\b', Path('/sys/firmware/acpi/interrupts/gpe6F').read_text()):
        raise RuntimeError('The test must begin with GPE 6F unmasked')
    result = phase('installed-sleep-hook', path, native, expected_ac=ac_online)
    receipt = json.loads(Path('/run/dynabook-sleep/last-cycle.json').read_text())
    result['installed_hook_receipt'] = receipt
    result['gpe_unmasked_after'] = re.search(r'\bmasked\b', Path('/sys/firmware/acpi/interrupts/gpe6F').read_text()) is None
    result['persistent_hook_verified'] = bool(
        result['held_expected_duration'] and result['effective_low_power'] and
        receipt.get('mask_applied') and receipt.get('mask_state_restored') and
        receipt.get('original_dsdt_unchanged') and result['gpe_unmasked_after'] and
        not Path('/run/dynabook-sleep/pending.json').exists())
    save(path, result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['before','after','journal']}, indent=2), flush=True)
    if not result['persistent_hook_verified']:
        raise RuntimeError('Installed-hook verification failed; inspect the saved result')


if __name__ == '__main__':
    main()
