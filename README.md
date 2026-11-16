**English** | [简体中文](README.zh-CN.md)

# Dynabook G83/HS: s2idle and ACPI CHRG workaround

A machine-tested workaround for **entering S0ix low-power sleep when the lid closes and resuming when it opens**. Verified on a Dynabook G83/HS (A010D), BIOS 8.90, Omarchy, kernel `7.2.15-1-omarchy`. Other firmware or kernels require validation.

This page is the human guide to diagnosis, installation and acceptance. For a coding agent, use the [LLM repair prompt](prompts/repair.md). Successful hardware measurements are in [validation/verified.json](validation/verified.json).

## Root cause

### 1. Premature wake comes through GPE 6F

The affected machine leaves s2idle shortly after lid-close, repeatedly suspends/resumes, remains warm and drains its battery. Hardware S0ix residency stays at zero. The main EC uses **GPE 6E**; a separate **6F** generates frequent events while awake. This firmware's `_L6F` dispatches to PCIe/PEG GPIO wake paths.

Isolation established the causal path: keeping the current native DSDT and other conditions unchanged, masking only 6F across sleep allowed approximately one minute of suspend with substantial hardware S0ix residency. Shared ACPI SCI IRQ 9 cannot identify a device by itself. **The exact downstream device is still unidentified**; the card reader has not been established as the unique cause.

### 2. Missing CHRG is a separate firmware error

`\_SB.PC00.LPCB.EC._Q01` executes `Notify (CHRG, 0x80)` after adapter and battery notifications. The target is absent from the Linux namespace, causing `AE_NOT_FOUND` and aborting the method. Adding CHRG corrects that error, but a same-boot comparison established that **the CHRG correction alone does not prevent premature wake**.

### 3. ACPI memory addresses legitimately vary between boots

Several `OperationRegion` base addresses in this machine's DSDT change across boots, including `GRSV`, which contains the lid-state field. Reusing a previously dumped whole DSDT preserves stale addresses and can misread lid or other state. Preserve the firmware's current table and addresses; supplement its namespace instead. A DSDT hash can establish **same-boot** integrity, but must not be pinned across boots as a machine identity.

## Reproduction and diagnosis

Check the model, BIOS, selected sleep mode and interrupt state:

```bash
cat /sys/class/dmi/id/product_name
cat /sys/class/dmi/id/bios_version
cat /sys/power/mem_sleep
sudo dmesg | rg 'CHRG|AE_NOT_FOUND|ACPI: EC: GPE'
cat /sys/firmware/acpi/interrupts/gpe6E
cat /sys/firmware/acpi/interrupts/gpe6F
sleep 2
cat /sys/firmware/acpi/interrupts/gpe6F
```

The supported machine reports `dynabook G83/HS`, `Version 8.90`, and selected `[s2idle]`. Rapid growth in the 6F count supports diagnosis; actual sleep measurements are needed to establish the failure.

On an untreated affected machine, save work, close the lid for about one minute, then reopen it. Compare the hardware residency counter before/after and check for repeated suspend/resume:

```bash
sudo cat /sys/kernel/debug/pmc_core/slp_s0_residency_usec
# Close the lid for about one minute and reopen it; subtract the first reading.
sudo cat /sys/kernel/debug/pmc_core/slp_s0_residency_usec
journalctl -b -u systemd-logind.service -u systemd-suspend.service --since '5 minutes ago'
journalctl -b -k --since '5 minutes ago' -g 'PM: suspend|CHRG|AE_NOT_FOUND'
```

The original open-lid comparison returned in approximately three seconds with zero S0ix residency; the closed-lid case could repeatedly suspend. A working installation need not be removed merely to reproduce the fault. The offline build also reproduces the missing-CHRG error in a fully simulated namespace and verifies its correction; it never loads that simulated DSDT into the running kernel.

## Fix design

- **Address-free SSDT:** a 146-byte table conditionally adds a disabled `_STA=0` notification target when CHRG is missing. An existing CHRG is preserved. It introduces no registers, physical addresses, OperationRegions or method replacement.
- **Sleep-only 6F mask:** the systemd pre-hook records the original mask and masks 6F before s2idle; the post-hook restores its previous state. The main EC 6E is unchanged.
- **Failure cleanup:** `ExecStopPost` also restores state. Cleanup is idempotent and preserves masks previously set by another tool. Runtime state in `/run/dynabook-sleep/` is scoped to a boot ID.

The SSDT loads through configfs. After a reboot, the hook reloads it on the first suspend. The hook avoids frozen user-session services. This is a local system sleep workaround; PCIe/PEG wake through 6F is restricted during sleep.

## Install

Requires Python 3, ACPICA's `iasl`/`acpiexec`, and kernel `CONFIG_ACPI_CONFIGFS` support. On Omarchy, install the compiler tools with `omarchy pkg add acpica`. These steps work from a clean clone without complete vendor firmware or previous experiment records.

```bash
gh repo clone soimy/dynabook-g83hs-acpi-fix
cd dynabook-g83hs-acpi-fix
python3 scripts/build_ssdt.py
python3 -m unittest discover -s tests -v
sudo python3 scripts/install_sleep_fix.py --check
sudo python3 scripts/install_sleep_fix.py
sudo python3 scripts/install_sleep_fix.py --status
```

The builder verifies source and compiled AML against the hardware-tested hashes and runs offline interpreter checks. The installer checks the model, BIOS, native DSDT metadata and s2idle; it refuses unrelated existing target files. Reinstalling the same revision only verifies the installation. A new installation briefly exercises pre/post cleanup and restores the mask; it does not suspend or reboot the machine.

Installed files are root-owned:

