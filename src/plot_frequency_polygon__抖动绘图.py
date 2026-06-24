import argparse
import os
import re
import struct
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


JITTER_PATTERN = re.compile(r'Jitter:\s*(-?\d+)')
JITTER_BATCH_PATTERN = re.compile(r'JitterBatch:\s*([\d\s-]+)')
CYCLICTEST_ACT_PATTERN = re.compile(r'\bAct:\s*(-?\d+)')
CYCLICTEST_VERBOSE_PATTERN = re.compile(r'^\s*\d+:\s*\d+:\s*(-?\d+)\s*$')
CYCLICTEST_HISTOGRAM_PATTERN = re.compile(r'^# Histogram|^#\s*ns\s*:')
DMESG_PREFIX_PATTERN = re.compile(r'^\[[^\]]+\]\s*(.*)$')
INTEGER_ONLY_PATTERN = re.compile(r'^-?\d+$')
JITTER_SUMMARY_PATTERN = re.compile(
    r'JitterSummary:\s*samples=(\d+)\s+signed_min_ns=(-?\d+)\s+signed_avg_ns=(-?\d+)\s+'
    r'signed_max_ns=(-?\d+)\s+abs_max_ns=(-?\d+)'
)
FUSED_SUMMARY_PATTERN = re.compile(
    r'FusedSummary:\s*tag=\S+\s+cycles=(\d+)\s+min_ns=(-?\d+)\s+max_ns=(-?\d+)'
)
FUSED_RING_MAGIC = 0x504C434A  # "JCLP"
FUSED_RING_HDR_SIZE_V1 = 40
FUSED_RING_HDR_SIZE_V2 = 56


def load_jitter_bin(bin_path):
    """jitter.bin v2: 降采样时间线 + 全周期直方图；v1: 16K 环回退。"""
    path = Path(bin_path)
    if not path.is_file():
        return [], None, []

    data = path.read_bytes()
    if len(data) < FUSED_RING_HDR_SIZE_V1:
        print(f'⚠️ jitter.bin 过短: {bin_path}')
        return [], None, []

    magic, version = struct.unpack('<II', data[:8])
    if magic != FUSED_RING_MAGIC:
        print(f'⚠️ jitter.bin magic 不匹配: {bin_path}')
        return [], None, []

    hist_entries = []
    if version >= 2 and len(data) >= FUSED_RING_HDR_SIZE_V2:
        (magic, version, cycles, min_ns, max_ns, sample_count,
         hist_bins, hist_lo, hist_step) = struct.unpack(
            '<IIQqqIIqq', data[:FUSED_RING_HDR_SIZE_V2]
        )
        pos = FUSED_RING_HDR_SIZE_V2
        raw = data[pos:pos + sample_count * 8]
        pos += sample_count * 8
        if sample_count > 2_000_000:
            values = np.frombuffer(raw, dtype=np.int64).copy()
        else:
            values = list(struct.unpack(f'<{sample_count}q', raw)) if sample_count else []
        if hist_bins > 0 and pos + hist_bins * 4 <= len(data):
            hist_counts = np.frombuffer(data[pos:pos + hist_bins * 4], dtype=np.uint32)
            for i, count in enumerate(hist_counts):
                if count:
                    lo = int(hist_lo + i * hist_step)
                    hi = lo + int(hist_step)
                    hist_entries.append((lo, hi, int(count)))
            hist_total = int(hist_counts.sum())
        else:
            hist_total = 0
        summary = {
            'samples': int(cycles),
            'signed_min_ns': int(min_ns),
            'signed_max_ns': int(max_ns),
            'abs_max_ns': max(abs(int(min_ns)), abs(int(max_ns))),
            'ring_samples': sample_count,
            'hist_total': hist_total,
            'source': 'fast_hrtimer_bin_v2',
        }
        print(f'📘 jitter.bin v2: cycles={cycles} decim={sample_count} '
              f'hist={hist_total} hdr_abs_max={summary["abs_max_ns"]} ns')
        if hist_total and hist_total != cycles:
            print(f'   ⚠️ 直方图计数 {hist_total} != cycles {cycles}')
        return values, summary, hist_entries

    cycles, min_ns, max_ns, sample_count, _reserved = struct.unpack(
        '<QqqII', data[8:FUSED_RING_HDR_SIZE_V1]
    )
    body = data[FUSED_RING_HDR_SIZE_V1:]
    expect = sample_count * 8
    if len(body) < expect:
        print(f'⚠️ jitter.bin 样本不足: want {sample_count}, got {len(body) // 8}')
        sample_count = len(body) // 8

    raw = body[: sample_count * 8]
    if sample_count > 2_000_000:
        values = np.frombuffer(raw, dtype=np.int64).copy()
        print(f'📘 大文件 numpy 加载: {sample_count} 点 (~{len(raw) >> 20} MiB)')
    else:
        values = list(struct.unpack(f'<{sample_count}q', raw))
    summary = {
        'samples': int(cycles),
        'signed_min_ns': int(min_ns),
        'signed_max_ns': int(max_ns),
        'abs_max_ns': max(abs(int(min_ns)), abs(int(max_ns))),
        'ring_samples': sample_count,
        'source': 'fast_hrtimer_bin',
    }
    kind = '全周期' if sample_count >= cycles else '环形(末段)'
    n = len(values)
    print(f'📘 已从 jitter.bin 读取{kind}样本: {n} 条 '
          f'(cycles={cycles}, hdr_abs_max={summary["abs_max_ns"]} ns)')
    if sample_count < cycles:
        print(f'   ℹ️ 出图波形为末段 {sample_count} 点；PASS 请以 FusedSummary abs_max 为准')
    return values, summary, hist_entries
