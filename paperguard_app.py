#!/usr/bin/env python3
"""PaperGuard Lite — Cross-Platform Local App Launcher & IDE Installer (v2.5.0).

Runs a loopback-only (127.0.0.1) browser workbench using 100% Python 3.11+ stdlib:
- Drag & drop or pick a LaTeX/Markdown manuscript and optional .bib file(s)
- Try the built-in flawed academic paper demo in one click
- Renders the full offline PaperGuard Lite HTML audit report without touching source files
- One-click cross-platform IDE installer for Antigravity, Claude Code & Cursor (Mac/Win/Linux)

Zero pip dependencies. Zero telemetry. Original manuscript files are never modified.
"""

from __future__ import annotations

import argparse
import atexit
import hmac
import http.server
import json
import pathlib
import platform
import re
import secrets
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from typing import Any

APP_ROOT = pathlib.Path(__file__).resolve().parent
SCRIPTS_DIR = APP_ROOT / "paperguard-lite" / "scripts"
LITE_SKILL_SRC = APP_ROOT / "paperguard-lite"
RENDER_SCRIPT = SCRIPTS_DIR / "render_audit_ui.py"
PRO_UPGRADE_URL = "https://zhangx2.gumroad.com/l/paperguard-pro"
APP_ICON_DATA_URI = "data:image/png;base64,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"
MAX_BODY_BYTES = 15 * 1024 * 1024  # 15 MiB cap per local request

CURSOR_RULE_MANAGED = """---
description: PaperGuard Lite read-only LaTeX/Markdown citation & style diagnostic workflow.
globs: "**/*.tex, **/*.bib, **/*.md"
alwaysApply: false
---

<!-- paperguard:managed-start -->
# PaperGuard Lite Bridge

Read `.agents/skills/paperguard-lite/SKILL.md` to run read-only citation forensics (`audit_bib_lite.py`), prose cliché diagnostics (`scan_ai_slop_lite.py`), and local HTML cockpit rendering (`render_audit_ui.py`). Never modify original manuscript files during a Lite audit.
<!-- paperguard:managed-end -->
"""

DEMO_MANUSCRIPT = r"""\documentclass{article}
\usepackage{amsmath}
\title{Delving into a Comprehensive Tapestry of Efficient Attention}
\begin{document}
\maketitle

\section{Introduction}
In this paper, we delve into a groundbreaking, comprehensive tapestry of sparse attention mechanisms.
It is worth noting that our novel framework seamlessly leverages cutting-edge insights to foster pivotal performance gains \cite{vaswani2017attention,smith2025hallucinated,missing2026cite}.
Furthermore, this paper underscores the transformative potential of adaptive routing:
\begin{equation}
  \text{Attention}(Q, K, V) = \text{softmax}\!\left(\frac{QK^\top}{\sqrt{d_k}}\right) V
\end{equation}

\section{Experiments}
Our method achieves 92.4\% accuracy on the benchmark while reducing inference latency by 25\% compared to the baseline \cite{hu2021lora}.
\bibliography{demo_refs}
\end{document}
"""

DEMO_BIB = r"""@inproceedings{vaswani2017attention,
  title={Attention is All You Need},
  author={Vaswani, Ashish and Shazeer, Noam and Parmar, Niki},
  booktitle={Advances in Neural Information Processing Systems},
  year={2017}
}

@article{hu2021lora,
  title={LoRA: Low-Rank Adaptation of Large Language Models},
  author={Hu, Edward J and Shen, Yelong and Wallis, Phillip},
  journal={arXiv preprint arXiv:2106.09685},
  year={2021}
}

@article{smith2025hallucinated,
  title={Quantum Hyper-Attention for Everything},
  author={Smith, John},
  journal={Proceedings of Nonexistent Conference},
  year={2025},
  doi={10.9999/invalid.doi.12345}
}
"""


