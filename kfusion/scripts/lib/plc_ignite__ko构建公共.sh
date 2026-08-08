# ============================================================================
# plc_ignite__ko构建公共.sh — fuse 后 .ko 链接（generic / L2 runner）
# ============================================================================
[[ -n "${_PLC_IGNITE_LOADED:-}" ]] && return 0 2>/dev/null || true
_PLC_IGNITE_LOADED=1

_IGNITE_COMMON="$(cd "$(dirname "${BASH_SOURCE[0]}")/../fuse" && pwd)/plc_fusion_common__公共库.sh"
# shellcheck source=../fuse/plc_fusion_common__公共库.sh
source "$_IGNITE_COMMON"
# shellcheck source=plc_kbuild__Kbuild公共.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/plc_kbuild__Kbuild公共.sh"

plc_ignite_fuse_script() {
    echo "$(plc_scripts_root)/plc_fuse__内核化主流程.sh"
}

plc_ignite_ensure_kernel_o() {
    local manifest="$1"
    local test_dir="$2"
    local kernel_o="$3"
    local force="${FORCE_REBUILD_KERNEL_O:-0}"
    local fuse_script
    fuse_script="$(plc_ignite_fuse_script)"

    if [ "$force" = "1" ] || [ ! -f "$test_dir/$kernel_o" ]; then
        echo "🔮 重建 ${kernel_o}..."
        bash "$fuse_script" "$manifest"
    elif [ -f "$test_dir/${kernel_o}_shipped" ]; then
        echo "🧽 复用 ${kernel_o}_shipped（FORCE_REBUILD_KERNEL_O=0）"
        cp -f "$test_dir/${kernel_o}_shipped" "$test_dir/$kernel_o"
    else
        plc_warn "无 ${kernel_o} 且无 _shipped，强制重新融合"
        bash "$fuse_script" "$manifest"
    fi
    plc_require_file "$test_dir/$kernel_o" "融合 .o" \
        "plc_fuse__内核化主流程.sh 可能失败，检查上方输出"
}

plc_ignite_apply_detected_entry() {
    local project_root="$1"
    local detected="$project_root/test/${FUSE_NAME}.detected.env"

    [ "${FUSE_AUTO_DETECT:-1}" = "1" ] || return 0
    [ -f "$detected" ] || return 0
    # shellcheck disable=SC1090
    source "$detected"
    if [ "${FUSE_RUN_MAIN:-0}" != "1" ] && [ -z "${FUSE_KTHREAD_ENTRY:-}" ] && \
       [ -n "${FUSE_DETECT_KTHREAD_ENTRY:-}" ]; then
        FUSE_KTHREAD_ENTRY="$FUSE_DETECT_KTHREAD_ENTRY"
        echo "    auto entry=$FUSE_KTHREAD_ENTRY (from detect)"
    fi
    if [ "${FUSE_RUN_MAIN:-0}" != "1" ] && [ -z "${FUSE_KTHREAD_ENTRY:-}" ] && \
       [ "${FUSE_DETECT_RUN_MAIN:-0}" = "1" ]; then
        FUSE_RUN_MAIN=1
        echo "    auto FUSE_RUN_MAIN=1 (from detect)"
    fi
}

plc_ignite_apply_host_profile() {
    local manifest="$1"
    local project_root="$2"
    local pre_ll="$project_root/test/${FUSE_NAME}_pre.ll"
    local host_profile="$project_root/test/${FUSE_NAME}.host_profile.env"
    local scripts_root fuse_dir

    scripts_root="$(plc_scripts_root)"
    fuse_dir="$scripts_root/fuse"

    if [ -f "$pre_ll" ]; then
        bash "$fuse_dir/plc_fusion_host_profile__宿主自动配置.sh" \
            "$manifest" "$pre_ll" >/dev/null || true
    fi
    [ -f "$host_profile" ] || return 0
    # shellcheck disable=SC1090
    source "$host_profile"

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
}

plc_ignite_finalize_pthread_default() {
    if [ "${FUSE_LINK_PTHREAD_HOST:-auto}" = auto ]; then
        FUSE_LINK_PTHREAD_HOST=0
    fi
}

plc_ignite_require_entry() {
    if [ "${FUSE_RUN_MAIN:-0}" != "1" ] && [ -z "${FUSE_KTHREAD_ENTRY:-}" ]; then
        plc_die "$PLC_E_MANIFEST" "缺少融合入口配置" \
            "设置 FUSE_KTHREAD_ENTRY=your_thread 或 FUSE_RUN_MAIN=1" \
            "或开启 FUSE_AUTO_DETECT=1 并确认 pre.ll 含 pthread_create"
    fi
}

