# Pi 5：U 盘与电脑热点（WiFi）同时用

## 现象

- 插 **USB 3.0** U 盘（如 SanDisk）后，Pi **连不上** Windows 手机热点 / 电脑共享 WiFi，Cursor SSH 频繁断开。
- **拔掉 U 盘** 后 WiFi 恢复。

## 原因（常见组合）

1. **USB 3 对 2.4 GHz 的电磁干扰**  
   当前热点若在 **2.4 GHz**（例如 `Frequency:2.412 GHz`），与 USB3 数据线/控制器噪声重叠，会导致关联失败、高重传、SSH 卡顿。  
   Pi 日志里可能出现 `Tx excessive retries` 升高。

2. **供电**  
   大容量 U 盘 + **满核编内核** 会拉高 5V 电流。若电源偏弱，WiFi 芯片会先不稳定（`vcgencmd get_throttled` 非 0 时查供电）。

3. **误拔 U 盘**  
   内核源码在 U 盘上时拔掉盘，**编译会立刻失败**（找不到头文件 / I/O error）。

## 推荐做法（按优先级）

| 做法 | 说明 |
|------|------|
| **热点改 5 GHz** | Windows「移动热点」→ 编辑 → 频段选 **5 GHz**（PC 网卡需支持） |
| **网线** | Pi `eth0` 接 PC 或路由器，最稳 |
| **USB 2.0 口 / 延长线** | 换黑色 USB2 口或加 **USB2 延长线**，降低 2.4G 干扰 |
| **编内核时别拔盘** | 用 `nohup` 后台编；远程只看 `tail -f` 日志 |
| **关 WiFi 省电** | 见下方一键脚本 |

## 本仓库脚本

```bash
# 插回 U 盘后挂载
bash scripts/crtos/mount_jhbuild_usb__挂载JHBUILD.sh

# 缓解 WiFi（NetworkManager 持久）
sudo bash scripts/crtos/fix_wifi_usb_coexist__WiFi与U盘共存.sh

# 后台续编内核
bash scripts/crtos/run_jailhouse_kernel_background__后台JAIL内核.sh
```

## 可选：开机自动挂载 U 盘

插盘后执行一次（**UUID 以 `sudo blkid LABEL=JHBUILD` 为准**）：

```bash
echo 'LABEL=JHBUILD /media/pi/JHBUILD ext4 defaults,noatime,nofail,x-systemd.device-timeout=5 0 2' | sudo tee -a /etc/fstab
sudo mkdir -p /media/pi/JHBUILD
```

`nofail`：没插 U 盘时开机不会卡住。
