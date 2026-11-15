# 无地址 SSDT 路线

## 为什么不再固定地址

固件在当次启动时提供有效的 DSDT 和 OperationRegion 基址。正确做法是保留这张表，另加一个仅包含相对命名空间引用的 SSDT，而不是把之前启动导出的 DSDT 作为静态覆盖表。

`ssdt/SSDT-CHRG.dsl` 编译后为 146 字节，没有 OperationRegion、Field、I/O、物理地址或对原始方法的替换。它只在 `\_SB.PC00.LPCB.EC.CHRG` 缺失时创建一个通知接收对象。对象的 `_STA` 为 0，不用于枚举真实硬件或绑定驱动；已有的真实 CHRG 对象会保留原状。`_Q01` 原有的适配器和电池通知不变。

这是一项针对错误通知目标的兼容性补充，不实现真实充电器驱动，也不能预先保证解决所有提前唤醒。

## 已完成的验证

- overlay 和临时探针均以 0 Error、0 Warning 编译。
- ACPICA 仿真复现原始 `AE_NOT_FOUND`；加载补充表后通知完成，盖子方法结果不变。
- `_STA=0` 得到验证；若原本存在真实 CHRG，补充表不会改变它的状态。
- 实机通过 configfs 加载、卸载、重新加载；探针依次返回缺失、成功、缺失。
- 探针只在目标存在时调用当前固件 `_Q01`。实机调用成功且无 CHRG 报错。
- 全程原始 DSDT 哈希逐字节不变，盖子始终 open，未发生意外休眠。

探针表仅用于验证，读取后立即卸载。内存中的补充表可随时卸载，重启后也会消失；当前已安装的睡眠钩子会在首次挂起时重新加载补充表，不改动启动镜像。见[本机睡眠修复](sleep-workaround.md)。

## 构建与运行时验证

```bash
python3 scripts/build_ssdt.py
sudo python3 scripts/runtime_ssdt.py validate
sudo python3 scripts/runtime_ssdt.py status
sudo python3 scripts/runtime_ssdt.py remove
```

目标机需要 `CONFIG_ACPI_CONFIGFS`、已挂载的 configfs 和 `acpi_configfs` 模块。加载器检查目标型号、BIOS、内核及原始固件表元数据，只比较同一次启动内的 DSDT 哈希，不把跨启动变化当作固件损坏。

验证过程临时持有休眠和盖子处理抑制器；成功后释放，失败时自动撤回。旧完整 DSDT A/B 路线仍保持停用。

## 睡眠效果对照

`scripts/ssdt_sleep_pair.py` 使用同一次原始启动，先撤下补充表测原生状态，再加载补充表测第二组。两组均为接电、开盖、s2idle、60 秒 RTC，原始 DSDT 始终不变。每组只允许一次尝试。

两组均已完成：原生组约 2.95 秒退出，补充 SSDT 后约 3.21 秒退出；两组 SLP_S0 驻留增量均为 0、唤醒 IRQ 均为 9。CHRG 通知错误已修正，但该修正不足以解决提前唤醒。原始 DSDT 和盖子状态保持正常，RTC 闹钟已清空。见 [对照结果](../results/2026-11-16-same-boot-sleep-comparison.json)。完整私有记录在 `local/ssdt-trials/`。

仅当两组数据完整后才判断睡眠效果，CHRG 报错消失与硬件低功耗成功必须分别报告。

后续仅在睡眠期间屏蔽 GPE 6F 的隔离试验成功，记录到约 60.97 秒后恢复、57.48 秒 S0ix 驻留。提前唤醒由 6F 路径造成的证据已建立，但具体下游设备尚未唯一确定。这个结果不改变上述判断：CHRG SSDT 单独使用仍不足以修复睡眠。见[隔离试验结果](../results/2026-11-16-gpe6f-isolation.json)。

## 来源

- [内核 SSDT overlays/configfs 文档](https://docs.kernel.org/admin-guide/acpi/ssdt-overlays.html#loading-acpi-ssdts-from-configfs)
- [ACPI configfs 加载和卸载实现](https://github.com/torvalds/linux/blob/master/drivers/acpi/acpi_configfs.c)
- [ACPICA 通知目标与无处理器时的行为](https://github.com/torvalds/linux/blob/master/drivers/acpi/acpica/evmisc.c)