def _safe_filename(raw_name: str, default_name: str) -> str:
    base = pathlib.Path(str(raw_name or "")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not cleaned:
        return default_name
    return cleaned[:120]


def _backup_path(target: pathlib.Path) -> None:
    if not target.exists():
        return
    orig_bak = target.parent / f"{target.name}.orig.bak"
    latest_bak = target.parent / f"{target.name}.bak"
    if not orig_bak.exists():
        if target.is_dir():
            shutil.copytree(target, orig_bak)
        else:
            shutil.copy2(target, orig_bak)
    if latest_bak.exists():
        if latest_bak.is_dir():
            shutil.rmtree(latest_bak)
        else:
            latest_bak.unlink()
    shutil.move(str(target), str(latest_bak))


def _copy_skill_dir(src: pathlib.Path, dest: pathlib.Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _backup_path(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    if platform.system() != "Windows":
        scripts_path = dest / "scripts"
        if scripts_path.is_dir():
            for py_file in scripts_path.glob("*.py"):
                py_file.chmod(0o755)


def _install_cursor_rule(rule_file: pathlib.Path, managed_content: str) -> bool:
    """Write Cursor .mdc rule while preserving user-added tail lines & .orig.bak/.bak backups."""
    rule_file.parent.mkdir(parents=True, exist_ok=True)
    preserved = False
    final_text = managed_content
    if rule_file.is_file():
        existing = rule_file.read_text(encoding="utf-8", errors="replace")
        if existing != managed_content:
            orig_bak = rule_file.parent / f"{rule_file.name}.orig.bak"
            latest_bak = rule_file.parent / f"{rule_file.name}.bak"
            if not orig_bak.exists():
                shutil.copy2(rule_file, orig_bak)
            shutil.copy2(rule_file, latest_bak)
            marker = "<!-- paperguard:managed-end -->"
            user_tail = ""
            if marker in existing:
                user_tail = existing.split(marker, 1)[1].strip()
            else:
                managed_lines = set(managed_content.splitlines())
                extra = [ln for ln in existing.splitlines() if ln.strip() and ln not in managed_lines]
                user_tail = "\n".join(extra).strip()
            if user_tail:
                final_text = managed_content.rstrip("\n") + "\n\n" + user_tail + "\n"
                preserved = True
    rule_file.write_text(final_text, encoding="utf-8")
    return preserved


def install_lite_skills(target_ide: str, scope: str, project_dir: str | None) -> dict[str, Any]:
    if target_ide not in {"antigravity", "claude", "cursor", "all"}:
        raise ValueError(f"Unsupported IDE target: {target_ide}")
    if scope not in {"project", "global"}:
        raise ValueError(f"Unsupported install scope: {scope}")
    if scope == "global" and target_ide == "cursor":
        raise ValueError("Cursor rules are project-scoped. Choose a project folder for Cursor.")

    if scope == "global":
        dest_root = pathlib.Path.home()
    else:
        raw_proj = (project_dir or "").strip()
        if not raw_proj:
            raise ValueError("Please provide your manuscript project folder path.")
        dest_root = pathlib.Path(raw_proj).expanduser().resolve()
        if not dest_root.exists() or not dest_root.is_dir():
            raise ValueError(f"Project folder does not exist: {dest_root}")

    installed: list[str] = []
    notes: list[str] = []

    if target_ide in {"antigravity", "all"}:
        skills_dir = (
            pathlib.Path.home() / ".gemini" / "config" / "skills"
            if scope == "global"
            else dest_root / ".agents" / "skills"
        )
        mod_dest = skills_dir / "paperguard-lite"
        _copy_skill_dir(LITE_SKILL_SRC, mod_dest)
        installed.append(str(mod_dest))

    if target_ide in {"claude", "all"}:
        mod_dest = dest_root / ".claude" / "skills" / "paperguard-lite"
        _copy_skill_dir(LITE_SKILL_SRC, mod_dest)
        installed.append(str(mod_dest))

    if target_ide in {"cursor", "all"} and scope == "project":
        if target_ide == "cursor":
            mod_dest = dest_root / ".agents" / "skills" / "paperguard-lite"
            _copy_skill_dir(LITE_SKILL_SRC, mod_dest)
            installed.append(str(mod_dest))
        rule_file = dest_root / ".cursor" / "rules" / "paperguard-lite.mdc"
        preserved = _install_cursor_rule(rule_file, CURSOR_RULE_MANAGED)
        installed.append(str(rule_file))
        if preserved:
            notes.append(f"Preserved your custom edits in {rule_file.name} (backup saved to .bak).")

    return {
        "ok": True,
        "installed": installed,
        "notes": notes,
        "message": f"Installed PaperGuard Lite into {len(installed)} destination(s).",
    }


def pick_native_folder() -> dict[str, Any]:
    system = platform.system()
    try:
        if system == "Darwin":
            proc = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Select your LaTeX or Markdown paper folder")',
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return {"supported": True, "path": proc.stdout.strip().rstrip("/")}
            return {"supported": True, "cancelled": True, "path": ""}
        if system == "Windows":
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$d.Description = 'Select your LaTeX or Markdown paper folder'; "
                "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return {"supported": True, "path": proc.stdout.strip()}
            return {"supported": True, "cancelled": True, "path": ""}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"supported": False, "path": ""}


def _parse_json_output(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


class _LiteSession:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(24)
        self.tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="paperguard_lite_app_"))
        self.reports: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.server: socketserver.TCPServer | None = None

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def run_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        use_demo = bool(payload.get("demo"))
        online = bool(payload.get("online"))
        run_id = secrets.token_hex(8)
        run_dir = self.tmp_root / run_id
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        ms_path: pathlib.Path | None = None
        bib_paths: list[pathlib.Path] = []

        if use_demo:
            ms_path = inputs_dir / "demo_paper.tex"
            ms_path.write_text(DEMO_MANUSCRIPT, encoding="utf-8")
            bib_path = inputs_dir / "demo_refs.bib"
            bib_path.write_text(DEMO_BIB, encoding="utf-8")
            bib_paths.append(bib_path)
        else:
            ms_obj = payload.get("manuscript")
            if isinstance(ms_obj, dict) and isinstance(ms_obj.get("content"), str) and ms_obj["content"].strip():
                ms_name = _safe_filename(str(ms_obj.get("name", "")), "manuscript.tex")
                ms_path = inputs_dir / ms_name
                ms_path.write_text(ms_obj["content"], encoding="utf-8")

            raw_bibs = payload.get("bibs") or []
            if isinstance(raw_bibs, list):
                for idx, item in enumerate(raw_bibs[:6]):
                    if isinstance(item, dict) and isinstance(item.get("content"), str) and item["content"].strip():
                        b_name = _safe_filename(str(item.get("name", "")), f"references_{idx + 1}.bib")
                        b_path = inputs_dir / b_name
                        b_path.write_text(item["content"], encoding="utf-8")
                        bib_paths.append(b_path)

        if ms_path is None and not bib_paths:
            raise ValueError("Upload at least one manuscript (.tex / .md) or bibliography (.bib) file, or click Try Built-in Demo.")

        out_html = run_dir / "paperguard_lite_report.html"
        cmd = [sys.executable, str(RENDER_SCRIPT), "--out", str(out_html)]
        if ms_path is not None:
            cmd.extend(["--manuscript", str(ms_path)])
        if bib_paths:
            cmd.extend(["--bib", str(bib_paths[0])])
        if not online:
            cmd.append("--offline")

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if proc.returncode != 0 or not out_html.is_file():
            err_msg = (proc.stderr or proc.stdout or "PaperGuard Lite renderer failed.").strip()
            raise RuntimeError(err_msg[:800])

        # Also collect compact structured summary counts (read-only)
        slop_hits = 0
        bib_alerts = 0
        if ms_path is not None:
            slop_json = run_dir / "slop.json"
            p_slop = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "scan_ai_slop_lite.py"), str(ms_path), "--json", str(slop_json)],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if p_slop.returncode in {0, 1} and slop_json.is_file():
                sdata = _parse_json_output(slop_json.read_text(encoding="utf-8", errors="replace"))
                slop_hits = int(sdata.get("total_hits", len(sdata.get("hits", []))))

        if bib_paths:
            bib_json = run_dir / "bib.json"
            b_cmd = [sys.executable, str(SCRIPTS_DIR / "audit_bib_lite.py"), str(bib_paths[0]), "--json", str(bib_json)]
            if ms_path is not None:
                b_cmd.extend(["--manuscript", str(ms_path)])
            if not online:
                b_cmd.append("--offline")
            p_bib = subprocess.run(b_cmd, capture_output=True, text=True, timeout=60, check=False)
            if p_bib.returncode in {0, 1} and bib_json.is_file():
                bdata = _parse_json_output(bib_json.read_text(encoding="utf-8", errors="replace"))
                bib_alerts = int(
                    bdata.get("critical_count", 0)
                    + bdata.get("warning_count", 0)
                    + bdata.get("info_count", 0)
                )

        html_bytes = out_html.read_bytes()
        with self.lock:
            self.reports[run_id] = {
                "html": html_bytes,
                "filename": f"paperguard-lite-{ms_path.stem if ms_path else bib_paths[0].stem}.html",
            }
            # Keep memory bounded to last 12 runs
            if len(self.reports) > 12:
                oldest = next(iter(self.reports))
                self.reports.pop(oldest, None)

        return {
            "ok": True,
            "run_id": run_id,
            "report_url": f"/report/{run_id}",
            "download_url": f"/report/{run_id}?download=1",
            "slop_hits": slop_hits,
            "bib_alerts": bib_alerts,
            "report_bytes": len(html_bytes),
            "mode": "Online Registry Lookup" if online else "100% Offline",
            "source_label": "Built-in Demo" if use_demo else (ms_path.name if ms_path else bib_paths[0].name),
        }


