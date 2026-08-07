#!/bin/bash
# ============================================================================
# ignite_fused__通用ko构建.sh — 通用 fused 内核模块 (.ko) 构建
# ============================================================================
# 功能: manifest → plc_fuse（可选）→ 编译宿主/桩 → 链接 ${FUSE_NAME}_kernel.o → .ko
# 输入: manifest.env
# 宿主: FUSE_HOST=generic|hrtimer|pthread（plc_fused_host / timer / pthread）
# 环境: FORCE_REBUILD_KERNEL_O, FUSE_AUTO_DETECT, FUSE_LINK_RUNTIME_STUBS
# 用法: bash scripts/ignite_fused__通用ko构建.sh manifests/manifest_signaltest__信号测试.env
# 注: cyclictest L2 测量 → scripts/deploy/ignite_official_cycletest__cyclictest主线.sh
#     cyclictest CI/功能 → FUSE_RUNNER_PROFILE=generic bash $0 manifests/manifest_cyclictest__主线压测.env
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
MANIFEST="${1:-${PLC_FUSE_MANIFEST:-}}"

plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
MANIFEST="$PLC_MANIFEST"
plc_require_file "$MANIFEST" "manifest"
plc_source_manifest "$MANIFEST"

KERNEL_O="${FUSE_NAME}_kernel.o"
FUSE_RUN_MAIN="${FUSE_RUN_MAIN:-0}"
FUSE_KTHREAD_ENTRY="${FUSE_KTHREAD_ENTRY:-}"
FUSE_LINK_RUNTIME_STUBS="${FUSE_LINK_RUNTIME_STUBS:-1}"
FUSE_LINK_PTHREAD_HOST="${FUSE_LINK_PTHREAD_HOST:-1}"
FUSE_HOST="${FUSE_HOST:-generic}"
FUSE_AUTO_DETECT="${FUSE_AUTO_DETECT:-1}"
FORCE_REBUILD_KERNEL_O="${FORCE_REBUILD_KERNEL_O:-1}"

HOST_SRC="$PROJECT_ROOT/src/plc_fused_host__通用宿主.c"
MOD_NAME="${FUSE_NAME}_mod"
HOST_OBJS="plc_fused_host.o"

plc_require_file "$HOST_SRC" "宿主源码"
plc_check_kernel_build >/dev/null
plc_ensure_dir "$PROJECT_ROOT/test"

cd "$PROJECT_ROOT/test"
echo "=== ignite_fused: ${FUSE_NAME} ==="

if [ "$FORCE_REBUILD_KERNEL_O" = "1" ] || [ ! -f "$KERNEL_O" ]; then
    echo "🔮 重建融合对象..."
    bash "$SCRIPT_DIR/plc_fuse__内核化主流程.sh" "$MANIFEST"
elif [ -f "${KERNEL_O}_shipped" ]; then
    echo "🧽 复用 ${KERNEL_O}_shipped（FORCE_REBUILD_KERNEL_O=0）"
    cp -f "${KERNEL_O}_shipped" "$KERNEL_O"
else
    plc_warn "无 ${KERNEL_O} 且无 _shipped，强制重新融合"
    bash "$SCRIPT_DIR/plc_fuse__内核化主流程.sh" "$MANIFEST"
fi

plc_require_file "$KERNEL_O" "融合 .o" \
    "plc_fuse__内核化主流程.sh 可能失败，检查上方输出"

DETECTED="$PROJECT_ROOT/test/${FUSE_NAME}.detected.env"
if [ "$FUSE_AUTO_DETECT" = "1" ] && [ -f "$DETECTED" ]; then
    # shellcheck disable=SC1090
    source "$DETECTED"
    if [ "$FUSE_RUN_MAIN" != "1" ] && [ -z "$FUSE_KTHREAD_ENTRY" ] && [ -n "${FUSE_DETECT_KTHREAD_ENTRY:-}" ]; then
        FUSE_KTHREAD_ENTRY="$FUSE_DETECT_KTHREAD_ENTRY"
        echo "    auto entry=$FUSE_KTHREAD_ENTRY (from detect)"
    fi
    if [ "${FUSE_RUN_MAIN:-0}" != "1" ] && [ -z "${FUSE_KTHREAD_ENTRY:-}" ] && [ "${FUSE_DETECT_RUN_MAIN:-0}" = "1" ]; then
        FUSE_RUN_MAIN=1
        echo "    auto FUSE_RUN_MAIN=1 (from detect)"
    fi
