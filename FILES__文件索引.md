# PLCFusion 文件索引

命名规则：`原名__功能说明.扩展名`（双下划线后为中文说明）

**仓库结构（FIXED / SWAPPABLE / GENERATED）** 见 [REPO_LAYOUT.md](REPO_LAYOUT.md)。

例外（工具链/上游/生成物，保持原名）：
- `CMakeLists.txt`、`README.md` / `FILES.md`（→ 符号链接指向 `*__*.md`）
- `test/rt-tests/` 上游源码、`test/plc_runner_official.c` 等 Kbuild 固定名
- `build/`、`test/${FUSE_NAME}_*` 融合生成物

## 入口（最常用）

| 文件 | 说明 |
|------|------|
| `scripts/plc_fuse__内核化主流程.sh` | C 源码 → 内核 `.o` 融合主流程 |
| `scripts/ignite_fused__通用ko构建.sh` | 通用 fused 模块 `.ko` 构建 |
| `scripts/deploy/ignite_official_cycletest__cyclictest主线.sh` | cyclictest 主线 `.ko` 构建与 insmod |
| `manifests/manifest_cyclictest__主线压测.env` | cyclictest 融合清单（WCET hotpath） |
| `manifests/manifest_cyclictest__多TU压测.env` | cyclictest + histogram 多 TU 演示 |
| `plc-cc__低抖动编译器` | plc-cc 低抖动管线（`plc-cc` 为符号链接） |

## scripts/ — 融合管线与 CI

| 目录/文件 | 角色 | 说明 |
|-----------|------|------|
| **`scripts/fuse/`** | FIXED | 融合主流程与子脚本（根目录 `plc_fuse__*` 为符号链接） |
| `scripts/fuse/plc_fuse_plc_cc__plc-cc融合.sh` | FIXED | plc-cc 融合包装（内含 AST） |
| `scripts/fuse/plc_fuse_plc_cc_ast__AST探测.sh` | FIXED | plc_ast → JSON / `.plc_ast.env` |
| `scripts/fuse/plc_fusion_ast_plan__AST方案.py` | FIXED | AST JSON → fusion_plan + profile 建议 |
| `scripts/fuse/plc_fusion_ast_plan__AST方案读取.sh` | FIXED | 加载 fusion_plan，export `PLC_FUSION_AST_*` |
| `scripts/fuse/plc_fusion_ast_preflight__AST融合预检.sh` | FIXED | AST 融合可行性门禁（全 manifest） |
| `scripts/plc_ast_suggest_manifest__manifest建议.sh` | FIXED | 从 .c 生成 manifest 建议片段 |
| `scripts/plc_ast_apply_manifest__应用manifest建议.sh` | FIXED | 将 AST 建议合并进 manifest（`--dry-run` / `--force`） |
| `scripts/fuse/plc_fusion_pipeline_policy__Pass策略解析.sh` | FIXED | ast-auto / wcet-benchmark / fixed 策略 |
| `scripts/run_ci_pipeline_policy__CI_Pass策略矩阵.sh` | FIXED | 13 manifest Pass 策略汇总 |
| `scripts/run_manifest_onboarding_check__manifest清单门禁.sh` | FIXED | 新 manifest 必填项 + policy 检查 |
| `scripts/fuse/plc_fusion_wcet_tail_pick__短测选tail.sh` | FIXED | wcet-benchmark 静态 3 组 tail 快选 |
| `scripts/ignite_fused__通用ko构建.sh` | FIXED | 通用 `.ko` 构建 |
| `scripts/run_ci__CI门禁.sh` 等 | FIXED | CI / 冒烟 / 批量验证 |

## scripts/fuse/ — 融合管线（详见 `scripts/fuse/README.md`）

| 文件 | 说明 |
|------|------|
| `plc_fusion_common__公共库.sh` | 脚本公共库（报错、工具检测、退出码） |
| `plc_fusion_analyze_ir__IR特征分析.sh` | 扫描 IR 浮点/外部符号/体量 |
| `plc_fusion_pipeline__Pass组合选择.sh` | 自动选择 kernelize profile |
| `plc_fuse_detect__入口探测.sh` | 自动推断 kthread 入口与 DCE roots |
| `plc_fuse_merge_stubs__桩合并.sh` | 缺失符号 → per-app runtime 桩 |
| `plc_fuse_stub_loop__桩闭环.sh` | 桩合并 ↔ 覆盖率闭环（多轮） |
| `plc_fuse_validate__安全验证器JSON.sh` | 融合产物 JSON 验证（CI 用） |
| `plc_fusion_wcet_sweep__tail对照.sh` | 6 组 tail WCET 对照实验 |
| `plc_fusion_wcet_probe__短测探针.sh` | 单份 kernel.o 短 insmod WCET 采样 |
| `plc_fusion_wcet_autotune__WCET自动调优.sh` | sweep + 短测自动选优 pipeline |
| `plc_fuse_report__覆盖率报告.sh` | 融合覆盖率统计 |
| `plc_fuse_fusion_report__一页报告.sh` | 一页融合摘要（pipeline + 体量 + 覆盖率） |
| `demo_compare__用户态vs融合.sh` | 用户态 cyclictest vs fused 短测对比 |
| `plc_fuse_check__覆盖率门禁.sh` | CI 门禁（缺桩数阈值） |
| `plc_fuse_gen_stubs__桩合并兼容入口.sh` | 转发至桩合并（旧名兼容） |
| `verify_fused_apps__批量验证.sh` | 批量 insmod/rmmod 验证 |
| `safe_rmmod_fused__安全卸载.sh` | 通用 fused 模块安全卸载 |
| `run_ci__CI门禁.sh` | 无 insmod：编译 + 4 manifest 融合 + 覆盖率 + JSON + WCET sweep |
| `run_smoke_tests__冒烟测试.sh` | CI + 可选 cyclictest insmod / 全量验证 |

