#!/bin/bash
# ============================================================================
# plc_fuse_add_interactive__交互接入.sh — 交互式源码接入与内核化向导
# ============================================================================
# 用法: bash scripts/plc_fuse_add_interactive__交互接入.sh
# 说明: 须在交互终端运行（需 TTY）；非交互请用 plc_fuse_add --help
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=fuse/plc_fusion_common__公共库.sh
source "$SCRIPT_DIR/fuse/plc_fusion_common__公共库.sh"

PROJECT_ROOT="$(plc_project_root)"
ADD="$SCRIPT_DIR/plc_fuse_add__接入应用.sh"
readonly UI_WIDTH=64

if [ ! -t 0 ] || [ ! -t 1 ]; then
    plc_die "$PLC_E_USAGE" "交互向导需要在终端中运行" \
        "请执行: bash scripts/plc_fuse_add_interactive__交互接入.sh" \
        "或: bash scripts/plc_fuse_add__接入应用.sh --help"
fi

# --- 终端 UI（无 tput 时自动降级为纯文本）---
UI_RESET="" UI_BOLD="" UI_DIM="" UI_CYAN="" UI_GREEN="" UI_YELLOW="" UI_RED="" UI_BLUE=""

ui_init() {
    if command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
        UI_RESET=$(tput sgr0)
        UI_BOLD=$(tput bold)
        UI_DIM=$(tput dim 2>/dev/null || true)
        UI_CYAN=$(tput setaf 6)
        UI_GREEN=$(tput setaf 2)
        UI_YELLOW=$(tput setaf 3)
        UI_RED=$(tput setaf 1)
        UI_BLUE=$(tput setaf 4)
    fi
}

ui_line() {
    local ch="${1:--}"
    printf '%*s' "$UI_WIDTH" '' | tr ' ' "$ch"
}

ui_box_top() {
    printf "${UI_DIM}  +"
    ui_line "-"
    printf "+${UI_RESET}\n"
}
ui_box_bottom() {
    printf "${UI_DIM}  +"
    ui_line "-"
    printf "+${UI_RESET}\n"
}
ui_box_row() {
    # shellcheck disable=SC2059
    printf "${UI_DIM}  |${UI_RESET} %-$((UI_WIDTH - 2))s ${UI_DIM}|${UI_RESET}\n" "$1"
}

ui_title() { echo "${UI_BOLD}${UI_CYAN}$*${UI_RESET}"; }
ui_ok()    { echo "  ${UI_GREEN}[OK]${UI_RESET}  $*"; }
ui_warn()  { echo "  ${UI_YELLOW}[!]${UI_RESET}  $*" >&2; }
ui_err()   { echo "  ${UI_RED}[X]${UI_RESET}  $*" >&2; }
ui_hint()  { echo "  ${UI_DIM}$*${UI_RESET}"; }

ui_section() {
    echo ""
    printf "${UI_DIM}  "
    ui_line "-"
    echo "${UI_RESET}"
    ui_title "  $1"
    printf "${UI_DIM}  "
    ui_line "-"
    echo "${UI_RESET}"
    echo ""
}

ui_step() {
    echo ""
    ui_hint "步骤 $1/$2 - $3"
    echo ""
}

# --- 交互辅助 ---
prompt() {
    local msg="$1"
    local default="${2:-}"
    local ans
    if [ -n "$default" ]; then
        printf "${UI_BOLD}${UI_BLUE}?${UI_RESET} %s ${UI_DIM}[%s]${UI_RESET}: " "$msg" "$default" >&2
        read -r ans || ans=""
        echo "${ans:-$default}"
    else
        printf "${UI_BOLD}${UI_BLUE}?${UI_RESET} %s: " "$msg" >&2
        read -r ans || ans=""
        echo "$ans"
    fi
}

prompt_yn() {
    local msg="$1"
    local default="${2:-n}"
    local hint="y/N"
    [ "$default" = "y" ] && hint="Y/n"
    local ans
    printf "${UI_BOLD}${UI_BLUE}?${UI_RESET} %s ${UI_DIM}(%s)${UI_RESET}: " "$msg" "$hint" >&2
    read -r ans || return 1
    ans="${ans:-$default}"
    case "${ans,,}" in
        y|yes|是) return 0 ;;
        *) return 1 ;;
    esac
}

