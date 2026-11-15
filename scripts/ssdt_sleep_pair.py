#!/usr/bin/env python3
"""One native/SSDT s2idle pair in one boot; never replace the DSDT."""
from pathlib import Path
import argparse
import datetime
import json
import os
import subprocess
import time

import runtime_ssdt as overlay
from run_trial import read, service, snapshot

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()
ALARM = Path('/sys/class/rtc/rtc0/wakealarm')


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.chmod(0o600)
    os.chown(temporary, OWNER.st_uid, OWNER.st_gid)
    os.replace(temporary, path)


def check_native(reference):
    state = overlay.native_state()
    for name in ['boot_id', 'dsdt_sha256', 'lid', 'lid_closed', 'mem_sleep']:
        if state[name] != reference[name]:
            raise RuntimeError('Native state changed: ' + name)
    if read('/sys/class/power_supply/ADP1/online') != '1':
        raise RuntimeError('AC power changed; stop the comparison')
    return state


def phase(name, path, native):
    before = snapshot()
    initial_service = service()
    check_native(native)
    if ALARM.read_text().strip():
        raise RuntimeError('An RTC alarm is already armed')
    start = datetime.datetime.now().astimezone()
    result = {'phase': name, 'started_at': start.isoformat(), 'before': before,
              'state': 'preparing', 'requested_seconds': 60}
    save(path, result)
    alarm = ''
    try:
        ALARM.write_text('+60')
        alarm = ALARM.read_text().strip()
        if not alarm:
            raise RuntimeError('RTC did not arm; no suspend requested')
        result.update(state='suspending', rtc_wake_epoch=alarm)
        save(path, result)
        wall = time.clock_gettime(time.CLOCK_BOOTTIME)
        active = time.monotonic()
        request = subprocess.run(['systemctl', 'suspend'], capture_output=True, text=True, timeout=150)
        result.update(request_return_code=request.returncode, request_stderr=request.stderr)
        save(path, result)
        if request.returncode:
            raise RuntimeError('The suspend request failed')
        while True:
            current = service()
            counts_changed = any(read('/sys/power/suspend_stats/' + name) != before['/sys/power/suspend_stats/' + name]
                                 for name in ['success', 'fail'])
            invocation_changed = current.get('ExecMainStartTimestampMonotonic', '0') not in (
                '0', initial_service.get('ExecMainStartTimestampMonotonic', '0'))
            if (counts_changed or invocation_changed) and current['ActiveState'] in ('inactive', 'failed'):
                break
            if time.clock_gettime(time.CLOCK_BOOTTIME) - wall > 180:
                raise RuntimeError('Suspend/resume did not finish in the observation window')
            time.sleep(0.5)
        if ALARM.read_text().strip() == alarm:
            ALARM.write_text('0')
        elapsed = time.clock_gettime(time.CLOCK_BOOTTIME) - wall
        after = snapshot()
        pmc = '/sys/kernel/debug/pmc_core/slp_s0_residency_usec'
        residency = int(after[pmc]) - int(before[pmc])
        result.update(state='returned', elapsed_including_suspend=elapsed,
                      elapsed_excluding_suspend=time.monotonic() - active,
                      slp_s0_residency_delta_us=residency, wake_irq=after['/sys/power/pm_wakeup_irq'],
                      held_expected_duration=elapsed >= 50, effective_low_power=residency >= 45_000_000,
                      after=after, native_after=check_native(native))
        result['journal'] = subprocess.run(
            ['journalctl', '-b', '--no-pager', '-o', 'short-iso-precise', '--since', start.isoformat(),
             '-g', 'PM:|suspend|resume|wakeup|ACPI.*(Error|Warning)|AE_NOT_FOUND'],
            capture_output=True, text=True).stdout
        return result
    except Exception as exc:
        result.update(state='error', error=str(exc), after=snapshot())
        raise
    finally:
        if alarm and ALARM.read_text().strip() == alarm:
            ALARM.write_text('0')
        result['rtc_alarm_after_cleanup'] = ALARM.read_text().strip()
        save(path, result)


def worker():
    if '/system.slice/' not in Path('/proc/self/cgroup').read_text():
        raise RuntimeError('The observer must run outside the user freezer')
    native = overlay.native_state()
    if native['lid_closed'] != 'b false' or '[s2idle]' not in native['mem_sleep']:
        raise RuntimeError('Native open-lid s2idle state is required')
    check_native(native)
    validation = json.loads((ROOT / 'local/runtime-ssdt/state.json').read_text())
    if validation.get('state') != 'validated_and_active' or validation['before']['boot_id'] != native['boot_id']:
        raise RuntimeError('Runtime overlay validation is required in the same boot')
    directory = ROOT / 'local/ssdt-trials'
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, OWNER.st_uid, OWNER.st_gid)
    pair_path = directory / (native['boot_id'] + '-pair.json')
    if pair_path.exists():
        raise RuntimeError('A pair was already attempted in this boot; no automatic repeat')
    result = {'state': 'preparing', 'native_before': native, 'started_at': datetime.datetime.now().astimezone().isoformat(),
              'maximum_trials': 2, 'reboot_required': False, 'persistent_boot_changes': False}
    save(pair_path, result)
    try:
        overlay.remove()
        result['state'] = 'native_trial'
        save(pair_path, result)
        result['native'] = phase('native', directory / (native['boot_id'] + '-native.json'), native)
        save(pair_path, result)
        time.sleep(5)
        subprocess.run(['/usr/bin/python3', str(ROOT / 'scripts/runtime_ssdt.py'), 'validate'], check=True)
        result['state'] = 'overlay_trial'
        save(pair_path, result)
        result['overlay'] = phase('overlay', directory / (native['boot_id'] + '-overlay.json'), native)
        result.update(state='completed', completed_at=datetime.datetime.now().astimezone().isoformat(),
                      native_after=check_native(native), overlay_active=overlay.OVERLAY.exists())
    except Exception as exc:
        result.update(state='error', error=str(exc), native_after=overlay.native_state())
        overlay.unload(overlay.PROBE)
        overlay.unload(overlay.OVERLAY)
        # If lid reporting unexpectedly regressed, keep logind from looping.
        if result['native_after']['lid_closed'] != 'b false':
            subprocess.run(['systemd-run', '--unit=chrg-recovery-hold', '--service-type=exec',
                            'systemd-inhibit', '--what=sleep:idle:handle-lid-switch', '--mode=block',
                            '--why=Unexpected lid state during CHRG test', '/usr/bin/sleep', 'infinity'], check=False)
        raise
    finally:
        result['rtc_alarm_after_cleanup'] = ALARM.read_text().strip()
        save(pair_path, result)
    print(json.dumps({k: v for k, v in result.items() if k not in ['native', 'overlay']}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root access is required')
    if args.worker:
        worker()
    else:
        boot = overlay.native_state()['boot_id'][:8]
        subprocess.run(['systemd-run', '--unit=chrg-ssdt-pair-' + boot, '--service-type=exec',
                        '--description=One native versus address-free SSDT comparison',
                        '/usr/bin/python3', str(Path(__file__).resolve()), '--worker'], check=True)
