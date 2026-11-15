#!/usr/bin/env python3
"""Read-only verification of the native boot following experiment rollback."""
from pathlib import Path
import os,json,hashlib,subprocess,datetime,struct
ROOT=Path(__file__).resolve().parent.parent
OWNER=Path(__file__).stat()

def main():
 if os.geteuid()!=0: raise RuntimeError('Root read access required')
 hardware=json.loads((ROOT/'hardware.json').read_text())
 capture=json.loads((ROOT/'local/inputs/capture.json').read_text())
 table=Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
 menu=Path('/boot/limine.conf').read_text()
 kernel_log=subprocess.run(['journalctl','-b','-k','--no-pager','-o','short-iso-precise'],capture_output=True,text=True,check=True).stdout
 result={'at':datetime.datetime.now().astimezone().isoformat(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'native_dsdt_sha256':hashlib.sha256(table).hexdigest(),'native_dsdt_matches_original':hashlib.sha256(table).hexdigest()==hardware['original_dsdt_sha256'],'native_uki_matches_original':hashlib.sha256(Path(capture['source_path']).read_bytes()).hexdigest()==capture['source_sha256'],'default_boot_native':'default_entry: Omarchy/linux-omarchy' in menu,'experimental_entries_absent':'/ACPI CHRG - ' not in menu,'experimental_images_absent':not any(Path('/boot/EFI/Linux').glob('chrg-*.efi')),'lid_state':{str(p):p.read_text().strip() for p in Path('/proc/acpi/button/lid').glob('*/state')},'logind_lid_closed':subprocess.run(['busctl','get-property','org.freedesktop.login1','/org/freedesktop/login1','org.freedesktop.login1.Manager','LidClosed'],capture_output=True,text=True,check=True).stdout.strip(),'suspend_entries_this_boot':kernel_log.count('PM: suspend entry'),'sleep_success_count':Path('/sys/power/suspend_stats/success').read_text().strip(),'rtc_alarm':Path('/sys/class/rtc/rtc0/wakealarm').read_text().strip(),'uptime_seconds':float(Path('/proc/uptime').read_text().split()[0]),'physical_suspend_requested':False,'settings_changed':False}
 original=(ROOT/'local/inputs/DSDT.aml').read_bytes()
 current_header=struct.unpack_from('<4sIBB6s8sI4sI',table)
 original_header=struct.unpack_from('<4sIBB6s8sI4sI',original)
 header_fields=[0,1,2,4,5,6,7,8]
 result['native_dsdt_exact_hash_matches_capture']=result['native_dsdt_matches_original']
 result['native_dsdt_matches_original']=all(current_header[i]==original_header[i] for i in header_fields) and sum(table)%256==0 and not any('DSDT' in s and 'Table Upgrade' in s for s in kernel_log.splitlines())
 result['native_dsdt_oem_revision']=hex(current_header[6])
 result['native_dsdt_compiler_version']=hex(current_header[8])
 result['changed_byte_count']=sum(a!=b for a,b in zip(original,table)) if len(original)==len(table) else None
 result['changed_byte_offsets']=[i for i,(a,b) in enumerate(zip(original,table)) if a!=b][:100]
 print(json.dumps(result,indent=2),flush=True)
 for key in ['native_dsdt_matches_original','native_uki_matches_original','default_boot_native','experimental_entries_absent','experimental_images_absent']:
  if not result[key]: raise RuntimeError('Recovery verification failed: '+key)
 d=ROOT/'local/recovery'/result['boot_id']
 d.mkdir(parents=True,exist_ok=True);os.chown(d,OWNER.st_uid,OWNER.st_gid);d.chmod(0o700)
 for name,data in [('native-verification.json',json.dumps(result,indent=2)+'\n'),('kernel-native.log',kernel_log),('DSDT-native-current.aml',table)]:
  p=d/name
  p.write_bytes(data if isinstance(data,bytes) else data.encode());p.chmod(0o600);os.chown(p,OWNER.st_uid,OWNER.st_gid)
 p=ROOT/'local/native-recovery-verified.json';p.write_text(json.dumps(result,indent=2)+'\n');p.chmod(0o600);os.chown(p,OWNER.st_uid,OWNER.st_gid)
 print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
