[English](README.md) | **简体中文**

# Dynabook G83/HS：s2idle 与 ACPI CHRG 修复

本仓库提供已通过实机验证的本机兼容性修复：**合盖进入 S0ix 低功耗睡眠，开盖正常恢复**。适用环境为 Dynabook G83/HS（A010D）、BIOS 8.90、Omarchy，实测内核为 `7.2.15-1-omarchy`。其他固件或内核需要重新验证。

人类使用本页即可完成诊断、安装和验收。交给编程代理时，使用[中文 LLM 修复提示词](prompts/repair.zh-CN.md)。机器可读的成功记录位于 [validation/verified.json](validation/verified.json)。

## 根因分析

### 1. 提前唤醒来自 GPE 6F 路径

故障表现为合盖后很快退出 s2idle、反复挂起/恢复，低功耗驻留为 0，机器温热且耗电。主 EC 的 GPE 是 **6E**，清醒时持续大量触发的是另一条 **6F**；本机固件 `_L6F` 分派到 PCIe/PEG GPIO 唤醒路径。

因果证据来自隔离试验：保持当前原始 DSDT 和其他条件不变，仅在睡眠期间屏蔽 6F，系统即可保持约一分钟睡眠，硬件 S0ix 计数明显增加。共享的 ACPI SCI IRQ 9 本身不能指出故障设备；**尚未唯一确定 6F 下游的具体设备**，不能把读卡器直接认定为根因。

### 2. CHRG 缺失是另一项固件错误

`\_SB.PC00.LPCB.EC._Q01` 完成适配器、电池通知后执行 `Notify (CHRG, 0x80)`，但 Linux 命名空间中没有该对象，产生 `AE_NOT_FOUND` 并中止方法。补齐 CHRG 可以修正这项错误；同次启动对照证明，**单独修正 CHRG 仍会提前唤醒**。

### 3. ACPI 内存地址随启动变化是正常的固件行为

本机同版本 DSDT 中的多个 `OperationRegion` 基址会随启动变化，例如包含盖子状态字段的 `GRSV`。把前次导出的整张 DSDT 固定为覆盖表，会保留过期地址，并可能读错盖子等状态。正确方案是保留固件在当次启动提供的表与地址，只补充命名空间。DSDT 哈希只适合比较**同次启动内**是否被修改，不能固定一个跨启动哈希作为机器身份。

## 复现与诊断

先确认型号、BIOS、睡眠模式和中断状态：

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

适用机器应报告 `dynabook G83/HS`、`Version 8.90`，当前选择 `[s2idle]`。6F 计数快速增长是诊断线索，需要结合实际睡眠结果判断。

在尚未修复的机器上，保存工作后合盖约一分钟再开盖；比较前后的硬件低功耗计数，并检查是否有多次挂起/恢复：

```bash
sudo cat /sys/kernel/debug/pmc_core/slp_s0_residency_usec
# 合盖约一分钟，随后开盖；再读同一计数器，取两次读数之差。
sudo cat /sys/kernel/debug/pmc_core/slp_s0_residency_usec
journalctl -b -u systemd-logind.service -u systemd-suspend.service --since '5 minutes ago'
journalctl -b -k --since '5 minutes ago' -g 'PM: suspend|CHRG|AE_NOT_FOUND'
```

原故障的开盖对照测试约 3 秒就返回、S0ix 增量为 0；合盖场景则可能反复自动挂起。已正常工作的机器无需为了复现而卸载修复。离线构建也会用完全模拟的命名空间复现 CHRG 错误，再验证补充表；不会加载模拟 DSDT 到实机。

## 修复方案

- **无地址 SSDT**：146 字节的补充表仅在 CHRG 缺失时创建 `_STA=0` 的通知目标。已有 CHRG 不受影响；没有寄存器、物理地址、OperationRegion 或原方法替换。
- **睡眠期间屏蔽 6F**：systemd 的 pre 钩子在 s2idle 前保存屏蔽状态并临时屏蔽 6F；post 钩子恢复原状态。主 EC 6E 保持原样。
- **失败清理**：`ExecStopPost` 补充恢复动作；重复清理不会改变其他工具先前设置的屏蔽策略。运行状态保存在 `/run/dynabook-sleep/`，按启动标识隔离。

