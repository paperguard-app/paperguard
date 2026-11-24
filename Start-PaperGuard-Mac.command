#!/usr/bin/env bash
set -euo pipefail

# PaperGuard Lite — Double-Click macOS Launcher (auto-detects Python 3.11+)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_python311() {
  local cand
  for cand in \
    python3.13 python3.12 python3.11 \
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 \
    /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3 \
    "${HOME}/miniconda3/bin/python3" "${HOME}/anaconda3/bin/python3" "${HOME}/.pyenv/shims/python3" \
    python3; do
    if command -v "${cand}" >/dev/null 2>&1; then
      if "${cand}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "${cand}"
        return 0
      fi
    fi
  done
  return 1
}

if PY_BIN="$(find_python311)"; then
  echo "[PaperGuard Lite] Starting local app with ${PY_BIN}..."
  exec "${PY_BIN}" "${SCRIPT_DIR}/paperguard_app.py"
elif command -v uv >/dev/null 2>&1; then
  echo "[PaperGuard Lite] Starting local app via uv..."
  exec uv run --python 3.12 python "${SCRIPT_DIR}/paperguard_app.py"
else
  echo "============================================================"
  echo " PaperGuard Lite requires Python 3.11 or newer."
  echo "------------------------------------------------------------"
  echo " Install in 30 seconds via either option:"
  echo "   1. Official installer: https://www.python.org/downloads/macos/"
  echo "   2. Homebrew:           brew install python@3.12"
  echo "============================================================"
  read -r -p "Press Enter to close..."
  exit 1
fi
