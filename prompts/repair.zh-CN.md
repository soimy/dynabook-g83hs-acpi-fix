[English](repair.md) | **简体中文**

# 交给编程代理的修复提示词

将下面的提示词复制给能够检查故障机器的代理。仓库：`https://github.com/soimy/dynabook-g83hs-acpi-fix`。[人类使用指南](../README.zh-CN.md)。

---

请诊断目标笔记本，并应用本仓库已经验证的 s2idle 兼容性修复。先阅读仓库及适用的本地指令，完成诊断、安装检查、验收并给出明确结论。复用现有实现，不重新设计 ACPI 表。

## 已知诊断与证据

- 已验证机器：dynabook G83/HS、主板 A010D、BIOS Version 8.90；实测内核 7.2.15-1-omarchy，使用 s2idle。运行时保护检查型号、BIOS 和原始 DSDT 元数据。不同内核仍需验证；其他型号或 BIOS 不在本修复已验证范围内。
- 主 EC 使用 GPE 0x6E。GPE 0x6F 连接固件的 PCIe/PEG GPIO 唤醒路径，隔离试验证明该路径造成被测提前唤醒。具体下游设备尚未唯一确定，IRQ 9 本身不是设备根因诊断。
- 固件 `\_SB.PC00.LPCB.EC._Q01` 在适配器、电池通知之后引用缺失的 CHRG，触发 AE_NOT_FOUND。单独补齐该对象不能阻止提前唤醒。
- 固件 OperationRegion 基址会随启动变化，包括存放盖子状态的区域。保留当次固件 DSDT；哈希只用于同次启动内的一致性检查，不能跨启动固定作为机器身份。
- 已验证修复包含两部分：146 字节、无地址的 SSDT，有条件地补充禁用的 CHRG 通知对象；systemd pre/post 钩子只在 s2idle 期间屏蔽 6F、随后恢复原状态，ExecStopPost 保障失败清理。6E 保持原样。
- 成功的电池测试：60 秒定时试验中 S0ix 驻留 57.60 秒；真实合盖试验在 55.32 秒挂起区间中有 53.17 秒 S0ix（96.1%），开盖后约 47 毫秒记录到内核恢复。后续整夜测试在 7 小时 36 分 33 秒内耗能 1.063 Wh（约 0.14 W、满充容量的 2.48%），睡眠期间 S0ix 驻留为 99.95%。期间有一次约 28 秒的合盖唤醒，用户反馈可能是家人碰到鼠标，随后自动恢复睡眠。具体唤醒设备、其他唤醒方式及 S3 功耗对照尚未证实。见 `validation/verified.json`。

## 改动前检查

1. 阅读 `README.zh-CN.md`、`acpi/manifest.json`、`fix/sleep_fix.py`、`scripts/install_sleep_fix.py`。记录 git 状态，保留无关的用户改动。
2. 核对 DMI 型号/BIOS、`/sys/power/mem_sleep`、原生盖子状态、供电状态、现有睡眠钩子及安装清单。
3. 检查内核日志中的 CHRG/AE_NOT_FOUND 及 EC 的 GPE 编号。至少读取两次 6F 计数，用增量判断，不从单个累积值认定中断风暴。计数增长只是辅助证据，不能唯一指认下游设备。
4. 确认 ACPI configfs 支持，以及 Python 3、iasl、acpiexec。按运行环境正常流程安装依赖和提权，不收集或代填用户密码；终端使用 sudo，后台图形代理可使用 pkexec。
5. 型号或固件不符时，在安装前停止并说明差异。不要放宽保护或屏蔽未经验证的 GPE 来让脚本通过。

## 构建和安装

在干净克隆中以普通用户执行：

```bash
python3 scripts/build_ssdt.py
python3 -m unittest discover -s tests -v
```

构建必须通过离线命名空间检查，并生成与固定摘要一致的源码/AML；睡眠钩子源码必须与已实机测试的摘要一致。不要只改 manifest 来绕过不一致。

以下命令使用管理员权限：

```bash
python3 scripts/install_sleep_fix.py --check
python3 scripts/install_sleep_fix.py
python3 scripts/install_sleep_fix.py --status
```

`--check` 和 `--status` 只读。安装会短暂验证 pre/post 清理，不主动挂起或重启；相同版本已经安装时，核验后保留。安装器拒绝覆盖无关的目标文件。保留安装清单，以便受控撤销。

## 与用户完成验收

1. 进行物理睡眠测试前确认工作已保存。仅整理或审阅仓库时，不触发新的挂起、重启或固件操作。沿用会话中已有的测试授权，只有确实缺少授权时才询问。
2. 请用户在电池供电下合盖至少一分钟后开盖。随后检查 logind/内核时间戳并运行状态命令；读日志不需要再次休眠。
3. 要求只有一次挂起/恢复、没有提前唤醒循环，醒后盖子 open、同次启动 DSDT 未变、6F 已恢复、主 EC 6E 未屏蔽、无待清理状态。同时核对 `last_cycle_is_current_boot`、`native_dsdt_matches_last_cycle` 与本轮回执，不能仅凭旧的成功记录下结论。
4. 检查回执里的 `slp_s0_residency_delta_us`。黑屏和风扇停转不能替代硬件计数；PMC/debugfs 不可用时明确报告限制。将恢复时刻与开盖时刻对应，不把 IRQ 9 本身当作错误。
5. 不增加周期性 RTC 唤醒；本安装器不设置闹钟。保留用户已有的 RTC 闹钟，不清除非本次创建的闹钟。
6. 报告测得的时长、S0ix 驻留与清理状态。区分仓库已记录的短测、整夜实测与当前新一轮结果；其他唤醒方式和未来重启后的首次挂起仍需验证，S3 功耗对照尚未实测。

## 边界与撤销

- 不安装静态整张 DSDT 覆盖，不固定前次启动的 OperationRegion 地址，不把测试用模拟 DSDT 加载到真实内核。
- 不屏蔽主 EC 6E，不永久屏蔽 6F。本修复的明确代价是仅在睡眠期间限制 6F 路径的 PCIe/PEG 唤醒。
- 不添加 `_OSI`、`acpi_mask_gpe`、`mem_sleep_default=deep`、休眠/恢复偏移参数、启动入口或 initramfs 改动。这些属于其他排查，与本修复无关。
- Omarchy 上不修改 `/usr/share/omarchy`。本修复使用自有的 root 代码目录、systemd 钩子和服务 drop-in。
- 不覆盖已被本地修改的安装文件，不删除无关钩子。验收出现回归时保存证据，通过安装器撤销，不反复重试失败的睡眠。

```bash
# 使用管理员权限；移除前检查文件所有者和摘要。
python3 scripts/install_sleep_fix.py --remove
```

最后简明报告：测量支持的根因、已安装或原有的文件、实际完成的验收、剩余范围及撤销命令。不要将效果单独归因于 CHRG 或某个读卡器；使用整夜能量记录描述耗电，不从短测外推整夜续航。
