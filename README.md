# plc_compiler_rpi5

Raspberry Pi 5 实时系统实验仓库：**PLCFusion**（C→内核融合）、**TimedC**（KTC 周期 C）、**cRTOS/Jailhouse**（分区 hypervisor 与 NuttX cell）。

> 目录结构见 [REPO_LAYOUT.md](REPO_LAYOUT.md)（`原名__功能说明` 命名规则见 [FILES.md](FILES.md)）。

## 顶层目录

| 目录 | 说明 |
|------|------|
| [`plcfusion/`](plcfusion/) | PLC 低抖动编译器 + LLVM Pass + manifest 融合管线 |
| [`crtos/`](crtos/) | Jailhouse hypervisor/driver 补丁、cRTOS 上游、Pi5 脚本 |
| [`timedc/`](timedc/) | TimedC / KTC 树莓派移植 |
| [`compare/`](compare/) | 本地基准与论文对比结果（**不提交 Git**，见目录 README） |
| [`results/`](results/) | 指向 `compare/` 的符号链接 |

## 快速入口

### PLCFusion

```bash
cd plcfusion
bash scripts/run_ci__CI门禁.sh          # 无 insmod 门禁
bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_plc_cc_hello__入门.env
```

详见 `plcfusion/scripts/README.md`。

### Jailhouse on Pi 5（进行中）

```bash
cd /home/pi/plc_compiler
bash crtos/scripts/rebuild_jailhouse_pi5__重编HV与驱动.sh
bash crtos/docs/README__Jailhouse树莓派5实验.md   # 完整流程
```

Pi5 关键补丁：`crtos/upstream/jailhouse/`（canonical VA、`cpu_soft_restart` EL2 入口、VHE leave、scratch 诊断）。

### TimedC

见 `timedc/docs/README__TimedC树莓派移植.md`。

## 环境

- 目标板：Raspberry Pi 5，内核 `6.12.x-jh`（PREEMPT_RT + Jailhouse ksym）
- 开发机：Linux + clang/llvm + 内核 headers

## 许可证

各子目录遵循对应上游许可证（Jailhouse GPL-2.0、PLCFusion 见源文件头等）。
