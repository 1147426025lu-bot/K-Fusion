# Gate 1 — 阶段执行时间实测机制（消除 exec 占位边界）

## 动机（诚实边界）
此前 `ast_to_plan` 在用户未给 `--exec-us` 时，按 `--default-exec-us`（默认 10 µs）
**占位**执行时间并写入 `dag.meta.auto.placeholder_exec`，明确标记「需实测替换，
不得进论文」。本机制让「占位」可被目标板上的**真实测量**消除，形成闭环。

## 做法
在共用周期体 `ecos_fused_body.c` 里，用**弱符号钩子** `ecos_stage_enter/exit(idx)`
包裹每个阶段的调用（三后端共用的 body 逐字节相同，不受影响）；Linux 宿主提供
**强定义**覆盖弱符号，用 `ktime_get()` 量出每阶段的真实执行时间（max/avg + 采样数），
通过 `exec_probe` 模块参数暴露（读即打印，无需卸载）。

- `kfusion/backend/synthesis/body_emit.py`：stage 调用外包 `ecos_stage_enter/exit`
- `kfusion/backend/synthesis/linux_kernel_emitter.py`：`_STAGE_PROFILE` 强定义
  （`ktime_get()` 计时、`div_s64` 算 avg、`module_param_cb(exec_probe,...)` 暴露）
- `kfusion/frontend/stage_extract/measure_exec.py`：`exec_probe` 文本 → `--exec-us`
  JSON（**默认取 max=WCET**，而非 avg；实时调度需要最坏情况边界；支持 `--scale`
  安全裕度、`--avg`）

## Pi4 实测（闭环演示，2026-10-28）
控制程序 `ctl.c`（3 阶段：sample/pid/clamp），周期 1000 µs，运行 3 秒采样 9027 次：

**第 1 次加载（默认占位生成）的 exec_probe：**
```
count=9027 samples
  [0] stage_sample: max=334ns avg=148ns n=3009
  [1] stage_pid:    max=185ns avg=150ns n=3009
  [2] stage_clamp:  max=203ns avg=148ns n=3009
```
（注：两次加载 max 略有波动——sample 241↔334、pid 167↔185，属正常测量抖动；
这正说明为何实时边界要取 WCET 并叠加裕度，而非用 avg。）

**第 2 步：measure_exec 出 exec_us.json（取 max/WCET）：**
```json
{"stage_sample": 0.334, "stage_pid": 0.185, "stage_clamp": 0.203}
```

**第 3 步：用实测值重生** → `dag.meta.auto.placeholder_exec = []`（不再是占位），
`phase_us` 直接用实测值累加：`sample=0.0, pid=0.334, clamp=0.519`。

**验证重生产物**：loop2（用实测值）重新 `make` 出 `.ko` → `insmod` → 运行 2 秒
`periods=2009/missed=0` → `rmmod rc=0`。闭合「测一次 → 回填 → 重生」无回归。

## 闭环命令（一条龙）
```sh
bash auto_kernelize.sh ctl.c loop1 1000 linux-kernel          # 占位生成
# insmod loop1/gen/plc_cycle.ko; cat /sys/module/plc_cycle/parameters/exec_probe; rmmod
cat /sys/module/plc_cycle/parameters/exec_probe | python3 measure_exec.py > exec_us.json
bash auto_kernelize.sh ctl.c loop2 1000 linux-kernel exec_us.json   # 用实测值重生
```

## 诚实声明（用于论文前）
- 实测取的是 **max（WCET 估计）**，但单工况短采样不足以证明全局最坏情况；
  正式使用前应在多种输入/负载/温度下做**充分长**的采样，并按 `measure_exec --scale`
  叠加安全裕度（如 1.5×）。
- 本机制只做「实测 → 回填」格式转换，不编造任何数据；占位缺省仍保留，未实测时
  明确标记、不混入结论。
- 跨域/多速率场景下，跨域通信代价另计（见 Gate 2/3 与 xdom 相关文档），此处只量
  单域计算体。