JITTER_HIST_PATTERN = re.compile(
    r'JitterHist:\s*left_ns=(-?\d+)\s+right_ns=(-?\d+)\s+count=(\d+)'
)


def extract_dmesg_payload(line):
    match = DMESG_PREFIX_PATTERN.match(line.strip())
    if match:
        return match.group(1).strip()
    return line.strip()


def parse_jitter_values_from_text(text):
    values = []
    in_batch = False
    for raw_line in text.splitlines():
        payload = extract_dmesg_payload(raw_line)
        if not payload:
            in_batch = False
            continue

        if payload.startswith('JitterBatch:'):
            in_batch = True
            suffix = payload[len('JitterBatch:'):].strip()
            if suffix:
                values.extend(int(part) for part in suffix.split())
            continue

        if in_batch and INTEGER_ONLY_PATTERN.fullmatch(payload):
            values.append(int(payload))
            continue

        in_batch = False

    if values:
        return values

    for match in JITTER_PATTERN.finditer(text):
        values.append(int(match.group(1)))
    if values:
        return values

    for line in text.splitlines():
        stripped = line.strip()
        m = CYCLICTEST_VERBOSE_PATTERN.match(stripped)
        if m:
            values.append(int(m.group(1)))
    if values:
        return values

    for match in CYCLICTEST_ACT_PATTERN.finditer(text):
        values.append(int(match.group(1)))
    return values


def parse_histogram_entries_from_text(text):
    entries = []
    for raw_line in text.splitlines():
        payload = extract_dmesg_payload(raw_line)
        match = JITTER_HIST_PATTERN.search(payload)
        if not match:
            continue
        left_ns = int(match.group(1))
        right_ns = int(match.group(2))
        count = int(match.group(3))
        entries.append((left_ns, right_ns, count))
    return entries


def parse_summary_from_text(text):
    last_summary = None
    for raw_line in text.splitlines():
        payload = extract_dmesg_payload(raw_line)
        match = JITTER_SUMMARY_PATTERN.search(payload)
        if not match:
            continue
        last_summary = {
            'samples': int(match.group(1)),
            'signed_min_ns': int(match.group(2)),
            'signed_avg_ns': int(match.group(3)),
            'signed_max_ns': int(match.group(4)),
            'abs_max_ns': int(match.group(5)),
        }
    return last_summary


def build_histogram_from_entries(entries):
    if not entries:
        return np.array([]), np.array([]), np.array([])

    filtered = [(left, right, count) for left, right, count in entries if count > 0 and right > left]
    if not filtered:
        return np.array([]), np.array([]), np.array([])

    filtered.sort(key=lambda item: item[0])
    counts = np.array([count for _, _, count in filtered], dtype=np.float64)
    edges = [filtered[0][0] / 1000.0]
    for _, right, _ in filtered:
        edges.append(right / 1000.0)
    bin_edges = np.array(edges, dtype=np.float64)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    return counts, bin_edges, centers