| Path | Purpose |
| --- | --- |
| `/usr/local/libexec/dynabook-sleep/sleep_fix.py` | Verified runtime logic |
| `/usr/local/libexec/dynabook-sleep/SSDT-CHRG.aml` | Address-free supplement |
| `/usr/lib/systemd/system-sleep/90-dynabook-gpe6f` | pre/post hook |
| `/etc/systemd/system/systemd-suspend.service.d/90-dynabook-gpe6f.conf` | Failure cleanup |

The installation record is `/var/lib/dynabook-sleep/installation.json`. No Limine, kernel-command-line, initramfs or UKI changes are needed, and no reboot is required. The workaround schedules no RTC alarm and does not configure hibernation.

## Optional: automatic sleep while the lid is open

The ACPI workaround fixes suspend itself; it does not add an inactivity policy. This Omarchy installation originally configured a screensaver at 150 seconds and locking at 300 seconds, with logind `IdleAction=ignore`. A locked or dark display therefore did not imply system sleep.

The verified optional plugin in `omarchy/sym.auto-suspend/` adds **automatic suspend after 600 seconds of idle on battery only**. AC power disables it. It uses the compositor's idle notifier, respects application idle inhibitors and Omarchy's Stay Awake state, and leaves the existing screensaver/lock behavior intact. It has a ten-minute startup grace period, requests suspend at most once per idle event and schedules no RTC wake.

Install as the desktop user, without sudo. If the target directory already exists, check it rather than overwriting it:

```bash
mkdir -p "$HOME/.config/omarchy/plugins"
test ! -e "$HOME/.config/omarchy/plugins/sym.auto-suspend" && \
  cp -a omarchy/sym.auto-suspend "$HOME/.config/omarchy/plugins/"
omarchy shell shell rescanPlugins
omarchy plugin list --json
# Once sym.auto-suspend appears in the list:
omarchy plugin enable sym.auto-suspend
omarchy shell sym.auto-suspend status
```

Save work and remain idle with the lid open on battery for ten minutes, then wake the machine. Confirm a `battery idle suspend requested` user-journal entry followed by kernel `PM: suspend entry (s2idle)`. The actual test requested sleep at 12:06:59, entered s2idle at 12:07:00 and resumed at 12:22:41: 938.35 seconds in S0ix during 941.12 seconds of suspend (99.7%). Stay Awake blocking/rearming was tested; physical AC plug/unplug transitions were not part of that test.

To disable only this optional policy, run `omarchy plugin disable sym.auto-suspend`. The firmware workaround has its separate removal command below.

## Acceptance and verified results

Save work, close the lid for at least one minute on battery, reopen it, then run:

```bash
sudo python3 scripts/install_sleep_fix.py --status
journalctl -b -u systemd-logind.service -u systemd-suspend.service --since '5 minutes ago'
```

Require one suspend/resume cycle and resume following lid-open. Check that `last_cycle_is_current_boot`, `mask_state_restored`, `original_dsdt_unchanged` and `native_dsdt_matches_last_cycle` are true, and `pending_cleanup_exists` is false. GPE 6F and the main EC 6E should be `unmasked` while awake. `slp_s0_residency_delta_us` should approach the actual sleeping interval. If PMC is not exposed, this value can be null; check the debugfs mount at `/sys/kernel/debug` and platform support. A black screen alone does not establish low-power success.

| Successful hardware test | Suspend/resume interval | S0ix residency |
| --- | ---: | ---: |
| Battery, production hook, 60-second test alarm | 61.44 s including overhead | 57.60 s |
| Actual lid-close/open, no test alarm | 55.32 s | 53.17 s (96.1%) |
| Overnight lid-closed, battery | 7 h 36 min 33 s including one brief wake | 99.95% of suspended time |
| Open-lid battery idle timeout | 15 min 41 s | 938.35 s (99.7%) |

In the physical lid test, the kernel logged suspend exit approximately 47 ms after lid-open. The native DSDT remained unchanged, with no new ACPI errors. The overnight battery test subsequently measured **1.063 Wh over 7 h 36 min 33 s, averaging approximately 0.14 W** (2.48% of the 42.873 Wh full-charge capacity). Energy changed from 31.739 Wh before sleep to 30.676 Wh immediately after resume; a refreshed reading ten seconds later was 30.646 Wh (71% displayed), including awake use. A single closed-lid wake at 07:35:42 lasted approximately 28 seconds before automatic resuspend. The user reports that a family member may have touched the mouse; the exact wake device is unconfirmed. Other physical wake methods and the first suspend after a future reboot still require their own acceptance checks. This is not a measured S3 comparison.

## Remove

```bash
sudo python3 scripts/install_sleep_fix.py --remove
```

Removal verifies the installation record and file hashes, restores the mask owned by this workaround, unloads the matching SSDT, deletes the installed files and reloads systemd. It refuses to delete locally changed files and also accepts the earlier successful installation record on this machine. Deleting the repository clone does not uninstall the workaround.

## Repository and references

`fix/` contains the verified runtime; `acpi/` the SSDT source and pinned hashes; `scripts/` the builder and install/check/status/remove entry point; `omarchy/` the optional battery idle-suspend plugin; `tests/` offline semantics and restoration safeguards; `validation/` successful hardware evidence; `prompts/` copyable agent instructions. Generated files go into ignored `build/`.

- [Linux SSDT overlays/configfs](https://docs.kernel.org/admin-guide/acpi/ssdt-overlays.html#loading-acpi-ssdts-from-configfs)
- [Linux GPE sysfs mask/unmask implementation](https://github.com/torvalds/linux/blob/master/drivers/acpi/sysfs.c)
- [systemd sleep-hook timing and frozen user sessions](https://www.freedesktop.org/software/systemd/man/latest/systemd-suspend.service.html)

New code is [MIT licensed](LICENSE). Complete vendor ACPI firmware is not distributed.
