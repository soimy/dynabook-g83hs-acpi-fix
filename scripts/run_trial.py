#!/usr/bin/env python3
"""One RTC-backed s2idle trial, only after verifying the loaded variant."""
from pathlib import Path
import argparse
import datetime
import json
import os
import subprocess
import time

from verify_loaded import identify

ROOT = Path(__file__).resolve().parent.parent
OWNER = Path(__file__).stat()
ALARM = Path('/sys/class/rtc/rtc0/wakealarm')


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {'error': str(exc)}


def service():
    command = subprocess.run(['systemctl', 'show', 'systemd-suspend.service',
                              '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result',
                              '-p', 'ExecMainStartTimestampMonotonic'], capture_output=True, text=True, check=True)
    return dict(line.split('=', 1) for line in command.stdout.splitlines() if '=' in line)


def snapshot():
    paths = ['/sys/power/pm_wakeup_irq', '/sys/power/mem_sleep',
             '/sys/kernel/debug/pmc_core/slp_s0_residency_usec',
             '/sys/firmware/acpi/interrupts/gpe6E', '/sys/firmware/acpi/interrupts/gpe6F',
             '/sys/class/power_supply/ADP1/online']
    paths += [str(p) for p in Path('/sys/power/suspend_stats').glob('*')]
    return {path: read(path) for path in paths}


def save(path, result):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    temporary.chmod(0o600)
    os.chown(temporary, OWNER.st_uid, OWNER.st_gid)
    os.replace(temporary, path)


def trial(variant):
    if '/system.slice/' not in Path('/proc/self/cgroup').read_text():
        raise RuntimeError('The worker must run as a system service outside the user freezer')
    identity = identify(variant)
    if '[s2idle]' not in read('/sys/power/mem_sleep'):
        raise RuntimeError('s2idle is not active; this experiment never switches modes')
    if ALARM.read_text().strip():
        raise RuntimeError('An RTC alarm already exists; refusing to replace it')
    if read('/sys/class/power_supply/ADP1/online') != '1':
        raise RuntimeError('Connect AC power to match the baseline conditions')
    directory = ROOT / 'local/trials'
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, OWNER.st_uid, OWNER.st_gid)
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    path = directory / (variant + '-' + boot_id + '.json')
    if path.exists():
        raise RuntimeError('This variant was already tested in this boot; no automatic repeat')
    start = datetime.datetime.now().astimezone()
    before = snapshot()
    initial_service = service()
    result = {'variant': variant, 'identity': identity, 'boot_id': boot_id,
              'started_at': start.isoformat(), 'requested_seconds': 60,
              'state': 'preparing', 'before': before, 'persistent_settings_changed': False}
    save(path, result)
    alarm_value = ''
    try:
        ALARM.write_text('+60')
        alarm_value = ALARM.read_text().strip()
        if not alarm_value:
            raise RuntimeError('RTC arming failed; no suspend requested')
        result.update(state='suspending', rtc_wake_epoch=alarm_value)
        save(path, result)
        wall = time.clock_gettime(time.CLOCK_BOOTTIME)
        active = time.monotonic()
        command = subprocess.run(['systemctl', 'suspend'], capture_output=True, text=True, timeout=150)
        result.update(request_return_code=command.returncode, request_stdout=command.stdout,
                      request_stderr=command.stderr)
        save(path, result)
        if command.returncode:
            raise RuntimeError('Suspend request failed')
        while True:
            current = service()
            changed = any(read('/sys/power/suspend_stats/' + name) != before['/sys/power/suspend_stats/' + name]
                          for name in ['success', 'fail'])
            new_invocation = current.get('ExecMainStartTimestampMonotonic', '0') not in (
                '0', initial_service.get('ExecMainStartTimestampMonotonic', '0'))
            if (changed or new_invocation) and current['ActiveState'] in ('inactive', 'failed'):
                break
            if time.clock_gettime(time.CLOCK_BOOTTIME) - wall > 180:
                raise RuntimeError('Suspend/resume did not finish within the observation window')
            time.sleep(0.5)
        # Cancel an early-wake alarm immediately rather than after collecting logs.
        if ALARM.read_text().strip() == alarm_value:
            ALARM.write_text('0')
        after = snapshot()
        pmc = '/sys/kernel/debug/pmc_core/slp_s0_residency_usec'
        elapsed = time.clock_gettime(time.CLOCK_BOOTTIME) - wall
        residency = int(after[pmc]) - int(before[pmc])
        result.update(state='returned', after=after,
                      returned_at=datetime.datetime.now().astimezone().isoformat(),
                      elapsed_including_suspend=elapsed,
                      elapsed_excluding_suspend=time.monotonic() - active,
                      slp_s0_residency_delta_us=residency,
                      held_expected_duration=elapsed >= 50,
                      effective_low_power=residency >= 45_000_000,
                      wake_irq=after['/sys/power/pm_wakeup_irq'], service_after=current)
        result['journal'] = subprocess.run(
            ['journalctl', '-b', '--no-pager', '-o', 'short-iso-precise', '--since', start.isoformat(),
             '-g', 'PM:|suspend|resume|wakeup|ACPI.*(Error|Warning)|AE_NOT_FOUND'],
            capture_output=True, text=True).stdout
        result['identity_after'] = identify(variant)
    except Exception as exc:
        result.update(state='error', error=str(exc), after=snapshot())
        raise
    finally:
        if alarm_value and ALARM.read_text().strip() == alarm_value:
            ALARM.write_text('0')
        result['rtc_alarm_after_cleanup'] = ALARM.read_text().strip()
        save(path, result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('variant', choices=['A', 'B'])
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root access is required. Use sudo in a visible terminal or pkexec from the agent.')
    if arguments.worker:
        trial(arguments.variant)
    else:
        identify(arguments.variant)
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()[:8]
        subprocess.run(['systemd-run', '--unit=chrg-trial-' + arguments.variant + '-' + boot_id,
                        '--service-type=exec', '--description=One CHRG variant s2idle trial',
                        '/usr/bin/python3', str(Path(__file__).resolve()), arguments.variant, '--worker'], check=True)
