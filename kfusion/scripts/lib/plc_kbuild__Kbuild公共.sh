# ============================================================================
# plc_kbuild__Kbuild公共.sh — Kbuild Makefile / 编译 / modpost 修复（source 使用）
# ============================================================================
[[ -n "${_PLC_KBUILD_LOADED:-}" ]] && return 0 2>/dev/null || true
_PLC_KBUILD_LOADED=1

plc_kbuild_touch_cmd() {
    local dir="$1"
    local kernel_o="$2"
    echo "savedcmd_${dir}/${kernel_o} := true" > "${dir}/.${kernel_o}.cmd"
}

plc_kbuild_globalize_kernel_o() {
    local kernel_o="$1"
    local syms="${FUSE_GLOBALIZE_SYMBOLS:-}"
    local extra_objcopy=() sym

    [ -n "$syms" ] || return 0
    for sym in $syms; do
        extra_objcopy+=(--globalize-symbol="$sym")
    done
    objcopy "${extra_objcopy[@]}" "$kernel_o"
}

plc_kbuild_write_makefile() {
    local mod_name="$1"
    local objs="$2"
    local ccflags="$3"
    local extra_cflags="${4:-}"

    cat > Makefile <<MAKEFILE_EOF
obj-m += ${mod_name}.o
${mod_name}-objs := ${objs}
KDIR := /lib/modules/\$(shell uname -r)/build
PWD := \$(shell pwd)
ccflags-y += ${ccflags}
${extra_cflags}
all:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) modules
clean:
	\$(MAKE) -C \$(KDIR) M=\$(PWD) clean
MAKEFILE_EOF
}

plc_kbuild_clean_artifacts() {
    local mod_name="$1"
    shift
    local host_objs=("$@")

    rm -f \
        "${mod_name}.ko" "${mod_name}.o" "${mod_name}.mod" "${mod_name}.mod.o" \
        "${mod_name}.mod.c" ".${mod_name}.ko.cmd" ".${mod_name}.o.cmd" \
        ".${mod_name}.mod.o.cmd" "modules.order" "Module.symvers" \
        ".module-common.o" ".module-common.o.cmd" ".modules.order.cmd" \
        ".Module.symvers.cmd" 2>/dev/null || true

    local o
    for o in "${host_objs[@]}"; do
        rm -f "$o" ".${o}.cmd" 2>/dev/null || true
    done
}

# 运行 Kbuild；可选 modpost 自动修复（需 PLC_KBUILD_MANIFEST / PLC_KBUILD_MOD_NAME 等）
plc_kbuild_run() {
    local manifest="$1"
    local mod_name="$2"
    local kbuild_log="$3"
    local fuse_dir="$4"
    local max="${FUSE_KO_FIXUP_MAX:-3}"
    local fix_loop="${FUSE_KO_FIXUP_LOOP:-1}"
    local attempt kbuild_ok=0 fix_out

    for attempt in $(seq 1 "$max"); do
        echo "    Kbuild 尝试 ${attempt}/${max}..."
        if make all 2>&1 | tee "$kbuild_log"; then
            kbuild_ok=1
            break
        fi
        if [ "$fix_loop" != "1" ]; then
            break
        fi
        if ! grep -qE 'modpost|undefined symbol|undefined!' "$kbuild_log"; then
            break
        fi
        echo "    modpost 未解析符号 → 自动修复..."
        fix_out="$(bash "$fuse_dir/plc_fusion_modpost_fix__ko链接修复.sh" "$manifest" "$kbuild_log" 2>&1)" || {
            echo "$fix_out"
            break
        }
        echo "$fix_out"
        if declare -F plc_kbuild_after_modpost_fix >/dev/null 2>&1; then
            plc_kbuild_after_modpost_fix
        fi
    done

    if [ "$kbuild_ok" != "1" ]; then
        plc_die "$PLC_E_BUILD" "Kbuild 编译 ${mod_name}.ko 失败" \
            "常见: 未解析符号 → bash scripts/plc_fuse_report__覆盖率报告.sh $manifest" \
            "modpost 日志: $kbuild_log" \
            "映射建议: bash scripts/plc_fusion_remap_hints__映射建议.sh $manifest" \
            "内核头版本与运行内核不一致 → 重装 headers"
    fi
    plc_require_file "${mod_name}.ko" "内核模块" "make 声称成功但 .ko 未生成"
}