## scripts/deploy/ — cyclictest 浸泡 / 加压测

| 文件 | 角色 | 说明 |
|------|------|------|
| `env_setup__测量环境.sh` | FIXED | 测量前主机隔离 / PRE_IDLE |
| **`profiles/`** | SWAPPABLE | 测量 profile（见 `profiles/README.md`） |
| `profile_soak_l2_best__安静浸泡.env.sh` | SWAPPABLE | → `profiles/` 符号链接 |
| `profile_soak_l2_honest__诚实浸泡.env.sh` | SWAPPABLE | 如实统计 + 长测 refresh |
| `profile_stress_l2__背景加压.env.sh` | SWAPPABLE | L1 + hackbench 加压 profile |
| `run_soak_cycletest__浸泡长测.sh` | FIXED | 浸泡长测 + 可选绘图 |
| `run_stress_cycletest__加压长测.sh` | 加压长测（调用 soak 核心 + 背景负载） |
| `run_soak_cycletest__八小时浸泡.sh` | 8h 浸泡包装 |
| `run_soak_tune_matrix__尖峰浸泡矩阵.sh` | 浸泡矩阵 15min / 60min |
| `run_stress_tune_matrix__加压矩阵.sh` | 加压矩阵 15min |
| `safe_rmmod_official__cyclictest卸载.sh` | cyclictest 模块安全卸载 |
| `README.md` | deploy 目录说明 |

## scripts/maintenance/

| 文件 | 说明 |
|------|------|
| `cleanup_results__清理结果.sh` | 删除 <15min 短测与 greedy 探索日志 |

## scripts/tune/ — RT 主机调优

| 文件 | 说明 |
|------|------|
| `rt_background_load__背景加压.sh` | hackbench 背景负载 start/stop |
| `rt_host_isolate__CPU隔离.sh` | CPU 隔离 setup/teardown |
| `rt_host_unisolate__解除隔离.sh` | 恢复隔离 |
| `rt_host_tune__主机调优.sh` | cpufreq / timer / IRQ 调优 |
| `rt_cgroup_plc__cgroup隔离.sh` | PLC cgroup |
| `rt_host_cset_exec__手动cset.sh` | 手动 cset 实验 |
| `rt_cmdline_suggest__cmdline建议.sh` | 内核 cmdline 检查建议 |
| `rt_cmdline_apply__cmdline应用.sh` | cmdline 参数应用 |
| `rt_kernel_build_nohz__内核nohz构建.sh` | 带 nohz 的内核构建辅助 |
| `kernel-nohz__nohz配置.fragment` | nohz 内核配置片段 |

## backend/ — LLVM Pass

| 文件 | 说明 |
|------|------|
| `backend/pass/PLCFusionPass__内核化Pass.cpp` | POSIX→plc_* 内核化 Pass（主用） |
| `backend/pass/PLCLowJitterPass__低抖动Pass.cpp` | plc-cc 低抖动 Pass |

## src/ — 运行时与宿主（FIXED，权威源）

| 文件 | 说明 |
|------|------|
| `plc_runtime_stubs__POSIX桩.c` | 通用 POSIX/rt-tests 桩 |
| `plc_fused_host__通用宿主.c` | 通用 fused kthread/main 宿主 |
| `plc_fused_timer_host__hrtimer宿主.c` | hrtimer 睡眠/定时宿主 |
| `plc_pthread_host__pthread宿主.c` | pthread→kthread 宿主 |
| `plc_runner_official__cyclictest宿主.c` | cyclictest 主线专用宿主 |
| `plot_frequency_polygon__抖动绘图.py` | 浸泡/加压结果绘图 |

## scripts/paper/ — 论文实验

| 文件 | 说明 |
|------|------|
| `run_paper_all__论文全流程.sh` | 分步全流程 |
| `run_paper_baseline_matrix__论文基线矩阵.sh` | 三基线矩阵 |
| `run_paper_ablation_matrix__论文消融矩阵.sh` | 消融 |
| `paper_summarize_results__论文结果汇总.py` | CSV 汇总 |

