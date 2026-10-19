#!/usr/bin/env bash
# cursor_remote_server__安装Cursor远端server.sh
#
# 背景（2026-10-18 实测）：Cursor 的 Remote-SSH 会在远端自动下载 server 包，
# 但该包 vscode-reh-linux-arm64.tar.gz 有 **116 MB**，而 Pi 到 Azure
# （cursor.blob.core.windows.net）单连接只有 **28.9 KB/s** → 下完需约 68 分钟，
# Cursor 的安装步骤早已超时，报 "Error installing server / Failed to connect"。
#
# 本脚本把下载搬到本机（并行分段，实测 42KB/s → 308KB/s，7× 提速），
# 再用局域网传到 Pi 并解压到位，绕开远端下载瓶颈。
#
# 用法:
#   bash cursor_remote_server__安装Cursor远端server.sh [host] [user] [pass] [arch]
# 默认: 192.168.1.4 root root arm64
#
# 前置: 本机装了 Cursor（用于读 commit）、有 curl/tar/PuTTY(plink/pscp)
set -euo pipefail

HOST="${1:-192.168.1.4}"
USER_="${2:-root}"
PASS="${3:-root}"
ARCH="${4:-arm64}"          # Pi4/Pi5 = arm64

SEG=8
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PLINK="/c/Program Files/PuTTY/plink.exe"
PSCP="/c/Program Files/PuTTY/pscp.exe"
[ -x "$PLINK" ] || PLINK="$(command -v plink || true)"
[ -x "$PSCP" ]  || PSCP="$(command -v pscp || true)"
[ -n "$PLINK" ] && [ -n "$PSCP" ] || { echo "FAIL: 找不到 plink/pscp"; exit 1; }

# ---- 1. 读本机 Cursor 的 commit ----
PROD=""
for p in "$LOCALAPPDATA/Programs/cursor/resources/app/product.json" \
         "$LOCALAPPDATA/Programs/Cursor/resources/app/product.json" \
         "/c/Users/$USERNAME/AppData/Local/Programs/cursor/resources/app/product.json" \
         "C:/Users/$USERNAME/AppData/Local/Programs/cursor/resources/app/product.json"; do
  [ -f "$p" ] && PROD="$p" && break
done
[ -n "$PROD" ] || { echo "FAIL: 找不到 Cursor 的 product.json"; exit 1; }

COMMIT="$(grep -oE '"commit"[[:space:]]*:[[:space:]]*"[a-f0-9]+"' "$PROD" | head -1 | grep -oE '[a-f0-9]{20,}')"
VER="$(grep -oE '"version"[[:space:]]*:[[:space:]]*"[^"]+"' "$PROD" | head -1 | grep -oE '[0-9][^"]*')"
[ -n "$COMMIT" ] || { echo "FAIL: 读不到 commit"; exit 1; }
echo "== Cursor $VER  commit=$COMMIT  arch=$ARCH =="

# ---- 2. 主机密钥（按实际取，避免换卡后指纹不一致） ----
HK="SHA256:$(ssh-keyscan -t ed25519 -T 5 "$HOST" 2>/dev/null | ssh-keygen -lf - 2>/dev/null | awk '{print $2}' | sed 's/^SHA256://')"
[ -n "$HK" ] || { echo "FAIL: 取不到 host key"; exit 1; }

R() { "$PLINK" -ssh -batch -hostkey "$HK" -pw "$PASS" "$USER_@$HOST" "$@"; }

# ---- 3. 远端是否已装 ----
if R "test -x /root/.cursor-server/bin/$COMMIT/bin/cursor-server" 2>/dev/null; then
  echo "✅ 远端已存在 $COMMIT 的 server，无需安装"
  exit 0
fi

# ---- 4. 并行分段下载 ----
URL="https://cursor.blob.core.windows.net/remote-releases/$COMMIT/vscode-reh-linux-$ARCH.tar.gz"
TOTAL="$(curl -sI --max-time 15 "$URL" | awk 'tolower($1)=="content-length:"{print $2}' | tr -d '\r')"
[ -n "$TOTAL" ] || { echo "FAIL: 取不到包大小（网络?）"; exit 1; }
echo "== 包大小 $((TOTAL/1048576)) MB，$SEG 段并行下载 =="

CHUNK=$(( (TOTAL + SEG - 1) / SEG ))
for i in $(seq 0 $((SEG-1))); do
  S=$((i*CHUNK)); E=$((S+CHUNK-1)); [ "$E" -ge "$TOTAL" ] && E=$((TOTAL-1))
  curl -s -r "$S-$E" -o "$WORK/p$i.bin" "$URL" &
done
wait

SUM=0
for i in $(seq 0 $((SEG-1))); do SUM=$((SUM + $(stat -c%s "$WORK/p$i.bin" 2>/dev/null || echo 0))); done
[ "$SUM" -eq "$TOTAL" ] || { echo "FAIL: 分片总字节 $SUM != $TOTAL"; exit 1; }

for i in $(seq 0 $((SEG-1))); do cat "$WORK/p$i.bin"; done > "$WORK/reh.tar.gz"
gzip -t "$WORK/reh.tar.gz" || { echo "FAIL: gzip 校验失败"; exit 1; }
echo "== 下载完成并校验通过 =="

# ---- 5. 传输 + 解压 ----
"$PSCP" -batch -hostkey "$HK" -pw "$PASS" "$WORK/reh.tar.gz" "$USER_@$HOST:/tmp/reh.tar.gz" >/dev/null
R "set -e
C=$COMMIT
mkdir -p /root/.cursor-server/bin/\$C
cd /root/.cursor-server/bin/\$C
tar -zxf /tmp/reh.tar.gz --strip-components=1
rm -f /tmp/reh.tar.gz
./node --version
test -x bin/cursor-server && echo LAUNCHER_OK"

echo "✅ 安装完成：/root/.cursor-server/bin/$COMMIT"
echo "   现在回到 Cursor 重新连接该主机即可（远端会跳过下载）。"