def _render_app_html(nonce: str, csrf_token: str) -> str:
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    os_label = f"{platform.system()} ({platform.machine()})"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<script nonce="{nonce}">
try {{
  var _cs = localStorage.getItem("pg-color-scheme");
  if (_cs === "light" || _cs === "dark") {{
    document.querySelector('meta[name="color-scheme"]').content = _cs;
  }}
}} catch (_) {{}}
</script>
<link rel="icon" type="image/png" href="{APP_ICON_DATA_URI}">
<title>PaperGuard Lite — Local Academic Audit App</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: light-dark(#f8f6f1, #111318);
  --surface: light-dark(#ffffff, #181c23);
  --surface-2: light-dark(#f1eee6, #202530);
  --ink: light-dark(#181a1f, #f1efe9);
  --muted: light-dark(#525866, #a3aab8);
  --border: light-dark(#d8d3c8, #2e3545);
  --accent: light-dark(#1d4ed8, #60a5fa);
  --accent-ink: light-dark(#ffffff, #0b1220);
  --ok: light-dark(#0f766e, #34d399);
  --warn: light-dark(#b45309, #fbbf24);
  --err: light-dark(#b91c1c, #f87171);
  --pro: light-dark(#111827, #f59e0b);
  --pro-ink: light-dark(#ffffff, #111827);
  --radius: 12px;
  --shadow: light-dark(0 6px 24px rgba(24,26,31,.07), 0 8px 28px rgba(0,0,0,.35));
}}
:root:has(> head > meta[name="color-scheme"][content="dark"]) {{ color-scheme: dark; }}
:root:has(> head > meta[name="color-scheme"][content="light"]) {{ color-scheme: light; }}

*, *::before, *::after {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.55;
  accent-color: var(--accent);
}}
.visually-hidden:where(:not(:focus-within, :active)) {{
  position: absolute !important;
  clip-path: inset(50%) !important;
  overflow: hidden !important;
  width: 1px !important;
  height: 1px !important;
  margin: -1px !important;
  padding: 0 !important;
  border: 0 !important;
  white-space: nowrap !important;
}}
header {{
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  position: sticky;
  top: 0;
  z-index: 10;
}}
.header-inner {{
  max-width: 960px;
  margin: 0 auto;
  padding: 0.9rem 1.25rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}}
.brand {{
  display: flex;
  align-items: center;
  gap: 0.65rem;
}}
.brand-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--accent);
  color: var(--accent-ink);
  font-weight: 700;
  font-size: 0.95rem;
}}
.brand h1 {{
  margin: 0;
  font-size: 1.08rem;
  font-weight: 700;
  letter-spacing: -0.01em;
}}
.brand-sub {{
  font-size: 0.8rem;
  color: var(--muted);
}}
.header-actions {{
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}}
main {{
  max-width: 960px;
  margin: 0 auto;
  padding: 1.5rem 1.25rem 3rem;
  display: grid;
  gap: 1.25rem;
}}
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.35rem;
  box-shadow: var(--shadow);
}}
.card h2 {{
  margin: 0 0 0.35rem;
  font-size: 1.15rem;
}}
.card-lead {{
  margin: 0 0 1.1rem;
  color: var(--muted);
  font-size: 0.92rem;
}}
.pill-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}}
.pill {{
  font-size: 0.78rem;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--muted);
}}
.pill.ok {{ color: var(--ok); border-color: var(--ok); }}
.pill.warn {{ color: var(--warn); border-color: var(--warn); }}

fieldset {{
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem;
  margin: 0 0 1rem;
}}
legend {{
  font-weight: 600;
  font-size: 0.9rem;
  padding: 0 0.4rem;
}}
.drop-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0.9rem;
}}
.drop-zone {{
  border: 2px dashed var(--border);
  border-radius: 10px;
  padding: 1rem;
  background: var(--surface-2);
  transition: border-color .15s, background-color .15s;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}}
