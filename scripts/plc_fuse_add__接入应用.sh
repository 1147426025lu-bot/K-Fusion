#!/bin/bash
# ============================================================================
# plc_fuse_add__接入应用.sh — 源码接入 → manifest → 内核化 → .ko [→ insmod]
# ============================================================================
# 功能: 通过 git / 本地文件 / 本地目录 接入 C 源码，自动生成 manifest，
#       运行 plc_fuse + ignite_fused，可选短 insmod 验证。
#
# 用法:
#   # Git 上游
#   bash scripts/plc_fuse_add__接入应用.sh \
#     --git git://git.kernel.org/pub/scm/utils/rt-tests/rt-tests.git \
#     --git-dir rt-tests --source src/cyclictest/cyclictest.c \
#     --name my_cyclic --include-dirs src/include
#
#   # 仓库内或本机单文件
#   bash scripts/plc_fuse_add__接入应用.sh \
#     --local examples/plc-cc__低抖动示例/hello_plc__入门示例.c \
#     --name my_hello --insmod
#
#   # 本机目录 + 相对主源
#   bash scripts/plc_fuse_add__接入应用.sh \
#     --local-dir /path/to/project --source src/main.c --name my_app
#
#   # 复制外部源码到 test/vendor/<name>/ 再融合
#   bash scripts/plc_fuse_add__接入应用.sh \
#     --local /path/outside/foo.c --copy --name foo_vendor --insmod
#
#   # 已有 manifest，只跑融合 + ko + insmod
#   bash scripts/plc_fuse_add_interactive__交互接入.sh   # 交互向导
#
# 选项:
#   --name NAME           FUSE_NAME（接入新模式必填）
#   --desc TEXT           FUSE_DESC
#   --manifest PATH       输出/复用 manifest 路径
#   --git URL             FUSE_GIT_URL
#   --git-dir DIR         FUSE_GIT_DIR（默认 vendor_<name>）
#   --git-branch BR       FUSE_GIT_BRANCH
#   --git-depth N         浅克隆深度（默认 1）
#   --update              已 clone 时 git fetch/pull
#   --local FILE          单个 .c 源文件
#   --local-dir DIR       源码根目录
#   --copy                将 --local/--local-dir 复制到 test/vendor/<name>/
#   --source PATH         主源相对路径（git/local-dir 必填；--local 可省略）
#   --include-dirs DIRS   FUSE_INCLUDE_DIRS（空格分隔，引号包裹）
#   --extra-sources LIST  FUSE_EXTRA_SOURCES
#   --fuse-only           仅 plc_fuse，不链 .ko
#   --insmod              构建后短 insmod 测试（需 sudo）
#   --insmod-sec N        insmod 等待秒数（默认 5）
#   --skip-suggest        跳过 AST manifest 建议合并
#   --force               覆盖已存在 manifest
#   --dry-run             只生成/更新 manifest，不融合
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"
plc_enable_err_trap

PROJECT_ROOT="$(plc_project_root)"
FUSE="$SCRIPT_DIR/plc_fuse__内核化主流程.sh"
IGNITE="$SCRIPT_DIR/ignite_fused__通用ko构建.sh"
APPLY="$SCRIPT_DIR/plc_ast_apply_manifest__应用manifest建议.sh"
TEMPLATE="$PROJECT_ROOT/manifests/manifest_template__清单模板.env"

# --- 参数 ---
FUSE_NAME=""
FUSE_DESC=""
MANIFEST=""
GIT_URL=""
GIT_DIR=""
GIT_BRANCH=""
GIT_DEPTH="1"
GIT_UPDATE=0
LOCAL_FILE=""
LOCAL_DIR=""
DO_COPY=0
SOURCE_REL=""
INCLUDE_DIRS=""
EXTRA_SOURCES=""
FUSE_ONLY=0
DO_INSMOD=0
INSMOD_SEC=5
SKIP_SUGGEST=0
FORCE=0
DRY_RUN=0
REUSE_MANIFEST=0