# 由 plc_ignite_build_generic 设置的 Kbuild 上下文（modpost 修复回调用）
PLC_KBUILD_CTX_MOD_NAME=""
PLC_KBUILD_CTX_HOST_OBJS=""
PLC_KBUILD_CTX_EXTRA_OBJS=""
PLC_KBUILD_CTX_KERNEL_O=""
PLC_KBUILD_CTX_CCFLAGS=""
PLC_KBUILD_CTX_BUILD_DIR=""
PLC_KBUILD_CTX_MODPOST_C=""
PLC_KBUILD_CTX_MODPOST_O=""

plc_kbuild_after_modpost_fix() {
    if [ -f "$PLC_KBUILD_CTX_MODPOST_C" ] && \
       ! echo "$PLC_KBUILD_CTX_EXTRA_OBJS" | grep -qF "$PLC_KBUILD_CTX_MODPOST_O"; then
        PLC_KBUILD_CTX_EXTRA_OBJS="${PLC_KBUILD_CTX_EXTRA_OBJS} ${PLC_KBUILD_CTX_MODPOST_O}"
        echo "    link modpost stubs (${PLC_KBUILD_CTX_MODPOST_C})"
        plc_kbuild_write_makefile "$PLC_KBUILD_CTX_MOD_NAME" \
            "${PLC_KBUILD_CTX_HOST_OBJS}${PLC_KBUILD_CTX_EXTRA_OBJS} ${PLC_KBUILD_CTX_KERNEL_O}" \
            "$PLC_KBUILD_CTX_CCFLAGS"
        plc_kbuild_touch_cmd "$PLC_KBUILD_CTX_BUILD_DIR" "$PLC_KBUILD_CTX_KERNEL_O"
    fi
}

