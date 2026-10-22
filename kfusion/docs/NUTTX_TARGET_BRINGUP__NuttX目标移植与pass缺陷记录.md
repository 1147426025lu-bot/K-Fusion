# NuttX 目标移植与 Pass 缺陷记录

> 记录时间：2026-10-19 ~ 10-20
> 平台：Raspberry Pi 4B，两块 SD 卡分工（见 §1）
> 目的：把 K-Fusion 内核化产物编成 NuttX 程序，与 cRTOS 在同一 cell、同一口径下对比

---

## 1. 双卡分工与跨卡构建桥

两张卡不能同时插，且能力互补：

| | NuttX 卡 | LLVM 卡 |
|---|---|---|
| 系统 | Debian 11 / 5.15.86（`demo`） | Debian 13 / 6.12.110-v8（`pi`） |
| NuttX 源码树 | ✅ `/root/kfusion-pi4/build/nuttx-build/{nuttx,apps}`（491 MB + 55 MB） | ❌ |
| aarch64-none-elf 工具链 | ✅ `/opt/arm/arm-gnu-toolchain-13.2…` | ❌ |
| LLVM / clang | ❌ | ✅ 19.1.7 |
| Jailhouse | ✅ | ❌ |

**桥的核心技巧：预处理成自包含 `.i`。**

```
NuttX 卡: aarch64-none-elf-gcc -E cyclictest.c -I nuttx/include -I apps/include
          -D__NuttX__ -D__KERNEL__=0 -mcpu=cortex-a72 -D_GNU_SOURCE
        → cyclictest_nuttx.i  135 KB，0 个 #include，-nostdinc 也能编
LLVM 卡: clang --target=aarch64-none-elf -x cpp-output … -fpass-plugin / opt -passes=…
        → fused.o  18~20 KB
NuttX 卡: gcc 编桩 + ld -r 合并 → 替换 apps/benchmarks/cyclictest/*.o → make
```

实测：`.i` 用 `-nostdinc` 编译通过，未定义符号仍是 27 个，与直接编 `.c` 完全一致。
**结论：跑 pass 的机器不需要 NuttX 源码树，只需这一个 135 KB 的文本文件。**

脚本：`scripts/nuttx_target__NuttX目标构建桥.sh`（`emit` / `fuse` / `stub` / `install` / `restore` / `verify`）。

---

## 2. 踩到的六个坑（按发现顺序）

### 坑 1：`-fpass-plugin` 完全无效

用 `clang -fpass-plugin=KFusionPass.so` 编三次，产物**字节完全相同**（20 664 B）。

原因：插件只注册了 `PB.registerPipelineParsingCallback(...)`，**没有注册 EP callback**
（`registerPipelineStartEPCallback` / `registerOptimizerLastEPCallback`）。
`-fpass-plugin` 只负责"加载并注册"，不会把 pass 加进管线。

**必须走 `opt`**：

```bash
opt -load-pass-plugin=KFusionPass.so -load-pass-plugin=PLCLowJitterPass.so \
    -passes="plc-fusion,plc-low-jitter" in.ll -S -o out.ll
```

> 这个坑的危险之处是**没有报错**：加载成功、编译成功、产物合法，只是什么都没做。

### 坑 2：GCC 预处理的 `.i` 喂给 clang 会报错

NuttX 头文件里有 14 处 GCC 扩展属性形式：

```c
char *strdup(const char *s) __attribute__((__malloc__(__builtin_free, 1)));
```

clang 19 直接拒绝：`'__malloc__' attribute takes no arguments`。

归一化：`sed -E 's/__malloc__\([^)]*\)/__malloc__/g'`。
根治办法是在 NuttX 卡上用 clang 做预处理（需装 clang，暂不划算）。

### 坑 3：DCE roots 必须显式给，否则入口被删

NuttX 版 cyclictest 的入口是 **`main`**（源码里 `grep cyclictest_main` 为 0 处），
测量线程是 **`testthread`**（不是原版 rt-tests 的 `timerthread`）。

第一次没设 roots，只设了 `PLC_FUSION_HOT_PATH_FUNCTIONS=timerthread,fifothread`（两个都不存在），
结果 DCE 把 `main` 和 `print_hist` 全删了，只剩 `testthread`，IR 从 72 775 B 掉到 25 083 B。

**看起来"效果惊人"，其实产物已经废了。**
正确配置：`PLC_FUSION_ROOTS=main` + `PLC_FUSION_HOT_PATH_FUNCTIONS=testthread`。

### 坑 4：入口符号名 —— 链接期才暴露