补充表通过 configfs 加载，重启后由首次挂起的钩子重新加载。钩子不使用冻结的用户会话服务。这是本机的系统睡眠兼容性补丁；睡眠期间 6F 路径的 PCIe/PEG 唤醒能力会被限制。

## 安装

依赖 Python 3、ACPICA 的 `iasl`/`acpiexec`，以及内核 `CONFIG_ACPI_CONFIGFS` 支持。Omarchy 可用 `omarchy pkg add acpica` 安装编译工具。以下从干净克隆开始，不需要厂商完整固件或此前的实验记录。

```bash
gh repo clone soimy/dynabook-g83hs-acpi-fix
cd dynabook-g83hs-acpi-fix
python3 scripts/build_ssdt.py
python3 -m unittest discover -s tests -v
sudo python3 scripts/install_sleep_fix.py --check
sudo python3 scripts/install_sleep_fix.py
sudo python3 scripts/install_sleep_fix.py --status
```

构建器检查源码、编译后 AML 与已验证摘要完全一致，并运行离线解释器检查。安装器检查型号、BIOS、原生 DSDT 元数据及 s2idle，拒绝覆盖无关的现有文件；相同版本重复安装只核验现有文件。安装时会短暂执行 pre/post 自检、恢复屏蔽状态，不主动挂起或重启机器。

安装位置如下，文件均由 root 拥有：

| 路径 | 用途 |
| --- | --- |
| `/usr/local/libexec/dynabook-sleep/sleep_fix.py` | 已验证的睡眠修复逻辑 |
| `/usr/local/libexec/dynabook-sleep/SSDT-CHRG.aml` | 无地址补充表 |
| `/usr/lib/systemd/system-sleep/90-dynabook-gpe6f` | pre/post 钩子 |
| `/etc/systemd/system/systemd-suspend.service.d/90-dynabook-gpe6f.conf` | 失败清理 |

安装清单位于 `/var/lib/dynabook-sleep/installation.json`。无需修改 Limine、内核参数、initramfs、UKI，也无需重启；本修复不设置 RTC 闹钟，不配置 Hibernate。

## 可选：开盖闲置时自动睡眠

ACPI 补丁修复的是挂起流程本身，不会增加闲置策略。本机 Omarchy 原先只有 150 秒屏保、300 秒锁屏，logind 为 `IdleAction=ignore`，所以锁屏或黑屏并不代表系统已睡眠。

`omarchy/sym.auto-suspend/` 中已实测通过的可选插件补充了 **仅电池供电时，闲置 600 秒后自动挂起** 的策略，插电时不自动挂起。它使用合成器的闲置通知，遵守应用闲置抑制器和 Omarchy 的 Stay Awake 状态，保留原有屏保/锁屏。首次加载有十分钟保护期，每轮闲置最多请求一次挂起，不设置 RTC 唤醒。

以桌面用户安装，不使用 sudo。目标目录已经存在时应先核对，不覆盖现有文件：

```bash
mkdir -p "$HOME/.config/omarchy/plugins"
test ! -e "$HOME/.config/omarchy/plugins/sym.auto-suspend" && \
  cp -a omarchy/sym.auto-suspend "$HOME/.config/omarchy/plugins/"
omarchy shell shell rescanPlugins
omarchy plugin list --json
# 列表中出现 sym.auto-suspend 后再启用：
omarchy plugin enable sym.auto-suspend
omarchy shell sym.auto-suspend status
```

保存工作后保持电池供电、盖子打开，闲置十分钟后唤醒。核对用户日志中的 `battery idle suspend requested`，以及随后内核的 `PM: suspend entry (s2idle)`。实测 12:06:59 请求睡眠、12:07:00 进入 s2idle、12:22:41 恢复；941.12 秒挂起中有 938.35 秒 S0ix，约占 99.7%。Stay Awake 阻止/恢复联动已测；本轮未进行实际插拔电源的切换测试。