.drop-zone.drag-over {{
  border-color: var(--accent);
  background: light-dark(#eff6ff, #1b2845);
}}
.drop-zone label {{
  font-weight: 600;
  font-size: 0.92rem;
}}
.hint {{
  font-size: 0.82rem;
  color: var(--muted);
}}
input[type="file"] {{
  font: inherit;
  font-size: 0.88rem;
  max-width: 100%;
}}
input[type="text"] {{
  font: inherit;
  font-size: max(1rem, 1em);
  min-height: 44px;
  width: 100%;
  padding: 0.55rem 0.75rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--ink);
}}
input:focus-visible, button:focus-visible, a:focus-visible {{
  outline: 3px solid var(--accent);
  outline-offset: 2px;
}}
:is(input:user-invalid, input.user-invalid-fallback) {{
  border-color: var(--err);
}}
.error-msg {{
  display: none;
  color: var(--err);
  font-size: 0.85rem;
  margin-top: 0.25rem;
}}
:is(input:user-invalid, input.user-invalid-fallback) ~ .error-msg {{
  display: block;
}}
.file-chip {{
  font-size: 0.82rem;
  color: var(--ok);
  font-weight: 500;
  word-break: break-all;
}}
.check-row, .radio-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.85rem 1.25rem;
  align-items: center;
}}
.check-item {{
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  min-height: 36px;
}}
.path-row {{
  display: flex;
  gap: 0.5rem;
  align-items: stretch;
}}
.path-row input {{
  flex: 1;
}}
.btn-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem;
  align-items: center;
  margin-top: 0.75rem;
}}
button, .btn-link {{
  font: inherit;
  font-size: 0.92rem;
  font-weight: 600;
  min-height: 44px;
  padding: 0.55rem 1.05rem;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--ink);
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
}}
button:disabled {{
  opacity: 0.6;
  cursor: wait;
}}
.btn-primary {{
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
}}
.btn-pro {{
  background: var(--pro);
  color: var(--pro-ink);
  border-color: var(--pro);
}}
.btn-ghost {{
  background: transparent;
}}
.status-box {{
  margin-top: 1rem;
  padding: 0.9rem 1rem;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  display: none;
}}
.status-box.visible {{
  display: block;
}}
.status-box.error {{
  border-color: var(--err);
}}
.status-box.success {{
  border-color: var(--ok);
}}
.status-title {{
  font-weight: 700;
  margin-bottom: 0.35rem;
}}
.status-list {{
  margin: 0.4rem 0 0;
  padding-inline-start: 1.2rem;
  font-size: 0.88rem;
}}
.pro-banner {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  background: light-dark(#f3f0e6, #1c202a);
}}
.pro-banner h3 {{
  margin: 0 0 0.25rem;
  font-size: 1rem;
}}
.pro-banner p {{
  margin: 0;
  font-size: 0.88rem;
  color: var(--muted);
  max-width: 62ch;
}}
footer {{
  max-width: 960px;
  margin: 0 auto 2rem;
  padding: 0 1.25rem;
  font-size: 0.8rem;
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 0.5rem;
}}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div class="brand">
      <img src="{APP_ICON_DATA_URI}" alt="" width="34" height="34" style="border-radius:8px;display:block">
      <div>
        <h1>PaperGuard Lite · Local App</h1>
        <div class="brand-sub">100% local loopback · Zero telemetry · Original files never modified</div>
      </div>
    </div>
    <div class="header-actions">
      <button type="button" id="themeToggle" class="btn-ghost" aria-label="Toggle color theme">Theme</button>
      <a class="btn-link btn-pro" href="{PRO_UPGRADE_URL}" target="_blank" rel="noopener">Unlock Pro · $29</a>
      <button type="button" id="quitBtn" class="btn-ghost">Quit App</button>
    </div>
  </div>
</header>