# 构建 generic 组合宿主 .ko（原 ignite_fused 主体）
plc_ignite_build_generic() {
    local manifest="$1"
    local project_root scripts_root fuse_dir test_dir
    local kernel_o mod_name host_src host_objs extra_objs host_cflags
    local stubs_c stubs_o modpost_stubs_c modpost_stubs_o entry
    local kbuild_log

    project_root="$(plc_project_root)"
    scripts_root="$(plc_scripts_root)"
    fuse_dir="$scripts_root/fuse"
    test_dir="$project_root/test"
    kernel_o="${FUSE_NAME}_kernel.o"
    mod_name="${FUSE_NAME}_mod"
    host_src="$project_root/src/plc_fused_host__通用宿主.c"
    host_objs="plc_fused_host.o"
    extra_objs=""
    stubs_c="plc_runtime_stubs.c"
    stubs_o="plc_runtime_stubs.o"
    modpost_stubs_c="${FUSE_NAME}_modpost_stubs.c"
    modpost_stubs_o="${FUSE_NAME}_modpost_stubs.o"

    plc_require_file "$host_src" "宿主源码"
    plc_check_kernel_build >/dev/null
    plc_ensure_dir "$test_dir"
    cd "$test_dir"

    echo "=== ignite generic: ${FUSE_NAME} ==="

    plc_ignite_ensure_kernel_o "$manifest" "$test_dir" "$kernel_o"
    plc_ignite_apply_detected_entry "$project_root"
    plc_ignite_apply_host_profile "$manifest" "$project_root"
    plc_ignite_finalize_pthread_default
    plc_ignite_require_entry

    entry="${FUSE_KTHREAD_ENTRY:-main}"
    host_cflags="-DFUSED_RUN_MAIN=${FUSE_RUN_MAIN:-0}"
    if [ "${FUSE_RUN_MAIN:-0}" != "1" ]; then
        host_cflags+=" -DFUSED_ENTRY_SYMBOL=${entry}"
    fi
    if [ -n "${FUSE_MAIN_ARGS:-}" ]; then
        local _ma="${FUSE_MAIN_ARGS//\"/\\\"}"
        host_cflags+=" -DFUSED_MAIN_ARGS_DEFAULT=\"\\\"${_ma}\\\"\""
    fi
    if [ "${FUSE_LINK_PTHREAD_HOST:-0}" = "1" ]; then
        host_cflags+=" -DFUSED_HAVE_PTHREAD_HOST=1"
    fi
    echo "    run_main=${FUSE_RUN_MAIN:-0} entry=${entry} host=${FUSE_HOST:-generic} pthread_host=${FUSE_LINK_PTHREAD_HOST:-0}"
    [ -n "${FUSE_MAIN_ARGS:-}" ] && echo "    main_args=${FUSE_MAIN_ARGS}"

    case "${FUSE_HOST:-generic}" in
        generic) ;;
        hrtimer)
            plc_require_file "$project_root/src/plc_hrtimer_core__定时核心.c" "hrtimer 核心"
            plc_require_file "$project_root/src/plc_fused_timer_host__hrtimer宿主.c" "hrtimer 宿主"
            cp "$project_root/src/plc_hrtimer_core__定时核心.c" "$test_dir/plc_hrtimer_core.c"
            cp "$project_root/src/plc_fused_timer_host__hrtimer宿主.c" "$test_dir/plc_fused_timer_host.c"
            host_objs="${host_objs} plc_hrtimer_core.o plc_fused_timer_host.o"
            ;;
        *)
            plc_die "$PLC_E_MANIFEST" "未知 FUSE_HOST=${FUSE_HOST:-}" \
                "可选: generic | hrtimer"
            ;;
    esac

    cp "$host_src" "$test_dir/plc_fused_host.c"
    cp "$project_root/src/plc_pthread_host__pthread宿主.c" "$test_dir/plc_pthread_host.c" 2>/dev/null || \
        plc_warn "plc_pthread_host.c 缺失，FUSE_LINK_PTHREAD_HOST=1 时链接可能失败"

    if [ -f "${FUSE_NAME}_runtime_stubs.c" ]; then
        stubs_c="${FUSE_NAME}_runtime_stubs.c"
        stubs_o="${FUSE_NAME}_runtime_stubs.o"
        echo "    stubs=${stubs_c} (auto-merged)"
    elif [ "${FUSE_LINK_RUNTIME_STUBS:-1}" = "1" ]; then
        plc_require_file "$project_root/src/plc_runtime_stubs__POSIX桩.c" "runtime 桩"
        cp "$project_root/src/plc_runtime_stubs__POSIX桩.c" "$test_dir/plc_runtime_stubs.c"
    fi

    if [ "${FUSE_LINK_RUNTIME_STUBS:-1}" = "1" ]; then
        extra_objs=" ${stubs_o}"
    fi
    if [ "${FUSE_LINK_PTHREAD_HOST:-0}" = "1" ]; then
        extra_objs="${extra_objs} plc_pthread_host.o"
    fi

    if plc_kernel_has_compiler_rt_syms "$kernel_o" 2>/dev/null; then
        plc_die "$PLC_E_BUILD" "kernel.o 仍含软浮点符号（应已由 Q 定点 Pass 消除）" \
            "确认 FUSE_FIXED_POINT=1 且 plc-kernelize Pass 已运行" \
            "见 test/${FUSE_NAME}.pipeline.log"
    fi

    plc_kbuild_globalize_kernel_o "$kernel_o"
    cp -f "$kernel_o" "${kernel_o}_shipped" 2>/dev/null || true

    plc_kbuild_write_makefile "$mod_name" \
        "${host_objs}${extra_objs} ${kernel_o}" \
        "-O2 -I${project_root}/include -I${project_root}/src ${host_cflags}"

    plc_kbuild_touch_cmd "$test_dir" "$kernel_o"
    plc_kbuild_clean_artifacts "$mod_name" \
        plc_fused_host.o plc_hrtimer_core.o plc_fused_timer_host.o plc_pthread_host.o "$stubs_o"

    PLC_KBUILD_CTX_MOD_NAME="$mod_name"
    PLC_KBUILD_CTX_HOST_OBJS="$host_objs"
    PLC_KBUILD_CTX_EXTRA_OBJS="$extra_objs"
    PLC_KBUILD_CTX_KERNEL_O="$kernel_o"
    PLC_KBUILD_CTX_CCFLAGS="-O2 -I${project_root}/include -I${project_root}/src ${host_cflags}"
    PLC_KBUILD_CTX_BUILD_DIR="$test_dir"
    PLC_KBUILD_CTX_MODPOST_C="$modpost_stubs_c"
    PLC_KBUILD_CTX_MODPOST_O="$modpost_stubs_o"

    kbuild_log="${PLC_BUILD_LOG:-$project_root/test/${FUSE_NAME}.kbuild.log}"
    plc_kbuild_run "$manifest" "$mod_name" "$kbuild_log" "$fuse_dir"

    plc_check_sudo 0
    if ! sudo -n true 2>/dev/null; then
        plc_warn "无免密 sudo，跳过自动 rmmod 检查" "加载前请手动: sudo -v"
    else
        plc_check_module_stuck "$mod_name" || true
        if lsmod | grep -q "^${mod_name//-/_}"; then
            echo "⚠️  ${mod_name} 已加载，尝试卸载..."
            bash "$scripts_root/safe_rmmod_fused__安全卸载.sh" "$mod_name" || \
                plc_warn "旧模块卸载失败，手动 rmmod 后再 insmod"
        fi
    fi

    echo "✅ built test/${mod_name}.ko"
    echo "   insmod: sudo insmod test/${mod_name}.ko"
    echo "   停止:   echo 1 | sudo tee /sys/module/${mod_name}/parameters/shutdown_request"
    echo "   卸载:   bash scripts/safe_rmmod_fused__安全卸载.sh ${mod_name}"
}