fi

PRE_LL="$PROJECT_ROOT/test/${FUSE_NAME}_pre.ll"
HOST_PROFILE="$PROJECT_ROOT/test/${FUSE_NAME}.host_profile.env"
if [ -f "$PRE_LL" ]; then
    bash "$SCRIPT_DIR/plc_fusion_host_profile__宿主自动配置.sh" "$MANIFEST" "$PRE_LL" >/dev/null || true
    if [ -f "$HOST_PROFILE" ]; then
        # shellcheck disable=SC1090
        source "$HOST_PROFILE"
        if [ -z "${FUSE_HOST:-}" ] || [ "${FUSE_HOST:-}" = generic ]; then
            if [ "${FUSE_DETECT_NEED_HRTIMER:-0}" = "1" ] || \
               [ "${FUSE_DETECT_NEED_SIGNAL:-0}" = "1" ]; then
                FUSE_HOST=hrtimer
                echo "    auto FUSE_HOST=hrtimer (from IR: timer/signal)"
            fi
        fi
        if [ "${FUSE_DETECT_NEED_PTHREAD:-0}" = "1" ] || \
           [ "${FUSE_DETECT_NEED_SEM:-0}" = "1" ] || \
           [ "${FUSE_DETECT_NEED_BARRIER:-0}" = "1" ]; then
            if [ "${FUSE_LINK_PTHREAD_HOST:-auto}" = auto ]; then
                FUSE_LINK_PTHREAD_HOST=1
                echo "    auto FUSE_LINK_PTHREAD_HOST=1 (pthread/sem/barrier)"
            fi
        elif [ "${FUSE_LINK_PTHREAD_HOST:-auto}" = auto ]; then
            FUSE_LINK_PTHREAD_HOST=0
        fi
        if [ "${FUSE_DETECT_NEED_FILEIO:-0}" = "1" ] && \
           [ "${FUSE_LINK_RUNTIME_STUBS:-1}" != "1" ]; then
            echo "    hint: IR 含 fileio，建议 FUSE_LINK_RUNTIME_STUBS=1"
        fi
    elif [ "${FUSE_LINK_PTHREAD_HOST:-auto}" = auto ]; then
        FUSE_LINK_PTHREAD_HOST=0
    fi
fi

if [ "${FUSE_LINK_PTHREAD_HOST:-auto}" = auto ]; then
    FUSE_LINK_PTHREAD_HOST=0
fi

if [ "$FUSE_RUN_MAIN" != "1" ] && [ -z "$FUSE_KTHREAD_ENTRY" ]; then
    plc_die "$PLC_E_MANIFEST" "缺少融合入口配置" \
        "设置 FUSE_KTHREAD_ENTRY=your_thread 或 FUSE_RUN_MAIN=1" \
        "或开启 FUSE_AUTO_DETECT=1 并确认 pre.ll 含 pthread_create"
fi

ENTRY="${FUSE_KTHREAD_ENTRY:-main}"
HOST_CFLAGS="-DFUSED_RUN_MAIN=${FUSE_RUN_MAIN}"
if [ "$FUSE_RUN_MAIN" != "1" ]; then
    HOST_CFLAGS+=" -DFUSED_ENTRY_SYMBOL=${ENTRY}"
fi
if [ -n "${FUSE_MAIN_ARGS:-}" ]; then
    _ma="${FUSE_MAIN_ARGS//\"/\\\"}"
    HOST_CFLAGS+=" -DFUSED_MAIN_ARGS_DEFAULT=\"\\\"${_ma}\\\"\""
fi
if [ "${FUSE_LINK_PTHREAD_HOST:-0}" = "1" ]; then
    HOST_CFLAGS+=" -DFUSED_HAVE_PTHREAD_HOST=1"
fi
echo "    run_main=${FUSE_RUN_MAIN} entry=${ENTRY} host=${FUSE_HOST} pthread_host=${FUSE_LINK_PTHREAD_HOST}"
[ -n "${FUSE_MAIN_ARGS:-}" ] && echo "    main_args=${FUSE_MAIN_ARGS}"

