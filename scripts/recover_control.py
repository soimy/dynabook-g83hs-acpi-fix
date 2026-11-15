#!/usr/bin/env python3
"""Capture the failed control boot, then remove only the experimental entries."""
from pathlib import Path
import os,json,datetime,subprocess,hashlib,sys
ROOT=Path(__file__).resolve().parent.parent
OWNER=Path(__file__).stat()

def main():
 if os.geteuid()!=0: raise RuntimeError('Root access required for rollback')
 boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 directory=ROOT/'local/recovery'/boot
 directory.mkdir(parents=True,exist_ok=True)
 for p in [directory.parent,directory]: os.chown(p,OWNER.st_uid,OWNER.st_gid);p.chmod(0o700)
 def save(name,data):
  p=directory/name
  p.write_bytes(data if isinstance(data,bytes) else data.encode())
  os.chown(p,OWNER.st_uid,OWNER.st_gid);p.chmod(0o600)
 data=Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
 save('loaded-DSDT.aml',data)
 tables=json.loads((ROOT/'local/tables/manifest.json').read_text())
 digest=hashlib.sha256(data).hexdigest()
 variant=next((k for k,v in tables['variants'].items() if digest==v['sha256']),'other')
 for name,args in [('kernel.log',['journalctl','-b','-k','--no-pager','-o','short-iso-precise']),('logind.log',['journalctl','-b','-u','systemd-logind','--no-pager','-o','short-iso-precise'])]:
  save(name,subprocess.run(args,capture_output=True,text=True,check=True).stdout)
 for name,path in [('wakeup-sources.txt','/sys/kernel/debug/wakeup_sources'),('pmc-residency.txt','/sys/kernel/debug/pmc_core/slp_s0_residency_usec')]:
  try: save(name,Path(path).read_bytes())
  except OSError: pass
 lid={str(p):p.read_text().strip() for p in Path('/proc/acpi/button/lid').glob('*/state')}
 rollback=subprocess.run(['/usr/bin/python3',str(ROOT/'scripts/install_boot_entries.py'),'--remove'],capture_output=True,text=True,check=True)
 save('rollback.log',rollback.stdout+rollback.stderr)
 capture=json.loads((ROOT/'local/inputs/capture.json').read_text())
 config=Path('/boot/limine.conf').read_text()
 original_ok=hashlib.sha256(Path(capture['source_path']).read_bytes()).hexdigest()==capture['source_sha256']
 if not original_ok or 'default_entry: Omarchy/linux-omarchy' not in config or '/ACPI CHRG - ' in config:
  raise RuntimeError('Native boot rollback did not verify')
 report={'at':datetime.datetime.now().astimezone().isoformat(),'boot_id':boot,'loaded_variant':variant,'loaded_dsdt_sha256':digest,'user_report':'Repeated black screen and return to lock screen after booting control A','lid_state':lid,'native_uki_unchanged':original_ok,'experimental_entries_removed':True,'default_boot':'Omarchy/linux-omarchy','reboot_required_to_unload_experimental_dsdt':True,'B_trial_started':False,'rtc_alarm':Path('/sys/class/rtc/rtc0/wakealarm').read_text().strip(),'reboot_performed':False}
 save('recovery.json',json.dumps(report,indent=2)+'\n')
 p=ROOT/'local/last-recovery.json'
 p.write_text(json.dumps(report,indent=2)+'\n');os.chown(p,OWNER.st_uid,OWNER.st_gid);p.chmod(0o600)
 print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__': main()