def adaptive_bin_width_us(values, target_bins):
    """µs 级 bin 宽：按数据跨度自适应，避免 min_width 大于全距时整图只有 1 根柱。"""
    if len(values) == 0:
        return 1.0

    min_val = float(np.min(values))
    max_val = float(np.max(values))
    span = max_val - min_val
    if span <= 0:
        return max(abs(min_val) * 0.05, 1e-6) if min_val else 1e-6

    return max(span / max(target_bins, 16), span / 64.0, 1e-6)


def build_edges(values, target_bins=200, min_width=1.0):
    if len(values) == 0:
        return np.array([])

    min_val = float(np.min(values))
    max_val = float(np.max(values))
    if min_val == max_val:
        half_width = max(min_width, abs(min_val) * 0.05 if min_val else min_width)
        return np.array([min_val - half_width, min_val + half_width])

    span = max_val - min_val
    if span > 0 and min_width > span:
        min_width = adaptive_bin_width_us(values, target_bins)
    width = max(span / max(target_bins, 1), min_width)
    start = np.floor(min_val / width) * width
    end = np.ceil(max_val / width) * width
    return np.arange(start, end + width, width)


def histogram(values, target_bins=200, min_width=1.0):
    edges = build_edges(values, target_bins=target_bins, min_width=min_width)
    if len(edges) < 2:
        return np.array([]), np.array([]), np.array([])
    counts, bin_edges = np.histogram(values, bins=edges)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    return counts, bin_edges, centers


def derive_absolute_histogram_from_signed(bin_edges, counts):
    if len(bin_edges) < 2 or len(counts) == 0:
        return np.array([]), np.array([]), np.array([])

    abs_counts = {}
    for idx, count in enumerate(counts):
        if count <= 0:
            continue
        left = float(bin_edges[idx])
        right = float(bin_edges[idx + 1])
        abs_left = min(abs(left), abs(right))
        abs_right = max(abs(left), abs(right))
        if abs_left == abs_right:
            abs_right = abs_left + max(0.001, abs_left * 0.01 if abs_left else 0.001)
        key = (round(abs_left, 9), round(abs_right, 9))
        abs_counts[key] = abs_counts.get(key, 0.0) + count

    ordered = sorted(abs_counts.items(), key=lambda item: item[0][0])
    edges = [ordered[0][0][0]]
    counts_out = []
    for (left, right), count in ordered:
        if right < edges[-1]:
            right = edges[-1]
        if left > edges[-1]:
            edges.append(left)
        counts_out.append(count)
        edges.append(right)

    bin_edges = np.array(edges, dtype=np.float64)
    counts_arr = np.array(counts_out, dtype=np.float64)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    return counts_arr, bin_edges, centers


def percentile_from_histogram(bin_edges, counts, percentile):
    total = float(np.sum(counts))
    if total <= 0:
        return 0.0
    threshold = total * percentile / 100.0
    cumulative = 0.0
    for idx, count in enumerate(counts):
        next_cumulative = cumulative + float(count)
        if next_cumulative >= threshold:
            left = float(bin_edges[idx])
            right = float(bin_edges[idx + 1])
            if count <= 0:
                return right
            fraction = (threshold - cumulative) / float(count)
            fraction = min(max(fraction, 0.0), 1.0)
            return left + (right - left) * fraction
        cumulative = next_cumulative
    return float(bin_edges[-1])