case "$FUSE_HOST" in
    generic) ;;
    hrtimer)
        plc_require_file "$PROJECT_ROOT/src/plc_hrtimer_core__定时核心.c" "hrtimer 核心"
        plc_require_file "$PROJECT_ROOT/src/plc_fused_timer_host__hrtimer宿主.c" "hrtimer 宿主"
        cp "$PROJECT_ROOT/src/plc_hrtimer_core__定时核心.c" "$PROJECT_ROOT/test/plc_hrtimer_core.c"
        cp "$PROJECT_ROOT/src/plc_fused_timer_host__hrtimer宿主.c" "$PROJECT_ROOT/test/plc_fused_timer_host.c"
        HOST_OBJS="${HOST_OBJS} plc_hrtimer_core.o plc_fused_timer_host.o"
        ;;
    *)
        plc_die "$PLC_E_MANIFEST" "未知 FUSE_HOST=$FUSE_HOST" \
            "可选: generic | hrtimer"
        ;;
esac

cp "$HOST_SRC" "$PROJECT_ROOT/test/plc_fused_host.c"
cp "$PROJECT_ROOT/src/plc_pthread_host__pthread宿主.c" "$PROJECT_ROOT/test/plc_pthread_host.c" 2>/dev/null || \
    plc_warn "plc_pthread_host.c 缺失，FUSE_LINK_PTHREAD_HOST=1 时链接可能失败"

STUBS_C="plc_runtime_stubs.c"
STUBS_O="plc_runtime_stubs.o"
if [ -f "${FUSE_NAME}_runtime_stubs.c" ]; then
    STUBS_C="${FUSE_NAME}_runtime_stubs.c"
    STUBS_O="${FUSE_NAME}_runtime_stubs.o"
    echo "    stubs=${STUBS_C} (auto-merged)"
elif [ "$FUSE_LINK_RUNTIME_STUBS" = "1" ]; then
    plc_require_file "$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c" "runtime 桩"
    cp "$PROJECT_ROOT/src/plc_runtime_stubs__POSIX桩.c" "$PROJECT_ROOT/test/plc_runtime_stubs.c"
fi

EXTRA_OBJS=""
if [ "$FUSE_LINK_RUNTIME_STUBS" = "1" ]; then
    EXTRA_OBJS=" ${STUBS_O}"
fi
if [ "$FUSE_LINK_PTHREAD_HOST" = "1" ]; then
    EXTRA_OBJS="${EXTRA_OBJS} plc_pthread_host.o"
fi

MODPOST_STUBS_C="${FUSE_NAME}_modpost_stubs.c"
MODPOST_STUBS_O="${FUSE_NAME}_modpost_stubs.o"

if plc_kernel_has_compiler_rt_syms "$KERNEL_O" 2>/dev/null; then
    plc_die "$PLC_E_BUILD" "kernel.o 仍含软浮点符号（应已由 Q 定点 Pass 消除）" \
        "确认 FUSE_FIXED_POINT=1 且 plc-kernelize Pass 已运行" \
        "见 test/${FUSE_NAME}.pipeline.log"
fi

write_kbuild_makefile() {
    cat > Makefile <<MAKEFILE_EOF
obj-m += ${MOD_NAME}.o
${MOD_NAME}-objs := ${HOST_OBJS}${EXTRA_OBJS} ${KERNEL_O}
KDIR := /lib/modules/\$(shell uname -r)/build
PWD := \$(shell pwd)
ccflags-y += -O2 -I${PROJECT_ROOT}/include -I${PROJECT_ROOT}/src ${HOST_CFLAGS}
all:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) modules
clean:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) clean
MAKEFILE_EOF
}

clean_current_ko_artifacts() {
    # 勿 make clean：会删掉 test/ 里其它 manifest 已生成的 *_mod.ko
    rm -f \
        "${MOD_NAME}.ko" "${MOD_NAME}.o" "${MOD_NAME}.mod" "${MOD_NAME}.mod.o" \
        "${MOD_NAME}.mod.c" ".${MOD_NAME}.ko.cmd" ".${MOD_NAME}.o.cmd" \
        ".${MOD_NAME}.mod.o.cmd" "modules.order" "Module.symvers" \
        ".module-common.o" ".module-common.o.cmd" ".modules.order.cmd" \
        ".Module.symvers.cmd" 2>/dev/null || true
    # 宿主 .o 带 manifest 相关 -DFUSED_*，切换应用时必须重编
    rm -f plc_fused_host.o plc_hrtimer_core.o plc_fused_timer_host.o plc_pthread_host.o \
        .plc_fused_host.o.cmd .plc_hrtimer_core.o.cmd .plc_fused_timer_host.o.cmd \
        .plc_pthread_host.o.cmd \
        "${STUBS_O}" ".${STUBS_O}.cmd" 2>/dev/null || true
}

