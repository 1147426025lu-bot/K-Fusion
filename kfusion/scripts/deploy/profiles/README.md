# scripts/deploy/profiles/ — 测量 profile（SWAPPABLE）

**角色：SWAPPABLE**

每个 `.env.sh` 是一组 **环境变量**，控制隔离级别、是否 Kbuild、resync 阈值、PRE_IDLE 等。换对照实验 = 换 profile，无需改长测脚本。

| 文件 | 典型场景 | 备注 |
|------|----------|------|
| `profile_soak_l2_best__安静浸泡.env.sh` | 正式浸泡、论文主结果 | L2、无 Kbuild、resync=3000 |
| `profile_soak_l2_honest__诚实浸泡.env.sh` | resync=0 对照 | 实验用 |
| `profile_stress_l2__背景加压.env.sh` | stress 长测 | L1 + hackbench |
| `profile_light__默认测量.env.sh` | 开发调试 | L3，非 ≥15min 论文默认 |

## 用法

```bash
# 被 env_setup 自动 source（先 light，再 PLC_PROFILE 覆盖）
source scripts/deploy/profiles/profile_soak_l2_best__安静浸泡.env.sh

# 长测入口
PLC_PROFILE=scripts/deploy/profiles/profile_soak_l2_best__安静浸泡.env.sh \
  DURATION_MIN=15 bash scripts/deploy/run_soak_cycletest__浸泡长测.sh
```

## 新建 profile

复制 `profile_soak_l2_best__*.env.sh`，改 `ISOLATION_LEVEL`、`JITTER_RESYNC_THRESH_NS`、`SOAK_SKIP_KBUILD` 等，勿改长测脚本。
