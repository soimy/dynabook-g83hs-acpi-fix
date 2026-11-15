#!/usr/bin/env python3
"""Read wake-source counters and trace capabilities without changing settings."""
from pathlib import Path
import datetime
import json
import os

import runtime_ssdt

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root read access required')
    state = runtime_ssdt.native_state()
    directory = ROOT / 'local/wake-inspection' / state['boot_id']
    directory.mkdir(parents=True, exist_ok=True)
    for path in [directory.parent, directory]:
        os.chown(path, OWNER.st_uid, OWNER.st_gid)
        path.chmod(0o700)
    result = {'at': datetime.datetime.now().astimezone().isoformat(), 'native': state,
              'settings_changed': False, 'physical_suspend_requested': False, 'pci': {}}
    for source in ['/sys/kernel/debug/wakeup_sources', '/proc/interrupts',
                   '/sys/kernel/debug/pmc_core/substate_requirements',
                   '/sys/kernel/debug/pmc_core/substate_live_status_registers',
                   '/sys/kernel/debug/pmc_core/pch_ip_power_gating_status',
                   '/sys/kernel/debug/pmc_core/ltr_show',
                   '/sys/kernel/tracing/available_events', '/sys/power/pm_debug_messages']:
        path = Path(source)
        try:
            data = path.read_text()
            target = directory / (path.name + '.txt')
            target.write_text(data)
            os.chown(target, OWNER.st_uid, OWNER.st_gid)
            target.chmod(0o600)
            if path.name == 'wakeup_sources':
                lines = data.splitlines()
                header = lines[0].split()
                rows = [dict(zip(header, line.split())) for line in lines[1:]]
                result['nonzero_wakeup_sources'] = [row for row in rows if int(row.get('wakeup_count', '0'))]
            elif path.name == 'available_events':
                result['trace_events'] = [line for line in data.splitlines()
                                          if line.startswith('acpi:') or line.startswith('power:') and any(k in line for k in ['wakeup','suspend'])]
            elif path.name == 'pm_debug_messages':
                result['pm_debug_messages'] = data.strip()
        except OSError as exc:
            result[source] = str(exc)
    for name in ['0000:00:1c.0','0000:57:00.0','0000:00:07.0','0000:00:07.1','0000:00:0d.0','0000:00:0d.2','0000:00:1f.6']:
        device = Path('/sys/bus/pci/devices') / name
        record = {}
        for attribute in ['power/wakeup','power/wakeup_count','power/wakeup_active_count',
                          'power/runtime_status','power/runtime_active_time','power/runtime_suspended_time','firmware_node/path']:
            try:
                record[attribute] = (device / attribute).read_text().strip()
            except OSError:
                pass
        result['pci'][name] = record
    path = directory / 'inspection.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    os.chown(path, OWNER.st_uid, OWNER.st_gid)
    path.chmod(0o600)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
