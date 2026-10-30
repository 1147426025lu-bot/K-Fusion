#!/usr/bin/env bash
# bringup_check__换卡自检.sh — 换 SD 卡后第一件事：判断旧画像还能不能用。
#
# 为什么必须先判这个：PR-12 的环境门禁规定「没有平台身份的画像不得用于决策」。
# 换卡常常顺带换了内核（补丁号/抢占模式/配置），一旦不同，之前那批 Q99 与
# 超期数就属于另一台机器——继续用就是无标识混表，比数据少更糟。
#
# 用法（新卡上，仓库根目录）：
#   bash kfusion/scripts/bringup_check__换卡自检.sh
# 退出码：0=环境一致（可直接复用旧画像）  5=环境变了（必须重新取证+重测）  2=缺依赖
set -uo pipefail

KF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$KF/.." && pwd)"
REC="$KF/docs/pr11_deploy_objective/env_evidence.json"
PY="${PYTHON:-python3}"
fail=0
note() { printf '  %-22s %s\n' "$1" "$2"; }
bad() { printf '  %-22s %s\n' "$1" "$2"; fail=1; }

echo "── 1) 板与内核 ──"
REL="$(uname -r)"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo '?')"
note "uname -r" "$REL"
note "model" "$MODEL"
CFG="/boot/config-$REL"
if [ -f "$CFG" ]; then
  PRE=""
  grep -q '^CONFIG_PREEMPT_RT=y' "$CFG" && PRE="PREEMPT_RT"
  [ -z "$PRE" ] && grep -q '^CONFIG_PREEMPT=y' "$CFG" && PRE="PREEMPT"
  [ -z "$PRE" ] && grep -q '^CONFIG_PREEMPT_VOLUNTARY=y' "$CFG" && PRE="PREEMPT_VOLUNTARY"
  [ -z "$PRE" ] && grep -q '^CONFIG_PREEMPT_NONE=y' "$CFG" && PRE="NONE"
  HZ="$(grep -m1 '^CONFIG_HZ=' "$CFG" | cut -d= -f2)"
  FT="$(grep -m1 '^CONFIG_FTRACE=' "$CFG" | cut -d= -f2)"
  note "preempt / HZ / ftrace" "${PRE:-未知} / ${HZ:-?} / ${FT:-未设置}"
  note "cmdline isolcpus" "$(grep -c 'isolcpus=' /proc/cmdline) 处"
else
  bad "kernel config" "缺 $CFG —— 无法判定抢占类别，画像不得使用"
fi

echo "── 2) 工具链与构建依赖 ──"
for t in clang-16 opt-16 gcc make ld; do
  if command -v "$t" >/dev/null; then note "$t" "$(command -v "$t")"; else bad "$t" "缺失"; fi
done
SO="$KF/analysis/build/PLCEffectSummary.so"
if [ -f "$SO" ]; then note "PLCEffectSummary.so" "就绪"; else bad "PLCEffectSummary.so" "缺失（先按仓库说明编译）"; fi

echo "── 3) 内核头/构建目录（kmod_hrtimer 与 UKU 都要）──"
KDIR="${KERNELDIR:-/root/linux-5.15.86}"
HDR="/lib/modules/$REL/build"
if [ -d "$HDR" ]; then
  note "headers" "$HDR ($(make -s -C "$HDR" kernelrelease 2>/dev/null || echo 版本未取到))"
else
  note "headers" "缺 $HDR（apt install linux-headers-$REL）"
fi
if [ -d "$KDIR" ]; then
  KV="$(make -s -C "$KDIR" kernelrelease 2>/dev/null || echo '?')"
  note "KERNELDIR 默认" "$KDIR (kernelrelease=$KV)"
  [ "$KV" != "$REL" ] && bad "KERNELDIR 版本不匹配" "$KV ≠ $REL → ko 会因 vermagic 加载失败"
else
  note "KERNELDIR 默认" "$KDIR 不存在（需 export KERNELDIR=... 或 SKIP_KMOD=1）"
fi

echo "── 4) 仓库与被测源 ──"
REV="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?')"
DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | grep -vc '^?? kfusion/docs/' || true)"
note "git rev" "$REV（源码脏文件 $DIRTY 个）"
SRC="$KF/examples/ecos/mixed_pid_stateful__积分混合.c"
[ -f "$SRC" ] && note "被测源 sha256" "$(sha256sum "$SRC" | cut -c1-16)" || bad "被测源" "缺失"

echo "── 5) 与上次画像的平台身份证比对 ──"
if [ -f "$REC" ]; then
  "$PY" - "$REC" "$REL" "$MODEL" "$PY" <<'PY'
import io, json, sys
rec = json.load(io.open(sys.argv[1], encoding="utf-8"))
rel, model = sys.argv[2], sys.argv[3]
k = rec.get("kernel") or {}
h = rec.get("host") or {}
same = (k.get("release") == rel and h.get("model") == model)
print("  上次记录: %s / %s / %s" % (k.get("release"), k.get("preempt_class"),
                                   h.get("model")))
print("  本次实机: %s / %s" % (rel, model))
if same:
    print("  ⇒ 环境一致：旧画像可复用（仍需重跑一次冒烟确认）")
    sys.exit(0)
print("  ⇒ 环境已变：旧画像全部作废，必须重新取证 + 重测（脚本见文档 §8）")
sys.exit(5)
PY
  rc=$?
  [ "$rc" = "5" ] && fail=1
else
  bad "旧画像" "无 $REC（换卡后是否克隆完整？）"
fi

echo
if [ "$fail" = "1" ]; then
  echo "结论：**不能**直接沿用旧数据。先按上面逐项补齐，再跑文档 §8 的 ①–⑤。"
  echo "尤其：内核不同 ⇒ 旧 Q99/超期数属于另一台机器，混表会被审稿直接打掉。"
  exit 5
fi
echo "结论：环境一致、依赖齐全 → 可以直接接着做内核宿主那条路径。"
exit 0
