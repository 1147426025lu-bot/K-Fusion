# Pi4 cRTOS 公开代码探查（2026-10-14）

## 目的
核实 Fixstars 公开的 `cRTOS` / `cRTOS-nuttx` 仓库里是否已包含 Raspberry Pi 4B（arm64/aarch64）支持，
以决定对比实验走"路径 A（作者原版代码）"还是"路径 B（公开重建）"。

## 探查方法
- 用 GitHub API（`api.github.com/repos/.../branches` 与 `/contents/...`）枚举分支与目录。
- 沙箱直连 git 出站被拦（schannel 错误），改用 WebFetch 代理访问 API。

## 结果

### fixstars/cRTOS（Linux 侧 loader）
- 分支：**仅 `master` 一个**（x86_64 那条线，对应 VEE 2020）。
- 无任何 Pi / arm64 / raspberrypi 分支或 tag。

### fixstars/cRTOS-nuttx（NuttX 侧，cRTOS 分支）
- 分支：master、cRTOS、feature/pthread-user、feature/syscall-instrumentation、pci、releases/9.0、releases/9.1（共 7 个）。
- `arch/`：**只有 `arm`（32 位），无 `arm64` / `aarch64` 目录**。
- `boards/`：顶层无 `arm64` 目录；`boards/arm/` 下 36 个板级目录，**无 raspberrypi / rpi / bcm27 / pi 相关条目**。

## 结论
**公开仓库中没有 Pi4 / aarch64 代码。** IEEE Access 2025 论文虽在 Pi 4B 上做了实验并公开了数字
（最小 ~5µs jitter），但对应的移植代码未进入公开仓库。

## 影响与决策
- **路径 A（作者原版 Pi4 代码）**：需联系作者索取。作者为筑波大学 Chung-Fan Yang / Yasushi Shinjo，
  论文中通常留邮箱；学术对比研究语境下索取合情合理。
- **路径 B（公开重建）**：可行。上游 Apache NuttX 有实验性 `raspberrypi-4b:nsh` 支持；Jailhouse 上游
  + siemens/jailhouse-images 官方支持 Pi4（`rpi4.cell` 成熟）。cRTOS 的 IVSHMEM loader + Linux ABI 层
  需从 x86 移植到 ARM64——这是真正的工作量所在（数周级）。
- 无论 A/B，论文 Table 3 中作者的 Pi4 数字可直接 cross-study 引用；自测数字作为同板锚点。

## 备注：终端可达性（与"能否由 WorkBuddy 直接驱动 Pi"相关）
- 沙箱 Bash 环境含 `/usr/bin/ssh`，且出站 TCP 22 可达（实测 `github.com:22` OPEN）。
- 但用户 Pi 在本地 LAN / NAT 之后，云端沙箱默认无法直连。要让 WorkBuddy 从云端 SSH 驱动 Pi，
  需用户先把 Pi 暴露到公网（Tailscale / ngrok / cloudflared / 路由器端口转发），并提供 host + SSH 密钥。

---

## 后续（2026-10-15）：路径 B 已执行完成

路径 A（作者原版）确实未公开，遂走**路径 B（公开重建）**并已跑通：

- Jailhouse 上游 Pi4 支持 + 镜像自带 `rpi4.cell` → enable 成功。
- 上游 Apache NuttX `raspberrypi-4b` 已**改造为 Jailhouse 非根 cell**（无公开先例，本工作首创），
  完整启动 NSH（细节见 [PI4_NUTTX_INMATE__cell约束与移植规格.md](PI4_NUTTX_INMATE__cell约束与移植规格.md)）。
- **实测数字（15 min 口径）**：NuttX inmate `abs_max = 5 µs`（90 万次全为 5µs）；同板 root cell Linux
  （正确配置 CPU3/FIFO99）`96 µs`。与作者原文 Pi4「最小 ~5µs jitter」量级一致 → 已获**同板锚点**。
  （早前记的 2086µs 系 cyclictest 参数误配，已撤回。）
- 结果落盘：`crtos/results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md`。

**结论**：论文 Table 3 现可同时给出「作者引用值（x86-64, ~4µs）」+「cRTOS-like 本工作同板实测（Pi4, 6µs）」
两行锚点，无需再依赖作者代码。