| `docs/paper/PAPER_PLAN__论文计划.md` | 论文计划 |

## include/

| 文件 | 说明 |
|------|------|
| `plc_abi__运行时ABI.h` | plc_* 内核运行时 ABI |
| `plc_shm__共享内存.h` | /dev/plcfusion 共享内存布局 |

## frontend/ — AST 工具

| 文件 | 说明 |
|------|------|
| `frontend/ast/ast_tool__AST工具.cpp` | **plc-cc 静态分析器**（`plc_ast`）：入口探测、周期函数检查、JSON；`--emit-kernel-c` 为 legacy |
| `frontend/README.md` | AST 工具用法与职责边界 |

## diagnostics/

| 文件 | 说明 |
|------|------|
| `diagnostics/rmmod_hang_diagnose__卸载挂死诊断.sh` | 模块 refcnt 挂死诊断 |

## docs/ — 文档与压测记录

| 文件 | 说明 |
|------|------|
| `README__项目说明.md` | 项目主文档（`README.md` 为符号链接） |
| `FILES__文件索引.md` | 本文件（`FILES.md` 为符号链接） |
| `docs/benchmarks/isolation_ab_results__隔离AB结果.json` | 隔离 A/B 压测结果 |
| `docs/benchmarks/optimization_continue__优化记录.json` | 优化迭代记录 |
| `docs/examples/jitter_distribution_15min_pass__15min抖动分布.png` | 15min 抖动分布示例图 |
| `docs/examples/jitter_distribution_v23.2_15min__v23.2抖动分布.png` | v23.2 抖动分布 |
| `docs/examples/jitter_latency_timeline_v23.2_15min__v23.2延迟时间线.png` | v23.2 延迟时间线 |

## 根目录杂项

| 文件 | 说明 |
|------|------|
| `gpio_map_info__GPIO映射.txt` | GPIO 引脚映射备忘 |
| `kernel_version__内核版本.txt` | 目标内核版本记录 |
| `CMakeLists.txt` | CMake 构建（工具链约定名，不更名） |

## manifests/（SWAPPABLE — 每应用融合清单）

| 文件 | 说明 |
|------|------|
| `manifest_template__清单模板.env` | 新应用清单模板 |
| `manifest_cyclictest__主线压测.env` | rt-tests cyclictest |
| `manifest_signaltest__信号测试.env` | rt-tests signaltest |
| `manifest_ptsematest__互斥锁测试.env` | rt-tests ptsematest |
| `manifest_github_rt_periodic__周期demo.env` | 本地 1ms 周期 demo |
| `manifest_github_stb_sprintf__sprintf_demo.env` | stb_sprintf demo |

## test/ — Kbuild 工作区（混合：FIXED 骨架 + SWAPPABLE 样例 + GENERATED 产物）

| 路径 | 角色 | 说明 |
|------|------|------|
| `test/rt-tests/` | UPSTREAM | git clone 的 rt-tests 上游（cyclictest 等，**不更名**） |
| `test/github_demo__本地demo/` | SWAPPABLE | 本地 demo（`rt_periodic__周期demo.c` 等） |
| `test/*_kernel.o` 等 | GENERATED | `plc_fuse` 生成物，可删后重建 |
| `test/plc_runner_official.c` 等 | GENERATED 副本 | Kbuild 固定文件名（由 `src/*__*.c` 复制，**不更名**） |
| `test/Makefile` | FIXED | 内核模块临时 Makefile（**不更名**） |

## examples/plc-cc__低抖动示例/ — plc-cc 示例（SWAPPABLE，非主流程）

| 文件 | 说明 |
|------|------|
| `hello_plc__入门示例.c` | plc-cc 入门 |
| `pure_logic__纯逻辑.c` | 纯逻辑示例 |
| `gpio_blink__GPIO闪烁.c` | GPIO 闪烁 |
| `dither_test__抖动测试.c` | 抖动测试 |
| `isolation_test__隔离测试.c` | 隔离测试 |
| `temp_control__温控.c` | 温控示例 |

## 已删除（勿再引用）

- `scripts/deploy/deploy_fusion_v*.sh` — v1–v7 旧融合管线
- `scripts/deploy/run_cycletest__*`、`profile_best_fused_l2__*`、`profile_spike_reduce__*` — 已统一为 soak/stress 命名
- `scripts/deploy/run_userspace_cycletest__*` — 由 `scripts/paper/run_paper_userspace__*` 替代
- `scripts/test/V*.sh` — 早期 cyclictest 实验脚本
- `src/ai_fusion_test.c`, `bridge.c`, `plc_runner.c` — 旧 ai_fusion 管线
- `results/raw/` 下 <15min 短测数据 — 用 `cleanup_results__清理结果.sh` 清理

## 待手动清理

- `/home/pi/plc_compiler/rt-tests/`（根目录重复 clone，应只保留 `test/rt-tests/`）
