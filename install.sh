#!/usr/bin/env bash
set -euo pipefail

# PaperGuard Lite — Safe Cross-IDE Skill Installer (Antigravity, Claude Code & Cursor)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_SKILL="${SCRIPT_DIR}/paperguard-lite"

TARGET_IDE="all"
DEST_ROOT="$(pwd)"
IS_GLOBAL=0

usage() {
  echo "Usage: ./install.sh [--target antigravity|claude|cursor|all] [--project <dir> | --global]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || { usage; exit 1; }
      TARGET_IDE="$2"
      shift 2
      ;;
    --project)
      [[ $# -ge 2 ]] || { usage; exit 1; }
      DEST_ROOT="$2"
      IS_GLOBAL=0
      shift 2
      ;;
    --global)
      DEST_ROOT="${HOME}"
      IS_GLOBAL=1
      shift 1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown argument '$1'" >&2
      usage
      exit 1
      ;;
  esac
done

case "${TARGET_IDE}" in
  antigravity|claude|cursor|all) ;;
  *)
    echo "Error: invalid --target '${TARGET_IDE}' (expected antigravity|claude|cursor|all)." >&2
    exit 1
    ;;
esac

if [[ "${IS_GLOBAL}" -eq 1 && "${TARGET_IDE}" == "cursor" ]]; then
  echo "Error: --target cursor requires --project <dir> (Cursor rules are workspace-scoped)." >&2
  exit 1
fi

backup_if_exists() {
  local target_dir="$1"
  if [[ -e "${target_dir}" ]]; then
    local orig_bak="${target_dir}.orig.bak"
    local latest_bak="${target_dir}.bak"
    if [[ ! -e "${orig_bak}" ]]; then
      cp -R "${target_dir}" "${orig_bak}"
    fi
    rm -rf "${latest_bak}"
    mv "${target_dir}" "${latest_bak}"
  fi
}

install_into() {
  local skills_dir="$1"
  mkdir -p "${skills_dir}"
  local mod_dest="${skills_dir}/paperguard-lite"
  backup_if_exists "${mod_dest}"
  cp -R "${SRC_SKILL}" "${mod_dest}"
  find "${mod_dest}" -type d -name "__pycache__" -exec rm -rf {} +
  chmod 0755 "${mod_dest}/scripts/"*.py
  echo "[PaperGuard Lite] Installed -> ${mod_dest}"
}

if [[ "${TARGET_IDE}" == "antigravity" || "${TARGET_IDE}" == "all" ]]; then
  if [[ "${IS_GLOBAL}" -eq 1 ]]; then
    install_into "${HOME}/.gemini/config/skills"
  else
    install_into "${DEST_ROOT}/.agents/skills"
  fi
fi

if [[ "${TARGET_IDE}" == "claude" || "${TARGET_IDE}" == "all" ]]; then
  install_into "${DEST_ROOT}/.claude/skills"
fi

install_cursor_rule() {
  local rule_file="$1"
  local tmp_managed
  tmp_managed="$(mktemp)"
  cat > "${tmp_managed}"
  if [[ -f "${rule_file}" ]]; then
    if ! cmp -s "${rule_file}" "${tmp_managed}"; then
      local orig_bak="${rule_file}.orig.bak"
      local latest_bak="${rule_file}.bak"
      if [[ ! -e "${orig_bak}" ]]; then
        cp "${rule_file}" "${orig_bak}"
      fi
      cp "${rule_file}" "${latest_bak}"
      local user_tail=""
      if grep -q '<!-- paperguard:managed-end -->' "${rule_file}"; then
        user_tail="$(awk 'found { print } /<!-- paperguard:managed-end -->/ { found=1 }' "${rule_file}")"
      else
        user_tail="$(grep -Fvx -f "${tmp_managed}" "${rule_file}" | grep -v '^[[:space:]]*$' || true)"
      fi
      if [[ -n "${user_tail}" ]]; then
        printf '\n%s\n' "${user_tail}" >> "${tmp_managed}"
      fi
      echo "[PaperGuard Lite] Preserved local Cursor rule edits & saved backup -> ${latest_bak}"
    fi
  fi
  mv "${tmp_managed}" "${rule_file}"
}

if [[ "${TARGET_IDE}" == "cursor" || "${TARGET_IDE}" == "all" ]]; then
  if [[ "${IS_GLOBAL}" -eq 0 ]]; then
    if [[ "${TARGET_IDE}" == "cursor" ]]; then
      install_into "${DEST_ROOT}/.agents/skills"
    fi
    CURSOR_DIR="${DEST_ROOT}/.cursor/rules"
    mkdir -p "${CURSOR_DIR}"
    install_cursor_rule "${CURSOR_DIR}/paperguard-lite.mdc" <<'EOF'
---
description: PaperGuard Lite read-only LaTeX/Markdown citation & style diagnostic workflow.
globs: "**/*.tex, **/*.bib, **/*.md"
alwaysApply: false
---

<!-- paperguard:managed-start -->
# PaperGuard Lite Bridge

Read `.agents/skills/paperguard-lite/SKILL.md` to run read-only citation forensics (`audit_bib_lite.py`), prose cliché diagnostics (`scan_ai_slop_lite.py`), and local HTML cockpit rendering (`render_audit_ui.py`). Never modify original manuscript files during a Lite audit.
<!-- paperguard:managed-end -->
EOF
    echo "[PaperGuard Lite] Generated Cursor rule -> ${CURSOR_DIR}/paperguard-lite.mdc"
  fi
fi

echo "Done! Ask your agent: 'Run paperguard-lite on paper.tex and references.bib'"
