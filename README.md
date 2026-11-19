# K-Fusion

**面向 LET 任务的读写抖动抑制编译工具链**

论文主线：**逻辑执行时间（LET）模型下共享数据读写抖动的抑制**——用 LLVM 分析任务的共享数据访问，
把符合条件的读写钉扎到定时器硬中断、计算留在实时核上的用户态实时线程，
在统一口径下分报最大读/写抖动并校验数据版本正确性。
（工具链仍具备 IR 层内核化 / 融合能力，见下文；那是对产物形态的客观描述，不是论文卖点。）

> 论文全貌与实验设计（**写作以此为准**）：[docs/PAPER_WALKTHROUGH__论文全貌与实验设计.md](docs/PAPER_WALKTHROUGH__论文全貌与实验设计.md)
> 目录结构：[REPO_LAYOUT.md](REPO_LAYOUT.md)

## 克隆

```bash
git clone git@github.com:1147426025lu-bot/K-Fusion.git
cd K-Fusion
```

仓库地址：https://github.com/1147426025lu-bot/K-Fusion

**Pi 本机推荐路径**：`/home/pi/K-Fusion`（旧名 `~/plc_compiler` 为指向该目录的符号链接，可继续使用）。

## 核心（主线）

| 路径 | 内容 |
|------|------|
| [`kfusion/`](kfusion/) | **K-Fusion 工具链**：LLVM Pass、manifest、`.ko` 构建 |
| [`compare/kfusion/`](compare/) | 本地 jitter 基准（gitignore） |
| [`docs/architecture_unify__统一架构方案.md`](docs/architecture_unify__统一架构方案.md) | 统一流水线架构（AST/TEIR/STKP → Pass → 宿主 → `.ko`） |

```bash
cd kfusion
# 统一入口：plan → PLCFusionPass → stage_impl.S → 单工作线程宿主 → .ko
FORCE_CHOSEN=D@linux_kernel \
  bash scripts/kf_unify__统一流水线.sh examples/ecos/four_stage_chain__四阶段示例.c
# 旧对照宿主：HOST_MODEL=per_group
```

## 参考（旧线路 / 非主线）

| 路径 | 角色 |
|------|------|
| [`crtos/`](crtos/) | 旧线路历史材料：cRTOS / Jailhouse 跨域归因（**不是当前论文主线**） |
| [`timedc/`](timedc/) | 灵感来源：Timed C / KTC 三基线对比 |
| [`kfusion/docs/paper/`](kfusion/docs/paper/) | 旧叙事论文草稿（DRAFT_A–D 等），写作时勿照搬主线论断 |

## 环境

- Raspberry Pi 4 Model B Rev1.5 · Linux **5.15.49-rt47 PREEMPT_RT**（`realtime=1`，`HZ=1000`）
  - SSH `pi@192.168.1.4`（IP 已从 `.3` 变为 `.4`）；实时核绑核 + FIFO
  - 旧平台（Pi5 / 6.12、旧卡 5.15.86 非 RT）的数字**已作废**，不得与新口径混表
- clang/llvm · cmake/ninja · 内核 headers

## 许可证

见各子目录源文件头；Jailhouse / KTC 等上游遵循各自许可证。

## 开发与贡献

- [`docs/DEV__开发指南.md`](docs/DEV__开发指南.md) — 脚本约定、环境依赖、Pass 测试与 `compare/` 说明  
- [`kfusion/manifests/README.md`](kfusion/manifests/README.md) — manifest 最小字段与 5 分钟演示  
- [`REPO_LAYOUT.md`](REPO_LAYOUT.md) — 目录角色（FIXED / SWAPPABLE / LOCAL）
