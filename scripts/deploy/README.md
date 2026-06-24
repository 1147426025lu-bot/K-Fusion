# scripts/deploy/ — cyclictest 测量与部署

## 固定脚本（FIXED）

| 脚本 | 用途 |
|------|------|
| `ignite_official_cycletest__cyclictest主线.sh` | cyclictest 专用宿主 `.ko` 构建 + insmod |
| `run_soak_cycletest__浸泡长测.sh` | 安静浸泡（≥15min） |
| `run_stress_cycletest__加压长测.sh` | hackbench 背景加压 |
| `run_soak_cycletest__八小时浸泡.sh` | 480min 包装 |
| `run_soak_tune_matrix__尖峰浸泡矩阵.sh` | 15 / 60 / 480 min 矩阵 |
| `run_stress_tune_matrix__加压矩阵.sh` | 加压矩阵 |
| `run_cold_soak__冷启动浸泡.sh` | 冷启动后单档浸泡 |
| `env_setup__测量环境.sh` | 隔离 / PRE_IDLE / profile 加载 |
| `safe_rmmod_official__cyclictest卸载.sh` | cyclictest 模块卸载 |

## 可替换 profile（SWAPPABLE）→ [`profiles/`](profiles/README.md)

| Profile | 用途 |
|---------|------|
| `profiles/profile_soak_l2_best__安静浸泡.env.sh` | **推荐** L2 安静浸泡（opt5 默认） |
| `profiles/profile_soak_l2_honest__诚实浸泡.env.sh` | resync=0 对照实验 |
| `profiles/profile_stress_l2__背景加压.env.sh` | stress 长测 |
| `profiles/profile_light__默认测量.env.sh` | L3 开发快测 |

`scripts/deploy/profile_*.env.sh` 为指向 `profiles/` 的兼容符号链接。

**换 profile：**

```bash
PLC_PROFILE=scripts/deploy/profiles/profile_stress_l2__背景加压.env.sh \
  DURATION_MIN=15 bash run_stress_cycletest__加压长测.sh
```

## 常用流程

```bash
sudo -v
bash ignite_official_cycletest__cyclictest主线.sh          # 构建 + 加载
DURATION_MIN=15 bash run_soak_cycletest__浸泡长测.sh       # 15min 浸泡
bash safe_rmmod_official__cyclictest卸载.sh
```

论文对照见 `scripts/paper/`。结果写入 `results/soak/`、`results/stress/`。