<main>
  <section class="card" aria-labelledby="audit-heading">
    <h2 id="audit-heading">1. Run Manuscript &amp; BibTeX Audit</h2>
    <p class="card-lead">Drop your LaTeX/Markdown manuscript and optional <code>.bib</code> file below, or try the built-in demo paper in one click. All analysis runs locally in an isolated temporary folder.</p>
    <div class="pill-row">
      <span class="pill ok">Offline-first (0 uploads to third-party servers)</span>
      <span class="pill">Python {py_ver}</span>
      <span class="pill">{os_label}</span>
    </div>

    <form id="auditForm" action="/api/run" method="POST" aria-labelledby="audit-heading" novalidate>
      <fieldset>
        <legend>Select or drop files</legend>
        <div class="drop-grid">
          <div class="drop-zone" data-drop-target="manuscriptFile">
            <label for="manuscriptFile">Manuscript (<code>.tex</code> or <code>.md</code>)</label>
            <input type="file" id="manuscriptFile" name="manuscript" accept=".tex,.md,.txt">
            <span class="hint">Checks AI clichés, rhetorical filler, and citation keys.</span>
            <span class="file-chip" id="manuscriptStatus">No file selected</span>
          </div>
          <div class="drop-zone" data-drop-target="bibFile">
            <label for="bibFile">Bibliography (<code>.bib</code>, optional)</label>
            <input type="file" id="bibFile" name="bib" accept=".bib" multiple>
            <span class="hint">Audits duplicate keys, suspicious DOIs, and missing references.</span>
            <span class="file-chip" id="bibStatus">No file selected</span>
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>Privacy &amp; verification options</legend>
        <div class="check-row">
          <div class="check-item">
            <input type="checkbox" id="onlineCheck" name="online">
            <label for="onlineCheck">Enable online <code>.bib</code> lookup (queries public Crossref/OpenAlex/arXiv with citation metadata only)</label>
          </div>
        </div>
      </fieldset>

      <div class="btn-row">
        <button type="submit" id="runBtn" class="btn-primary">Run Audit &amp; Open Report</button>
        <button type="button" id="demoBtn">Try Built-in Demo</button>
        <button type="button" id="clearFilesBtn" class="btn-ghost">Clear Files</button>
      </div>
    </form>

    <div id="auditResult" class="status-box" role="status" aria-live="polite"></div>
  </section>

  <section class="card" aria-labelledby="install-heading">
    <h2 id="install-heading">2. One-Click Install into Your AI IDE</h2>
    <p class="card-lead">Install <code>paperguard-lite</code> into Antigravity, Claude Code, or Cursor with automatic <code>.orig.bak</code> / <code>.bak</code> backups and Cursor rule preservation—no terminal commands required.</p>

    <form id="installForm" action="/api/install" method="POST" aria-labelledby="install-heading">
      <fieldset>
        <legend>Target IDE</legend>
        <div class="radio-row">
          <div class="check-item">
            <input type="radio" id="ideAll" name="target_ide" value="all" checked>
            <label for="ideAll">All (Antigravity + Claude Code + Cursor)</label>
          </div>
          <div class="check-item">
            <input type="radio" id="ideAntigravity" name="target_ide" value="antigravity">
            <label for="ideAntigravity">Antigravity</label>
          </div>
          <div class="check-item">
            <input type="radio" id="ideClaude" name="target_ide" value="claude">
            <label for="ideClaude">Claude Code</label>
          </div>
          <div class="check-item">
            <input type="radio" id="ideCursor" name="target_ide" value="cursor">
            <label for="ideCursor">Cursor (Project only)</label>
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>Install scope</legend>
        <div class="radio-row">
          <div class="check-item">
            <input type="radio" id="scopeProject" name="scope" value="project" checked>
            <label for="scopeProject">Paper project folder (recommended)</label>
          </div>
          <div class="check-item">
            <input type="radio" id="scopeGlobal" name="scope" value="global">
            <label for="scopeGlobal">Global user home (Antigravity &amp; Claude Code)</label>
          </div>
        </div>
        <div id="projectDirWrap" style="margin-top:0.85rem">
          <label for="projectDir">Manuscript project folder path</label>
          <div class="path-row" style="margin-top:0.35rem">
            <input
              type="text"
              id="projectDir"
              name="project_dir"
              placeholder="/Users/you/papers/neurips2026"
              autocorrect="off"
              autocapitalize="none"
              spellcheck="false"
              data-error="projectDirError"
              aria-invalid="false"
              required
            >
            <button type="button" id="browseFolderBtn">Browse Folder…</button>
          </div>
          <span id="projectDirError" class="error-msg">Please choose or paste an existing folder path.</span>
        </div>
      </fieldset>

      <div class="btn-row">
        <button type="submit" id="installBtn" class="btn-primary">Install PaperGuard Lite Skill</button>
      </div>
    </form>

    <div id="installResult" class="status-box" role="status" aria-live="polite"></div>
  </section>

  <section class="card pro-banner" aria-label="PaperGuard Pro Upgrade">
    <div>
      <h3>Need Lossless BibTeX Repair, Formula Locks &amp; Reviewer-2 Table Audit?</h3>
      <p>PaperGuard Pro ($29 one-time) adds byte-preserving <code>.bib</code> patching (Crossref/DataCite/OpenAlex/DBLP/arXiv), vendored <code>pylatexenc</code> AST formula lockfiles, table-vs-prose number verification, and the 4-lens Word-Diff Studio.</p>
    </div>
    <a class="btn-link btn-pro" href="{PRO_UPGRADE_URL}" target="_blank" rel="noopener">Get PaperGuard Pro ($29) →</a>
  </section>