usage() {
    plc_die "$PLC_E_USAGE" "参数不正确" \
        "用法: $0 --name <app> (--git URL --source path | --local file | --local-dir dir --source path)" \
        "      $0 --manifest manifests/manifest_xxx.env [--insmod]" \
        "      $0 --help"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) FUSE_NAME="$2"; shift 2 ;;
        --desc) FUSE_DESC="$2"; shift 2 ;;
        --manifest)
            MANIFEST="$2"
            REUSE_MANIFEST=1
            shift 2
            ;;
        --git) GIT_URL="$2"; shift 2 ;;
        --git-dir) GIT_DIR="$2"; shift 2 ;;
        --git-branch) GIT_BRANCH="$2"; shift 2 ;;
        --git-depth) GIT_DEPTH="$2"; shift 2 ;;
        --update) GIT_UPDATE=1; shift ;;
        --local) LOCAL_FILE="$2"; shift 2 ;;
        --local-dir) LOCAL_DIR="$2"; shift 2 ;;
        --copy) DO_COPY=1; shift ;;
        --source) SOURCE_REL="$2"; shift 2 ;;
        --include-dirs) INCLUDE_DIRS="$2"; shift 2 ;;
        --extra-sources) EXTRA_SOURCES="$2"; shift 2 ;;
        --fuse-only) FUSE_ONLY=1; shift ;;
        --insmod) DO_INSMOD=1; shift ;;
        --insmod-sec) INSMOD_SEC="$2"; shift 2 ;;
        --skip-suggest) SKIP_SUGGEST=1; shift ;;
        --force) FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '1,55p' "$0" | tail -n +3
            exit 0
            ;;
        *) usage ;;
    esac
done

