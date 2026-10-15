# cRTOS on Pi4 — 脚本索引（路径 B：公开重建）

配套方案文档：`../docs/PI4_CRTOS__Pi4对比实验方案.md`

所有脚本在 **Pi 本机** 执行（Windows 侧通过 SSH 调用；WorkBuddy 产出与排错，不在 Windows 跑编译）。

| 脚本 | 作用 | 安全性 |
|------|------|--------|
| `preflight_pi4__平台探测.sh` | 只读探测板型/内核/EL2/工具链/源码 | ✅ 只读，先跑 |
| `install_jailhouse_pi4__安装Jailhouse.sh` | 编译+安装上游 Jailhouse | ⚠️ 编译+install，不 reboot/不 enable |
| `deploy_crtos_pi4__部署cRTOS.sh` | enable / rt-cell / status / teardown | ⚠️ 需 root；操作 hypervisor |
| `measure_pi4_crtos__对比测量.sh` | 测 jitter，输出 PLCFusion 同 schema JSON | ✅ 只读测量 |

## 顺序

```bash
bash preflight_pi4__平台探测.sh
bash install_jailhouse_pi4__安装Jailhouse.sh
sudo bash deploy_crtos_pi4__部署cRTOS.sh enable
sudo bash deploy_crtos_pi4__部署cRTOS.sh rt-cell
bash measure_pi4_crtos__对比测量.sh 15
sudo bash deploy_crtos_pi4__部署cRTOS.sh teardown
```

## 已知缺口（阶段 2）
- `rt-cell` 当前加载上游裸机 demo 作占位；NuttX/FreeRTOS 自测 inmate 就绪后替换 `RT_CELL`/`RT_BIN`。
- `realm=inmate` 测量待 inmate 侧导出逻辑完成后启用。
- 路径 A（作者原版 Pi4 cRTOS）代码若未公开，本目录仅覆盖路径 B。
