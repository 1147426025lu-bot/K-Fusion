# ============================================================================
# plc_fusion_common__公共库.sh — PLCFusion 脚本公共库（source 使用，勿直接执行）
# ============================================================================
# 功能: 统一退出码、路径解析、工具检测、带说明的报错/警告、构建步骤包装
# 退出码:
#   2=参数  3=文件缺失  4=命令缺失  5=manifest  6=编译/IR  7=git
#   8=内核模块  9=权限  10=IR分析  11=模块卡住(refcnt=-1)
# ============================================================================
[[ -n "${_PLC_FUSION_COMMON_LOADED:-}" ]] && return 0 2>/dev/null || true
_PLC_FUSION_COMMON_LOADED=1

readonly PLC_E_USAGE=2
readonly PLC_E_NOFILE=3
readonly PLC_E_NOCMD=4
readonly PLC_E_MANIFEST=5
readonly PLC_E_BUILD=6
readonly PLC_E_GIT=7
readonly PLC_E_KMOD=8
readonly PLC_E_PERM=9
readonly PLC_E_IR=10
readonly PLC_E_STUCK=11

# 被 source 时失败用 return，独立执行用 exit
plc_die() {
    local code="$1"
    shift
    local msg="$1"
    shift
    echo "" >&2
    echo "❌ ${msg}" >&2
    while [ $# -gt 0 ]; do
        echo "   💡 $1" >&2
        shift
    done
    echo "" >&2
    if [ -n "${BASH_SOURCE[1]:-}" ] && [ "${BASH_SOURCE[0]}" != "${0}" ]; then
        return "$code" 2>/dev/null || exit "$code"
    fi
    exit "$code"
}

plc_warn() {
    local msg="$1"
    shift
    echo "⚠️  ${msg}" >&2
    while [ $# -gt 0 ]; do
        echo "   💡 $1" >&2
        shift
    done
}

plc_hint_usage() {
    local script="$1"
    shift
    plc_die "$PLC_E_USAGE" "参数不正确" \
        "用法: $script $*" \
        "示例: $script manifests/manifest_cyclictest__主线压测.env"
}

plc_project_root() {
    local dir start
    start="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
    dir="$start"
    while [ "$dir" != "/" ]; do
        if [ -f "$dir/CMakeLists.txt" ] && [ -d "$dir/manifests" ]; then
            echo "$dir"
            return
        fi
        dir="$(dirname "$dir")"
    done
    if [ "$(basename "$start")" = "scripts" ]; then
        cd "$start/.." && pwd
        return
    fi
    echo "$start"
}

# 解析 manifest 绝对路径 → 变量 PLC_MANIFEST
plc_resolve_manifest() {
    local raw="${1:-}"
    local root="${2:-}"
    if [ -z "$raw" ]; then
        plc_hint_usage "${BASH_SOURCE[1]:-script}" "<manifest.env>"
    fi
    if [[ "$raw" != /* ]]; then
        raw="${root}/${raw}"
    fi
    PLC_MANIFEST="$raw"
}

plc_require_file() {
    local path="$1"
    local label="${2:-文件}"
    shift 2 || true
    if [ ! -f "$path" ]; then
        plc_die "$PLC_E_NOFILE" "${label}不存在: ${path}" "$@"
    fi
}

plc_require_dir() {
    local path="$1"
    local label="${2:-目录}"
    shift 2 || true
    if [ ! -d "$path" ]; then
        plc_die "$PLC_E_NOFILE" "${label}不存在: ${path}" "$@"
    fi
}

plc_ensure_dir() {
    local path="$1"
    if ! mkdir -p "$path" 2>/dev/null; then
        plc_die "$PLC_E_PERM" "无法创建目录: ${path}" \
            "检查磁盘空间与写权限" \
            "可设置 FUSE_WORK_DIR 指向可写路径"
    fi
}

plc_source_manifest() {
    local manifest="$1"
    local req_name="${2:-FUSE_NAME}"
    local req_source="${3:-FUSE_SOURCE}"
    # shellcheck disable=SC1090
    if ! source "$manifest" 2>/dev/null; then
        plc_die "$PLC_E_MANIFEST" "无法加载 manifest: ${manifest}" \
            "检查文件语法（勿用未加引号的空格列表）" \
            "参考 manifests/manifest_template__清单模板.env"
    fi
    if [ -z "${!req_name:-}" ]; then
        plc_die "$PLC_E_MANIFEST" "manifest 缺少 ${req_name}" \
            "在 ${manifest} 中设置 ${req_name}=my_app"
    fi
    if [ -n "$req_source" ] && [ -z "${!req_source:-}" ]; then
        plc_die "$PLC_E_MANIFEST" "manifest 缺少 ${req_source}" \
            "设置 ${req_source}=path/to/source.c（相对 FUSE_GIT_DIR 或 FUSE_WORK_DIR）"
    fi
}

plc_require_cmd() {
    local cmd="$1"
    shift
    if ! command -v "$cmd" >/dev/null 2>&1; then
        plc_die "$PLC_E_NOCMD" "未找到命令: ${cmd}" "$@"
    fi
}

# sudo 会重置 PATH，需把 /usr/local/llvm-* 补回（本机常见安装位置）
plc_prepend_llvm_path() {
    local d ver
    for ver in 19 18 17; do
        d="/usr/local/llvm-${ver}/bin"
        if [ -x "$d/clang" ]; then
            case ":${PATH}:" in
                *":${d}:"*) ;;
                *) export PATH="${d}:${PATH}" ;;
            esac
            return 0
        fi
    done
}

# 解析 LLVM 工具链；找不到时给出安装说明
plc_resolve_tool() {
    plc_prepend_llvm_path
    local var="$1"
    shift
    local candidate hint="" abs=""
    if [ -n "${!var:-}" ]; then
        if command -v "${!var}" >/dev/null 2>&1; then
            echo "${!var}"
            return 0
        fi
        plc_warn "环境变量 ${var}=${!var} 指向的命令不可用，尝试自动查找"
    fi
    for candidate in "$@"; do
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
        for ver in 19 18 17; do
            abs="/usr/local/llvm-${ver}/bin/${candidate}"
            if [ -x "$abs" ]; then
                echo "$abs"
                return 0
            fi
        done
    done
    hint="安装 LLVM 17+（clang/opt/llc）或设置 ${var}=/path/to/clang"
    plc_die "$PLC_E_NOCMD" "未找到 LLVM 工具（尝试过: $*）" "$hint" \
        "Debian/Raspberry Pi: sudo apt install clang-19 llvm-19"
}

# 交叉 LLC 时用 llvm-objcopy（GNU objcopy 不识别异架构 ELF）
plc_resolve_objcopy() {
    local llc_arch="${1:-}"
    local host cross=0
    host="$(uname -m 2>/dev/null || echo unknown)"
    case "$llc_arch" in
        x86-64|x86_64)
            case "$host" in x86_64|amd64) cross=0 ;; *) cross=1 ;; esac ;;
        aarch64|arm64)
            case "$host" in aarch64|arm64) cross=0 ;; *) cross=1 ;; esac ;;
    esac
    if [ "$cross" = "1" ]; then
        plc_resolve_tool OBJCOPY_BIN llvm-objcopy-19 llvm-objcopy-18 llvm-objcopy-17 llvm-objcopy
    else
        plc_resolve_tool OBJCOPY_BIN llvm-objcopy-19 llvm-objcopy-18 llvm-objcopy-17 llvm-objcopy objcopy
    fi
}

plc_check_kernel_build() {
    local kdir="/lib/modules/$(uname -r)/build"
    if [ ! -d "$kdir" ]; then
        plc_die "$PLC_E_NOCMD" "内核头文件未安装: ${kdir}" \
            "执行: sudo apt install raspberrypi-kernel-headers 或 linux-headers-\$(uname -r)" \
            "安装后重新运行本脚本"
    fi
    echo "$kdir"
}

plc_check_sudo() {
    local need_nopass="${1:-0}"
    if ! command -v sudo >/dev/null 2>&1; then
        plc_die "$PLC_E_PERM" "需要 sudo 但未安装" "以 root 运行或安装 sudo"
    fi
    if [ "$need_nopass" = "1" ] && ! sudo -n true 2>/dev/null; then
        plc_die "$PLC_E_PERM" "需要免密 sudo（insmod/rmmod）" \
            "先执行: sudo -v" \
            "或配置 NOPASSWD（仅测试机）: $(whoami) ALL=(ALL) NOPASSWD: ALL"
    fi
}

plc_check_module_stuck() {
    local mod="$1"
    if lsmod | awk -v m="$mod" '$1==m && $3=="-1" {found=1} END{exit !found}'; then
        plc_die "$PLC_E_STUCK" "模块 ${mod} refcnt=-1（内核 oops 后卡住）" \
            "勿再次 insmod/rmmod/pkill" \
            "执行 sudo reboot 后重试"
    fi
}

# 运行构建步骤；失败时打印步骤名与常见原因
plc_run_step() {
    local step="$1"
    local log="${PLC_BUILD_LOG:-}"
    shift
    echo "   ▶ ${step}"
    if [ -n "$log" ]; then
        if ! "$@" >>"$log" 2>&1; then
            plc_die "$PLC_E_BUILD" "[${step}] 命令失败" \
                "命令: $*" \
                "日志: ${log}" \
                "常见原因: 源文件语法错误 / 缺头文件 / Pass 插件与 opt 版本不匹配"
        fi
    else
        if ! "$@"; then
            plc_die "$PLC_E_BUILD" "[${step}] 命令失败" \
                "命令: $*" \
                "可设置 PLC_BUILD_LOG=test/build.log 保留完整输出"
        fi
    fi
}

# 可选步骤：失败只警告，返回 1
plc_try_step() {
    local step="$1"
    shift
    if "$@"; then
        return 0
    fi
    local rc=$?
    plc_warn "[${step}] 非致命失败 (rc=${rc})，继续主流程" \
        "若结果异常可设 FUSE_STRICT=1 强制失败"
    return 1
}

plc_git_sync() {
    local url="$1"
    local dest="$2"
    local depth="${3:-1}"
    local branch="${4:-${FUSE_GIT_BRANCH:-}}"
    local update="${5:-${FUSE_GIT_UPDATE:-0}}"

    if [ -d "$dest/.git" ]; then
        if [ "$update" = "1" ]; then
            echo "   -> 更新 git: ${dest}"
            (
                cd "$dest"
                if [ -n "$branch" ]; then
                    git fetch origin "$branch" --depth "$depth" 2>/dev/null \
                        || git fetch origin "$branch" 2>/dev/null \
                        || git fetch origin 2>/dev/null || true
                    git checkout "$branch" 2>/dev/null \
                        || git checkout -B "$branch" "origin/$branch" 2>/dev/null \
                        || plc_die "$PLC_E_GIT" "无法 checkout 分支: ${branch}" \
                            "检查 FUSE_GIT_BRANCH / --git-branch"
                else
                    git fetch origin --depth "$depth" 2>/dev/null \
                        || git fetch origin 2>/dev/null || true
                fi
                git pull --ff-only 2>/dev/null || true
            )
        else
            echo "   -> 已存在 ${dest}，跳过 clone（--update 或 FUSE_GIT_UPDATE=1 可同步）"
        fi
        return 0
    fi

    plc_ensure_dir "$(dirname "$dest")"
    local clone_args=(clone --depth "$depth")
    if [ -n "$branch" ]; then
        clone_args+=(-b "$branch")
    fi
    clone_args+=("$url" "$dest")
    if ! git "${clone_args[@]}" 2>/dev/null; then
        plc_die "$PLC_E_GIT" "git clone 失败: ${url}" \
            "检查网络与 git:// 是否被防火墙拦截" \
            "可手动 clone 到 ${dest} 后重试" \
            "或改用本地源码：--local / --local-dir"
    fi
}

# 兼容旧名
plc_git_clone() {
    plc_git_sync "$@"
}

# 从 key=value 文件加载环境（值可含括号/逗号，避免 source 语法错误）
plc_load_kv_env_file() {
    local file="$1"
    local line key val
    [ -f "$file" ] || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|\#*) continue ;;
        esac
        case "$line" in
            FUSE_*=*|PLC_*=*)
                key="${line%%=*}"
                val="${line#*=}"
                printf -v "$key" '%s' "$val"
                export "$key"
                ;;
        esac
    done < "$file"
}

# 检测 fused .o 是否仍引用 compiler-rt 软浮点符号（定点 Pass 应已消除）
plc_kernel_has_compiler_rt_syms() {
    local obj="$1"
    [ -f "$obj" ] || return 1
    nm -u "$obj" 2>/dev/null | awk '{print $2}' | grep -qE \
        '^__(add|sub|mul|div|fix|float|extends|trunc|gt|lt|ge|le|eq|ne)(df|sf|tf|hf|bf|)'
}

# kernel.ll 是否仍含浮点 IR 指令（FUSE_FIXED_POINT=1 时应为 0；忽略 struct/tbaa 中的 double 字段名）
plc_kernel_ll_has_float_ir() {
    local kll="$1"
    [ -f "$kll" ] || return 1
    if grep -qE '^[[:space:]]*%[0-9a-zA-Z.]+ = (fadd|fsub|fmul|fdiv|fcmp|uitofp|sitofp|fptosi|fptoui|fptrunc|fpext) ' "$kll" 2>/dev/null; then
        return 0
    fi
    if grep -qE '^[[:space:]]*(load|store|atomicrmw|cmpxchg).*\b(double|float)\b' "$kll" 2>/dev/null; then
        return 0
    fi
    if grep -qE '^[[:space:]]*%.+ = (invoke|call)[^@]*@.*\b(double|float)\b' "$kll" 2>/dev/null; then
        return 0
    fi
    return 1
}

# 内核/编译器已提供的符号 — modpost 缺符号时不生成桩
plc_is_kernel_libc_sym() {
    case "$1" in
        memcpy|memmove|memset|memcmp|strlen|strcmp|strncmp|strncasecmp|strncpy|strnlen|\
        strcpy|strcat|strerror|__errno_location|snprintf|printk|sprintf|vsprintf|\
        kasprintf|kstrdup|kfree|kmalloc|kcalloc|krealloc)
            return 0 ;;
    esac
    return 1
}

plc_is_compiler_rt_sym() {
    case "$1" in
        __adddf3|__subdf3|__muldf3|__divdf3|__fixdfdi|__fixunsdfdi|__floatdidf|\
        __floatundidf|__extendsfdf2|__truncdfsf2|__gtdf2|__ltdf2|__gedf2|__ledf2|\
        __eqdf2|__nedf2|fma|pow|ceil|floor|sqrt|sin|cos|tan|log|exp)
            return 0 ;;
    esac
    [[ "$1" == __*df* ]] && return 0
    return 1
}

# 已知 POSIX → plc_* 或 stub 策略（供 report/modpost/merge 共用）
plc_remap_hint_for_sym() {
    case "$1" in
        printf) echo plc_printk ;;
        puts) echo plc_puts ;;
        fprintf) echo plc_fprintf ;;
        dprintf) echo plc_dprintf ;;
        warn) echo plc_warn ;;
        info) echo plc_info ;;
        fatal) echo plc_fatal ;;
        err_msg) echo plc_warn ;;
        err_msg_n) echo plc_err_msg_n ;;
        perror) echo plc_perror ;;
        clock_gettime|clock_getres) echo plc_ktime_get_ts ;;
        nanosleep) echo plc_nanosleep ;;
        clock_nanosleep) echo plc_clock_nanosleep ;;
        malloc) echo plc_kmalloc ;;
        calloc) echo plc_kcalloc ;;
        realloc) echo plc_krealloc ;;
        strdup) echo plc_kstrdup ;;
        free) echo plc_kfree ;;
        exit|_exit|abort|raise) echo plc_exit ;;
        gettimeofday) echo plc_gettimeofday ;;
        getpid) echo plc_getpid ;;
        gettid) echo plc_gettid ;;
        open|read|write|close|mmap|munmap|lseek|ftruncate|stat|unlink|mkfifo|\
        shm_open|shm_unlink|fopen|fclose|fdopen)
            echo "plc_${1}" ;;
        pthread_self) echo plc_pthread_self ;;
        pthread_create) echo plc_pthread_create ;;
        pthread_join) echo plc_pthread_join ;;
        pthread_kill) echo plc_pthread_kill ;;
        pthread_mutex_init) echo plc_mutex_init ;;
        pthread_mutex_destroy) echo plc_mutex_destroy ;;
        pthread_mutex_lock) echo plc_mutex_lock ;;
        pthread_mutex_unlock) echo plc_mutex_unlock ;;
        pthread_cond_wait) echo plc_cond_wait ;;
        pthread_cond_signal) echo plc_cond_signal ;;
        pthread_cond_broadcast) echo plc_cond_broadcast ;;
        pthread_cond_timedwait) echo plc_cond_timedwait ;;
        pthread_barrier_init) echo plc_barrier_init ;;
        pthread_barrier_wait) echo plc_barrier_wait ;;
        pthread_setaffinity_np) echo plc_pthread_setaffinity_np ;;
        pthread_sigmask|sigprocmask) echo plc_sigprocmask ;;
        sched_setscheduler) echo plc_setscheduler ;;
        sched_setaffinity) echo plc_sched_setaffinity ;;
        mlockall|munlockall|mlock) echo "plc_${1}" ;;
        timer_create|timer_settime|timer_getoverrun|timer_delete) echo "plc_${1}" ;;
        sigemptyset|sigaddset|sigwait|signal|sigaction) echo "plc_${1}" ;;
        sched_yield|atexit) echo stub:noop ;;
        getenv) echo stub:null ;;
        *) return 1 ;;
    esac
}

# 从 Kbuild/modpost 日志提取 undefined 符号
plc_parse_modpost_undefined() {
    local log="$1"
    [ -f "$log" ] || return 1
    grep -oE 'undefined symbol: [A-Za-z_][A-Za-z0-9_]*|undefined![[:space:]]+[A-Za-z_][A-Za-z0-9_]*' "$log" 2>/dev/null | \
        sed -E 's/undefined symbol: //;s/undefined![[:space:]]+//' | sort -u
}

plc_on_err() {
    local rc=$?
    local line="${BASH_LINENO[0]:-?}"
    local cmd="${BASH_COMMAND:-?}"
    echo "" >&2
    echo "❌ 脚本意外退出 (rc=${rc}) 于第 ${line} 行" >&2
    echo "   💡 失败命令: ${cmd}" >&2
    echo "   💡 设置 bash -x scripts/... 查看详细 trace" >&2
    exit "$rc"
}

# 主脚本可: trap plc_on_err ERR
plc_enable_err_trap() {
    trap 'plc_on_err' ERR
}

# scripts/ 根目录（本库在 scripts/fuse/ 内时返回上级，供 WCET Pass 库等）
plc_scripts_root() {
    local d
    d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ "$(basename "$d")" = fuse ]; then
        cd "$d/.." && pwd
    else
        echo "$d"
    fi
}

# manifest 源码根（与 plc_fuse [2/6] 一致）
plc_fusion_resolve_src_root() {
    local project_root="$1"
    local work_dir="${FUSE_WORK_DIR:-$project_root/test}"
    local root
    if [ -n "${FUSE_SRC_ROOT:-}" ]; then
        if [[ "$FUSE_SRC_ROOT" = /* ]]; then
            root="$FUSE_SRC_ROOT"
        else
            root="$project_root/$FUSE_SRC_ROOT"
        fi
    else
        root="$work_dir"
    fi
    if [ -n "${FUSE_GIT_DIR:-}" ]; then
        root="$work_dir/$FUSE_GIT_DIR"
    fi
    echo "$root"
}

plc_fusion_is_plc_cc_manifest() {
    [[ "${FUSE_NAME:-}" == plc_cc_* ]] || [[ "${FUSE_SOURCE:-}" == *plc-cc* ]]
}

# plc_ast 传给 Clang 的额外参数（对齐融合：FUSE_CLANG_FLAGS + FUSE_INCLUDE_DIRS）
# 用法: SRC_ROOT=$(plc_fusion_resolve_src_root "$PROJECT_ROOT"); plc_fusion_ast_extra_clang "$PROJECT_ROOT" "$SRC_ROOT"
#       "$PLC_AST" ... -- "${PLC_FUSION_AST_EXTRA_CLANG[@]}"
plc_fusion_ast_extra_clang() {
    local project_root="$1"
    local src_root="$2"
    PLC_FUSION_AST_EXTRA_CLANG=()
    if plc_fusion_is_plc_cc_manifest; then
        return 0
    fi
    if [ -n "${FUSE_CLANG_FLAGS:-}" ]; then
        # shellcheck disable=SC2206
        PLC_FUSION_AST_EXTRA_CLANG=($FUSE_CLANG_FLAGS)
    fi
    if [ -n "${FUSE_INCLUDE_DIRS:-}" ]; then
        local d inc_path
        for d in $FUSE_INCLUDE_DIRS; do
            if [[ "$d" = /* ]]; then
                inc_path="$d"
            else
                inc_path="$src_root/$d"
            fi
            if [ -d "$inc_path" ]; then
                PLC_FUSION_AST_EXTRA_CLANG+=(-I"$inc_path")
            fi
        done
    fi
    local clang_bin="" candidate res
    plc_prepend_llvm_path
    for candidate in clang-19 clang-18 clang-17 clang; do
        if command -v "$candidate" >/dev/null 2>&1; then
            clang_bin="$candidate"
            break
        fi
    done
    if [ -n "$clang_bin" ]; then
        res="$("$clang_bin" -print-resource-dir 2>/dev/null || true)"
        if [ -n "$res" ] && [ -d "$res/include" ]; then
            PLC_FUSION_AST_EXTRA_CLANG+=(-isystem "$res/include")
        fi
    fi
}

plc_fusion_ast_use_no_shim() {
    ! plc_fusion_is_plc_cc_manifest
}

# Clang 非 0 退出时 JSON 是否仍可信（entry 或 has_main 已解析）
plc_fusion_ast_json_parse_ok() {
    local json="$1"
    python3 - "$json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("0")
    raise SystemExit
entry = (d.get("entry") or "").strip()
has_main = bool(d.get("has_main"))
eligible = bool(d.get("fusion_eligible"))
crit = int(d.get("fusion_critical_count") or 0)
print("1" if eligible and crit == 0 and (entry or has_main) else "0")
PY
}
