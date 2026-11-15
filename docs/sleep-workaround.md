# 本机已安装的睡眠修复

## 证据与适用范围

原生状态和仅补充 CHRG 的同次启动对照都约 3 秒退出 s2idle，硬件 S0ix 驻留为 0。只在睡眠期间屏蔽非主 EC 的 GPE 6F 后，60 秒试验成功，硬件低功耗驻留增加 57,480,300 微秒。6F 在清醒时持续出现大量事件，其固件处理路径涉及 PCIe/PEG GPIO 唤醒。

这支持 6F 路径造成被测提前唤醒的结论，尚未唯一定位到具体下游设备。主 EC 使用 6E，未被屏蔽。CHRG 缺失是另一项已证实的固件错误，修正它本身未解决提前唤醒。

## 两部分如何协作

`production/sleep_fix.py` 在正常 `systemctl suspend` 流程的 pre 阶段检查型号 G83/HS、BIOS 8.90、原始 DSDT 元数据以及 s2idle 模式。通过 configfs 加载或复用同一份无地址 SSDT，然后保存原始 6F 屏蔽状态，再临时屏蔽 6F。

原始固件 DSDT 始终由当次启动提供，不替换内存操作区，也不跨启动固定 DSDT 哈希。SSDT 只在 CHRG 缺失时创建 `_STA=0` 的通知目标，不实现设备寄存器或充电驱动。当前运行中已验证的表会被复用；重启后在首次挂起时重新加载。

post 阶段恢复原始屏蔽状态。`systemd-suspend.service` 的 `ExecStopPost` 还会在挂起失败或取消时清理；清理可重复执行，不改变先前已有的屏蔽策略。所有状态位于 `/run/dynabook-sleep/`，使用启动标识防止跨启动误用。

生产钩子不设置 RTC。试验里约一分钟的自动唤醒来自独立测试脚本，完成后清除定时器。

## 安装位置

| 文件 | 用途 |
| --- | --- |
| `/usr/local/libexec/dynabook-sleep/sleep_fix.py` | 型号保护、SSDT 加载、睡眠期间屏蔽与恢复 |
| `/usr/local/libexec/dynabook-sleep/SSDT-CHRG.aml` | 146 字节、无物理地址的补充表 |
| `/usr/lib/systemd/system-sleep/90-dynabook-gpe6f` | systemd pre/post 钩子 |
| `/etc/systemd/system/systemd-suspend.service.d/90-dynabook-gpe6f.conf` | 失败时的清理保障 |

文件均由 root 拥有，安装器会回读校验。安装前后的操作与摘要记录在 `/var/lib/dynabook-sleep/installation.json`；备份在 `/var/backups/dynabook-sleep/`。内核参数、引导镜像和启动菜单未改动。

## 验证与限制

安装时已实际调用 pre/post，验证屏蔽和恢复、原始 DSDT 不变。`scripts/verify_installed_sleep.py` 另外通过正常 systemd 挂起流程做一次 60 秒试验，由生产钩子执行屏蔽，不在测试脚本中代替它操作 GPE。测试记录同时检查低功耗计数、运行时回执、醒后解除屏蔽和 RTC 清理。

2026-11-16 00:53:52 的电池供电、开盖验证已通过：约 61.44 秒后恢复，SLP_S0 驻留增加 57,598,518 微秒。运行时回执确认由已安装的钩子屏蔽并恢复 6F，没有遗留待清理状态；主 EC 6E 保持未屏蔽，原始 DSDT 和 UKI 不变。四个安装文件的摘要、root 所有者均经回读核验。[实测结果](../results/2026-11-16-installed-sleep-fix.json)

之前的一次安装后检查因固定要求接通电源，在发现本机使用电池时于挂起前停止，未启动另一次睡眠。验证器现按测试开始时的供电状态检查，仍要求整个试验供电状态不变。

2026-11-16 01:08:34 用户实际合盖、01:09:30 开盖的验证也通过。内核挂起区间约 55.32 秒，S0ix 驻留增加 53,173,334 微秒，约占区间的 96.1%。日志只有一次挂起/恢复，开盖后约 47 毫秒内核退出挂起，与正常开盖唤醒一致。本轮未安排 RTC 测试闹钟，醒后读数为空。钩子已恢复 6F 屏蔽状态，主 EC 6E 保持未屏蔽，原始 DSDT 不变，无 CHRG/ACPI 错误及待清理状态，安装文件摘要和所有者仍正确。[实际合盖结果](../results/2026-11-16-lid-close-open.json)

GPE 6F 的 PCIe/PEG 唤醒能力会在睡眠期间关闭，清醒时恢复。实际合盖/开盖和 RTC 定时恢复均已测试；其他物理唤醒方式、长时间电池耗电及未来重启后的首次挂起仍需单独验收。一分钟试验不能给出可靠的整夜掉电估计。

## 撤销

在本机仓库目录运行：

```bash
sudo python3 scripts/install_sleep_fix.py --remove
```

撤销器核对已安装文件摘要，恢复本修复持有的 GPE 屏蔽，卸载相同补充表，移除四个文件并重新加载 systemd。它拒绝删除已被其他工具修改的文件。无需重建引导镜像。
