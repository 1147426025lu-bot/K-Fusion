# xlat 测量结果目录（跨域延迟 / 五档负载 / root 基线）

本目录只入库**图、直方图文本与说明**；**原始 `.bin` 不入库**（见下）。

## 1. 为什么 `.bin` 不入库

| 文件 | 体积 |
|---|---|
| `xlat_8h.bin` | **418 MB** |
| `xlat_8h.jitter.bin` / `.latency.bin` | 各 **209 MB** |
| `xlat_1h.bin` | 53 MB |
| `xlat_1h.jitter.bin` / `.latency.bin` | 各 27 MB |
| `load_{noload,cpu,cache,vmem,stream}.bin` | 各 14 MB |
| 合计 | **≈ 1.1 GB** |

GitHub 单文件硬限 **100 MB**，其中三个文件直接超限；且这些数据**可由脚本重跑**。
`.gitignore` 已排除 `crtos/results/xlat/**/*.bin`。

**所有统计结论已固化在**
`crtos/results/COMPARE_Pi4_cRTOS_vs_KFusion__Pi4对比K-Fusion.md` §9（含分位数、窗口、引用纪律），
不依赖本地 `.bin`。

## 2. 入库的内容

| 文件 | 说明 |
|---|---|
| `xlat_{15min,1h,8h}_jitter_dist.png` / `_jitter_latency.png` | inmate 域内抖动（分布 / 时间线） |
| `xlat_{15min,1h,8h}_xdomain_dist.png` / `_xdomain_latency.png` | 跨域回传延迟（分布 / 时间线） |
| `xlat_load_matrix_5tier.png` | 五档负载对比（分布叠加 + 分位数柱状图） |
| `inmate_cyclictest_100s_vs_1h.png` | 同尺子不同窗口（100 s vs 1 h）对比 |
| `rootbase/root_1h.hist` | root cell 1 h 直方图原文（cyclictest `--histfile`，200 桶） |

## 3. 原始数据在哪

Pi 4B：`/root/kfusion-pi4/results/xlat/`（`xlat_*.bin`、`load_*.bin`）。
Pi 每次重启或重测会覆盖同名文件，**如需长期保留请及时拉回**。

## 4. 如何重跑

```bash
# 0) 前提：hypervisor 已 enable，NuttX cell 运行中（重启后不会自动启用）
jailhouse enable /etc/jailhouse/rpi4.cell
jailhouse cell create /root/kfusion-pi4/out/rpi4-nuttx.cell
jailhouse cell load rpi4-nuttx /root/kfusion-pi4/build/nuttx-build/nuttx/nuttx.bin -a 0x480000
jailhouse cell start rpi4-nuttx

# 1) 在接 USB-TTL(COM11) 的主机上，经 NSH 启动探针（inmate 控制台在 GPIO14/15，
#    jailhouse console 看不到）
#    nsh> xlat -i 1000 &

# 2) root cell 侧采集（duration 秒）
chrt -f 60 /root/kfusion-pi4/scripts/xlat_collect -d 900 -o /root/kfusion-pi4/results/xlat/xlat_15min.bin -c 2

# 3) 五档负载矩阵（noload/cpu/cache/vmem/stream，各 900 s）
bash /root/kfusion-pi4/scripts/xlat_load_matrix__负载档矩阵.sh stream 900 stream

# 4) 转换 + 出图（本机）
python3 crtos/scripts/pi4/xlat2jitterbin__跨域结果转图格式.py <rec.bin> <prefix>
python3 crtos/scripts/pi4/xlat_load_compare__五档对比图.py <结果目录> <out.png>
```

详见每个脚本头部的注释。

## 5. 两个必须记住的统计/校准约定

1. **插值校准**：两域 CLOCK_MONOTONIC 起点不同，跨域延迟用「按 >1 ms 跳变切段 + 段内 10 s 取 min」
   校准（8 h 曾出现一次 50.952 ms 时钟跳变，全局 min 会被它污染）。
   → **延迟分位数可比，绝对延迟不可比**。
2. **会话级基线**：inmate 的 min 在**不同 cell 会话**之间会差 0.3–0.5 µs（同一会话内恒定，
   五档 429 万样本 min 全为 4.796 µs）。→ **同会话内严格可比，跨会话只可比趋势与量级**。