prompt_name() {
    local name
    while true; do
        name="$(prompt "应用名 FUSE_NAME（字母开头，如 my_app）")"
        if [[ "$name" =~ ^[a-zA-Z][a-zA-Z0-9_]*$ ]]; then
            echo "$name"
            return 0
        fi
        ui_warn "名称无效，请使用字母/数字/下划线且以字母开头"
    done
}

expand_path() {
    local p="$1"
    p="${p/#\~/$HOME}"
    if [[ "$p" != /* ]]; then
        p="$PROJECT_ROOT/$p"
    fi
    readlink -f "$p" 2>/dev/null || realpath "$p" 2>/dev/null || echo "$p"
}

list_c_examples() {
    find "$PROJECT_ROOT/examples" "$PROJECT_ROOT/test" -name '*.c' 2>/dev/null \
        | head -16 | while read -r f; do
        python3 - "$f" "$PROJECT_ROOT" <<'PY'
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))
PY
    done
}

pick_from_list() {
    local prompt_msg="$1"
    shift
    local -a items=("$@")
    local n="${#items[@]}"
    if [ "$n" -eq 0 ]; then
        echo ""
        return 1
    fi
    echo "" >&2
    ui_title "  $prompt_msg" >&2
    echo "" >&2
    local i pick
    for i in "${!items[@]}"; do
        printf "${UI_DIM}  %2d)${UI_RESET} %s\n" "$((i + 1))" "${items[$i]}" >&2
    done
    echo "" >&2
    while true; do
        pick="$(prompt "输入序号 1-${n}，或直接输入路径")"
        if [ -z "$pick" ]; then
            echo ""
            return 1
        fi
        if [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "$n" ]; then
            echo "${items[$((pick - 1))]}"
            return 0
        fi
        echo "$pick"
        return 0
    done
}

show_banner() {
    clear 2>/dev/null || true
    echo ""
    ui_box_top
    ui_box_row "PLCFusion - 源码接入向导"
    ui_box_row ""
    ui_box_row "  源码 -> manifest -> kernel.o -> .ko"
    ui_box_row "                      \\-> insmod (可选)"
    ui_box_bottom
    echo ""
    ui_hint "项目  $PROJECT_ROOT"
    ui_hint "退出  主菜单选 6，或 Ctrl+C"
    echo ""
}

show_main_menu() {
    ui_section "主菜单 - 选择接入方式"
    printf "${UI_DIM}  %2s${UI_RESET}  %-28s  %s\n" "1" "本地 .c 文件" "${UI_DIM}单文件${UI_RESET}"
    printf "${UI_DIM}  %2s${UI_RESET}  %-28s  %s\n" "2" "本地项目目录" "${UI_DIM}多文件 / 相对路径${UI_RESET}"
    printf "${UI_DIM}  %2s${UI_RESET}  %-28s  %s\n" "3" "Git 仓库" "${UI_DIM}clone + 融合${UI_RESET}"
    printf "${UI_DIM}  %2s${UI_RESET}  %-28s  %s\n" "4" "仓库内示例" "${UI_GREEN}推荐首次体验${UI_RESET}"
    printf "${UI_DIM}  %2s${UI_RESET}  %-28s  %s\n" "5" "已有 manifest" "${UI_DIM}重新融合 / insmod${UI_RESET}"
    printf "${UI_DIM}  %2s${UI_RESET}  %-28s  %s\n" "6" "退出" ""
    echo ""
}

show_run_mode_menu() {
    ui_section "运行模式"
    printf "  ${UI_BOLD}1${UI_RESET}  完整流水线     融合 + 链接 .ko ${UI_DIM}(默认)${UI_RESET}\n"
    printf "  ${UI_BOLD}2${UI_RESET}  仅 manifest    生成配置，暂不融合\n"
    printf "  ${UI_BOLD}3${UI_RESET}  仅 kernel.o    融合到 .o，不链模块\n"
    printf "  ${UI_BOLD}4${UI_RESET}  完整 + insmod  构建后进内核短测 ${UI_DIM}(需 sudo)${UI_RESET}\n"
    echo ""
}

ui_mode_label() {
    case "$1" in
        2) echo "仅 manifest (dry-run)" ;;
        3) echo "仅 kernel.o (fuse-only)" ;;
        4) echo "完整 + insmod 短测" ;;
        *) echo "完整流水线 (fuse + .ko)" ;;
    esac
}

ui_summarize_args() {
    local -n _a=$1
    local name="" mode="完整流水线" src="" git="" manifest=""
    local i=0
    while [ $i -lt ${#_a[@]} ]; do
        case "${_a[$i]}" in
            --name) name="${_a[$((i + 1))]}" ;;
            --local) src="文件: ${_a[$((i + 1))]}" ;;
            --local-dir) src="目录: ${_a[$((i + 1))]}" ;;
            --source) src="${src:+$src / }主源: ${_a[$((i + 1))]}" ;;
            --git) git="Git: ${_a[$((i + 1))]}" ;;
            --manifest) manifest="${_a[$((i + 1))]}" ;;
            --dry-run) mode="仅 manifest" ;;
            --fuse-only) mode="仅 kernel.o" ;;
            --insmod) mode="完整 + insmod" ;;
        esac
        i=$((i + 1))
    done
    ui_section "执行摘要"
    [ -n "$name" ] && ui_box_row "应用名    ${UI_BOLD}${name}${UI_RESET}"
    [ -n "$manifest" ] && ui_box_row "Manifest  $(basename "$manifest")"
    [ -n "$src" ] && ui_box_row "源码      ${src}"
    [ -n "$git" ] && ui_box_row "仓库      ${git}"
    ui_box_row "模式      ${mode}"
    echo ""
}

mode_local_file() {
    ui_section "接入 - 本地 .c 文件"
    ui_step 1 3 "选择源文件"
    mapfile -t samples < <(list_c_examples)
    local path
    if [ "${#samples[@]}" -gt 0 ]; then
        path="$(pick_from_list "仓库内 .c 示例（也可直接输入路径）" "${samples[@]}")"
    else
        path="$(prompt "源文件路径")"
    fi
    [ -n "$path" ] || return 1
    path="$(expand_path "$path")"
    if [ ! -f "$path" ]; then
        ui_err "文件不存在: $path"
        return 1
    fi
    ui_ok "已选 $(basename "$path")"
    ui_step 2 3 "应用标识"
    local name
    name="$(prompt_name)"
    local -a args=(--local "$path" --name "$name")
    if [[ "$path" != "$PROJECT_ROOT"/* ]]; then
        echo ""
        if prompt_yn "源码在项目外，复制到 test/vendor/${name}/ ?" "y"; then
            args+=(--copy)
            ui_ok "将复制到 test/vendor/${name}/"
        fi
    fi
    ui_step 3 3 "运行选项"
    collect_run_options args
    confirm_and_run args
}

mode_local_dir() {
    ui_section "接入 - 本地项目目录"
    ui_step 1 3 "源码根与主文件"
    local dir src name
    dir="$(prompt "源码根目录（绝对或相对路径）")"
    [ -n "$dir" ] || return 1
    dir="$(expand_path "$dir")"
    plc_require_dir "$dir" "源码目录"
    src="$(prompt "主源相对路径（如 src/main.c）")"
    [ -n "$src" ] || return 1
    plc_require_file "$dir/$src" "主源文件"
    ui_ok "根目录 $dir"
    ui_step 2 3 "应用标识"
    name="$(prompt_name)"
    local -a args=(--local-dir "$dir" --source "$src" --name "$name")
    if [[ "$dir" != "$PROJECT_ROOT"/* ]]; then
        echo ""
        if prompt_yn "复制整个目录到 test/vendor/${name}/ ?" "y"; then
            args+=(--copy)
        fi
    fi
    ui_step 3 3 "运行选项"
    collect_run_options args
    confirm_and_run args
}

mode_git() {
    ui_section "接入 - Git 仓库"
    ui_step 1 3 "仓库来源"
    echo ""
    printf "  ${UI_BOLD}a${UI_RESET}  rt-tests / cyclictest ${UI_DIM}(kernel.org 预设)${UI_RESET}\n"
    printf "  ${UI_BOLD}b${UI_RESET}  自定义 Git URL\n"
    echo ""
    local preset url git_dir source name branch
    preset="$(prompt "选择 a / b" "b")"
    case "${preset,,}" in
        a)
            url="git://git.kernel.org/pub/scm/utils/rt-tests/rt-tests.git"
            git_dir="rt-tests"
            source="src/cyclictest/cyclictest.c"
            name="my_cyclictest"
            ui_ok "预设 rt-tests / cyclictest"
            ui_hint "URL   $url"
            ui_hint "主源  $source"
            name="$(prompt "应用名 FUSE_NAME" "$name")"
            ;;
        *)
            url="$(prompt "Git 仓库 URL")"
            [ -n "$url" ] || return 1
            name="$(prompt_name)"
            git_dir="$(prompt "克隆目录 FUSE_GIT_DIR" "vendor_${name}")"
            source="$(prompt "主源相对路径 FUSE_SOURCE")"
            [ -n "$source" ] || return 1
            ;;
    esac
    ui_step 2 3 "克隆选项"
    branch="$(prompt "分支（回车=默认）" "")"
    local -a args=(--git "$url" --git-dir "$git_dir" --source "$source" --name "$name")
    [ -n "$branch" ] && args+=(--git-branch "$branch")
    local inc
    inc="$(prompt "include 目录（空格分隔，回车跳过）" "")"
    [ -n "$inc" ] && args+=(--include-dirs "$inc")
    if prompt_yn "目录已存在时 git pull 更新?" "y"; then
        args+=(--update)
    fi
    ui_step 3 3 "运行选项"
    collect_run_options args
    confirm_and_run args
}

mode_presets() {
    ui_section "接入 - 仓库内示例"
    ui_hint "适合第一次体验完整内核化流程"
    local -a labels=(
        "hello_plc      - plc-cc / plc_main 周期任务"
        "rt_periodic    - 1ms 周期 demo"
        "gpio_blink     - PLC GPIO 闪烁"
        "stb_sprintf    - sprintf 库 demo"
        "tacle_cover    - TACLeBench cover (MRTC 多路径)"
        "tacle_minver   - TACLeBench minver (浮点矩阵)"
        "plc_multitask  - 多任务优先级 + pthread + 定点"
    )
    local -a paths=(
        "examples/plc-cc__低抖动示例/hello_plc__入门示例.c"
        "test/github_demo__本地demo/rt_periodic__周期demo.c"
        "examples/plc-cc__低抖动示例/gpio_blink__GPIO闪烁.c"
        "test/github_demo__本地demo/stb_sprintf__sprintf_demo.c"
        "manifests/manifest_tacle_cover__路径覆盖.env"
        "manifests/manifest_tacle_minver__矩阵求逆.env"
        "manifests/manifest_plc_multitask__多任务优先级.env"
    )
    local -a names=(demo_hello demo_rt_periodic demo_gpio demo_sprintf tacle_cover tacle_minver plc_multitask)
    local -a kind=(local local local local manifest manifest manifest)
    local pick idx=-1 i
    pick="$(pick_from_list "选择一个示例" "${labels[@]}")"
    if [[ "$pick" =~ ^[0-9]+$ ]]; then
        idx=$((pick - 1))
    else
        for i in "${!labels[@]}"; do
            [ "$pick" = "${labels[$i]}" ] && idx=$i && break
        done
    fi
    if [ "$idx" -lt 0 ] || [ "$idx" -ge ${#paths[@]} ]; then
        ui_warn "已取消"
        return 1
    fi
    ui_ok "示例 ${names[$idx]}"
    local -a args=()
    if [ "${kind[$idx]}" = manifest ]; then
        args=(--manifest "$PROJECT_ROOT/${paths[$idx]}")
    else
        local name
        name="$(prompt "应用名 FUSE_NAME" "${names[$idx]}")"
        args=(--local "${paths[$idx]}" --name "$name")
    fi
    collect_run_options args
    confirm_and_run args
}

mode_reuse_manifest() {
    ui_section "接入 - 已有 manifest"
    mapfile -t manifests < <(find "$PROJECT_ROOT/manifests" -maxdepth 1 -name 'manifest_*.env' \
        ! -name '*template*' | sort)
    if [ "${#manifests[@]}" -eq 0 ]; then
        ui_err "manifests/ 下无可用 manifest"
        return 1
    fi
    local -a shown=()
    local m rel
    for m in "${manifests[@]}"; do
        rel="${m#"$PROJECT_ROOT/"}"
        shown+=("$rel")
    done
    local pick manifest
    pick="$(pick_from_list "选择 manifest" "${shown[@]}")"
    manifest="$pick"
    if [[ "$manifest" != /* ]]; then
        manifest="$PROJECT_ROOT/$manifest"
    fi
    if [ ! -f "$manifest" ]; then
        ui_err "未找到: $manifest"
        return 1
    fi
    ui_ok "$(basename "$manifest")"
    local -a args=(--manifest "$manifest")
    collect_run_options args
    confirm_and_run args
}

collect_run_options() {
    local -n _args=$1
    local desc mode
    desc="$(prompt "描述 FUSE_DESC（回车跳过）" "")"
    [ -n "$desc" ] && _args+=(--desc "$desc")
    show_run_mode_menu
    mode="$(prompt "选择模式" "1")"
    case "${mode:-1}" in
        2) _args+=(--dry-run) ;;
        3) _args+=(--fuse-only) ;;
        4)
            _args+=(--insmod)
            local sec
            sec="$(prompt "insmod 等待秒数" "5")"
            _args+=(--insmod-sec "$sec")
            ;;
        1|*) ;;
    esac
    ui_hint "模式 → $(ui_mode_label "${mode:-1}")"

    local name=""
    local i
    for ((i = 0; i < ${#_args[@]}; i++)); do
        if [ "${_args[$i]}" = "--name" ] && [ $((i + 1)) -lt ${#_args[@]} ]; then
            name="${_args[$((i + 1))]}"
            break
        fi
    done
    if [ -n "$name" ] && [ -f "$PROJECT_ROOT/manifests/manifest_${name}.env" ]; then
        echo ""
        if prompt_yn "manifest_${name}.env 已存在，覆盖?" "n"; then
            _args+=(--force)
        fi
    fi
}

confirm_and_run() {
    local -n _args=$1
    ui_summarize_args _args
    ui_box_top
    ui_box_row "命令预览"
    ui_box_bottom
    echo ""
    ui_hint "scripts/plc_fuse_add__接入应用.sh \\"
    local a
    for a in "${_args[@]}"; do
        printf "    ${UI_DIM}%q${UI_RESET}\n" "$a"
    done
    echo ""
    if ! prompt_yn "确认并开始执行?" "y"; then
        ui_warn "已取消，未执行"
        return 0
    fi
    echo ""
    printf "${UI_DIM}  "
    ui_line "="
    echo "${UI_RESET}"
    ui_title "  执行中..."
    printf "${UI_DIM}  "
    ui_line "="
    echo "${UI_RESET}"
    echo ""
    if bash "$ADD" "${_args[@]}"; then
        echo ""
        printf "${UI_DIM}  "
        ui_line "="
        echo "${UI_RESET}"
        ui_ok "全部完成"
        printf "${UI_DIM}  "
        ui_line "="
        echo "${UI_RESET}"
    else
        echo ""
        ui_err "执行失败，请查看上方日志"
        ui_hint "报告: ls test/*.fusion_report test/*.validate.json 2>/dev/null"
        return 1
    fi
}

# --- 主程序 ---
ui_init
show_banner

while true; do
    show_main_menu
    choice="$(prompt "请选择")"
    if [ -z "$choice" ]; then
        echo ""
        ui_ok "再见"
        exit 0
    fi
    case "$choice" in
        1) mode_local_file || true ;;
        2) mode_local_dir || true ;;
        3) mode_git || true ;;
        4) mode_presets || true ;;
        5) mode_reuse_manifest || true ;;
        6|q|Q|exit)
            echo ""
            ui_ok "再见"
            exit 0
            ;;
        *)
            ui_warn "无效选项: $choice"
            ;;
    esac
    echo ""
    if ! prompt_yn "返回主菜单?" "y"; then
        echo ""
        ui_ok "再见"
        exit 0
    fi
done