仅停用该闲置策略时执行 `omarchy plugin disable sym.auto-suspend`。固件修复使用下方独立的撤销命令。

## 验收与已验证结果

保存工作后，在电池供电下合盖至少一分钟再开盖，执行：

```bash
sudo python3 scripts/install_sleep_fix.py --status
journalctl -b -u systemd-logind.service -u systemd-suspend.service --since '5 minutes ago'
```

检查只有一次挂起/恢复、唤醒紧随开盖，`last_cycle_is_current_boot`、`mask_state_restored`、`original_dsdt_unchanged` 和 `native_dsdt_matches_last_cycle` 为 true，`pending_cleanup_exists` 为 false。清醒时 6F 与主 EC 6E 应为 `unmasked`；`slp_s0_residency_delta_us` 应接近实际睡眠时长。若内核未暴露 PMC 计数，值可能为空，应检查 `/sys/kernel/debug` 的 debugfs 挂载及平台支持，不以屏幕黑屏单独判定低功耗成功。

| 已通过的实机验证 | 挂起/恢复区间 | S0ix 驻留 |
| --- | ---: | ---: |
| 电池供电，生产钩子，60 秒测试闹钟 | 61.44 秒（含流程开销） | 57.60 秒 |
| 实际合盖/开盖，无测试闹钟 | 55.32 秒 | 53.17 秒（96.1%） |
| 整夜合盖、电池供电 | 7 小时 36 分 33 秒，含一次短暂唤醒 | 睡眠时间的 99.95% |
| 开盖、电池供电、闲置超时 | 15 分 41 秒 | 938.35 秒（99.7%） |

实际合盖测试在开盖后约 47 毫秒记录到内核退出挂起，原始 DSDT 保持不变，无新增 ACPI 错误。后续整夜测试实测 **7 小时 36 分 33 秒耗能 1.063 Wh，平均约 0.14 W**，相当于满充容量 42.873 Wh 的 2.48%。实际睡前为 31.739 Wh，醒来即时为 30.676 Wh；醒后十秒刷新为 30.646 Wh（显示 71%），这份刷新读数另含清醒耗电。07:35:42 合盖状态下有一次约 28 秒的短暂唤醒，随后自动恢复睡眠；用户反馈家人可能碰到鼠标，但具体设备未由日志唯一确认。其他物理唤醒方式、未来重启后的首次挂起仍需各自验收，本次未进行 S3 功耗对照。

## 撤销

```bash
sudo python3 scripts/install_sleep_fix.py --remove
```

撤销器核对安装清单与文件摘要，恢复本修复持有的屏蔽状态，卸载相同的补充表，移除已安装文件并重新加载 systemd。它拒绝删除已被其他工具修改的文件，也兼容此前本机成功安装的清单。移除本仓库的克隆目录不会自动卸载修复。

## 仓库结构与来源

`fix/` 为已验证的运行代码；`acpi/` 为补充表源码和固定摘要；`scripts/` 为构建及安装/检查/撤销入口；`omarchy/` 为可选的电池闲置睡眠插件；`tests/` 为离线语义和恢复保护检查；`validation/` 仅保留成功的实机证据；`prompts/` 提供可复制给 LLM 的修复指令。构建产物位于忽略的 `build/`。

- [Linux SSDT overlays/configfs](https://docs.kernel.org/admin-guide/acpi/ssdt-overlays.html#loading-acpi-ssdts-from-configfs)
- [Linux GPE sysfs 的 mask/unmask 实现](https://github.com/torvalds/linux/blob/master/drivers/acpi/sysfs.c)
- [systemd 睡眠钩子的时序与用户会话冻结](https://www.freedesktop.org/software/systemd/man/latest/systemd-suspend.service.html)

新写的代码采用 [MIT 许可](LICENSE)。仓库不分发厂商完整 ACPI 固件。