# L2 runner .ko（cyclictest 测量宿主）
# 可选: PLC_IGNITE_BUILD_DIR, PLC_IGNITE_KERNEL_O_SRC, PLC_IGNITE_MOD_NAME
plc_ignite_build_l2() {
    local manifest="$1"
    local project_root test_dir build_dir kernel_o mod_name stubs stubs_o
    local cflags extra_cflags kbuild_log

    project_root="$(plc_project_root)"
    test_dir="$project_root/test"
    build_dir="${PLC_IGNITE_BUILD_DIR:-$test_dir}"
    kernel_o="${FUSE_NAME}_kernel.o"
    mod_name="${PLC_IGNITE_MOD_NAME:-${FUSE_NAME}_mod}"

    plc_check_kernel_build >/dev/null
    plc_ignite_ensure_kernel_o "$manifest" "$test_dir" "$kernel_o"

    plc_ensure_dir "$build_dir"
    cd "$build_dir"

    cp "$project_root/src/plc_hrtimer_core__定时核心.c" plc_hrtimer_core.c
    cp "$project_root/src/plc_runner_official__cyclictest宿主.c" plc_runner_official.c

    stubs="${FUSE_NAME}_runtime_stubs.c"
    if [ -f "$test_dir/$stubs" ]; then
        cp "$test_dir/$stubs" "./$(basename "$stubs")"
        stubs="$(basename "$stubs")"
    elif [ -f "$stubs" ]; then
        :
    else
        stubs="plc_runtime_stubs.c"
        cp "$project_root/src/plc_runtime_stubs__POSIX桩.c" "$stubs"
    fi
    stubs_o="${stubs%.c}.o"

    if [ -n "${PLC_IGNITE_KERNEL_O_SRC:-}" ]; then
        cp -f "$PLC_IGNITE_KERNEL_O_SRC" "$kernel_o"
    elif [ "$build_dir" != "$test_dir" ]; then
        cp -f "$test_dir/$kernel_o" "$kernel_o"
    fi

    plc_kbuild_globalize_kernel_o "$kernel_o"
    if [ "$build_dir" = "$test_dir" ]; then
        cp -f "$kernel_o" "${kernel_o}_shipped" 2>/dev/null || true
    fi
    plc_kbuild_touch_cmd "$build_dir" "$kernel_o"

    cflags="-O2 -I${project_root}/include -I${project_root}/src"
    if [ "${HOST_DRIVER_OPT_LEVEL:--O2}" != "-O2" ]; then
        cflags="${HOST_DRIVER_OPT_LEVEL} -I${project_root}/include -I${project_root}/src"
    fi
    extra_cflags="CFLAGS_plc_runner_official.o += ${HOST_DRIVER_OPT_LEVEL:--O2}"

    plc_kbuild_write_makefile "$mod_name" \
        "plc_runner_official.o plc_hrtimer_core.o ${stubs_o} ${kernel_o}" \
        "$cflags" "$extra_cflags"

    plc_kbuild_clean_artifacts "$mod_name" \
        plc_runner_official.o plc_hrtimer_core.o "$stubs_o"

    kbuild_log="${PLC_BUILD_LOG:-$build_dir/${FUSE_NAME}.kbuild.log}"
    echo "🔧 Kbuild L2 runner → ${mod_name}.ko"
    FUSE_KO_FIXUP_LOOP="${FUSE_KO_FIXUP_LOOP:-0}"
    plc_kbuild_run "$manifest" "$mod_name" "$kbuild_log" "$(plc_scripts_root)/fuse"
    echo "✅ built ${build_dir}/${mod_name}.ko (FUSE_RUNNER_PROFILE=l2)"
}
