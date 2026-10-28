#!/bin/bash
# fiveloop_nuttx_local__本机串口跑五阶段.sh — 在 Pi 本机经 ROOTSHARED mini-UART 抓 inmate
#
# 与主机 USB-TTL 脚本的差别：不走 plink。Linux 控制台在 ttyS1，inmate mini-UART
# 一般是 ttyS0（cell 把 AUX 块标成 ROOTSHARED，Linux 仍可能读到）。
#
# 用法：
#   bash fiveloop_nuttx_local__本机串口跑五阶段.sh [cycles=5000] [dev=/dev/ttyS0]
set -euo pipefail

CYCLES="${1:-5000}"
DEV="${2:-/dev/ttyS0}"
BAUD="${BAUD:-115200}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="${OUTDIR:-$HERE/../../results/raw}"
mkdir -p "$OUTDIR"
STAMP="$(date +%Y%m%d_%H%M%S)"

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

[ -e "$DEV" ] || die "没有 $DEV"
grep -q CONFIG_BENCHMARK_FIVELOOP=y /root/nuttx-build/nuttx/.config \
  || die "nuttx.bin 未开 FIVELOOP"
[ -f /root/nuttx-build/nuttx/nuttx.bin ] || die "缺 nuttx.bin"

say "1) 部署 inmate cell"
bash /root/kfusion-pi4/scripts/deploy_nuttx_cell__部署NuttX_cell.sh | tail -8

python3 - "$DEV" "$BAUD" "$CYCLES" "$OUTDIR" "$STAMP" <<'PY'
import sys, time, os, select
dev, baud, cycles, outdir, stamp = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
import termios, tty
fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
attr = termios.tcgetattr(fd)
# 8N1, raw, given baud
baud_map = {115200: termios.B115200, 9600: termios.B9600}
bconst = baud_map.get(baud, termios.B115200)
attr[0] = termios.IGNPAR
attr[1] = 0
attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
attr[3] = 0
attr[4] = bconst
attr[5] = bconst
termios.tcsetattr(fd, termios.TCSANOW, attr)
termios.tcflush(fd, termios.TCIOFLUSH)

def recv_until(deadline, needle=None):
    buf = bytearray()
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if chunk:
            buf.extend(chunk)
            if needle and needle in buf:
                break
    return buf

def send(s):
    os.write(fd, s.encode("ascii"))

print("[serial] probing NSH on", dev)
send("\r\n")
boot = recv_until(time.time() + 8, b"nsh>")
sys.stdout.buffer.write(boot[-400:] if boot else b"(no boot text)\n")
sys.stdout.flush()
if b"nsh>" not in boot:
    os.close(fd)
    raise SystemExit("NO_NSH")

digests = {}
hold = max(20, int(cycles) // 1000 + 15)
for mode in ("plan", "flat"):
    path = os.path.join(outdir, "fiveloop_nuttx_%s_%s_%s.log" % (mode, cycles, stamp))
    print("[serial] fiveloop %s %s -> %s" % (mode, cycles, path))
    send("fiveloop %s %s\r" % (mode, cycles))
    body = recv_until(time.time() + hold, b"# digest:")
    extra = recv_until(time.time() + 3)  # rest of hist
    data = bytes(body + extra)
    open(path, "wb").write(data)
    dgst = None
    for line in data.decode("utf-8", "replace").splitlines():
        if line.startswith("# digest:"):
            dgst = line.split()[-1]
        if line.startswith("# mode:") or line.startswith("# digest:") \
           or line.startswith("# stat_response") or line.startswith("# deadline_miss"):
            print("   " + line)
    digests[mode] = dgst
    send("\r")
    recv_until(time.time() + 2, b"nsh>")

os.close(fd)
print("[serial] digest plan=%s flat=%s" % (digests.get("plan"), digests.get("flat")))
if digests.get("plan") and digests.get("plan") == digests.get("flat"):
    print("[serial] DIGEST_OK")
    raise SystemExit(0)
print("[serial] DIGEST_FAIL")
raise SystemExit(2)
PY
RC=$?
say "3) 拆 cell"
jailhouse cell destroy rpi4-nuttx 2>/dev/null || true
jailhouse cell list | tail -3
exit "$RC"