NuttX 的 `Application.mk` 对 `MAINSRC` 会传 `-Dmain=$(PROGNAME)_main`，
所以 `.o` 必须导出 `cyclictest_main`，否则 `builtin_list.c` 链接失败：

```
undefined reference to `cyclictest_main'
```

由于 `.i` 是直接 `gcc -E` 出来的（没带那个宏），入口仍叫 `main`。
处理：`objcopy --redefine-sym main=cyclictest_main`（等价变换，无语义影响）。

### 坑 5：★ NuttX 的访问器函数被"黑洞化" → 运行即崩

**现象**：fused 版能链接、能装进 NuttX，一执行就 data abort：

```
default_fatal_handler: level 0 translation fault
ESR_ELn: 0x96000004    FAR_ELn: 0xffffffffffffffff    ELR_ELn: 0x48c7b0
```

反汇编定位：ELR 落在 **`lib_skipspace`（被 `strtol` 调用）**，`ldrb w0,[x20]` 而 `x20 = -1`
——**`strtol` 收到的字符串指针是 -1**。

**根因**：NuttX 把 `optarg`／`stdout` 做成**访问器函数**而非全局变量：

```c
// nuttx/include/unistd.h:292-295
#define optarg   (*(getoptargp()))
#define opterr   (*(getopterrp()))
#define optind   (*(getoptindp()))
#define optopt   (*(getoptoptp()))
// nuttx/include/stdio.h:84-86
#define stdin    lib_get_stream(0)
#define stdout   lib_get_stream(1)
#define stderr   lib_get_stream(2)
```

而 pass 的保留名单 `isPreservedExternal()` 里只有 glibc 的**全局**形式
（`"optarg"`, `"optind"`, `"stdout"`, `"stderr"`），**没有函数形式**。
于是 `getoptargp` 被判"未映射"，交给 `blackholeCall()`：

```llvm
; 融合前
%20 = call ptr @getoptargp()
; 融合后
%20 = load ptr, ptr null      ← 从地址 0 读一个指针
```

**为什么现场那么难认**：NuttX 的异常向量表在 `0x0`，地址 0 **可读**，
所以第一次 load 不 fault，读到 `0xffffffffffffffff`，
**第二次解引用才崩** —— 崩溃点离真正原因隔了好几层。

全程序共 **17 处**这种调用点。

### 坑 6：★ 更隐蔽 —— `pthread_setschedparam` 被静默删除，测量口径被悄悄改掉

顺着坑 5 排查"还有哪些符号消失"，发现：

| 符号 | 融合前 | 融合后 | 后果 |
|---|---|---|---|
| `pthread_setschedparam` | 2 处 | **0 处，且无 `plc_` 替代** | **`-p 200` 的 RT 优先级根本没设上** |
| `ioctl` / `poll` | 有 | 消失，返回值变成 0 | 等于谎报"成功" |

`blackholeCall()` 对 **void 返回值**的处理是"直接删掉整条调用"：

```cpp
if (!CB->getType()->isVoidTy()) {
    if (CB->getType()->isPointerTy()) CB->replaceAllUsesWith(ConstantPointerNull::get(...));
    else if (CB->getType()->isIntegerTy()) CB->replaceAllUsesWith(ConstantInt::get(..., 0));
}
if (auto *Invoke = dyn_cast<InvokeInst>(CB)) { ... } else { CB->eraseFromParent(); }
```

**这个比崩溃严重得多**：如果没有崩、直接跑出了数据，那批数据是**默认优先级下的抖动**，
和论文的 `-p 200` 口径对不上，而且从结果里完全看不出来。

---

## 3. 修复

`backend/pass/PLCFusionPass__内核化Pass.cpp` 两处：

**① 保留名单补 NuttX 访问器与关键调用**

```cpp
"getoptargp", "getoptindp", "getopterrp", "getoptoptp", "lib_get_stream",
"pthread_setschedparam", "ioctl", "poll",
```

**② `logUnmapped()` 从"仅写文件"改为默认 stderr 警告**

原来只有设了 `PLC_FUSION_UNMAPPED_LOG` 才写文件，不设就完全静默。
黑洞化属于"静默的错误替换"，必须喊出来：

```
[PLCFusion] WARN: 未映射的外部符号 'xxx'，调用已被黑化（返回值置 0）
```

---

## 4. 修复前后的量化对比

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `load ptr, ptr null` | 17 处 | **0 处** |
| `getoptargp` 调用 | 被黑洞 | 18 处保留 |
| `getoptindp` | 被黑洞 | 2 处保留 |
| `lib_get_stream` | 被黑洞 | 3 处保留 |
| `pthread_setschedparam` | **被删除** | 2 处保留 |
| `ioctl` / `poll` | 被删除 | 10 / 2 处保留 |
| 未映射警告 | 静默 | 零输出（无其他黑化） |
| 浮点指令 | 3 | **0**（FixedPoint Pass 生效） |
| `plc_*` 调用点 | 0 | **84 处** |

### ⚠ 一个被撤回的指标

修复前 fused.o = **18 120 B**，base.o = 20 008 B，看起来"减少 9.4%"。
**这个数是假的** —— 那 9.4% 主要是"17 处 getopt 访问器 + 2 处 setschedparam 被删掉"省下来的。

修复后 fused.o = **20 240 B**，比 base.o **大 1.2%**。

> **教训：体积不能作为这类 pass 的主指标。** 一个会把调用删掉的 bug，能"优化"出很好看的大小。
> 主指标只能是抖动与正确性。

---

## 5. 正确的构建参数（可直接复用）

```bash
# ① 预处理（NuttX 卡）
aarch64-none-elf-gcc -E cylcictest.c -o cyc.i \
  -I $NUTTX_BUILD/nuttx/include -I $NUTTX_BUILD/apps/include \
  -D__NuttX__ -D__KERNEL__=0 -mcpu=cortex-a72 -fno-builtin -D_GNU_SOURCE