ensure_modpost_stubs_obj() {
    if [ -f "$MODPOST_STUBS_C" ] && ! echo "$EXTRA_OBJS" | grep -qF "$MODPOST_STUBS_O"; then
        EXTRA_OBJS="${EXTRA_OBJS} ${MODPOST_STUBS_O}"
        echo "    link modpost stubs (${MODPOST_STUBS_C})"
    fi
}

write_kbuild_makefile

echo "savedcmd_${PWD}/${KERNEL_O} := true" > ".${KERNEL_O}.cmd"
clean_current_ko_artifacts
echo "savedcmd_${PWD}/${KERNEL_O} := true" > ".${KERNEL_O}.cmd"

FUSE_KO_FIXUP_LOOP="${FUSE_KO_FIXUP_LOOP:-1}"
FUSE_KO_FIXUP_MAX="${FUSE_KO_FIXUP_MAX:-3}"
KBUILD_LOG="${PLC_BUILD_LOG:-$PROJECT_ROOT/test/${FUSE_NAME}.kbuild.log}"
KBUILD_OK=0

for attempt in $(seq 1 "$FUSE_KO_FIXUP_MAX"); do
    echo "    Kbuild 尝试 ${attempt}/${FUSE_KO_FIXUP_MAX}..."
    if make all 2>&1 | tee "$KBUILD_LOG"; then
        KBUILD_OK=1
        break
    fi
    if [ "$FUSE_KO_FIXUP_LOOP" != "1" ]; then
        break
    fi
    if ! grep -qE 'modpost|undefined symbol|undefined!' "$KBUILD_LOG"; then
        break
    fi
    echo "    modpost 未解析符号 → 自动修复..."
    fix_out="$(bash "$SCRIPT_DIR/plc_fusion_modpost_fix__ko链接修复.sh" "$MANIFEST" "$KBUILD_LOG" 2>&1)" || {
        echo "$fix_out"
        break
    }
    echo "$fix_out"
    ensure_modpost_stubs_obj
    write_kbuild_makefile
    echo "savedcmd_${PWD}/${KERNEL_O} := true" > ".${KERNEL_O}.cmd"
done

if [ "$KBUILD_OK" != "1" ]; then
    plc_die "$PLC_E_BUILD" "Kbuild 编译 ${MOD_NAME}.ko 失败" \
        "常见: 未解析符号 → bash scripts/plc_fuse_report__覆盖率报告.sh $MANIFEST" \
        "modpost 日志: $KBUILD_LOG" \
        "映射建议: bash scripts/plc_fusion_remap_hints__映射建议.sh $MANIFEST" \
        "内核头版本与运行内核不一致 → 重装 headers"
fi

plc_require_file "${MOD_NAME}.ko" "内核模块" "make 声称成功但 .ko 未生成"

plc_check_sudo 0
if ! sudo -n true 2>/dev/null; then
    plc_warn "无免密 sudo，跳过自动 rmmod 检查" \
        "加载前请手动: sudo -v"
else
    plc_check_module_stuck "$MOD_NAME" || true
    if lsmod | grep -q "^${MOD_NAME//-/_}"; then
        echo "⚠️  ${MOD_NAME} 已加载，尝试卸载..."
        bash "$SCRIPT_DIR/safe_rmmod_fused__安全卸载.sh" "$MOD_NAME" || \
            plc_warn "旧模块卸载失败，手动 rmmod 后再 insmod"
    fi
fi

echo "✅ built test/${MOD_NAME}.ko"
echo "   insmod: sudo insmod test/${MOD_NAME}.ko"
echo "   停止:   echo 1 | sudo tee /sys/module/${MOD_NAME}/parameters/shutdown_request"
echo "   卸载:   bash scripts/safe_rmmod_fused__安全卸载.sh ${MOD_NAME}"
echo "   覆盖率: bash scripts/plc_fuse_check__覆盖率门禁.sh $MANIFEST"
