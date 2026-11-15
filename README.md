# Dynabook G83/HS：CHRG 通知保护实验

**实验已停止，勿安装或启动 A/B 镜像。** A Control 启动后，系统在盖子实际打开时报告盖子合上，logind 因此反复发起休眠。A/B 入口和镜像已移除，B 组未启动。恢复原始 Omarchy 后，盖子状态及桌面运行已核验正常。

还发现同版本原始 DSDT 在不同启动间包含变化的操作区地址；静态整表覆盖固定了旧地址。这是本实验设计的遗漏，意味着编译及 A/B 差异检查不足以建立可靠控制组。详见[失败与恢复分析](docs/failure-analysis.md)。原始 s2idle 故障尚未修复，本轮不能用于判定 CHRG 补丁效果。

当前仓库保留失败记录和恢复工具，安装器及实测脚本已增加停止保护。以下构建/实验说明作为历史记录，不是可用修复的安装指南。

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

启动镜像的构建、安装和回滚步骤见[实验步骤](docs/experiment.md)。不要将未核验的表用于其他型号或 BIOS。

当前构建已通过 8 项测试；A/B 两组均为 0 编译错误、219 条相同的编译警告。编译后再次反编译对比，仅存在 CHRG 保护这一处逻辑差异。原始微码、early CPIO 已有的 578 个条目、压缩主 initramfs 和其他 UKI 段均经验证保持原样。见[镜像校验记录](results/2026-11-15-build-validation.json)和[编译产物逻辑差异](results/2026-11-15-compiled-semantic.diff)。这些是静态检查，不代表已通过实际启动或休眠验证。

## 回滚

在 Limine 中选择原有 `Omarchy/linux-omarchy` 启动即可使用固件原始表。实验入口不设为默认项；不刷写 BIOS。安装器保存菜单备份，移除实验时只删除本实验入口和镜像。

## 来源与许可

- [内核 ACPI 表覆盖说明](https://docs.kernel.org/admin-guide/acpi/initrd_table_override.html)
- [Linux 睡眠状态](https://docs.kernel.org/admin-guide/pm/sleep-states.html)
- [Limine Entry Tool](https://gitlab.com/Zesko/limine-entry-tool)

本仓库新写的脚本采用 MIT 许可。厂商固件及补丁上下文中的厂商代码保留其原有权利；本仓库不附带完整固件。
