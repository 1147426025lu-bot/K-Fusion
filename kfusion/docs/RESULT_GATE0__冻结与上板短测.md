# Gate 0 冻结 + 本机短测（经验结论）

机器可读：`kfusion/analysis/build/gate0/freeze.json`

**没有** checkout `feat/ecos-temporal-semantics`，**没有** merge `origin/main`（本工作区相对它 ahead 1 / behind 31，且有 Gate 1–6 未提交改动）。

| 引用 | SHA |
|---|---|
| 本机 HEAD | `884a5f2` Implement ECoS fusion planning and Pi4 fiveloop integration |
| origin/main | `6059d7c` PR10-D 跨域闭环上板实测 |
| origin/feat/ecos-temporal-semantics | `108e94b` Gate 2 数据版本依赖 + 独立复核 |

## `.ko`

- 生成模块 `kfusion/analysis/build/gate1_from_c/kmod/kfusion_g1.ko`：对 `/root/linux-5.15.86` 编出，vermagic `5.15.86 SMP preempt`，`insmod`/`rmmod` 成功。dmesg：`kfusion_cycle: cycles=7982 (not a WCET)`。这是 kthread + `usleep_range`，**不是** hrtimer 的 R 探针。
- 板上已有融合宿主 `/root/ecos_build/five_stage_loop/five_stage_loop.ko`：同样能加载；约 250 ms 内 `periods≈264`。

## 用户态 R（统一探针 r/s/f，200 周期）

plan/flat digest 均为 `5d4cda7d`。默认优先级下 plan 出现过 4/200 deadline miss（max R≈2.2 ms）。`chrt -f 80` 后 plan `response` p50≈13 µs、max≈58 µs、0 miss。flat 的 response p50≈879 µs（含相位空等）。**隔离条件会改数字，不能当 WCET。**
