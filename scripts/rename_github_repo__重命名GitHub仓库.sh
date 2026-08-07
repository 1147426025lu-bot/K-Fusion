#!/bin/bash
# Rename GitHub repo plc_compiler_rpi5 → k-fusion (run once on dev machine with gh auth).
set -euo pipefail
REPO="1147426025lu-bot/plc_compiler_rpi5"
NEW="K-Fusion"

if ! command -v gh >/dev/null 2>&1; then
	echo "Install GitHub CLI: sudo apt install gh && gh auth login"
	echo "Or rename manually: https://github.com/$REPO/settings → Repository name → $NEW"
	exit 1
fi

gh repo rename "$NEW" --repo "$REPO" --yes
gh repo edit "$REPO" --description "K-Fusion: An LLVM-based Kernelization and Fusion Compiler for low-jitter control on PREEMPT_RT" 2>/dev/null \
	|| gh repo edit "1147426025lu-bot/$NEW" --description "K-Fusion: An LLVM-based Kernelization and Fusion Compiler for low-jitter control on PREEMPT_RT"

git remote set-url origin "git@github.com:1147426025lu-bot/${NEW}.git"
git remote -v
echo "OK: https://github.com/1147426025lu-bot/${NEW}"
