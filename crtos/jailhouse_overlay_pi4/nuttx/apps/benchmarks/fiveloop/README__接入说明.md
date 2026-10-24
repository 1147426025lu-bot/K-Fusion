# fiveloop — 闭环五阶段负载的 NuttX builtin 骨架

`fiveloop_main.c` **不入库**：它由 `fusion_codegen__生成融合代码.py --entry fiveloop_main`
生成（源 = `kfusion/examples/ecos/five_stage_loop__闭环五阶段示例.c` + 板上的 plan.json）。
`plc_probe__统一测量探针.h` 从 `../bm2pid/` 复制（两处必须同版本，CI 无强制——换探针时记得同步）。

## 接入（Pi4，/root/nuttx-build）

1. `mkdir -p apps/benchmarks/fiveloop`，放入本目录三件套 + 生成的 `fiveloop_main.c` + 探针头；
2. `apps/benchmarks/Kconfig` 追加 `source "$APPDIR/benchmarks/fiveloop/Kconfig"`
   （Make.defs 用 wildcard 自动收，无需改）；
3. `nuttx/.config` 追加 `CONFIG_BENCHMARK_FIVELOOP=y` → `make olddefconfig && make -j4`；
4. `bash /root/kfusion-pi4/scripts/deploy_nuttx_cell__部署NuttX_cell.sh`；
5. 串口（GPIO14/15，115200 8N1）：`fiveloop plan 60000` / `fiveloop flat 60000`。

## ⚠ 摘要等价检查的纪律

NuttX builtin **在同一次开机内继承全局状态**（g_seed/g_plant/g_integ/g_hist）。
实测：同一 boot 里第二跑（无论模式）摘要恒为 `47649d0c`，第一跑才是 `b6eb20b8`。
所以 digest 对比**必须每种模式重建 cell**（destroy + 重部署 = 重载镜像 = 全新 .data），
或固定只看第一跑。Linux 侧无此问题（每次运行是新进程）。