def collect_from_dmesg(duration_seconds, raw_log_path=None):
    print(f"🚀 开始采集硬件抖动数据，时长：{duration_seconds:.1f} 秒...")
    subprocess.run(['sudo', '-n', 'dmesg', '-c'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    jitters_ns = []
    start_time = time.time()
    raw_lines = []
    process = None

    try:
        process = subprocess.Popen(
            ['sudo', '-n', 'dmesg', '-w'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid,
        )
        in_batch = False
        while True:
            if time.time() - start_time >= duration_seconds:
                break

            ready, _, _ = select.select([process.stdout], [], [], 1.0)
            if not ready:
                continue

            line = process.stdout.readline()
            if not line:
                break

            raw_lines.append(line)
            payload = extract_dmesg_payload(line)

            if payload.startswith('JitterBatch:'):
                in_batch = True
                batch_text = payload[len('JitterBatch:'):].strip()
                if batch_text:
                    jitters_ns.extend(int(part) for part in batch_text.split())
                continue

            if in_batch and INTEGER_ONLY_PATTERN.fullmatch(payload):
                jitters_ns.append(int(payload))
                continue

            in_batch = False

            match = JITTER_PATTERN.search(line)
            if not match:
                match = CYCLICTEST_ACT_PATTERN.search(line)
            if not match:
                continue

            jitters_ns.append(int(match.group(1)))
    except KeyboardInterrupt:
        print("\n⚠️ 采样被手动停止，开始生成图表...")
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)

    if raw_log_path:
        Path(raw_log_path).write_text(''.join(raw_lines), encoding='utf-8')
        print(f"📝 原始日志已保存到: {raw_log_path}")

    if process is not None and process.returncode not in (0, -signal.SIGTERM, -signal.SIGKILL):
        stderr_text = ''
        if process.stderr is not None:
            try:
                stderr_text = process.stderr.read().strip()
            except Exception:
                stderr_text = ''
        if stderr_text:
            print(f"⚠️ dmesg 采集进程异常退出: {stderr_text}")

    return jitters_ns


def normalize_jitter_to_ns(values):
    """旧 fused 未设 use_nsecs 时，cyclictest 的 diff 为微秒；绘图按纳秒处理。"""
    if values is None or len(values) == 0:
        return values
    if isinstance(values, np.ndarray):
        peak = int(np.max(np.abs(values)))
        if peak < 500:
            print('ℹ️ 检测到微秒量级样本（旧 fused 日志），已 ×1000 换算为纳秒再绘图')
            return values * 1000
        return values
    peak = max(abs(v) for v in values)
    if peak < 500:
        print('ℹ️ 检测到微秒量级样本（旧 fused 日志），已 ×1000 换算为纳秒再绘图')
        return [v * 1000 for v in values]
    return values


def parse_args():
    parser = argparse.ArgumentParser(description='采集或读取 Jitter 日志，并生成全分布直方图。')
    parser.add_argument('--minutes', type=float, default=1.0, help='在线采集时长（分钟）')
    parser.add_argument('--bins', type=int, default=180, help='直方图 bin 数')
    parser.add_argument('--input-log', default='', help='离线 dmesg 日志（可选，用于 FusedSummary）')
    parser.add_argument('--input-jitter-bin', default='',
                        help='快路径环形缓冲二进制全样本（优先于 Jitter: 日志）')
    parser.add_argument('--save-raw-log', default='', help='在线采集时保存原始日志到指定文件')
    parser.add_argument('--output', default='', help='输出图片名；默认自动按时间戳命名')
    parser.add_argument('--latency-output', default='', help='单独导出的延迟时序图文件名；默认基于 --output 自动生成')
    parser.add_argument('--timeline-max-points', type=int, default=20000,
                        help='时序图最大点数，超过后自动降采样')
    parser.add_argument('--spike-log', default='',
                        help='内核导出的尖峰 log（标注 |j|>=threshold 的时刻）')
    return parser.parse_args()


def load_spike_log(path):
    """返回 [(cycle, jitter_ns), ...]"""
    spikes = []
    p = Path(path)
    if not p.is_file():
        return spikes
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('cycle'):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            spikes.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return spikes


def plot_all_distributions(jitters_ns, args, hist_entries=None, summary=None):
    signed_us = (np.asarray(jitters_ns, dtype=np.float64) / 1000.0
                 if jitters_ns is not None and len(jitters_ns) else np.array([], dtype=np.float64))
    abs_us = np.abs(signed_us) if len(signed_us) else np.array([], dtype=np.float64)

    using_histogram_fallback = hist_entries is not None and len(hist_entries) > 0 and len(signed_us) == 0

    if using_histogram_fallback:
        signed_counts, signed_edges, signed_centers = build_histogram_from_entries(hist_entries)
        abs_counts, abs_edges, abs_centers = derive_absolute_histogram_from_signed(signed_edges, signed_counts)
    else:
        signed_min_w = adaptive_bin_width_us(signed_us, args.bins)
        abs_min_w = adaptive_bin_width_us(abs_us, args.bins)
        signed_counts, signed_edges, signed_centers = histogram(
            signed_us, target_bins=args.bins, min_width=signed_min_w)
        abs_counts, abs_edges, abs_centers = histogram(
            abs_us, target_bins=args.bins, min_width=abs_min_w)

    signed_non_zero = signed_counts > 0
    abs_non_zero = abs_counts > 0
    if not np.any(signed_non_zero) or not np.any(abs_non_zero):
        print('❌ 统计结果为空，无法绘图。')
        sys.exit(1)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor='white')
    for ax in axes.flat:
        ax.set_facecolor('white')
        ax.tick_params(colors='#222222', labelsize=9)
        for spine in ax.spines.values():
            spine.set_color('#bfbfbf')
        ax.grid(True, which='major', alpha=0.35, color='#d9d9d9', linestyle='--', linewidth=0.7)
        ax.grid(True, which='minor', alpha=0.15, color='#efefef', linestyle=':', linewidth=0.5)
        ax.minorticks_on()

    line_color = '#7b2cff'
    fill_color = '#c9a7ff'
    avg_color = '#00a3ff'
    min_color = '#23a55a'
    max_color = '#e55353'
    p99_color = '#ff9800'

    ax = axes[0, 0]
    ax.fill_between(signed_centers[signed_non_zero], signed_counts[signed_non_zero],
                    step='mid', color=fill_color, alpha=0.22)
    ax.step(signed_centers[signed_non_zero], signed_counts[signed_non_zero],
            where='mid', color=line_color, linewidth=1.2)
    ax.set_title('Signed Jitter Histogram', color='#111111', fontsize=12, fontweight='semibold')
    ax.set_xlabel('Jitter (us)', color='#222222')
    ax.set_ylabel('Samples', color='#222222')

    ax = axes[0, 1]
    ax.fill_between(signed_centers[signed_non_zero], signed_counts[signed_non_zero],
                    step='mid', color=fill_color, alpha=0.18)
    ax.step(signed_centers[signed_non_zero], signed_counts[signed_non_zero],
            where='mid', color=line_color, linewidth=1.2)
    ax.set_yscale('log')
    ax.set_title('Signed Jitter Histogram (log-y)', color='#111111', fontsize=12, fontweight='semibold')
    ax.set_xlabel('Jitter (us)', color='#222222')
    ax.set_ylabel('Samples (log)', color='#222222')

    ax = axes[1, 0]
    ax.fill_between(abs_centers[abs_non_zero], abs_counts[abs_non_zero],
                    step='mid', color=fill_color, alpha=0.22)
    ax.step(abs_centers[abs_non_zero], abs_counts[abs_non_zero],
            where='mid', color=line_color, linewidth=1.2)
    ax.set_title('Absolute Jitter Histogram', color='#111111', fontsize=12, fontweight='semibold')
    ax.set_xlabel('|Jitter| (us)', color='#222222')
    ax.set_ylabel('Samples', color='#222222')

    ax = axes[1, 1]
    ax.fill_between(abs_centers[abs_non_zero], abs_counts[abs_non_zero],
                    step='mid', color=fill_color, alpha=0.18)
    ax.step(abs_centers[abs_non_zero], abs_counts[abs_non_zero],
            where='mid', color=line_color, linewidth=1.2)
    ax.set_yscale('log')
    ax.set_title('Absolute Jitter Histogram (log-y)', color='#111111', fontsize=12, fontweight='semibold')
    ax.set_xlabel('|Jitter| (us)', color='#222222')
    ax.set_ylabel('Samples (log)', color='#222222')

    if using_histogram_fallback:
        signed_min = float(summary['signed_min_ns']) / 1000.0 if summary else float(signed_edges[0])
        if summary and 'signed_avg_ns' in summary:
            signed_avg = float(summary['signed_avg_ns']) / 1000.0
        elif len(signed_centers) and np.sum(signed_counts) > 0:
            signed_avg = float(np.average(signed_centers[signed_non_zero],
                                          weights=signed_counts[signed_non_zero]))
        else:
            signed_avg = 0.0
        signed_max = float(summary['signed_max_ns']) / 1000.0 if summary else float(signed_edges[-1])
        abs_max = float(summary['abs_max_ns']) / 1000.0 if summary else float(abs_edges[-1])
        abs_p99 = float(percentile_from_histogram(abs_edges, abs_counts, 99.0))
        sample_count = int(summary['samples']) if summary else int(np.sum(signed_counts))
        summary_source = 'histogram_fallback'
    else:
        signed_min = float(np.min(signed_us))
        signed_avg = float(np.mean(signed_us))
        signed_max = float(np.max(signed_us))
        abs_p99 = float(np.percentile(abs_us, 99))
        abs_max = float(np.max(abs_us))
        sample_count = len(jitters_ns)
        summary_source = 'batch_or_raw_samples'

    for ax in axes[0, :]:
        ax.axvline(signed_avg, color=avg_color, linestyle='--', linewidth=1.1, label='avg')
        ax.axvline(signed_min, color=min_color, linestyle=':', linewidth=1.0, label='min')
        ax.axvline(signed_max, color=max_color, linestyle='--', linewidth=1.0, label='max')

    for ax in axes[1, :]:
        ax.axvline(abs_p99, color=p99_color, linestyle='--', linewidth=1.1, label='p99')
        ax.axvline(abs_max, color=max_color, linestyle='--', linewidth=1.0, label='max')

    summary = (
        f"samples={sample_count}\n"
        f"signed min={signed_min:.3f} us\n"
        f"signed avg={signed_avg:.3f} us\n"
        f"signed max={signed_max:.3f} us\n"
        f"|jitter| p99={abs_p99:.3f} us\n"
        f"|jitter| max={abs_max:.3f} us\n"
        f"source={summary_source}"
    )
    axes[0, 1].text(
        0.98,
        0.97,
        summary,
        transform=axes[0, 1].transAxes,
        ha='right',
        va='top',
        color='#222222',
        fontsize=9,
        bbox=dict(facecolor='white', edgecolor='#bbbbbb', alpha=0.96, boxstyle='round,pad=0.35'),
    )

    axes[0, 0].legend(loc='upper left', frameon=True, facecolor='white', edgecolor='#cccccc', fontsize=8)
    axes[1, 0].legend(loc='upper right', frameon=True, facecolor='white', edgecolor='#cccccc', fontsize=8)

    fig.suptitle('Full Jitter Distribution Histogram', color='#111111', fontsize=15, fontweight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    output = args.output or f'Jitter_Full_Distribution_{int(time.time())}.png'
    plt.savefig(output, dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f'✅ 全抖动直方图已保存为: {output}')


def build_latency_output_path(args):
    if args.latency_output:
        return args.latency_output

    if args.output:
        output_path = Path(args.output)
        if output_path.suffix:
            return str(output_path.with_name(f'{output_path.stem}_latency{output_path.suffix}'))
        return f'{args.output}_latency.png'

    return f'Jitter_Latency_{int(time.time())}.png'


def plot_latency_timeline(jitters_ns, args):
    if jitters_ns is None or len(jitters_ns) == 0:
        print('ℹ️ 未提供可还原时序的逐点样本，跳过延迟时序图。')
        return

    signed_us = np.asarray(jitters_ns, dtype=np.float64) / 1000.0
    step = max(1, int(np.ceil(len(signed_us) / max(args.timeline_max_points, 1))))
    if step > 1:
        signed_us = signed_us[::step]
    sample_index = np.arange(len(signed_us), dtype=np.int64) * step
    decim_stride = getattr(args, 'decim_stride', 0)
    if decim_stride and decim_stride > 1 and step == 1:
        sample_index = np.arange(len(signed_us), dtype=np.int64) * decim_stride
    elif decim_stride and decim_stride > 1 and step > 1:
        sample_index = np.arange(len(signed_us), dtype=np.int64) * decim_stride * step

    line_color = '#7b2cff'
    avg_color = '#00a3ff'
    min_color = '#23a55a'
    max_color = '#e55353'

    fig, ax = plt.subplots(figsize=(14, 5), facecolor='white')
    ax.set_facecolor('white')
    ax.tick_params(colors='#222222', labelsize=9)
    for spine in ax.spines.values():
        spine.set_color('#bfbfbf')
    ax.grid(True, which='major', alpha=0.35, color='#d9d9d9', linestyle='--', linewidth=0.7)
    ax.grid(True, which='minor', alpha=0.15, color='#efefef', linestyle=':', linewidth=0.5)
    ax.minorticks_on()

    ax.plot(sample_index, signed_us, color=line_color, linewidth=0.6, alpha=0.9, label='latency')
    spike_marked = 0
    if getattr(args, 'spike_log', ''):
        for cycle, jitter_ns in load_spike_log(args.spike_log):
            if abs(jitter_ns) < 4000:
                continue
            x = cycle
            if decim_stride and decim_stride > 1:
                x = cycle  # spike cycle 与 decim index 同刻度
            y = jitter_ns / 1000.0
            ax.axvline(x, color='#e55353', alpha=0.35, linewidth=0.8)
            ax.scatter([x], [y], color='#e55353', s=18, zorder=5)
            spike_marked += 1
        if spike_marked:
            ax.scatter([], [], color='#e55353', s=18, label=f'spike>4us ({spike_marked})')
    ax.axhline(float(np.mean(signed_us)), color=avg_color, linestyle='--', linewidth=1.0, label='avg')
    ax.axhline(float(np.max(signed_us)), color=max_color, linestyle='--', linewidth=0.9, alpha=0.9, label='max')
    ax.axhline(float(np.min(signed_us)), color=min_color, linestyle=':', linewidth=0.9, alpha=0.9, label='min')

    ax.set_title('Latency Timeline', color='#111111', fontsize=13, fontweight='semibold')
    ax.set_xlabel('Sample Index', color='#222222')
    ax.set_ylabel('Latency / Jitter (us)', color='#222222')
    if step > 1:
        ax.text(0.01, 0.98,
                f'downsample_step={step}',
                transform=ax.transAxes,
                ha='left', va='top', color='#222222', fontsize=8,
                bbox=dict(facecolor='white', edgecolor='#cccccc', alpha=0.9, boxstyle='round,pad=0.25'))

    ax.legend(loc='upper right', frameon=True, facecolor='white', edgecolor='#cccccc', fontsize=8)
    fig.tight_layout(rect=[0, 0, 1, 1])

    output = build_latency_output_path(args)
    plt.savefig(output, dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f'✅ 延迟时序图已保存为: {output}')


def main():
    args = parse_args()
    hist_entries = []
    summary = None
    jitters_ns = []

    if args.input_jitter_bin:
        jitters_ns, bin_summary, bin_hist = load_jitter_bin(args.input_jitter_bin)
        jitters_ns = normalize_jitter_to_ns(jitters_ns)
        if bin_summary is not None:
            summary = bin_summary
        if bin_hist:
            hist_entries = bin_hist
            print(f'📊 全周期直方图 bin: {len(bin_hist)} 个非零, 总样本={summary.get("hist_total", summary.get("samples"))}')
            args.decim_stride = max(1, int(summary['samples'] // max(summary.get('ring_samples', 1), 1)))

    if args.input_log:
        log_text = Path(args.input_log).read_text(encoding='utf-8', errors='ignore')
        if not jitters_ns and not hist_entries:
            jitters_ns = normalize_jitter_to_ns(parse_jitter_values_from_text(log_text))
            print(f'📘 已从离线日志读取样本: {len(jitters_ns)} 条')
        if not hist_entries:
            hist_entries = parse_histogram_entries_from_text(log_text)
        if summary is None:
            summary = parse_summary_from_text(log_text)
        if hist_entries and not args.input_jitter_bin:
            print(f'📊 已从离线日志读取直方图 bin: {len(hist_entries)} 个')
        if summary is not None and summary.get('source') not in ('fast_hrtimer_bin', 'fast_hrtimer_bin_v2'):
            print(f'🧾 已读取 JitterSummary: samples={summary.get("samples")}')
    elif not args.input_jitter_bin:
        duration_seconds = max(args.minutes, 0.01) * 60.0
        jitters_ns = collect_from_dmesg(
            duration_seconds,
            raw_log_path=args.save_raw_log or None,
        )

    if not jitters_ns and not hist_entries:
        print('❌ 未抓取到有效数据！请检查内核模块或 Jitter 日志输出。')
        sys.exit(1)

    plot_jitters = jitters_ns if not hist_entries else []
    plot_all_distributions(plot_jitters, args, hist_entries=hist_entries, summary=summary)
    plot_latency_timeline(jitters_ns, args)


if __name__ == '__main__':
    main()
