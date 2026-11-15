# Dynabook G83/HS：ACPI 与 s2idle 修复

本机已定位到 **GPE 6F 唤醒路径造成 s2idle 提前退出**。只补齐 CHRG 对象时仍约 3 秒醒来；睡眠期间临时屏蔽 6F 后，60 秒测试记录到 **57.48 秒硬件 S0ix 驻留**。具体是哪一个下游 PCIe/PEG 设备触发，尚未确认。[隔离试验结果](results/2026-11-16-gpe6f-isolation.json)

当前修复由两部分组成：

- **146 字节的无地址 SSDT**：只补齐缺失的 CHRG 通知目标，保留当前启动的固件 DSDT 和全部 OperationRegion 地址，解决 ACPI 地址跨启动变化的问题。
- **仅在睡眠期间屏蔽 GPE 6F**：进入 s2idle 前屏蔽，恢复后解除；挂起失败时也清理。主 EC GPE 6E 保持原样。

已安装的配置针对本机 G83/HS、BIOS 8.90；当前实测内核为 7.2.15-1-omarchy。未修改内核参数、initramfs、UKI 或 Limine 入口。正常使用不设置定时唤醒，不要求重启。[修复机制及撤销方法](docs/sleep-workaround.md)

**已安装配置的电池供电验证通过**：正常 systemd 挂起流程约 61.44 秒后恢复，硬件 S0ix 驻留增加 57.60 秒。醒后 6F 已解除屏蔽，主 EC 正常，DSDT 与原始 UKI 均未变，RTC 已清空。[安装后实测结果](results/2026-11-16-installed-sleep-fix.json)

这会限制睡眠期间经 6F 路径发起的 PCIe/PEG 唤醒。定时恢复已验证，实际合盖/开盖唤醒与较长时间的电池耗电仍需单独验收。不能将一分钟试验换算为整夜续航，也不能据此认定读卡器是唯一故障源。

## 验证记录

| 条件 | 返回时间 | 硬件 S0ix 驻留 |
| --- | ---: | ---: |
| 原生 DSDT | 2.95 秒 | 0 |
| 原生 DSDT + CHRG SSDT | 3.21 秒 | 0 |
| CHRG SSDT + 临时屏蔽 6F | 60.97 秒 | 57.48 秒 |
| 已安装睡眠钩子，电池供电 | 61.44 秒 | 57.60 秒 |

前两组为同次启动对照，见[原始结果](results/2026-11-16-same-boot-sleep-comparison.json)。上述测试均使用 60 秒 RTC 定时器，完成后清空；IRQ 9 在异常提前醒来与正常 RTC 唤醒时都可能出现，不能单独据此判错。

CHRG 补充表已经通过离线 ACPICA 仿真和实机加载/撤销验证。原始 DSDT 在同次启动内逐字节不变，盖子状态保持正常。[无地址 SSDT 设计](docs/address-free-ssdt.md)

## 本地构建与检查

依赖 Python 3、ACPICA 的 iasl/acpiexec。完整固件、编译产物、UKI 和原始系统日志仅保存在忽略的 `local/` 中。

```bash
python3 scripts/build_ssdt.py
python3 -m unittest discover -s tests -v
```

本机安装器要求此前的成功隔离记录及本地捕获输入，不是面向任意型号的一键安装器。已安装的四个文件及摘要记录在本机 `/var/lib/dynabook-sleep/installation.json`。

```bash
# 在本机撤销已安装的配置
sudo python3 scripts/install_sleep_fix.py --remove
```

## 已停用的完整 DSDT 实验

早期 A Control 使用重新编译的完整 DSDT，因固定了前次启动的 OperationRegion 地址，导致盖子误报合上和反复休眠。A/B 入口与镜像已移除；B 未启动，旧安装器已加停止保护。不要恢复这条路线。[失败与恢复分析](docs/failure-analysis.md)、[历史实验步骤](docs/experiment.md)

BIOS 升级前 deep 模式曾黑屏并需要强制关机；当前修复只使用 s2idle。

## 来源与许可

- [内核 SSDT overlays/configfs 文档](https://docs.kernel.org/admin-guide/acpi/ssdt-overlays.html#loading-acpi-ssdts-from-configfs)
- [ACPI GPE sysfs 实现](https://github.com/torvalds/linux/blob/master/drivers/acpi/sysfs.c)
- [Linux 睡眠状态](https://docs.kernel.org/admin-guide/pm/sleep-states.html)

本仓库新写脚本采用 MIT 许可。厂商固件及补丁上下文中的厂商代码保留原有权利；仓库不附带完整固件。
