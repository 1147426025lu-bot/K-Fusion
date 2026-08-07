#!/bin/bash
# cgroup v2：PLC/RT 独占 CPU3，家务任务 CPU0-2（与 isolcpus 主线一致）
set -euo pipefail

ACTION="${1:-setup}"
CGROUP_ROOT="${CGROUP_ROOT:-/sys/fs/cgroup}"
PROBE_CPU="${JITTER_PROBE_CPU:-3}"
HK_CPUS="${HOUSEKEEPING_CPUS:-0-2}"
PLC_SLICE="${PLC_CGROUP_NAME:-plcrt}"
HK_SLICE="${HK_CGROUP_NAME:-housekeeping}"

PLC_DIR="${CGROUP_ROOT}/${PLC_SLICE}"
HK_DIR="${CGROUP_ROOT}/${HK_SLICE}"

sudo_cmd() {
	if sudo -n true 2>/dev/null; then
		sudo -n "$@"
	else
		"$@"
	fi
}

cgroup_write() {
	local path="$1"
	local val="$2"
	if [ ! -e "$path" ]; then
		echo "   ⚠️ 不存在: $path"
		return 1
	fi
	echo "$val" | sudo_cmd tee "$path" >/dev/null
}

enable_controllers() {
	if [ -f "${CGROUP_ROOT}/cgroup.subtree_control" ]; then
		cgroup_write "${CGROUP_ROOT}/cgroup.subtree_control" "+cpuset +memory" || true
	fi
}

setup_plc_cgroup() {
	if [ ! -d "$CGROUP_ROOT" ]; then
		echo "❌ 无 cgroup v2: $CGROUP_ROOT"
		return 1
	fi

	echo "🧩 [CGROUP] PLC=${PLC_SLICE} cpus=${PROBE_CPU} | HK=${HK_SLICE} cpus=${HK_CPUS}"

	sudo_cmd mkdir -p "$PLC_DIR" "$HK_DIR"
	enable_controllers

	if [ -f "${PLC_DIR}/cgroup.subtree_control" ]; then
		cgroup_write "${PLC_DIR}/cgroup.subtree_control" "+cpuset" 2>/dev/null || true
	fi
	if [ -f "${HK_DIR}/cgroup.subtree_control" ]; then
		cgroup_write "${HK_DIR}/cgroup.subtree_control" "+cpuset" 2>/dev/null || true
	fi

	cgroup_write "${PLC_DIR}/cpuset.cpus" "${PROBE_CPU}" || true
	cgroup_write "${PLC_DIR}/cpuset.mems" "0" 2>/dev/null || true
	cgroup_write "${HK_DIR}/cpuset.cpus" "${HK_CPUS}" || true
	cgroup_write "${HK_DIR}/cpuset.mems" "0" 2>/dev/null || true

	if [ -f "${HK_DIR}/memory.high" ]; then
		cgroup_write "${HK_DIR}/memory.high" "max" 2>/dev/null || true
	fi

	if [ -w "${HK_DIR}/cgroup.procs" ]; then
		echo "$$" | sudo_cmd tee "${HK_DIR}/cgroup.procs" >/dev/null 2>&1 || true
		echo "   ✅ 当前 shell → housekeeping cgroup"
	fi

	echo "   ℹ️  RT 模块 kthread 由内核 pin 到 CPU${PROBE_CPU}；用户态: echo PID > ${PLC_DIR}/cgroup.procs"
}

teardown_plc_cgroup() {
	echo "🧩 [CGROUP] teardown（保留目录）"
}

case "$ACTION" in
	setup) setup_plc_cgroup ;;
	teardown) teardown_plc_cgroup ;;
	*) echo "用法: $0 setup|teardown"; exit 2 ;;
esac
