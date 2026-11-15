# 实验步骤

状态：**实验已停止。A Control 启动后出现盖子状态误报和自动休眠循环，未进入计划的 60 秒实测；B 未启动。A/B 入口及镜像已移除，原始启动已恢复并核验。发现跨启动的固件操作区地址变化，当前静态整表覆盖设计无效。以下步骤仅保留为历史记录，请勿继续执行安装或实测。**

## 准备与核验

从目标机本地捕获原始输入，生成表和镜像，再安装两个独立入口：

```bash
sudo python3 scripts/capture_inputs.py
python3 scripts/build_tables.py --input local/inputs/DSDT.aml --output local/tables
python3 scripts/build_images.py
python3 -m unittest discover -s tests -v
sudo python3 scripts/install_boot_entries.py
```

捕获脚本只读 `/boot` 和固件，拒绝覆盖已有采集。构建脚本保留原始 early CPIO 中的微码和其他文件，并逐字节验证主 initramfs、内核和其余 UKI 段。实验镜像未签名，安装器要求 Secure Boot 关闭。

## A 组

保存工作，重启后选择 **ACPI CHRG - A Control**。登录后先核验实际加载表：

```bash
sudo python3 scripts/verify_loaded.py --expected A
```

只有显示 A 且内核、BIOS、命令行与基线一致，才运行一次实测。保持与基线相同的接通电源、开盖条件，保存工作后运行：

```bash
sudo python3 scripts/run_trial.py A
```

等待 60 秒，不操作键盘、电源键或盖子。提前醒来也不要手动再次休眠。结果位于 `local/trials/`；脚本每个变体每次启动只允许一次尝试。

## B 组

重启选择 **ACPI CHRG - B Guard**，执行相同条件的检查和一次测试：

```bash
sudo python3 scripts/verify_loaded.py --expected B
sudo python3 scripts/run_trial.py B
```

只有实际加载哈希正确且 B 组消除了 CHRG 报错，才能认为补丁命中了目标。只有 B 组能够保持约 60 秒并明显增加 SLP_S0 驻留时间，且 A 组仍提前醒来，才支持补丁改善睡眠的判断。两组都失败或都成功时，不能宣称 CHRG 是根因。开盖接电测试不能替代后续电池合盖耗电验证。

## 回滚与移除

任意时候选择原始 **Omarchy / linux-omarchy** 启动。默认项始终保持这个原始入口。实验不会刷写 BIOS，也不把 ACPI 覆盖安装到全局 mkinitcpio 配置。

移除两个实验入口和镜像：

```bash
sudo python3 scripts/install_boot_entries.py --remove
```

菜单备份保存在 `/var/backups/limine-chrg-experiment/`。出现无法恢复的黑屏时停止本轮，恢复原始入口；不要自动重试或转为 deep。未完成的试验记录会保留 `suspending` 状态用于排查。