sed -E 's/__malloc__\([^)]*\)/__malloc__/g' -i cyc.i     # GCC 扩展属性归一化

# ② 内核化（LLVM 卡）
clang --target=aarch64-none-elf -mcpu=cortex-a72 -O2 -fno-builtin -ffreestanding \
      -fno-stack-protector -x cpp-output -S -emit-llvm cyc.i -o cyc.ll
opt -passes="function(mem2reg,instcombine,simplifycfg)" cyc.ll -S -o cyc.pre.ll
PLC_FUSION_ROOTS=main PLC_FUSION_HOT_PATH_FUNCTIONS=testthread \
opt -load-pass-plugin=KFusionPass.so -load-pass-plugin=PLCLowJitterPass.so \
    -passes="plc-fusion,plc-low-jitter" cyc.pre.ll -S -o cyc.kll
llc -O3 -relocation-model=static -march=aarch64 -mattr="-fp-armv8,-neon" \
    -filetype=obj cyc.kll -o fused.o

# ③ 桩与合并（NuttX 卡）
aarch64-none-elf-gcc -c src/plc_runtime_stubs__NuttX桩.c -o stubs.o \
  -I $NUTTX_BUILD/nuttx/include -I $NUTTX_BUILD/apps/include \
  -D__NuttX__ -mcpu=cortex-a72 -Os -fno-builtin -ffreestanding
aarch64-none-elf-objcopy --redefine-sym main=cyclictest_main fused.o fused.renamed.o
aarch64-none-elf-ld -r fused.renamed.o stubs.o -o cyclictest_combined.o
```

---

## 6. 校验规则（避免同类问题再被漏掉）

1. **符号判据**：合并后 `.o` 的每个未定义符号，必须在 `nuttx` ELF 的符号表里真实存在
   （动态查询，不用硬编码清单；静态 27 符号白名单仅作兜底）。
   注意 `vfprintf`/`__errno` 是 NuttX 有、原程序没用的合法符号，硬编码会误判。
2. **pass 生效判据**：IR 必须变化 **且** 出现 `plc_*` 调用。
   **不能**用"函数数变少"作判据 —— `roots=main` 时可达函数全部保留，3 → 3 才是对的。
3. **禁止静默黑化**：任何未映射符号都应打印警告（已改为默认行为）。
4. **构建不可复现**：`nuttx.bin` 内含编译时间戳（串口 dump 里可见 `Oct 19 2026 14:14:42`），
   **hash 每次重建都不同**。比对只能用大小与内容逻辑，不能用 hash。

---

## 7. 对论文的意义

这两条 bug 不是"移植不顺"，而是**编译器内核化这类方案特有的失效模式**：

- 它把"函数调用"当成可优化的外部依赖，一旦判定错误，**不会报错，只会静默替换**；
- 静默替换在错误的位置表现为崩溃（坑 5），在正确的位置表现为**看似正常但口径已变的数据**（坑 6）；
- 后者尤其值得写进方法学：**编译器优化必须配备符号级审计**，
  即"融合产物引用的每一个外部符号，都必须能追溯到目标平台的真实实现"。

这正好与本文已有的"测量自扰消融""偏置反证"形成一组方法学：
**前者证明测量没有自我干扰，后者证明产物没有自我欺骗。**
