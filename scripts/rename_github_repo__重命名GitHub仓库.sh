#!/bin/bash
# Legacy helper — GitHub repo is already 1147426025lu-bot/K-Fusion.
# Local clone on Pi: /home/pi/K-Fusion (symlink ~/plc_compiler → same tree).
set -euo pipefail
echo "Remote: https://github.com/1147426025lu-bot/K-Fusion"
echo "Local:  ${HOME}/K-Fusion"
git -C "${HOME}/K-Fusion" remote -v 2>/dev/null || git -C "${HOME}/plc_compiler" remote -v 2>/dev/null || true
