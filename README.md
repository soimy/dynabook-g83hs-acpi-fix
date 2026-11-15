# Dynabook G83/HS：CHRG 通知保护实验

这是针对 Dynabook G83/HS（A010D，BIOS 8.90）的 ACPI 对照实验仓库，**尚未证明能修复休眠**。

固件的 `\_SB.PC00.LPCB.EC._Q01` 在适配器和电池通知之后执行 `Notify (CHRG, 0x80)`。本机 Linux 无法解析这个对象，报告 `AE_NOT_FOUND`。补丁只给这一条通知增加 `CondRefOf` 检查，并增加 OEM Revision 以允许启动时覆盖 ACPI 表。

```asl
If (CondRefOf (CHRG))
{
    Notify (CHRG, 0x80)
}
```

## 已知基线

- BIOS：8.90；内核：7.2.15-1-omarchy；模式：s2idle。
- 接通电源、开盖、RTC 计划 60 秒；挂起流程约 2.254 秒后提前退出。
- 唤醒 IRQ：9（ACPI SCI）；SLP_S0 低功耗驻留增量：0。
- 主 EC GPE 为 6E；清醒状态 GPE6F 事件更多。共享 IRQ 和计数不能证明具体设备或 CHRG 报错是唤醒根因。
- BIOS 升级前 deep 测试曾黑屏并需要强制关机；本实验不切换为 deep。

## 对照设计

为控制 DSDT 反编译、重新编译造成的差异，准备两个独立启动镜像：

- **A Control**：重新编译原始 DSDT，只增加 OEM Revision。
- **B Guard**：相同编译流程和 OEM Revision，另增加 CHRG 存在性检查。

两者使用同一个原始 UKI 的内核、命令行及 initramfs 内容；只在 early CPIO 中增加对应 ACPI 表。原始 Omarchy 启动入口和默认项保留，实验不写入全局 mkinitcpio 配置、不改 `_OSI`、Hibernate 或设备唤醒设置。

必须先核验启动后实际加载的 DSDT 哈希，再进行单次 s2idle 测试。每次记录挂起/恢复日志、唤醒 IRQ、SLP_S0 驻留时间和 RTC 清理结果。消除 CHRG 报错不等于修复睡眠。

## 构建

需要 Python 3、ACPICA 的 `iasl` 和 GNU binutils。原始固件表应从目标机器本地读取，不在本仓库分发。

```bash
python3 scripts/build_tables.py --input local/DSDT.aml --output local/tables
python3 -m unittest discover -s tests -v
```

脚本会核验型号对应的表标识和原始哈希，生成控制组和补丁组 AML，检查 ACPI 校验和、版本，以及两组编译诊断是否一致。编译日志、完整固件表、UKI 和含机器标识的采集结果均放在忽略的 `local/` 中。

启动镜像的构建、安装和回滚步骤见后续实验记录。不要将未核验的表用于其他型号或 BIOS。

## 回滚

在 Limine 中选择原有 `Omarchy/linux-omarchy` 启动即可使用固件原始表。实验入口不设为默认项；不刷写 BIOS。安装器保存菜单备份，移除实验时只删除本实验入口和镜像。

## 来源与许可

- [内核 ACPI 表覆盖说明](https://docs.kernel.org/admin-guide/acpi/initrd_table_override.html)
- [Linux 睡眠状态](https://docs.kernel.org/admin-guide/pm/sleep-states.html)
- [Limine Entry Tool](https://gitlab.com/Zesko/limine-entry-tool)

本仓库新写的脚本采用 MIT 许可。厂商固件及补丁上下文中的厂商代码保留其原有权利；本仓库不附带完整固件。
