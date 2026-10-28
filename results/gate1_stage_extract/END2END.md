# Gate 1 端到端验收证据（2026-10-27）

**一份 C 周期程序 → 一条命令 → 提取 → 自动构造计划(经验证器签署) → 生成用户态/内核态源码
→ 内核源码编成 .ko → insmod 运行**。全程**不手写 DAG、不手写两份 stage 实现**。
在目标板 Pi4 上完成。

## 输入（唯一人工产物：一份 C）

```c
int g_target; int g_output; int g_integral;
void stage_sample(void){ g_output += g_target; }
void stage_pid(void)   { g_integral += g_output; g_output = g_integral; }
void stage_clamp(void) { if (g_output > 100) g_output = 100; }
void plc_cycle(void)   { stage_sample(); stage_pid(); stage_clamp(); }
int main(void){ plc_cycle(); return 0; }
```

## 一条命令

```sh
bash kfusion/frontend/stage_extract/auto_kernelize.sh /tmp/ctl.c /root/gate1/out 1000 linux-kernel
```

内部 4 步：stage_extract(IR 提取) → ast_to_plan(建 dag/contract，调 fusion_plan 出 plan，
`verification.ok=True`，1 组) → emit.py(生成源码)。

## 输出与构建（Pi4，kernel 5.15.86 / clang-llvm 16.0.6）

生成内核源码 7 件（ecos_fused_body.{h,c}、ecos_fused_linux.c、compat/stdint.h、Kbuild、…）。
补 stage_impl.c（阶段函数，去 main）后：

```
$ make -C /root/linux-5.15.86 M=/root/gate1/out/gen modules
  LD [M]  plc_cycle.o
  LD [M]  plc_cycle.ko
```

（有一批 `undefined` 的 modpost 警告——因为该内核树缺 `Module.symvers`；`CONFIG_MODVERSIONS`
未开，符号在 insmod 时由运行内核解析。）

```
$ modinfo plc_cycle.ko
vermagic: 5.15.86 SMP preempt mod_unload aarch64     # 与运行内核一致
$ insmod ./plc_cycle.ko period_us=1000 ; echo rc=$?
rc=0
$ cat /sys/module/plc_cycle/parameters/periods     # 跑 ~2 秒后
2009
$ cat /sys/module/plc_cycle/parameters/missed
0
$ rmmod plc_cycle ; echo rc=$?
rc=0
dmesg: ecos: 1 组已启动，周期 1000 µs，FIFO 优先级 99（证据级别 empirical）
       ecos: 已停止
```

`periods=2009`（2 s / 1000 µs ≈ 2000 拍）、`missed=0`（严格周期无积压），说明周期体确实按
方案在跑。

## 同源双域

同一份 `/tmp/ctl.c` 再跑 `--target nuttx`：生成 7 件用户态 app 源码
（ecos_nuttx_main.c、Makefile、Kconfig、body…）。即**一份 C 自动产出内核态 + 用户态两个部署**。

## 诚实边界（Gate 1 首版覆盖范围）

1. **单域、简单链、无参阶段**：本例 3 阶段线性、无参数、无跨域。多域/跨域/带参/多速率未覆盖。
2. **执行时间占位**：阶段 exec 用 `--default-exec-us=10` 占位（日志已 warn），非实测；不得进论文。
3. **数据依赖边未恢复**：按调用序建 topo_order；真实版本依赖交 Gate 2（TEIR, IR use-def）。
4. **NuttX 只生成未构建**：NuttX 树构建未做（本步只证"同源生成"；构建是后续）。
5. **不支持构造的拒绝**：已实现 K-C0 拒绝（fail-closed）：含 `malloc/printf/free` 的阶段 →
   `k_c0.ok=false` + 逐条理由，`ast_to_plan` 拒绝且不产出 gen/（Pi4 实测）。细项（循环边界、
   指针别名递归）仍待 Gate 2/3。

## 结论

Gate 1 的**核心命题成立**：给定一份受支持的 C 周期程序，编译器自动分析、自动构造合法计划
（由独立验证器签署）、生成内核态源码并构建/加载/运行成功，**全程无人工 DAG、无人工双实现**。
剩余是把覆盖范围（多域/跨域/带参/多速率）、拒绝策略、NuttX 构建补齐。
