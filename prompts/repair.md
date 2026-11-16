**English** | [简体中文](repair.zh-CN.md)

# Repair prompt for a coding agent

Copy the prompt below into an agent that can inspect the affected machine. Repository: `https://github.com/soimy/dynabook-g83hs-acpi-fix`. [Human guide](../README.md).

---

Diagnose and apply this repository's verified s2idle workaround on the target laptop. Read the repository and any applicable local instructions first. Carry the work through diagnosis, installation checks, validation and a clear report. Use the existing implementation; do not redesign the ACPI tables.

## Known diagnosis and evidence

- Verified target: dynabook G83/HS, board A010D, BIOS Version 8.90; tested kernel 7.2.15-1-omarchy, s2idle. The runtime guard checks model, BIOS and native DSDT metadata. A different kernel still needs validation; a different model or BIOS is outside this workaround's verified scope.
- The main EC is GPE 0x6E. GPE 0x6F feeds firmware PCIe/PEG GPIO wake paths and caused premature s2idle exit in isolation tests. The precise downstream device remains unidentified. IRQ 9 alone is not a device diagnosis.
- Firmware `\_SB.PC00.LPCB.EC._Q01` notifies missing CHRG after adapter/battery notifications, causing AE_NOT_FOUND. Correcting this object alone did not prevent premature wake.
- Firmware OperationRegion bases can change between boots, including the region holding the lid-state field. Preserve the current firmware DSDT. A DSDT hash may be compared within one boot, never pinned across boots as a machine identifier.
- The verified fix has two parts: a 146-byte address-free SSDT that conditionally adds a disabled CHRG notification target, and a systemd pre/post quirk that masks only GPE 6F across s2idle and restores its previous state. ExecStopPost provides failure cleanup. GPE 6E is untouched.
- Successful battery tests: a 60-second timed trial produced 57.60 seconds of hardware S0ix; a real lid cycle spent 53.17 of 55.32 suspended seconds in S0ix (96.1%), with resume logged 47 ms after lid-open. A subsequent overnight test measured 1.063 Wh over 7 h 36 min 33 s (about 0.14 W, 2.48% of full charge) and 99.95% S0ix residency during suspended time. It included one 28-second closed-lid wake, possibly from a family member touching the mouse, followed by automatic resuspend. The exact wake device, other wake methods and S3 power comparison are not established. See `validation/verified.json`.

## Inspect before changing anything

1. Read `README.md`, `acpi/manifest.json`, `fix/sleep_fix.py`, and `scripts/install_sleep_fix.py`. Record git status and preserve unrelated user changes.
2. Check DMI model/BIOS, `/sys/power/mem_sleep`, native lid state, selected power source, existing sleep hooks and any installation record.
3. Inspect kernel logs for CHRG/AE_NOT_FOUND and the EC's GPE assignment. Sample GPE 6F counters twice, rather than diagnosing a storm from a single cumulative count. Counter growth is supporting evidence, not proof of a unique downstream device.
4. Confirm ACPI configfs support and availability of Python 3, iasl and acpiexec. Use the operating environment's normal package and privilege workflow. Do not collect or type the user's password; a terminal uses sudo, a background graphical agent may use pkexec.
5. If hardware or firmware differs, stop before installation and explain the mismatch. Do not relax guards or mask an unverified GPE to make the script run.

If the complaint is open-lid idle drain, inspect the inactivity policy separately: screen-off/lock is not suspend. This Omarchy installation had only screensaver/lock timeouts and logind IdleAction=ignore. The verified optional `omarchy/sym.auto-suspend/` plugin adds 600-second battery-only idle suspend, respects Stay Awake and application idle inhibitors, and leaves AC automatic suspend disabled. Follow the human guide; the firmware installer does not install this optional policy. Its open-lid test reached S0ix for 99.7% of a 941-second suspend. Do not claim physical AC transitions were tested.

## Build and install

Run as the ordinary user from a clean clone:

```bash
python3 scripts/build_ssdt.py
python3 -m unittest discover -s tests -v
```

The build must pass offline namespace checks and reproduce the pinned source/AML hashes. The sleep hook source must match the pinned hardware-tested hash. Do not edit manifests merely to bypass a mismatch.

Use administrator privileges for:

```bash
python3 scripts/install_sleep_fix.py --check
python3 scripts/install_sleep_fix.py
python3 scripts/install_sleep_fix.py --status
```

`--check` and `--status` are read-only. Installation briefly tests pre/post cleanup but does not suspend or reboot. If this exact revision is already installed, verify it and keep it. The installer refuses unrelated existing target files. Keep the installation record; it is needed for controlled removal.

## Validate with the user

1. Ensure work is saved before a physical suspend test. Do not trigger a new suspend, reboot or firmware operation merely while reviewing repository files. Use authorization already provided for testing; request missing authorization only when needed.
2. Ask the user to close the lid for at least one minute and reopen it on battery. Inspect logind/kernel timestamps and run the status command afterward. Reading logs does not require another suspend.
3. Require one suspend/resume cycle, no premature wake loop, an open lid after return, same-boot DSDT unchanged, GPE 6F restored, EC 6E unmasked, and no pending cleanup. Use `last_cycle_is_current_boot`, `native_dsdt_matches_last_cycle` and the hook receipt together, not an old successful receipt alone.
4. Inspect the receipt's `slp_s0_residency_delta_us`; screen-off and stopped fans are supporting observations, not a substitute for hardware residency. If PMC/debugfs is unavailable, report that limitation explicitly. Correlate resume with lid-open; do not call IRQ 9 itself an error.
5. Do not add a recurring RTC alarm. This installer schedules none. Preserve any pre-existing user RTC alarm; do not clear an alarm you did not create.
6. Report measured intervals, S0ix residency and cleanup state. Distinguish the recorded short and overnight successes from results on a new test session. Other wake methods and first suspend after a future reboot still need validation; a S3 power comparison has not been measured.

## Boundaries and rollback

- Do not install a static whole-DSDT override, hard-code previous-boot OperationRegion addresses, or load the simulated test fixture into the running kernel.
- Do not mask the main EC 6E or permanently mask 6F. The intended tradeoff is loss of 6F PCIe/PEG wake only during sleep.
- Do not add `_OSI`, `acpi_mask_gpe`, `mem_sleep_default=deep`, hibernation/resume-offset changes, boot entries or initramfs changes. Those are separate investigations and unnecessary for this fix.
- On Omarchy, do not edit `/usr/share/omarchy`. The installed workaround uses its own root-owned code, systemd hook and service drop-in.
- Do not overwrite locally modified installed files or remove unrelated hooks. If validation regresses, preserve evidence and use the installer removal path instead of repeatedly retrying a failing suspend.

```bash
# Administrator privileges; verifies ownership and hashes before removal.
python3 scripts/install_sleep_fix.py --remove
```

Finish with a concise report: diagnosis supported by measurements; files installed or already present; validation actually completed; remaining scope and rollback command. Do not attribute success to CHRG alone or a particular card reader. Use the overnight energy record for battery-drain claims, rather than extrapolating from a short trial.