plc_abs_path() {
    local p="$1"
    if [[ "$p" != /* ]]; then
        p="$PROJECT_ROOT/$p"
    fi
    readlink -f "$p" 2>/dev/null || realpath "$p" 2>/dev/null || echo "$p"
}

plc_rel_to_project() {
    local abs="$1"
    python3 - "$abs" "$PROJECT_ROOT" <<'PY'
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))
PY
}

validate_name() {
    if [[ ! "$FUSE_NAME" =~ ^[a-zA-Z][a-zA-Z0-9_]*$ ]]; then
        plc_die "$PLC_E_ARGS" "非法 FUSE_NAME: $FUSE_NAME" \
            "仅允许字母/数字/下划线，且以字母开头"
    fi
}

ensure_build_tools() {
    if [ "$DRY_RUN" = "1" ] && [ "$SKIP_SUGGEST" = "1" ]; then
        return 0
    fi
    if [ ! -x "$PROJECT_ROOT/build/plc_ast" ] || [ ! -f "$PROJECT_ROOT/build/PLCFusionPass.so" ]; then
        echo "🛠️  编译 plc_ast + PLCFusionPass..."
        (cd "$PROJECT_ROOT/build" && cmake .. >/dev/null \
            && make plc_ast PLCFusionPass PLCLowJitterPass -j"$(nproc)" >/dev/null)
    fi
}

write_manifest_header() {
    local path="$1"
    cat > "$path" <<EOF
# 由 plc_fuse_add 生成 — $(date -Iseconds 2>/dev/null || date)
# 接入: ${GIT_URL:-local}${LOCAL_FILE:+ $LOCAL_FILE}${LOCAL_DIR:+ $LOCAL_DIR}
FUSE_NAME=${FUSE_NAME}
FUSE_DESC="${FUSE_DESC:-${FUSE_NAME} (plc_fuse_add)}"

EOF
}

append_manifest_kv() {
    local path="$1"
    local key="$2"
    local val="$3"
    if [[ "$val" == *" "** ]] || [[ "$val" == *"'"* ]]; then
        val="${val//\'/\'\\\'\'}"
        echo "${key}='${val}'" >> "$path"
    else
        echo "${key}=${val}" >> "$path"
    fi
}

finalize_manifest_defaults() {
    local path="$1"
    cat >> "$path" <<'EOF'

FUSE_PIPELINE_POLICY=ast-auto
FUSE_PIPELINE=auto
FUSE_LINK_RUNTIME_STUBS=1
FUSE_LINK_PTHREAD_HOST=1
FUSE_GLOBALIZE_SYMBOLS='shutdown'
FUSE_FIXED_POINT=1
FUSE_AUTO_DETECT=1
FUSE_AUTO_STUBS=1
FUSE_PREFLIGHT=1
FUSE_AST_PREFLIGHT=1
FUSE_AST_PLAN=1
FUSE_LLC_ARCH=aarch64
FUSE_LLC_ATTR=-fp-armv8,-neon
EOF
    if [ "$SKIP_SUGGEST" = "1" ]; then
        echo "FUSE_CLANG_FLAGS='-O2 -fno-builtin -D_GNU_SOURCE'" >> "$path"
    fi
}

generate_manifest() {
    validate_name
    MANIFEST="${MANIFEST:-$PROJECT_ROOT/manifests/manifest_${FUSE_NAME}.env}"

    if [ -f "$MANIFEST" ] && [ "$FORCE" != "1" ]; then
        plc_die "$PLC_E_MANIFEST" "manifest 已存在: $MANIFEST" \
            "使用 --force 覆盖，或 --manifest 指定新路径"
    fi

    local mode=0
    [ -n "$GIT_URL" ] && mode=$((mode + 1))
    [ -n "$LOCAL_FILE" ] && mode=$((mode + 1))
    [ -n "$LOCAL_DIR" ] && mode=$((mode + 1))
    if [ "$mode" -ne 1 ]; then
        plc_die "$PLC_E_ARGS" "请指定且仅指定一种接入方式: --git | --local | --local-dir"
    fi

    local src_root="" source_path="" git_dir=""

    if [ -n "$GIT_URL" ]; then
        git_dir="${GIT_DIR:-vendor_${FUSE_NAME}}"
        [ -n "$SOURCE_REL" ] || plc_die "$PLC_E_ARGS" "--git 模式需要 --source（相对 FUSE_GIT_DIR 的路径）"
        write_manifest_header "$MANIFEST"
        append_manifest_kv "$MANIFEST" "FUSE_GIT_URL" "$GIT_URL"
        append_manifest_kv "$MANIFEST" "FUSE_GIT_DIR" "$git_dir"
        append_manifest_kv "$MANIFEST" "FUSE_GIT_DEPTH" "$GIT_DEPTH"
        [ -n "$GIT_BRANCH" ] && append_manifest_kv "$MANIFEST" "FUSE_GIT_BRANCH" "$GIT_BRANCH"
        [ "$GIT_UPDATE" = "1" ] && append_manifest_kv "$MANIFEST" "FUSE_GIT_UPDATE" "1"
        append_manifest_kv "$MANIFEST" "FUSE_SOURCE" "$SOURCE_REL"
        src_root="$PROJECT_ROOT/test/$git_dir"
        source_path="$src_root/$SOURCE_REL"
    elif [ -n "$LOCAL_FILE" ]; then
        local abs
        abs="$(plc_abs_path "$LOCAL_FILE")"
        plc_require_file "$abs" "本地源文件"
        local base
        base="$(basename "$abs")"
        SOURCE_REL="${SOURCE_REL:-$base}"

        if [ "$DO_COPY" = "1" ]; then
            local vendor="$PROJECT_ROOT/test/vendor/${FUSE_NAME}"
            plc_ensure_dir "$vendor"
            cp -f "$abs" "$vendor/$base"
            src_root="$vendor"
            append_src_root="test/vendor/${FUSE_NAME}"
        elif [[ "$abs" == "$PROJECT_ROOT"/* ]]; then
            src_root="$(dirname "$abs")"
            append_src_root="$(plc_rel_to_project "$src_root")"
            SOURCE_REL="$base"
        else
            src_root="$(dirname "$abs")"
            append_src_root="$src_root"
            SOURCE_REL="$base"
        fi
        source_path="$src_root/$SOURCE_REL"
        write_manifest_header "$MANIFEST"
        append_manifest_kv "$MANIFEST" "FUSE_SRC_ROOT" "$append_src_root"
        append_manifest_kv "$MANIFEST" "FUSE_SOURCE" "$SOURCE_REL"
    else
        local abs_dir
        abs_dir="$(plc_abs_path "$LOCAL_DIR")"
        plc_require_dir "$abs_dir" "本地源码目录"
        [ -n "$SOURCE_REL" ] || plc_die "$PLC_E_ARGS" "--local-dir 模式需要 --source"

        if [ "$DO_COPY" = "1" ]; then
            local vendor="$PROJECT_ROOT/test/vendor/${FUSE_NAME}"
            plc_ensure_dir "$vendor"
            cp -a "$abs_dir/." "$vendor/"
            append_src_root="test/vendor/${FUSE_NAME}"
            source_path="$vendor/$SOURCE_REL"
        elif [[ "$abs_dir" == "$PROJECT_ROOT"/* ]]; then
            append_src_root="$(plc_rel_to_project "$abs_dir")"
            source_path="$abs_dir/$SOURCE_REL"
        else
            append_src_root="$abs_dir"
            source_path="$abs_dir/$SOURCE_REL"
        fi
        write_manifest_header "$MANIFEST"
        append_manifest_kv "$MANIFEST" "FUSE_SRC_ROOT" "$append_src_root"
        append_manifest_kv "$MANIFEST" "FUSE_SOURCE" "$SOURCE_REL"
    fi

    plc_require_file "$source_path" "主源文件" \
        "检查 --source 路径是否正确"

    [ -n "$INCLUDE_DIRS" ] && append_manifest_kv "$MANIFEST" "FUSE_INCLUDE_DIRS" "$INCLUDE_DIRS"
    [ -n "$EXTRA_SOURCES" ] && append_manifest_kv "$MANIFEST" "FUSE_EXTRA_SOURCES" "$EXTRA_SOURCES"
    finalize_manifest_defaults "$MANIFEST"

    echo "📝 manifest → $MANIFEST"
    echo "    source=$source_path"

    if [ "$SKIP_SUGGEST" != "1" ]; then
        echo "🧠 AST manifest 建议（fill-empty）..."
        if bash "$APPLY" "$MANIFEST"; then
            :
        else
            plc_warn "apply-manifest 未完全成功，继续用基础 manifest" \
                "可稍后手动: bash $APPLY $MANIFEST --dry-run"
        fi
        plc_fuse_add_patch_manifest_from_ast "$MANIFEST"
    fi
}

plc_fuse_add_patch_manifest_from_ast() {
    local manifest="$1"
    # shellcheck disable=SC1090
    source "$manifest"
    local json="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}/${FUSE_NAME}.apply_ast.json"
    [ -f "$json" ] || return 0
    python3 - "$manifest" "$json" <<'PY'
import json, re, sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
json_path = Path(sys.argv[2])
data = json.loads(json_path.read_text(encoding="utf-8"))
text = manifest_path.read_text(encoding="utf-8")

def has_key(k):
    return re.search(rf"^{re.escape(k)}=", text, re.M) is not None

extras = []
entry = data.get("entry") or ""
has_main = bool(data.get("has_main"))
if entry and not has_key("FUSE_KTHREAD_ENTRY") and not has_key("FUSE_RUN_MAIN"):
    extras += [
        f"FUSE_KTHREAD_ENTRY={entry}",
        f"FUSE_HOT_PATH_FUNCTIONS={entry}",
        f"FUSE_DCE_ROOTS={entry}",
        "FUSE_HOST=hrtimer",
    ]
elif has_main and not has_key("FUSE_KTHREAD_ENTRY") and not has_key("FUSE_RUN_MAIN"):
    extras += [
        "FUSE_KTHREAD_ENTRY=main",
        "FUSE_HOT_PATH_FUNCTIONS=main",
        "FUSE_DCE_ROOTS=main",
        "FUSE_HOST=hrtimer",
        "FUSE_LINK_PTHREAD_HOST=0",
    ]
if not has_key("FUSE_CLANG_FLAGS"):
    extras.append("FUSE_CLANG_FLAGS='-O2 -fno-builtin -D_GNU_SOURCE'")
if not has_key("FUSE_WCET_MODE"):
    extras.append("FUSE_WCET_MODE=1")

if not extras:
    sys.exit(0)
block = "\n# plc_fuse_add AST 补全\n" + "\n".join(extras) + "\n"
manifest_path.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
print("    AST 补全:", ", ".join(x.split("=")[0] for x in extras))
PY
}

run_fuse_pipeline() {
    plc_resolve_manifest "$MANIFEST" "$PROJECT_ROOT"
    MANIFEST="$PLC_MANIFEST"
    plc_require_file "$MANIFEST" "manifest"

    # 接入流程只在生成 manifest 时写建议；融合阶段不再改 manifest
    export FUSE_AST_APPLY_SUGGEST=0

    echo ""
    echo "=== plc_fuse_add: $(basename "$MANIFEST") ==="
    echo "    manifest=$MANIFEST"
    echo "    fuse_only=$FUSE_ONLY insmod=$DO_INSMOD"

    if [ -n "${FUSE_GIT_URL:-}" ]; then
        # shellcheck disable=SC1090
        source "$MANIFEST"
        local dest="${FUSE_WORK_DIR:-$PROJECT_ROOT/test}/${FUSE_GIT_DIR}"
        plc_git_sync "${FUSE_GIT_URL}" "$dest" "${FUSE_GIT_DEPTH:-1}" \
            "${FUSE_GIT_BRANCH:-}" "${FUSE_GIT_UPDATE:-0}"
    fi

    echo "🔮 [1/2] plc_fuse..."
    bash "$FUSE" "$MANIFEST"

    if [ "$FUSE_ONLY" = "1" ]; then
        echo "✅ 融合完成（--fuse-only，未构建 .ko）"
        return 0
    fi

    echo "📦 [2/2] ignite_fused → .ko..."
    bash "$IGNITE" "$MANIFEST"

    # shellcheck disable=SC1090
    source "$MANIFEST"
    local ko="$PROJECT_ROOT/test/${FUSE_NAME}_mod.ko"
    plc_require_file "$ko" "内核模块 .ko"
    echo "    ko=$ko"

    if [ "$DO_INSMOD" = "1" ]; then
        do_insmod_test "$MANIFEST" "$INSMOD_SEC"
    fi

    echo ""
    echo "✅ 接入完成"
    echo "    manifest: $MANIFEST"
    echo "    kernel.o: test/${FUSE_NAME}_kernel.o"
    echo "    .ko:      test/${FUSE_NAME}_mod.ko"
    echo "    加载:     sudo insmod test/${FUSE_NAME}_mod.ko"
    echo "    卸载:     bash scripts/safe_rmmod_fused__安全卸载.sh ${FUSE_NAME}_mod"
    echo "    报告:     bash scripts/plc_fuse_fusion_report__一页报告.sh $MANIFEST"
}

do_insmod_test() {
    local manifest="$1"
    local wait_sec="${2:-5}"
    # shellcheck disable=SC1090
    source "$manifest"
    local mod="${FUSE_NAME}_mod"
    local ko="$PROJECT_ROOT/test/${mod}.ko"

    plc_check_sudo 1
    echo "🔌 insmod 短测 (${wait_sec}s)..."

    if lsmod | awk '{print $1}' | grep -qx "$mod"; then
        echo 1 | sudo tee "/sys/module/${mod}/parameters/shutdown_request" >/dev/null 2>&1 || true
        bash "$SCRIPT_DIR/safe_rmmod_fused__安全卸载.sh" "$mod" 2>/dev/null || sudo rmmod "$mod" 2>/dev/null || true
    fi

    if ! sudo insmod "$ko"; then
        plc_die "$PLC_E_KMOD" "insmod 失败: $mod" \
            "检查 dmesg | tail" \
            "检查 test/${FUSE_NAME}.kbuild.log"
    fi

    sleep "$wait_sec"
    if [ -r /sys/kernel/debug/fused_stats ]; then
        echo "--- fused_stats ---"
        head -20 /sys/kernel/debug/fused_stats 2>/dev/null || true
    fi
    if [ -f "/sys/module/${mod}/parameters/shutdown_request" ]; then
        echo 1 | sudo tee "/sys/module/${mod}/parameters/shutdown_request" >/dev/null
        sleep 1
    fi
    bash "$SCRIPT_DIR/safe_rmmod_fused__安全卸载.sh" "$mod" 2>/dev/null || sudo rmmod "$mod"
    echo "✅ insmod 短测通过"
}

# --- main ---
if [ "$REUSE_MANIFEST" = "1" ]; then
    [[ "$MANIFEST" != /* ]] && MANIFEST="$PROJECT_ROOT/$MANIFEST"
    plc_require_file "$MANIFEST" "manifest"
    # shellcheck disable=SC1090
    source "$MANIFEST"
    FUSE_NAME="${FUSE_NAME:-?}"
    if [ "$DRY_RUN" = "1" ]; then
        echo "    reuse manifest=$MANIFEST (dry-run, 无操作)"
        exit 0
    fi
    ensure_build_tools
    run_fuse_pipeline
    exit 0
fi

[ -n "$FUSE_NAME" ] || usage
ensure_build_tools
generate_manifest

if [ "$DRY_RUN" = "1" ]; then
    echo "✅ dry-run: manifest 已写入，未融合"
    echo "    下一步: bash $0 --manifest $MANIFEST [--insmod]"
    exit 0
fi

run_fuse_pipeline