</main>

<footer>
  <span>PaperGuard Lite v2.5.0 · MIT License · Runs strictly on 127.0.0.1</span>
  <span>Close this tab or click Quit App when finished.</span>
</footer>

<script nonce="{nonce}">
(function () {{
  "use strict";
  const CSRF_TOKEN = {json.dumps(csrf_token)};

  // Accessible polite announcer for form validation
  const announcer = document.createElement("div");
  announcer.setAttribute("aria-live", "polite");
  announcer.className = "visually-hidden";
  document.body.append(announcer);

  function linkError(el, shown) {{
    const errorId = el.dataset.error;
    if (!errorId) return;
    const ids = (el.getAttribute("aria-describedby") || "").split(/\\s+/).filter(function (id) {{ return id && id !== errorId; }});
    if (shown) ids.push(errorId);
    if (ids.length) el.setAttribute("aria-describedby", ids.join(" "));
    else el.removeAttribute("aria-describedby");
  }}

  function syncAria(el, announce) {{
    if (!el || !el.matches || !el.matches("input, select, textarea")) return;
    let invalid = false;
    try {{ invalid = el.matches(":user-invalid"); }} catch (_) {{ invalid = !el.validity.valid; }}
    if (invalid) {{
      el.setAttribute("aria-invalid", "true");
      linkError(el, true);
      if (announce && el.dataset.error) {{
        const errNode = document.getElementById(el.dataset.error);
        if (errNode) announcer.textContent = errNode.textContent;
      }}
    }} else {{
      el.setAttribute("aria-invalid", "false");
      linkError(el, false);
    }}
  }}

  document.addEventListener("blur", function (e) {{ syncAria(e.target, true); }}, true);
  document.addEventListener("input", function (e) {{
    if (e.target && e.target.hasAttribute && e.target.hasAttribute("aria-invalid")) syncAria(e.target, false);
  }});
  document.addEventListener("invalid", function (e) {{ syncAria(e.target, false); }}, true);

  // Dark-mode two-state toggle following modern-web-guidance (reverts to system light dark)
  const themeMeta = document.querySelector('meta[name="color-scheme"]');
  const themeBtn = document.getElementById("themeToggle");
  function systemIsDark() {{
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }}
  function currentRenderedScheme() {{
    const stored = (() => {{ try {{ return localStorage.getItem("pg-color-scheme"); }} catch (_) {{ return null; }} }})();
    if (stored === "light" || stored === "dark") return stored;
    return systemIsDark() ? "dark" : "light";
  }}
  function refreshThemeLabel() {{
    const rendered = currentRenderedScheme();
    themeBtn.textContent = rendered === "dark" ? "Light mode" : "Dark mode";
  }}
  refreshThemeLabel();
  themeBtn.addEventListener("click", function () {{
    const target = currentRenderedScheme() === "dark" ? "light" : "dark";
    const sys = systemIsDark() ? "dark" : "light";
    try {{
      if (target === sys) {{
        localStorage.removeItem("pg-color-scheme");
        themeMeta.content = "light dark";
      }} else {{
        localStorage.setItem("pg-color-scheme", target);
        themeMeta.content = target;
      }}
    }} catch (_) {{
      themeMeta.content = target;
    }}
    refreshThemeLabel();
  }});

  // Drag & drop + file selection state
  const msInput = document.getElementById("manuscriptFile");
  const bibInput = document.getElementById("bibFile");
  const msStatus = document.getElementById("manuscriptStatus");
  const bibStatus = document.getElementById("bibStatus");

  const pickedFiles = {{ manuscript: null, bibs: [] }};

  function updateFileChips() {{
    if (pickedFiles.manuscript) {{
      msStatus.textContent = "Selected: " + pickedFiles.manuscript.name + " (" + pickedFiles.manuscript.size.toLocaleString() + " B)";
    }} else {{
      msStatus.textContent = "No file selected";
    }}
    if (pickedFiles.bibs.length) {{
      const names = pickedFiles.bibs.map(function (f) {{ return f.name; }}).join(", ");
      bibStatus.textContent = "Selected: " + names;
    }} else {{
      bibStatus.textContent = "No file selected";
    }}
  }}

  msInput.addEventListener("change", function () {{
    pickedFiles.manuscript = msInput.files && msInput.files[0] ? msInput.files[0] : null;
    updateFileChips();
  }});
  bibInput.addEventListener("change", function () {{
    pickedFiles.bibs = bibInput.files ? Array.from(bibInput.files) : [];
    updateFileChips();
  }});

  document.querySelectorAll(".drop-zone").forEach(function (zone) {{
    zone.addEventListener("dragover", function (e) {{
      e.preventDefault();
      zone.classList.add("drag-over");
    }});
    zone.addEventListener("dragleave", function () {{
      zone.classList.remove("drag-over");
    }});
    zone.addEventListener("drop", function (e) {{
      e.preventDefault();
      zone.classList.remove("drag-over");
      const files = e.dataTransfer && e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
      if (!files.length) return;
      if (zone.dataset.dropTarget === "manuscriptFile") {{
        pickedFiles.manuscript = files[0];
      }} else {{
        pickedFiles.bibs = files;
      }}
      updateFileChips();
    }});
  }});

  document.getElementById("clearFilesBtn").addEventListener("click", function () {{
    msInput.value = "";
    bibInput.value = "";
    pickedFiles.manuscript = null;
    pickedFiles.bibs = [];
    updateFileChips();
  }});

  async function apiPost(url, bodyObj) {{
    const resp = await fetch(url, {{
      method: "POST",
      headers: {{
        "Content-Type": "application/json",
        "X-PaperGuard-Token": CSRF_TOKEN
      }},
      body: JSON.stringify(bodyObj)
    }});
    const data = await resp.json().catch(function () {{ return {{ ok: false, error: "Invalid server response." }}; }});
    if (!resp.ok || !data.ok) {{
      throw new Error(data.error || ("HTTP " + resp.status));
    }}
    return data;
  }}

  function clearNode(node) {{
    while (node.firstChild) node.removeChild(node.firstChild);
  }}

  function renderStatus(box, isError, titleText, items, links) {{
    clearNode(box);
    box.className = "status-box visible " + (isError ? "error" : "success");
    const title = document.createElement("div");
    title.className = "status-title";
    title.textContent = titleText;
    box.appendChild(title);
    if (items && items.length) {{
      const ul = document.createElement("ul");
      ul.className = "status-list";
      items.forEach(function (txt) {{
        const li = document.createElement("li");
        li.textContent = txt;
        ul.appendChild(li);
      }});
      box.appendChild(ul);
    }}
    if (links && links.length) {{
      const row = document.createElement("div");
      row.className = "btn-row";
      links.forEach(function (lnk) {{
        const a = document.createElement("a");
        a.className = "btn-link" + (lnk.primary ? " btn-primary" : "");
        a.href = lnk.href;
        if (lnk.newTab) {{
          a.target = "_blank";
          a.rel = "noopener";
        }}
        a.textContent = lnk.label;
        row.appendChild(a);
      }});
      box.appendChild(row);
    }}
  }}

  const auditForm = document.getElementById("auditForm");
  const auditResult = document.getElementById("auditResult");
  const runBtn = document.getElementById("runBtn");
  const demoBtn = document.getElementById("demoBtn");
  const onlineCheck = document.getElementById("onlineCheck");

  async function executeAudit(useDemo) {{
    runBtn.disabled = true;
    demoBtn.disabled = true;
    renderStatus(auditResult, false, useDemo ? "Running built-in demo audit…" : "Running local PaperGuard Lite audit…", [], []);
    try {{
      const payload = {{ demo: useDemo, online: onlineCheck.checked }};
      if (!useDemo) {{
        if (pickedFiles.manuscript) {{
          payload.manuscript = {{
            name: pickedFiles.manuscript.name,
            content: await pickedFiles.manuscript.text()
          }};
        }}
        if (pickedFiles.bibs.length) {{
          payload.bibs = [];
          for (const bf of pickedFiles.bibs) {{
            payload.bibs.push({{ name: bf.name, content: await bf.text() }});
          }}
        }}
      }}
      const res = await apiPost("/api/run", payload);
      renderStatus(
        auditResult,
        false,
        "Audit ready · " + res.source_label + " (" + res.mode + ")",
        [
          "AI cliché / style findings: " + res.slop_hits,
          "BibTeX alerts & warnings: " + res.bib_alerts,
          "Standalone HTML report size: " + res.report_bytes.toLocaleString() + " bytes"
        ],
        [
          {{ href: res.report_url, label: "Open Full Audit Report ↗", primary: true, newTab: true }},
          {{ href: res.download_url, label: "Download HTML Report", primary: false, newTab: false }}
        ]
      );
    }} catch (err) {{
      renderStatus(auditResult, true, "Audit could not complete", [err.message || String(err)], []);
    }} finally {{
      runBtn.disabled = false;
      demoBtn.disabled = false;
    }}
  }}

  auditForm.addEventListener("submit", function (e) {{
    e.preventDefault();
    executeAudit(false);
  }});
  demoBtn.addEventListener("click", function () {{
    executeAudit(true);
  }});

  // IDE Installer form logic
  const installForm = document.getElementById("installForm");
  const installBtn = document.getElementById("installBtn");
  const installResult = document.getElementById("installResult");
  const projectDirInput = document.getElementById("projectDir");
  const projectDirWrap = document.getElementById("projectDirWrap");
  const browseFolderBtn = document.getElementById("browseFolderBtn");

  function syncScopeUI() {{
    const scope = installForm.elements.scope.value;
    const isProject = scope === "project";
    projectDirWrap.hidden = !isProject;
    projectDirInput.required = isProject;
    if (!isProject) {{
      projectDirInput.setAttribute("aria-invalid", "false");
      linkError(projectDirInput, false);
    }}
  }}
  installForm.querySelectorAll('input[name="scope"]').forEach(function (r) {{
    r.addEventListener("change", syncScopeUI);
  }});
  syncScopeUI();

  browseFolderBtn.addEventListener("click", async function () {{
    browseFolderBtn.disabled = true;
    try {{
      const res = await apiPost("/api/pick-folder", {{}});
      if (res.path) {{
        projectDirInput.value = res.path;
        syncAria(projectDirInput, false);
      }} else if (!res.supported) {{
        renderStatus(installResult, false, "Paste folder path directly", ["Native folder picker is unavailable in this environment. Paste your paper folder path into the box above."], []);
      }}
    }} catch (err) {{
      renderStatus(installResult, true, "Folder picker error", [err.message || String(err)], []);
    }} finally {{
      browseFolderBtn.disabled = false;
    }}
  }});

  installForm.addEventListener("submit", async function (e) {{
    e.preventDefault();
    if (!installForm.checkValidity()) {{
      const firstInvalid = installForm.querySelector(":invalid");
      if (firstInvalid) {{
        syncAria(firstInvalid, true);
        firstInvalid.focus();
      }}
      return;
    }}
    installBtn.disabled = true;
    renderStatus(installResult, false, "Installing PaperGuard Lite skill…", [], []);
    try {{
      const res = await apiPost("/api/install", {{
        target_ide: installForm.elements.target_ide.value,
        scope: installForm.elements.scope.value,
        project_dir: projectDirInput.value
      }});
      const lines = (res.installed || []).map(function (p) {{ return "Installed → " + p; }}).concat(res.notes || []);
      renderStatus(installResult, false, res.message, lines, []);
    }} catch (err) {{
      renderStatus(installResult, true, "Installation failed", [err.message || String(err)], []);
    }} finally {{
      installBtn.disabled = false;
    }}
  }});

  document.getElementById("quitBtn").addEventListener("click", async function () {{
    try {{
      await apiPost("/api/shutdown", {{}});
    }} catch (_) {{}}
    document.body.textContent = "PaperGuard Lite local app has stopped. You may close this browser tab.";
  }});
}})();
</script>
</body>
</html>
"""
    return html


def create_server(host: str = "127.0.0.1", port: int = 0) -> tuple[socketserver.TCPServer, _LiteSession]:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("PaperGuard Lite App only binds to loopback (127.0.0.1) for local security.")
    session = _LiteSession()

    class _Handler(http.server.BaseHTTPRequestHandler):
        server_version = "PaperGuardLiteLocal/2.5"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_common_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
            self.send_header("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
            self.send_header("Cache-Control", "no-store")

        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._send_common_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check_post_security(self) -> bool:
            sfs = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if sfs and sfs not in {"same-origin", "none"}:
                self._send_json(403, {"ok": False, "error": "Cross-site request rejected."})
                return False
            origin = (self.headers.get("Origin") or "").strip()
            expected_origin = f"http://127.0.0.1:{self.server.server_address[1]}"
            expected_local = f"http://localhost:{self.server.server_address[1]}"
            if origin and origin not in {expected_origin, expected_local}:
                self._send_json(403, {"ok": False, "error": "Unexpected Origin rejected."})
                return False
            tok = self.headers.get("X-PaperGuard-Token") or ""
            if not hmac.compare_digest(tok, session.token):
                self._send_json(403, {"ok": False, "error": "Invalid or missing session token."})
                return False
            return True

        def _read_json_body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"ok": False, "error": "Invalid Content-Length."})
                return None
            if length < 0 or length > MAX_BODY_BYTES:
                self._send_json(413, {"ok": False, "error": "Upload exceeds 15 MiB local limit."})
                return None
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"ok": False, "error": "Malformed JSON payload."})
                return None
            if not isinstance(parsed, dict):
                self._send_json(400, {"ok": False, "error": "Expected JSON object."})
                return None
            return parsed

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/":
                nonce = secrets.token_urlsafe(16)
                body = _render_app_html(nonce, session.token).encode("utf-8")
                self.send_response(200)
                self._send_common_headers()
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'self'; img-src 'self' data:; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                )
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path.startswith("/report/"):
                run_id = parsed.path[len("/report/") :].strip("/")
                with session.lock:
                    rec = session.reports.get(run_id)
                if not rec:
                    self._send_json(404, {"ok": False, "error": "Report not found or expired."})
                    return
                body = rec["html"]
                qs = urllib.parse.parse_qs(parsed.query)
                self.send_response(200)
                self._send_common_headers()
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "img-src data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
                self.send_header("Content-Type", "text/html; charset=utf-8")
                if qs.get("download") == ["1"]:
                    self.send_header("Content-Disposition", f'attachment; filename="{rec["filename"]}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self._send_json(404, {"ok": False, "error": "Not found."})

        def do_POST(self) -> None:
            if not self._check_post_security():
                return
            parsed = urllib.parse.urlsplit(self.path)
            payload = self._read_json_body()
            if payload is None:
                return

            try:
                if parsed.path == "/api/run":
                    self._send_json(200, session.run_audit(payload))
                    return
                if parsed.path == "/api/install":
                    result = install_lite_skills(
                        str(payload.get("target_ide", "all")),
                        str(payload.get("scope", "project")),
                        payload.get("project_dir"),
                    )
                    self._send_json(200, result)
                    return
                if parsed.path == "/api/pick-folder":
                    self._send_json(200, {"ok": True, **pick_native_folder()})
                    return
                if parsed.path == "/api/shutdown":
                    self._send_json(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
                return

            self._send_json(404, {"ok": False, "error": "Unknown API endpoint."})

    class _ReusableServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = _ReusableServer(("127.0.0.1", port), _Handler)
    session.server = srv
    return srv, session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PaperGuard Lite — Local Browser App & IDE Installer")
    parser.add_argument("--port", type=int, default=0, help="Local port on 127.0.0.1 (0 = auto free port)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the system web browser")
    args = parser.parse_args(argv)

    srv, session = create_server("127.0.0.1", args.port)
    atexit.register(session.cleanup)
    actual_port = srv.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"[PaperGuard Lite] Local app running at {url}")
    print("[PaperGuard Lite] Press Ctrl+C or click 'Quit App' in the browser to stop.")

    if not args.no_open:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        session.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
