# cRTOS on Pi4 — 脚本索引（路径 B：公开重建）

配套方案文档：`../docs/PI4_CRTOS__Pi4对比实验方案.md`
实测结果与对照：`../results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md`

> **状态（2026-10-15）**：NuttX-inmate 已跑通；`realm=inmate` 测量分支已补齐；
> NuttX 移植产物含**一键复现**入口在 `../jailhouse_overlay_pi4/`。

## 脚本

| 脚本 | 作用 | 运行位置 | 安全性 |
|------|------|----------|--------|
| `../sync_pi4__同步Pi4脚本与产物.sh` | **把本目录+overlay 脚本/产物同步到 Pi**（tar 打包，规避中文名乱码） | **PC(Git Bash)** | ✅ 只上传 |
| `preflight_pi4__平台探测.sh` | 只读探测板型/内核/EL2/jailhouse | Pi | ✅ 只读，先跑 |
| `install_jailhouse_pi4__安装Jailhouse.sh` | 编译+安装上游 Jailhouse | Pi | ⚠️ 编译+install，不 reboot/不 enable |
| `deploy_crtos_pi4__部署cRTOS.sh` | enable / demo-inmate / linux-demo / status / teardown | Pi | ⚠️ 需 root；操作 hypervisor |
| `measure_pi4_crtos__对比测量.sh` | 测 jitter，输出 PLCFusion 同 schema JSON（`REALM=root_cell\|inmate`） | Pi | ✅ 只读测量 |
| `measure_inmate_serial__串口测inmate.sh` | 从串口驱动 inmate 的 rtlat（Linux/macOS 主机） | **主机** | ✅ 只读测量 |
| `measure_inmate_serial__串口测inmate.ps1` | 同上（Windows 主机，无依赖） | **主机** | ✅ 只读测量 |
| `measure_inmate_cyclictest__串口测cyclictest.ps1` | 串口驱动 inmate 的 **cyclictest**（与 root 侧 rt-tests **同族同参数**；默认无需 `/dev/timer`）。增量落盘 + 自适应停止，支持小时级长跑 | **主机** | ✅ 只读测量 |
| `ivshmem_probe__跨域共享内存.py` | root 侧读写 IVSHMEM 共享内存（`poke`/`watch`/`read`），验证跨域通道 | Pi | ✅ 读写共享区 |
| `organize_pi4_root__整理Pi4目录.sh` | 幂等整理 Pi 的 `/root`（只挪不删，绕开第三方目录） | Pi | ⚠️ 移动文件 |

NuttX 移植产物与一键复现（`../jailhouse_overlay_pi4/`）：
- `scripts/reproduce_pi4_crtos__一键复现.sh` —— Pi 上执行：构建 → 部署 → 提示测量
- `scripts/build_nuttx_inmate__构建NuttX_inmate.sh` / `scripts/deploy_nuttx_cell__部署NuttX_cell.sh`
- `patches/apply_patches__应用补丁.sh` —— arm64_head.S mpidr 补丁（幂等）

## 顺序（首次全新复现）

> ⚠️ **脚本不会自己出现在 Pi 上。** 这些脚本在仓库里，需先同步到 Pi 工作区。
> PC 侧（Git Bash，仓库根目录）一条命令搞定（tar 打包，规避 pscp 中文名乱码）：
> ```bash
> bash crtos/scripts/sync_pi4__同步Pi4脚本与产物.sh        # 默认 root@192.168.1.4
> ```
> 同步后 Pi 上落在 `/root/kfusion-pi4/scripts/pi4/`（**注意是子目录**），并附带 ASCII 软链
> （`preflight_pi4.sh`、`deploy_crtos_pi4.sh` …），可不带中文直接跑。

```bash
# A. 底座（在 Pi 上）—— 务必先 cd 到脚本所在目录
cd /root/kfusion-pi4/scripts/pi4
bash preflight_pi4.sh                 # 或 bash preflight_pi4__平台探测.sh
sudo bash deploy_crtos_pi4.sh enable  # 镜像自带 jailhouse；激活 root cell
#   注：若 `jailhouse cell list` 已能列出 cell，说明 hypervisor 已启用，可跳过 enable

# B. 上传 NuttX 依赖到 Pi（PC 侧 pscp）：toolchain.tar.xz / nuttx.tar.gz / apps.tar.gz /
#    rpi4-nuttx-nsh-inmate.defconfig / rpi4-nuttx.c / jailhouse 同版本头文件(解压到 /root/jh)

# C. 构建 + 部署 inmate（Pi，一键；overlay 脚本在 scripts/，不在 scripts/pi4/）
sudo bash /root/kfusion-pi4/scripts/reproduce_pi4_crtos.sh --root-soak 1

# D. inmate 抖动测量（在「接串口的主机」上，inmate 输出走物理 mini-UART）
bash measure_inmate_serial__串口测inmate.sh /dev/ttyUSB0 1000 60000          # 或 .ps1
INMATE_LOG=<上一步日志> REALM=inmate bash measure_pi4_crtos__对比测量.sh 1

# D'. 与 root 侧「同一套工具」的对照测量（推荐用于论文同板对照表）
#     root 侧：rt-tests cyclictest；inmate 侧：其 NuttX 移植版，同参数同输出格式。
#     cyclictest 直方图以 1us 为桶、值随时长增长到 7 位；长跑（1h）示例：
powershell -ExecutionPolicy Bypass -File measure_inmate_cyclictest__串口测cyclictest.ps1 `
    -Port COM11 -IntervalUs 1000 -Loops 3600000 -Priority 200 -HistMax 200

# E. 收尾
sudo bash deploy_crtos_pi4__部署cRTOS.sh teardown
```

## 缺口状态
- ✅ `realm=inmate` 测量：已实现（解析主机侧 rtlat 日志；因物理串口与 root cell 共享，无法 Pi 本地可靠读）。
- ✅ NuttX 自测 inmate：已跑通（见 `../jailhouse_overlay_pi4/`）。
- ⛔ 路径 A（作者原版 Pi4 cRTOS）代码未公开，本目录仅覆盖路径 B。
- ⬜ `deploy_crtos_pi4` 的 `demo-inmate` 仍加载上游裸机 demo（占位）——装载真正的 NuttX inmate 请用
  `../jailhouse_overlay_pi4/scripts/deploy_nuttx_cell__部署NuttX_cell.sh`。
