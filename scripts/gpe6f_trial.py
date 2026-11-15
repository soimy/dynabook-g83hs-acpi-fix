#!/usr/bin/env python3
"""One temporary non-EC GPE 6F isolation test, with automatic restoration."""
from pathlib import Path
import argparse
import datetime
import json
import os
import re
import subprocess

import runtime_ssdt as overlay
from ssdt_sleep_pair import phase, save

ROOT = Path(__file__).resolve().parent.parent
GPE = Path('/sys/firmware/acpi/interrupts/gpe6F')
EC = Path('/sys/firmware/acpi/interrupts/gpe6E')
OWNER = Path(__file__).stat()


def paths():
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    directory = ROOT / 'local/gpe6f-trial'
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, OWNER.st_uid, OWNER.st_gid)
    directory.chmod(0o700)
    return boot, directory / (boot + '-state.json'), directory / (boot + '-sleep.json')


def masked():
    return re.search(r'\bmasked\b', GPE.read_text()) is not None


def restore():
    _, state_path, _ = paths()
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text())
    if state.get('originally_unmasked') and masked():
        GPE.write_text('unmask\n')
    state.update(restored_at=datetime.datetime.now().astimezone().isoformat(),
                 gpe_after_restore=GPE.read_text().strip(),
                 ec_after_restore=EC.read_text().strip(), gpe_restored=not masked())
    save(state_path, state)
    if not state['gpe_restored']:
        raise RuntimeError('GPE mask restoration failed')


def worker():
    if '/system.slice/' not in Path('/proc/self/cgroup').read_text():
        raise RuntimeError('The test must run outside the user freezer')
    boot, state_path, sleep_path = paths()
    if state_path.exists() or sleep_path.exists():
        raise RuntimeError('This one-shot isolation test was already attempted in this boot')
    native = overlay.native_state()
    if native['lid_closed'] != 'b false' or '[s2idle]' not in native['mem_sleep']:
        raise RuntimeError('Native open-lid s2idle state is required')
    kernel = subprocess.run(['journalctl', '-b', '-k', '--no-pager', '-o', 'cat'],
                            capture_output=True, text=True, check=True).stdout
    if 'ACPI: EC: GPE=0x6e' not in kernel:
        raise RuntimeError('The main EC GPE is not the reviewed 6E; no mask will be applied')
    ec_before = EC.read_text().strip()
    if masked() or 'unmasked' not in ec_before:
        raise RuntimeError('Unexpected existing GPE mask state')
    if not overlay.OVERLAY.exists():
        raise RuntimeError('Keep the validated CHRG supplement active for this comparison')
    state = {'started_at': datetime.datetime.now().astimezone().isoformat(),
             'native_before': native, 'originally_unmasked': True,
             'gpe_before': GPE.read_text().strip(), 'ec_before': ec_before,
             'state': 'preparing', 'persistent_settings_changed': False,
             'main_ec_modified': False, 'maximum_trials': 1}
    save(state_path, state)
    try:
        GPE.write_text('mask\n')
        if not masked() or 'unmasked' not in EC.read_text():
            raise RuntimeError('GPE isolation failed; no suspend requested')
        state.update(state='testing', gpe_during_test=GPE.read_text().strip())
        save(state_path, state)
        result = phase('ssdt-plus-temporary-gpe6f-mask', sleep_path, native)
        state.update(state='completed', result={k: result[k] for k in [
            'elapsed_including_suspend', 'slp_s0_residency_delta_us', 'wake_irq',
            'held_expected_duration', 'effective_low_power', 'rtc_alarm_after_cleanup']},
                     native_after=overlay.native_state())
    except Exception as exc:
        state.update(state='error', error=str(exc))
        raise
    finally:
        save(state_path, state)
        restore()
    print(state_path.read_text(), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--restore', action='store_true', help='Idempotently restore only this test mask')
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root access required')
    if arguments.restore:
        restore()
    elif arguments.worker:
        worker()
    else:
        boot, _, _ = paths()
        restore_command = '/usr/bin/python3 ' + str(Path(__file__).resolve()) + ' --restore'
        subprocess.run(['systemd-run', '--unit=gpe6f-isolation-' + boot[:8], '--service-type=exec',
                        '--description=One temporary non-EC GPE isolation trial',
                        '--property=ExecStopPost=' + restore_command,
                        '/usr/bin/python3', str(Path(__file__).resolve()), '--worker'], check=True)
