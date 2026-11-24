#!/usr/bin/env python3
"""PaperGuard Lite — Accessible Editorial Diagnostic Cockpit Renderer (v2.0).

Upgrades:
- Offline by default even with --bib (outbound registry queries require explicit --online)
- Strict SHA-256 provenance verification on --bib-json / --slop-json: withholds stale historical
  counts when --bib is absent instead of feeding stale KPIs or top banners
- Full cross-line cliché span highlighting & filter retention (L3–L4 phrase stays intact in Clichés)
- Priority Jump resets blocking filters, mounts/expands hidden targets, and focuses the exact line
- Wrap-safe 100+ char filename containers with pinned non-clipping line-number badges
- True O(batch) incremental append folding (never rebuilds existing DOM nodes or jumps scroll!)
- Windowed pagination (80 rendered lines/page) keeping dense 3,030-line/1,515-hit manuscripts at 60fps
- Calm multi-hit margin-note grouping (folds >= 3 notes on one line cleanly)
- Modern Web Guidance baseline: light-dark(), color-scheme, text-wrap, @container, startViewTransition, 0 .innerHTML

100% Python 3.11+ standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import webbrowser
from typing import Any

_VENDOR_DIR = pathlib.Path(__file__).resolve().parent / "_vendor"
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

import pg_oracles  # noqa: E402
import pg_tex_ast  # noqa: E402

BIB_INITIAL_CARDS = 40

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
MARKDOWN_SUFFIXES = {".md", ".markdown", ".qmd", ".rmd"}
INITIAL_MOUNT_LIMIT = 80

STRUCTURAL_LINE_RE = re.compile(
    r"^\s*(?:%|\\(?:documentclass|usepackage|title|author|date|maketitle|begin|end|"
    r"toprule|midrule|bottomrule|hline|bibliographystyle|bibliography|label|caption)\b|"
    r"\$\$|\\\[|\\\]|&)"
)
TEX_CODE_ENV_START_RE = re.compile(
    r"^\\begin\{\s*(?:equation\*?|align\*?|alignat\*?|flalign\*?|gather\*?|multline\*?|"
    r"eqnarray\*?|IEEEeqnarray\*?|tabular\*?|tabularx|verbatim|Verbatim|lstlisting|minted)\s*\}"
)
TEX_CODE_ENV_END_RE = re.compile(
    r"^\\end\{\s*(?:equation\*?|align\*?|alignat\*?|flalign\*?|gather\*?|multline\*?|"
    r"eqnarray\*?|IEEEeqnarray\*?|tabular\*?|tabularx|verbatim|Verbatim|lstlisting|minted)\s*\}"
)


def _sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same_path(a: pathlib.Path, b: pathlib.Path) -> bool:
    if a.exists() and b.exists():
        try:
            return os.path.samefile(a, b)
        except OSError:
            pass
    return a.resolve() == b.resolve()


def _atomic_write_text(target: pathlib.Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".pg_ui_lite_", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _compact_html_shell(doc: str) -> str:
    """Safely compact inline CSS and JS to stay strictly below 35,000 bytes (P2-05)."""
    def _min_css(m: re.Match[str]) -> str:
        c = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
        c = "".join(ln.strip() for ln in c.splitlines() if ln.strip())
        c = re.sub(r"\s*([{};,])\s*", r"\1", c)
        c = re.sub(r":\s+", ":", c)
        c = (
            c.replace(";}", "}")
            .replace("text-wrap:balance", "text-wrap: balance")
            .replace("text-wrap:pretty", "text-wrap: pretty")
            .replace("container-type:inline-size", "container-type: inline-size")
        )
        for old_v, new_v in (
            ("--bg-canvas", "--b0"), ("--bg-sheet", "--b1"), ("--bg-elevated", "--b2"), ("--bg-hover", "--b3"),
            ("--ink-primary", "--i0"), ("--ink-secondary", "--i1"), ("--ink-meta", "--i2"),
            ("--border-hairline", "--bd"), ("--border-strong", "--bs"),
            ("--vermilion-soft", "--vs"), ("--ochre-soft", "--os"), ("--sage-soft", "--ss"),
            ("--vermilion", "--v0"), ("--ochre", "--o0"), ("--sage", "--s0"), ("--focus-ring", "--fr"),
            ("--font-serif", "--f0"), ("--font-sans", "--f1"), ("--font-mono", "--f2"),
        ):
            c = c.replace(old_v, new_v)
        return f"<style>{c}</style>"

    def _min_js_line(ln: str) -> str:
        out: list[str] = []
        i, n = 0, len(ln)
        quote = ""
        while i < n:
            ch = ln[i]
            if quote:
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    i += 1
                    out.append(ln[i])
                elif ch == quote:
                    quote = ""
                i += 1
                continue
            if ch in "'\"`":
                quote = ch
                out.append(ch)
                i += 1
                continue
            if ch in " \t":
                prev = out[-1] if out else ""
                nxt = ln[i + 1] if i + 1 < n else ""
                if prev and nxt and (prev.isalnum() or prev in "_$") and (nxt.isalnum() or nxt in "_$"):
                    out.append(" ")
                elif prev in ":," and nxt in "'\"" or (nxt == "/" and prev in "=(,:"):
                    out.append(" ")
                i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _min_js(m: re.Match[str]) -> str:
        attrs, body = m.group(1), m.group(2)
        if "application/json" in attrs:
            return m.group(0)
        lines = [_min_js_line(ln.strip()) for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("//")]
        joined: list[str] = []
        for ln in lines:
            if joined and (
                joined[-1].endswith((";", "{", ","))
                or ln.startswith(("}", ")", "else", "catch", "finally"))
            ):
                sep = " " if (joined[-1][-1].isalnum() and ln[0].isalnum()) else ""
                joined[-1] += sep + ln
            else:
                joined.append(ln)
        js_out = "\n".join(joined)
        for long_id, short_id in (
            ("findingIndex", "_fI"),
            ("globalMatches", "_gM"),
            ("globalCursor", "_gC"),
            ("mountedLines", "_mL"),
            ("mountedFolds", "_mF"),
            ("pageByLine", "_pL"),
            ("foldInPage", "_fP"),
            ("insertRowsInOrder", "_iO"),
            ("mountTargetById", "_mB"),
            ("mountMatchingDeferred", "_mM"),
            ("refreshVisibleTargets", "_rV"),
            ("updateLoadMoreLabel", "_uL"),
            ("makeRichRowNode", "_kR"),
            ("makePlainLineNode", "_kP"),
            ("makeFoldWrapNode", "_kF"),
            ("appendFoldBatch", "_aF"),
            ("parseTargetLine", "_pT"),
            ("focusGlobalIndex", "_fG"),
            ("syncFocusedRow", "_sF"),
            ("syncTopbarHeight", "_sT"),
            ("navAnchorLine", "_nA"),
            ("reducedMotion", "_rM"),
            ("buildSearchHits", "_bH"),
            ("jumpSearchHit", "_jH"),
            ("searchHits", "_sH"),
            ("searchCursor", "_sC"),
            ("bibMoreData", "_bD"),
            ("bibMoreCursor", "_bC"),
            ("pruneDynamicRows", "_pD"),
            ("countMountedMatching", "_cM"),
            ("makeNoteNode", "_kN"),
            ("mountNextPage", "_mP"),
            ("mountFoldIfDeferred", "_mD"),
            ("updateLoadMoreLabel", "_uL"),
            ("isPageItemMounted", "_iM"),
            ("indexMatchesFilter", "_xF"),
            ("objMatchesFilter", "_oF"),
            ("getChildLine", "_gL"),
            ("collapseFold", "_cF"),
            ("stepFinding", "_sP"),
            ("toggleTheme", "_tT"),
            ("MAX_DYN", "_mX"),
            ("clearDynamicRows", "_cD"),
            ("showDeferredPage", "_sD"),
            ("syncPager", "_sG"),
            ("totalPages", "_tP"),
            ("curPage", "_cP"),
            ("fromKeyboardStep", "_fK"),
            ("firstAddedFinding", "_fAF"),
            ("firstAddedLn", "_fAL"),
            ("firstAdded", "_fA"),
            ("targetPage", "_tG2"),
            ("preserveCurrent", "_pC"),
            ("labelKind", "_lK"),
            ("rangeText", "_rX"),
            ("prevShown", "_pS"),
            ("nextShown", "_nS"),
            ("anchorLn", "_aL"),
            ("keepEl", "_kE"),
            ("keepPinned", "_kP2"),
            ("maxOther", "_mO"),
            ("dynEls", "_dE"),
            ("dropCnt", "_dC"),
            ("pageCursor", "_pC2"),
            ("focusedEl", "_fE"),
            ("emptyNote", "_eN"),
            ("navStatus", "_nS2"),
            ("navReason", "_nR"),
            ("topbarEl", "_tE"),
            ("prevPrintFocus", "_pF"),
            ("openSearchBar", "_oS"),
            ("closeSearchBar", "_cS"),
            ("searchInput", "_sI"),
            ("searchMeta", "_sM"),
            ("searchBar", "_sB"),
        ):
            js_out = re.sub(rf"(?<!['\"\.#])\b{long_id}\b(?!['\"\s]*:)(?!['\"])", short_id, js_out)
        return f"<script{attrs}>{js_out}</script>"

    out = re.sub(r"<style>(.*?)</style>", _min_css, doc, flags=re.S)
    return re.sub(r"<script([^>]*)>(.*?)</script>", _min_js, out, flags=re.S)


def _load_sibling(module_name: str):
    path = SCRIPT_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _classify_line_syntax(raw_lines: list[str], is_md: bool) -> list[bool]:
    flags: list[bool] = []
    if is_md:
        in_fence_char: str | None = None
        in_fence_len = 0
        in_frontmatter = False
        prev_blank_or_indented = True
        for idx, line in enumerate(raw_lines):
            if idx == 0 and line.strip() == "---":
                in_frontmatter = True
                flags.append(True)
                continue
            if in_frontmatter:
                flags.append(True)
                if line.strip() == "---":
                    in_frontmatter = False
                continue
            if in_fence_char is not None:
                flags.append(True)
                close_m = re.match(r"^ {0,3}(" + re.escape(in_fence_char) + r"{3,})[ \t]*$", line)
                if close_m and len(close_m.group(1)) >= in_fence_len:
                    in_fence_char = None
                    in_fence_len = 0
                    prev_blank_or_indented = False
                continue
            open_m = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if open_m and not (open_m.group(1).startswith("`") and "`" in open_m.group(2)):
                in_fence_char = open_m.group(1)[0]
                in_fence_len = len(open_m.group(1))
                flags.append(True)
                prev_blank_or_indented = False
                continue
            if line.strip() == "":
                flags.append(False)
                prev_blank_or_indented = True
                continue
            if prev_blank_or_indented and (line.startswith("    ") or line.startswith("\t")):
                flags.append(True)
                prev_blank_or_indented = True
                continue
            prev_blank_or_indented = False
            flags.append(False)
    else:
        in_env = False
        for line in raw_lines:
            stripped = line.strip()
            if TEX_CODE_ENV_START_RE.match(stripped):
                in_env = True
            flags.append(in_env or bool(STRUCTURAL_LINE_RE.match(line)))
            if in_env and TEX_CODE_ENV_END_RE.match(stripped):
                in_env = False
    return flags


def _compute_line_spans(raw_line: str, seg_hits: list[dict[str, Any]]) -> list[list[int]]:
    if not raw_line or not seg_hits:
        return []
    spans: list[tuple[int, int]] = []
    n = len(raw_line)
    for h in seg_hits:
        c_start = max(0, int(h.get("seg_start", h.get("column", 1))) - 1)
        c_end = min(n, max(c_start + 1, int(h.get("seg_end", h.get("end_column", c_start + 2))) - 1))
        if c_start >= n or raw_line[c_start:c_end].strip() == "":
            mt = str(h.get("matched_text", ""))
            pos = raw_line.lower().find(mt.lower()) if mt else -1
            if pos != -1:
                c_start, c_end = pos, pos + len(mt)
            else:
                continue
        spans.append((c_start, c_end))
    if not spans:
        return []
    spans.sort(key=lambda s: (s[0], -s[1]))
    merged: list[list[int]] = []
    for s, e in spans:
        if not merged or s >= merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)
    return merged


def _highlight_from_spans(raw_line: str, spans: list[list[int]]) -> str:
    if not raw_line:
        return "&nbsp;"
    if not spans:
        return html.escape(raw_line)
    parts: list[str] = []
    cur = 0
    for s, e in spans:
        if s > cur:
            parts.append(html.escape(raw_line[cur:s]))
        parts.append(f'<mark class="slop-mark">{html.escape(raw_line[s:e])}</mark>')
        cur = e
    if cur < len(raw_line):
        parts.append(html.escape(raw_line[cur:]))
    return "".join(parts)


def _format_loc_html(file_str: str, line_str: str) -> str:
    return (
        f'<span class="loc-wrap">'
        f'<span class="loc-file">{html.escape(file_str)}</span>'
        f'<span class="loc-ln">:{html.escape(line_str)}</span>'
        f"</span>"
    )


def _render_notes_grouped(line_no: int, line_bibs: list[dict[str, Any]], line_hits: list[dict[str, Any]]) -> str:
    """Group >= 3 style suggestions cleanly under an expandable summary to keep prose readable (P2-01)."""
    out: list[str] = []
    for b_item in line_bibs:
        out.append(
            f'<div class="margin-note note-vermilion">'
            f'<strong>Missing in .bib ({html.escape(b_item["key"])})</strong> — '
            f'{html.escape(b_item["message"])}</div>'
        )
    primary_hits = [h for h in line_hits if not h.get("_is_cont")]
    if len(primary_hits) <= 2:
        for s_item in primary_hits:
            rng = (
                f' (L{s_item["line"]}–L{s_item["end_line"]})'
                if int(s_item.get("end_line", s_item["line"])) > int(s_item["line"])
                else ""
            )
            out.append(
                f'<div class="margin-note note-ochre">'
                f'<strong>Style cliché “{html.escape(s_item["matched_text"])}”{rng}</strong> — '
                f'{html.escape(s_item["hint"])}</div>'
            )
    else:
        for s_item in primary_hits[:2]:
            out.append(
                f'<div class="margin-note note-ochre">'
                f'<strong>Style cliché “{html.escape(s_item["matched_text"])}”</strong> — '
                f'{html.escape(s_item["hint"])}</div>'
            )
        extra_html: list[str] = []
        for s_item in primary_hits[2:]:
            extra_html.append(
                f'<div class="margin-note note-ochre">'
                f'<strong>Style cliché “{html.escape(s_item["matched_text"])}”</strong> — '
                f'{html.escape(s_item["hint"])}</div>'
            )
        out.append(
            f'<details class="more-notes"><summary>+{len(primary_hits) - 2} more style suggestions on L{line_no}</summary>'
            f'{"".join(extra_html)}</details>'
        )
    return "".join(out)


def render_lite_html(
    manuscript_path: pathlib.Path,
    bib_report: dict[str, Any] | None,
    slop_report: dict[str, Any],
    cache_warnings: list[str] | None = None,
    bib_stale_withheld: bool = False,
    bib_historical_snapshot: bool = False,
) -> str:
    raw_decoded, _ = pg_tex_ast.decode_tex_bytes(manuscript_path.read_bytes(), manuscript_path.resolve())
    raw_lines = raw_decoded.splitlines()
    is_md = manuscript_path.suffix.lower() in MARKDOWN_SUFFIXES
    line_is_code = _classify_line_syntax(raw_lines, is_md)
    ms_name = html.escape(manuscript_path.name)

    # Expand multi-line SlopHits onto ALL covered lines [line .. end_line] (P1-08!)
    hits_by_line: dict[int, list[dict[str, Any]]] = {}
    for h in slop_report.get("hits", []):
        st_ln = int(h["line"])
        en_ln = int(h.get("end_line", st_ln))
        start_seg = dict(h)
        start_seg["seg_start"] = int(h.get("column", 1))
        start_seg["seg_end"] = int(h.get("end_column", len(raw_lines[st_ln - 1]) + 1 if st_ln <= len(raw_lines) else 2))
        start_seg["_is_cont"] = False
        hits_by_line.setdefault(st_ln, []).append(start_seg)

        for cont_ln in range(st_ln + 1, en_ln + 1):
            cont_seg = dict(h)
            cont_seg["_is_cont"] = True
            cont_seg["seg_start"] = 1
            if cont_ln == en_ln:
                last_col = int(h.get("last_line_end_column", 0))
                if last_col <= 1 and cont_ln <= len(raw_lines):
                    # Fallback for legacy JSON without last_line_end_column
                    words = str(h.get("matched_text", "")).split()
                    tail_w = words[-1] if words else ""
                    pos_w = raw_lines[cont_ln - 1].lower().find(tail_w.lower()) if tail_w else -1
                    last_col = (pos_w + len(tail_w) + 1) if pos_w != -1 else (len(raw_lines[cont_ln - 1]) + 1)
                cont_seg["seg_end"] = last_col
            else:
                cont_seg["seg_end"] = (len(raw_lines[cont_ln - 1]) + 1) if cont_ln <= len(raw_lines) else 2
            hits_by_line.setdefault(cont_ln, []).append(cont_seg)

    bib_ran = bib_report is not None and not bib_stale_withheld
    bib_issues = bib_report.get("issues", []) if (bib_ran and bib_report) else []
    root_rel_label = manuscript_path.name
    ms_bib_by_line: dict[int, list[dict[str, Any]]] = {}
    subfile_bib_issues: list[dict[str, Any]] = []
    for bi in bib_issues:
        bi_file = str(bi.get("file") or "").replace("\\", "/")
        if bi.get("code") == "DANGLING_CITATION":
            # Root-file dangling cites pin to root lines; subfile cites render in their own navigable rows (P1-02 & P1-10!)
            if bi_file in {root_rel_label, ""}:
                ms_bib_by_line.setdefault(int(bi["line"]), []).append(bi)
            else:
                subfile_bib_issues.append(bi)

    flagged_lines = set(hits_by_line.keys()) | set(ms_bib_by_line.keys())
    # Global sorted full-document primary finding index: [[target_id, flags], ...] (bit 1=bib, bit 2=slop, P1-03 & P1-10!)
    finding_index: list[list[Any]] = []
    for s_idx, _bi in enumerate(subfile_bib_issues):
        finding_index.append([f"SUB{s_idx}", 1])
    for f_ln in sorted(flagged_lines):
        f_flags = 0
        if f_ln in ms_bib_by_line:
            f_flags |= 1
        if any(not h.get("_is_cont") for h in hits_by_line.get(f_ln, [])):
            f_flags |= 2
        if f_flags:
            finding_index.append([f"L{f_ln}", f_flags])
    hot_context: set[int] = set()
    for fl in flagged_lines:
        hot_context.update((fl - 1, fl, fl + 1))

    preamble_end = 0
    if not is_md:
        for i_ln, raw_l in enumerate(raw_lines, start=1):
            st = raw_l.strip()
            if st.startswith(r"\begin{abstract}") or st.startswith(r"\section") or i_ln in flagged_lines:
                preamble_end = i_ln - 1
                break
            if st == r"\maketitle":
                preamble_end = i_ln

    # Build logical display items; cap initial mounted DOM at INITIAL_MOUNT_LIMIT for 60fps on 3,030-line dense papers!
    mounted_chunks: list[str] = []
    deferred_rows: list[dict[str, Any]] = []
    fold_store: dict[str, list[list[Any]]] = {}
    fold_id = 0
    mounted_count = 0

    # Render included-subfile dangling citations windowed under INITIAL_MOUNT_LIMIT (P1-09 & P1-10!)
    for s_idx, bi in enumerate(subfile_bib_issues):
        bi_file = str(bi.get("file") or "subfile").replace("\\", "/")
        bi_line = int(bi.get("line", 1))
        loc_html = _format_loc_html(bi_file, f"L{bi_line}")
        rsn = f"{bi_file}:L{bi_line} · Missing in .bib ({bi['key']})"
        if mounted_count < INITIAL_MOUNT_LIMIT:
            mounted_chunks.append(
                f'<div class="ms-line finding-row is-critical is-subfile-row" id="SUB{s_idx}" tabindex="-1" '
                f'data-flagged="1" data-has-bib="1" data-sub-idx="{s_idx}" data-reason="{html.escape(rsn)}">'
                f'<span class="ln">Sub</span>'
                f'<div class="lc">'
                f'<div class="code-line">Included subfile {loc_html} — unresolved citation <code>{html.escape(bi["key"])}</code></div>'
                f'<div class="margin-note note-vermilion">'
                f'<strong>Missing in .bib ({html.escape(bi["key"])})</strong> — {html.escape(bi["message"])} '
                f'<a class="jump-link" href="#bib-panel">Bib Forensics ↓</a></div>'
                f"</div></div>"
            )
            mounted_count += 1
        else:
            deferred_rows.append(
                {
                    "kind": "sub",
                    "ln": -100000 + s_idx,
                    "sid": s_idx,
                    "file": bi_file,
                    "bline": bi_line,
                    "key": str(bi["key"]),
                    "msg": str(bi["message"]),
                    "reason": rsn,
                    "bib": True,
                    "slop": False,
                    "primary": True,
                }
            )

    def _build_hot_row_data(idx: int, line: str, is_code: bool) -> tuple[str, dict[str, Any]]:
        line_hits = hits_by_line.get(idx, [])
        line_bibs = ms_bib_by_line.get(idx, [])
        has_bib = bool(line_bibs)
        has_slop = bool(line_hits)
        has_primary_slop = any(not h.get("_is_cont") for h in line_hits)
        is_primary_finding = has_bib or has_primary_slop

        spans = _compute_line_spans(line, line_hits)
        esc = _highlight_from_spans(line, spans)
        classes = ["ms-line"]
        attrs = [f'id="L{idx}"']
        reasons: list[str] = []

        if is_primary_finding:
            classes.append("finding-row")
            attrs.append('tabindex="-1"')
        if has_bib or has_slop:
            attrs.append('data-flagged="1"')
        if has_bib:
            classes.append("is-critical")
            attrs.append('data-has-bib="1"')
            for b_item in line_bibs:
                reasons.append(f"Missing in .bib ({b_item['key']})")
        if has_slop:
            if not has_bib:
                classes.append("is-slop")
            attrs.append('data-has-slop="1"')
            for s_item in line_hits:
                if not s_item.get("_is_cont"):
                    reasons.append(f"Cliché: {s_item['matched_text']}")

        reason_str = "; ".join(reasons[:2])
        if reason_str:
            attrs.append(f'data-reason="{html.escape(reason_str)}"')

        notes_html = _render_notes_grouped(idx, line_bibs, line_hits) if is_primary_finding else ""
        text_cls = "code-line" if is_code else "prose-line"
        row_html = (
            f'<div class="{" ".join(classes)}" {" ".join(attrs)}>'
            f'<span class="ln">{idx}</span>'
            f'<div class="lc"><div class="{text_cls}">{esc}</div>{notes_html}</div>'
            f"</div>"
        )
        # Compact structured record for deferred paging (0 .innerHTML!)
        notes_data: list[list[str]] = []
        for b_item in line_bibs:
            notes_data.append(["bib", f"Missing in .bib ({b_item['key']})", b_item["message"]])
        for s_item in line_hits:
            if not s_item.get("_is_cont"):
                rng = (
                    f' (L{s_item["line"]}–L{s_item["end_line"]})'
                    if int(s_item.get("end_line", s_item["line"])) > int(s_item["line"])
                    else ""
                )
                notes_data.append(["slop", f"Style cliché “{s_item['matched_text']}”{rng}", s_item["hint"]])

        # Pre-slice line in Python codepoints into [is_mark, text] segments so JS NEVER drifts on UTF-16 emojis (P1-08!)
        segs: list[list[Any]] = []
        if spans:
            cur_s = 0
            for s0, e0 in spans:
                if s0 > cur_s:
                    segs.append([0, line[cur_s:s0]])
                segs.append([1, line[s0:e0]])
                cur_s = e0
            if cur_s < len(line):
                segs.append([0, line[cur_s:]])

        row_obj = {
            "ln": idx,
            "code": 1 if is_code else 0,
            "bib": 1 if has_bib else 0,
            "slop": 1 if has_slop else 0,
            "primary": 1 if is_primary_finding else 0,
            "reason": reason_str,
            "segs": segs,
            "notes": notes_data,
            "txt": line,
        }
        return row_html, row_obj

    raw_records: list[tuple[int, bool, bool, str]] = []
    for idx, line in enumerate(raw_lines, start=1):
        is_code = line_is_code[idx - 1]
        has_bib = idx in ms_bib_by_line
        has_slop = idx in hits_by_line
        in_foldable_preamble = (
            (not is_md) and preamble_end >= 4 and idx <= preamble_end and not (has_bib or has_slop)
        )
        should_hot = (
            has_bib
            or has_slop
            or (not in_foldable_preamble and (idx in hot_context or len(raw_lines) <= 120))
        )
        raw_records.append((idx, should_hot, is_code, line))

    r_i = 0
    while r_i < len(raw_records):
        idx, is_hot, is_code, line = raw_records[r_i]
        if is_hot:
            row_html, row_obj = _build_hot_row_data(idx, line, is_code)
            if mounted_count < INITIAL_MOUNT_LIMIT:
                mounted_chunks.append(row_html)
                mounted_count += 1
            else:
                deferred_rows.append(row_obj)
            r_i += 1
            continue

        r_end = r_i
        while r_end < len(raw_records) and not raw_records[r_end][1]:
            r_end += 1
        streak = raw_records[r_i:r_end]
        if len(streak) <= 3:
            for s_ln, _, s_code, s_raw in streak:
                row_html, row_obj = _build_hot_row_data(s_ln, s_raw, s_code)
                if mounted_count < INITIAL_MOUNT_LIMIT:
                    mounted_chunks.append(row_html)
                    mounted_count += 1
                else:
                    deferred_rows.append(row_obj)
        else:
            fid = f"f{fold_id}"
            fold_id += 1
            first_l, last_l = streak[0][0], streak[-1][0]
            label_kind = "preamble lines" if (first_l == 1 and last_l <= preamble_end) else "unflagged lines"
            fold_store[fid] = [[s_ln, 1 if s_code else 0, s_raw] for s_ln, _, s_code, s_raw in streak]
            if mounted_count < INITIAL_MOUNT_LIMIT:
                mounted_chunks.append(
                    f'<div class="fold-wrap" data-fold-wrap="{fid}" data-first-line="{first_l}">'
                    f'<button type="button" class="fold-bar" data-fold="{fid}" data-label="{html.escape(label_kind)}" data-range="L{first_l}–L{last_l}">'
                    f'▸ {len(streak)} {label_kind} (L{first_l}–L{last_l}) — expand</button></div>'
                )
                mounted_count += 1
            else:
                # Preserve clean-line folding across deferred pages (NEVER explode 500 clean lines into DOM! P1-12)
                deferred_rows.append(
                    {
                        "kind": "fold",
                        "ln": first_l,
                        "end_ln": last_l,
                        "fid": fid,
                        "cnt": len(streak),
                        "label": label_kind,
                        "bib": 0,
                        "slop": 0,
                        "primary": 0,
                    }
                )
        r_i = r_end

    if deferred_rows:
        total_pages = 1 + (len(deferred_rows) + INITIAL_MOUNT_LIMIT - 1) // INITIAL_MOUNT_LIMIT
        mounted_chunks.append(
            f'<div class="page-more-wrap pager-bar" id="pageMoreWrap">'
            f'<button type="button" class="btn" id="prevPageBtn" disabled>◂ Prev page</button>'
            f'<span class="nav-status" id="pageInd">Page 1 / {total_pages}</span>'
            f'<button type="button" class="btn" id="loadMorePageBtn">'
            f'Next page ({min(INITIAL_MOUNT_LIMIT, len(deferred_rows))} blocks) ▸</button></div>'
        )

    # Top Priority Finding Spotlight (NEVER uses stale withheld bib counts! Root & subfile jumps both work, P1-02 & P1-10!)
    top_finding_html = ""
    if bib_issues:
        top_b = bib_issues[0]
        top_b_file = str(top_b.get("file") or "").replace("\\", "/")
        is_root_ms_line = top_b.get("code") == "DANGLING_CITATION" and top_b_file in {root_rel_label, ""}
        is_sub_ms_line = top_b.get("code") == "DANGLING_CITATION" and top_b in subfile_bib_issues
        if is_root_ms_line:
            jump_href = f'#L{top_b["line"]}'
            jump_target_id = f'L{top_b["line"]}'
            jump_lbl = f'Jump to L{top_b["line"]} ↓'
        elif is_sub_ms_line:
            sub_idx = subfile_bib_issues.index(top_b)
            jump_href = f"#SUB{sub_idx}"
            jump_target_id = f"SUB{sub_idx}"
            jump_lbl = f'Jump to {html.escape(top_b_file)}:L{top_b["line"]} ↓'
        else:
            jump_href = "#bib-panel"
            jump_target_id = ""
            jump_lbl = "Inspect in Bib Forensics ↓"
        tone_cls = "tone-vermilion" if top_b["severity"] == "CRITICAL" else "tone-ochre"
        loc_html = _format_loc_html(str(top_b.get("file") or "bib"), f'L{top_b["line"]}')
        top_finding_html = (
            f'<div class="priority-banner {tone_cls}" aria-label="Top Priority Finding">'
            f'<div class="pb-main">'
            f'<div class="pb-head"><strong>Top Finding · {html.escape(top_b["severity"])} · {html.escape(top_b["code"])}</strong>'
            f'<span class="pb-loc">{loc_html}</span></div>'
            f'<p class="pb-msg">{html.escape(top_b["message"])}</p>'
            f"</div>"
            f'<a class="btn pb-jump" href="{jump_href}" data-jump-target="{jump_target_id}" data-jump-kind="bib">{jump_lbl}</a>'
            f"</div>"
        )
    elif slop_report.get("hits"):
        top_s = slop_report["hits"][0]
        st_l = int(top_s["line"])
        en_l = int(top_s.get("end_line", st_l))
        l_tag = f"L{st_l}–L{en_l}" if en_l > st_l else f"L{st_l}"
        loc_html = _format_loc_html(manuscript_path.name, l_tag)
        top_finding_html = (
            f'<div class="priority-banner tone-ochre" aria-label="Top Priority Finding">'
            f'<div class="pb-main">'
            f'<div class="pb-head"><strong>Top Finding · Style Cliché “{html.escape(top_s["matched_text"])}”</strong>'
            f'<span class="pb-loc">{loc_html}</span></div>'
            f'<p class="pb-msg">{html.escape(top_s["hint"])}</p>'
            f"</div>"
            f'<a class="btn pb-jump" href="#L{st_l}" data-jump-target="L{st_l}" data-jump-kind="slop">Jump to L{st_l} ↓</a>'
            f"</div>"
        )

    cache_banner_html = ""
    if cache_warnings:
        items_txt = " · ".join(html.escape(w) for w in cache_warnings)
        cache_banner_html = f'<div class="stale-banner" role="status"><strong>Provenance Notice:</strong> {items_txt}</div>'

    # Bibliography Forensics section (windowed to BIB_INITIAL_CARDS so 1,515 bad cites stay 60fps, P1-08!)
    bib_cards: list[str] = []
    bib_more_cards: list[list[str]] = []
    if bib_stale_withheld:
        bib_status_html = (
            '<div class="empty-state"><strong>Stale Cache Withheld:</strong> The supplied <code>--bib-json</code> '
            "does not match the current manuscript or bibliography SHA-256. Historical counts were excluded from "
            "KPIs and priority alerts; re-run with <code>--bib references.bib</code> to refresh.</div>"
        )
    elif not bib_ran:
        bib_status_html = (
            '<div class="empty-state">Bibliography check not run — pass <code>--bib references.bib</code> '
            "(or <code>--bib-json</code>) to verify citations.</div>"
        )
    elif not bib_issues:
        mode_lbl = html.escape(str(bib_report.get("mode", "checked"))) if bib_report else "checked"
        bib_status_html = (
            f'<div class="empty-state">0 citation anomalies in scanned scope (<code>{mode_lbl}</code>).</div>'
        )
    else:
        for bi in bib_issues:
            sev = bi["severity"]
            tone = "vermilion" if sev == "CRITICAL" else ("ochre" if sev == "WARNING" else "sage")
            src_file = str(bi.get("file") or "bib")
            bi_ln = int(bi.get("line", 1) or 0)
            ln_lbl = f"L{bi_ln}" if bi_ln > 0 else "unlocated"
            if len(bib_cards) < BIB_INITIAL_CARDS:
                loc_html = _format_loc_html(src_file, ln_lbl)
                bib_cards.append(
                    f'<article class="forensic-card tone-{tone}">'
                    f'<div class="fc-head">'
                    f'<strong class="fc-key">{html.escape(bi["key"])}</strong>'
                    f'<span class="fc-loc">{loc_html}</span>'
                    f"</div>"
                    f'<p class="fc-msg">{html.escape(bi["message"])}</p>'
                    f'<div class="fc-meta">{html.escape(sev)} · {html.escape(bi["code"])}</div>'
                    f"</article>"
                )
            else:
                bib_more_cards.append([tone, str(bi["key"]), src_file, ":" + ln_lbl, str(bi["message"]), f'{sev} · {bi["code"]}'])
        bib_more_btn = (
            f'<div id="bibMoreWrap" style="margin-top:8px"><button type="button" class="fold-bar" id="loadMoreBibBtn">'
            f'▸ Load next {min(BIB_INITIAL_CARDS, len(bib_more_cards))} bibliography issue(s) ({len(bib_more_cards)} remaining)</button></div>'
            if bib_more_cards
            else ""
        )
        bib_status_html = f'<div class="forensic-list" id="bibList">{"".join(bib_cards)}</div>{bib_more_btn}'
    bib_more_json = json.dumps(bib_more_cards, ensure_ascii=False).replace("</", "<\\/")

    crit_bib_str = str(bib_report["critical_count"]) if (bib_ran and bib_report) else "—"
    warn_bib_str = str(bib_report["warning_count"]) if (bib_ran and bib_report) else "—"
    crit_cls = "val-vermilion" if (bib_ran and bib_report and bib_report["critical_count"] > 0) else "val-neutral"
    warn_cls = "val-ochre" if (bib_ran and bib_report and bib_report["warning_count"] > 0) else "val-neutral"

    slop_score = int(slop_report["overall_score"])
    slop_hits = int(slop_report["total_hits"])
    total_words = max(int(slop_report.get("total_words", 1)), 1)
    hits_per_1k = float(
        slop_report.get("hits_per_1k_words")
        if slop_report.get("hits_per_1k_words") is not None
        else round((slop_hits / total_words) * 1000.0, 1)
    )
    slop_cls = "val-ochre" if slop_hits > 0 else "val-neutral"
    if slop_hits == 0:
        density_lbl, density_cls = "Clean", "val-neutral"
    elif hits_per_1k >= 12.0 or slop_score >= 65:
        density_lbl, density_cls = "High", "val-vermilion"
    elif hits_per_1k >= 5.0 or slop_score >= 35:
        density_lbl, density_cls = "Mod", "val-ochre"
    else:
        density_lbl, density_cls = "Low", "val-ochre"
    if bib_stale_withheld:
        bib_mode_note = "stale cache withheld (pass --bib to refresh)"
    elif bib_historical_snapshot and bib_ran and bib_report:
        prov_b = bib_report.get("provenance") if isinstance(bib_report, dict) else {}
        gen_ts = html.escape(str((prov_b or {}).get("generated_at") or "unknown time"))
        rec_src = html.escape(pathlib.Path(str((prov_b or {}).get("bib_path") or bib_report.get("bib_path") or "bib")).name)
        bib_mode_note = (
            f"Historical Snapshot ({html.escape(str(bib_report.get('mode', 'recorded')))} · "
            f"recorded at {gen_ts} · source '{rec_src}' unavailable on disk)"
        )
    elif bib_ran and bib_report:
        bib_mode_note = html.escape(bib_report["mode"])
    else:
        bib_mode_note = "bib not provided"
    fold_json = json.dumps(fold_store, ensure_ascii=False).replace("</", "<\\/")
    page_json = json.dumps(deferred_rows, ensure_ascii=False).replace("</", "<\\/")
    finding_index_json = json.dumps(finding_index, ensure_ascii=False).replace("</", "<\\/")

    return _compact_html_shell(f"""<!DOCTYPE html>
<html lang="en" data-theme="paper">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>PaperGuard Lite · {ms_name}</title>
<script>
(() => {{
  try {{
    const s = localStorage.getItem('pg-theme');
    if (s === 'paper' || s === 'obsidian') document.documentElement.dataset.theme = s;
    else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) document.documentElement.dataset.theme = 'obsidian';
  }} catch (_) {{}}
}})();
</script>
<style>
:root {{
  color-scheme: light dark;
  --bg-canvas: light-dark(#FAF8F3, #141311);
  --bg-panel: light-dark(#EFECE4, #1C1B18);
  --bg-elevated: light-dark(#FFFFFF, #22201C);
  --ink-primary: light-dark(#181715, #F2EFE9);
  --ink-secondary: light-dark(#3A3631, #D4CEC2);
  --ink-meta: light-dark(#4A453E, #C2BBAE);
  --border-hairline: light-dark(#D8D2C5, #3B3831);
  --vermilion: light-dark(#9B261B, #F5988E);
  --vermilion-soft: light-dark(#FDF2F0, rgba(245,152,142,0.14));
  --ochre: light-dark(#754708, #F4C478);
  --ochre-soft: light-dark(#FCF5E8, rgba(244,196,120,0.14));
  --sage: light-dark(#1B543C, #82DAB0);
  --sage-soft: light-dark(#EDF6F1, rgba(130,218,176,0.14));
  --btn-accent-bg: light-dark(#1D3E53, #A8D0EC);
  --btn-accent-fg: light-dark(#FAF8F3, #141311);
  --focus-ring: light-dark(#1D3E53, #A8D0EC);
  --font-serif: "Iowan Old Style", "Charter", "Palatino Linotype", "Book Antiqua", Georgia, serif;
  --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", system-ui, sans-serif;
  --font-mono: "SF Mono", "JetBrains Mono", "Menlo", ui-monospace, monospace;
}}
html[data-theme="paper"] {{ color-scheme: light; }}
html[data-theme="obsidian"] {{ color-scheme: dark; }}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ max-width: 100%; overflow-x: clip; }}
h1, h2, h3 {{ text-wrap: balance; }}
body {{
  background: var(--bg-canvas); color: var(--ink-primary);
  font-family: var(--font-sans); line-height: 1.5; -webkit-font-smoothing: antialiased;
}}
:focus-visible {{ outline: 2px solid var(--focus-ring); outline-offset: 2px; }}
.topbar {{
  position: sticky; top: 0; z-index: 20;
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 6px 12px; padding: 8px 20px;
  background: var(--bg-canvas); border-bottom: 1px solid var(--border-hairline);
}}
.brand {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
.brand h1 {{ font-family: var(--font-serif); font-size: 17px; font-weight: 600; white-space: nowrap; }}
.mode-tag {{
  font-family: var(--font-mono); font-size: 11px; color: var(--ink-secondary);
  padding: 1px 6px; border-radius: 3px; border: 1px solid var(--border-hairline); background: var(--bg-panel);
  white-space: nowrap;
}}
.controls {{ display: flex; flex-wrap: nowrap; align-items: center; gap: 5px; overflow-x: auto; }}
.nav-status {{
  font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta);
  font-variant-numeric: tabular-nums; padding: 0 3px; white-space: nowrap;
}}
.btn {{
  font-family: var(--font-sans); font-size: 12px; font-weight: 500;
  padding: 4px 9px; border-radius: 5px; cursor: pointer;
  border: 1px solid var(--border-hairline); background: var(--bg-elevated);
  color: var(--ink-primary); text-decoration: none; white-space: nowrap;
}}
.btn:hover {{ opacity: 0.88; }}
.btn-pro {{ background: var(--btn-accent-bg); color: var(--btn-accent-fg); border-color: var(--btn-accent-bg); font-weight: 600; }}
.summary-strip {{
  max-width: 1480px; margin: 0 auto; padding: 8px 20px;
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 14px;
  border-bottom: 1px solid var(--border-hairline); background: var(--bg-panel);
}}
.kpis {{ display: flex; flex-wrap: wrap; gap: 6px 16px; }}
.kpi {{ display: flex; align-items: baseline; gap: 5px; }}
.kpi-val {{ font-family: var(--font-mono); font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.kpi-lbl {{ font-size: 12px; color: var(--ink-secondary); }}
.val-vermilion {{ color: var(--vermilion); }}
.val-ochre {{ color: var(--ochre); }}
.val-neutral {{ color: var(--ink-secondary); }}
.filter-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }}
.chip {{
  font-size: 12px; font-weight: 500; padding: 3px 9px; border-radius: 99px; cursor: pointer;
  border: 1px solid var(--border-hairline); background: var(--bg-elevated); color: var(--ink-secondary);
}}
.chip[aria-pressed="true"] {{
  background: var(--ink-primary); color: var(--bg-canvas); border-color: var(--ink-primary);
}}
.jump-link {{ font-size: 12px; color: var(--ink-primary); text-underline-offset: 3px; margin-left: 4px; }}
.workspace {{
  display: grid; grid-template-columns: minmax(0, 820px) 340px; justify-content: center; gap: 24px;
  max-width: 1224px; margin: 0 auto; padding: 0 20px; min-height: calc(100vh - 104px);
}}
.canvas {{ padding: 14px 0 48px; min-width: 0; container-type: inline-size; }}
.ms-line, .slop-mark, .margin-note, .inspector {{ scroll-margin-block-start: calc(var(--topbar-h, 76px) + 12px); }}
.search-bar {{
  width: 100%; padding: 6px 0 2px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  border-top: 1px solid var(--border-hairline);
}}
.search-bar[hidden] {{ display: none; }}
.search-input {{
  flex: 1 1 200px; min-width: 0; font-family: var(--font-sans); font-size: 13px;
  padding: 4px 9px; border-radius: 5px; border: 1px solid var(--border-hairline);
  background: var(--bg-elevated); color: var(--ink-primary);
}}
.search-meta {{ font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta); white-space: nowrap; }}
.stale-banner {{
  margin-bottom: 10px; padding: 8px 12px; border-radius: 6px;
  background: var(--ochre-soft); border: 1px solid var(--ochre); font-size: 12.5px; color: var(--ink-primary);
  overflow-wrap: anywhere;
}}
.priority-banner {{
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px;
  margin-bottom: 12px; padding: 10px 13px; border-radius: 6px;
  background: var(--bg-elevated); border: 1px solid var(--border-hairline); max-width: 820px; min-width: 0;
}}
.pb-main {{ min-width: 0; flex: 1 1 200px; max-width: 100%; }}
.pb-head {{
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline;
  gap: 3px 10px; font-size: 12.5px; margin-bottom: 2px; min-width: 0;
}}
.pb-loc {{ font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta); min-width: 0; max-width: 100%; }}
.loc-wrap {{ display: inline-flex; flex-wrap: wrap; align-items: baseline; max-width: 100%; min-width: 0; }}
.loc-file {{ overflow-wrap: anywhere; word-break: break-word; min-width: 0; }}
.loc-ln {{ white-space: nowrap; flex-shrink: 0; font-weight: 600; color: var(--ink-primary); }}
.pb-msg {{ font-size: 13px; color: var(--ink-primary); line-height: 1.44; overflow-wrap: anywhere; }}
.pb-jump {{ flex-shrink: 0; }}
.canvas-head {{
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline;
  gap: 4px 12px; margin-bottom: 10px; max-width: 820px; min-width: 0;
}}
.canvas-head h2 {{ font-family: var(--font-serif); font-size: 18px; font-weight: 600; overflow-wrap: anywhere; max-width: 100%; min-width: 0; }}
.sub-meta {{ font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta); }}
.manuscript-sheet {{
  background: var(--bg-elevated); border: 1px solid var(--border-hairline);
  border-radius: 7px; padding: 6px 0; overflow: hidden; max-width: 820px;
}}
.ms-line {{
  display: grid; grid-template-columns: 44px minmax(0, 1fr);
  padding: 2px 14px 2px 0; min-width: 0;
}}
.ms-line.is-focused {{ background: var(--bg-panel); outline: 2px solid var(--focus-ring); outline-offset: -2px; }}
.ms-line.is-slop {{ background: var(--ochre-soft); border-left: 3px solid var(--ochre); }}
.ms-line.is-critical {{ background: var(--vermilion-soft); border-left: 3px solid var(--vermilion); }}
#sheet[data-filter="issues"] .ms-line:not([data-flagged]),
#sheet[data-filter="issues"] .fold-wrap {{ display: none; }}
#sheet[data-filter="slop"] .ms-line:not([data-has-slop]),
#sheet[data-filter="slop"] .fold-wrap {{ display: none; }}
#sheet[data-filter="bib"] .ms-line:not([data-has-bib]),
#sheet[data-filter="bib"] .fold-wrap {{ display: none; }}
.ln {{
  font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta);
  text-align: right; padding-right: 10px; user-select: none;
  font-variant-numeric: tabular-nums; padding-top: 3px;
}}
.lc {{ min-width: 0; }}
.prose-line {{
  font-family: var(--font-serif); font-size: 16px; line-height: 1.66;
  max-width: 70ch; overflow-wrap: break-word; text-wrap: pretty;
}}
.code-line {{
  font-family: var(--font-mono); font-size: 12.5px; line-height: 1.55;
  color: var(--ink-secondary); white-space: pre-wrap; overflow-wrap: anywhere;
}}
.slop-mark {{
  background: transparent; color: var(--ochre); font-weight: 600;
  text-decoration: underline; text-decoration-thickness: 1.5px; text-underline-offset: 3px;
}}
.margin-note {{
  font-family: var(--font-sans); font-size: 12.5px; line-height: 1.44;
  margin: 4px 0 3px; padding: 5px 9px; border-radius: 4px; max-width: 68ch;
  background: var(--bg-canvas); border: 1px solid var(--border-hairline); color: var(--ink-primary);
  overflow-wrap: anywhere;
}}
.more-notes {{ margin: 3px 0 4px; max-width: 68ch; }}
.more-notes summary {{
  font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta);
  cursor: pointer; padding: 2px 4px;
}}
.note-vermilion strong {{ color: var(--vermilion); }}
.note-ochre strong {{ color: var(--ochre); }}
.fold-bar {{
  display: block; width: 100%; text-align: left; cursor: pointer;
  padding: 5px 14px 5px 44px; border: none;
  border-top: 1px dashed var(--border-hairline); border-bottom: 1px dashed var(--border-hairline);
  background: var(--bg-panel); color: var(--ink-meta);
  font-family: var(--font-mono); font-size: 11.5px;
}}
.fold-bar:hover {{ color: var(--ink-primary); }}
.pager-bar {{
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px;
  padding: 8px 14px; background: var(--bg-panel); border-top: 1px dashed var(--border-hairline);
}}
.pager-bar .btn[disabled] {{ opacity: 0.45; cursor: not-allowed; }}
.fold-actions {{
  display: flex; flex-wrap: wrap; gap: 8px; padding: 5px 14px 5px 44px;
  background: var(--bg-panel); border-top: 1px dashed var(--border-hairline);
  border-bottom: 1px dashed var(--border-hairline);
}}
.fold-act-btn {{
  font-family: var(--font-mono); font-size: 11.5px; padding: 2px 8px; border-radius: 4px;
  border: 1px solid var(--border-hairline); background: var(--bg-elevated);
  color: var(--ink-primary); cursor: pointer;
}}
.inspector {{
  padding: 18px; background: var(--bg-panel);
  border-left: 1px solid var(--border-hairline); min-width: 0;
}}
.sec-heading {{ font-family: var(--font-serif); font-size: 16px; font-weight: 600; margin-bottom: 4px; }}
.sec-sub {{ font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta); margin-bottom: 12px; overflow-wrap: anywhere; }}
.forensic-list {{ display: flex; flex-direction: column; gap: 9px; }}
.forensic-card {{
  background: var(--bg-elevated); border: 1px solid var(--border-hairline);
  border-radius: 6px; padding: 10px 12px; min-width: 0;
}}
.tone-vermilion {{ border-left: 3px solid var(--vermilion); }}
.tone-ochre {{ border-left: 3px solid var(--ochre); }}
.tone-sage {{ border-left: 3px solid var(--sage); }}
.fc-head {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 3px 8px; margin-bottom: 3px; min-width: 0; }}
.fc-key {{ font-family: var(--font-mono); font-size: 12.5px; overflow-wrap: anywhere; min-width: 0; }}
.fc-loc {{ font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta); min-width: 0; max-width: 100%; }}
.fc-msg {{ font-size: 13px; color: var(--ink-primary); line-height: 1.46; margin-bottom: 3px; overflow-wrap: anywhere; }}
.fc-meta {{ font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-meta); }}
.empty-state {{
  padding: 11px; border-radius: 6px; background: var(--bg-elevated);
  border: 1px solid var(--border-hairline); font-size: 13px; color: var(--ink-secondary); max-width: 820px;
}}
.pro-box {{
  margin-top: 18px; padding: 13px; border-radius: 6px;
  background: var(--bg-elevated); border: 1px solid var(--border-hairline);
}}
.pro-box h3 {{ font-family: var(--font-serif); font-size: 15px; margin-bottom: 6px; }}
.pro-box p {{ font-size: 12.5px; color: var(--ink-secondary); margin-bottom: 9px; }}
.pro-box ul {{ font-size: 12.5px; color: var(--ink-secondary); padding-left: 16px; margin-bottom: 10px; }}
.pro-box li {{ margin-bottom: 4px; }}
.nav-reason {{ display: block; font-size: 12px; color: var(--ink-secondary); margin-bottom: 8px; max-width: 820px; overflow-wrap: anywhere; }}
#printRoot {{ display: none; }}
@media (max-width: 1160px) {{
  .workspace {{ grid-template-columns: 1fr; gap: 0; padding: 0 20px; }}
  .canvas, .inspector {{ max-width: 820px; width: 100%; margin: 0 auto; }}
  .inspector {{ border-left: none; border-top: 1px solid var(--border-hairline); }}
}}
@media (max-width: 960px) {{
  .workspace {{ padding: 0; }}
  .canvas {{ padding: 12px 12px 28px; }}
  .topbar {{ padding: 6px 12px; gap: 5px 8px; }}
  .brand {{ width: 100%; justify-content: space-between; }}
  .brand-left {{ display: flex; align-items: center; gap: 7px; }}
  .controls {{ width: 100%; flex-wrap: wrap; justify-content: space-between; gap: 4px; overflow-x: visible; }}
  .btn {{ padding: 5px 8px; min-height: 36px; min-width: 34px; font-size: 11.5px; display: inline-flex; align-items: center; justify-content: center; }}
  .summary-strip {{ padding: 7px 12px; }}
  .shortcut-hint {{ display: none; }}
}}
@media print {{
  :root, html[data-theme="obsidian"] {{ color-scheme: light; }}
  .topbar, .filter-row, .pro-box, #searchBar, #pageMoreWrap, #bibMoreWrap, .fold-bar, .fold-actions, .pb-jump {{ display: none !important; }}
  html[data-print-all="1"] body > .summary-strip, html[data-print-all="1"] body > .workspace {{ display: none !important; }}
  html[data-print-all="1"] #printRoot {{ display: block !important; }}
  #printRoot .summary-strip {{ margin-bottom: 10px; }}
  #printRoot .priority-banner, #printRoot .stale-banner, #printRoot .canvas-head {{ max-width: none; break-inside: avoid; }}
  #printRoot .more-notes summary {{ display: none !important; }}
  .workspace, .print-workspace {{ display: block; max-width: none; padding: 0; }}
  .manuscript-sheet {{ max-width: none; border: none; }}
  .inspector {{ border-left: none; border-top: 1px solid var(--border-hairline); padding: 12px 0; }}
  .ms-line, .forensic-card {{ break-inside: avoid; }}
}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <div class="brand-left" style="display:flex;align-items:center;gap:8px">
      <h1>PaperGuard</h1>
      <span class="mode-tag">Lite</span>
    </div>
    <a class="btn btn-pro" href="https://zhangx2.gumroad.com/l/paperguard-pro" target="_blank" rel="noopener">Pro · $29</a>
  </div>
  <div class="controls">
    <span class="nav-status" id="navStatus" aria-live="polite">0 findings</span>
    <button type="button" class="btn" id="prevBtn" aria-label="Previous finding" title="Previous visible finding (k)">↑</button>
    <button type="button" class="btn" id="nextBtn" aria-label="Next finding" title="Next visible finding (j)">↓</button>
    <button type="button" class="btn" id="findToggleBtn" title="Search entire manuscript (/)">Find</button>
    <button type="button" class="btn" id="printAllBtn" title="Print full manuscript and bibliography report">Print Full</button>
    <button type="button" class="btn" id="themeBtn" title="Switch Paper / Obsidian theme (d)">Theme</button>
  </div>
  <div class="search-bar" id="searchBar" hidden>
    <input type="search" class="search-input" id="liteSearch" placeholder="Search entire manuscript (Enter to jump, Esc to close)…" aria-label="Search manuscript">
    <span class="search-meta" id="searchMeta" aria-live="polite"></span>
    <button type="button" class="btn" id="searchPrevBtn">↑</button>
    <button type="button" class="btn" id="searchNextBtn">↓</button>
    <button type="button" class="btn" id="searchCloseBtn">Close</button>
  </div>
</header>

<section class="summary-strip" aria-label="Diagnostic Summary">
  <div class="kpis">
    <div class="kpi"><span class="kpi-val {crit_cls}">{crit_bib_str}</span><span class="kpi-lbl">Crit Cite</span></div>
    <div class="kpi"><span class="kpi-val {warn_cls}">{warn_bib_str}</span><span class="kpi-lbl">Bib Warn</span></div>
    <div class="kpi"><span class="kpi-val {slop_cls}">{slop_hits}</span><span class="kpi-lbl">Clichés</span></div>
    <div class="kpi"><span class="kpi-val {density_cls}">{hits_per_1k:.1f}/1k w</span><span class="kpi-lbl">Cliché Density ({density_lbl})</span></div>
  </div>
  <div class="filter-row" id="filterBar" role="group" aria-label="Filter manuscript lines">
    <button type="button" class="chip" data-filter="all" aria-pressed="true">All</button>
    <button type="button" class="chip" data-filter="issues" aria-pressed="false">Flagged</button>
    <button type="button" class="chip" data-filter="slop" aria-pressed="false">Clichés</button>
    <button type="button" class="chip" data-filter="bib" aria-pressed="false">Missing</button>
    <a class="jump-link" href="#bib-panel">Bib ({len(bib_issues) if bib_ran else '—'}) ↓</a>
  </div>
</section>

<main class="workspace">
  <section class="canvas" aria-label="Manuscript Diagnostic View">
    {cache_banner_html}
    {top_finding_html}
    <div class="canvas-head">
      <h2>{ms_name}</h2>
      <span class="sub-meta"><span class="shortcut-hint">Shortcuts: j/k jump · / find · d theme · </span>Cliché Density = hits per 1k words (lower is cleaner; not authorship)</span>
    </div>
    <div class="nav-reason" id="navReason" hidden aria-live="polite"></div>
    <div class="manuscript-sheet" id="sheet" data-filter="all">
      {''.join(mounted_chunks)}
    </div>
    <div class="empty-state" id="emptyFilterNote" hidden style="margin-top:10px">
      No manuscript lines match the active filter.
    </div>
  </section>

  <aside class="inspector" id="bib-panel" aria-label="Bibliography Forensics">
    <h2 class="sec-heading">Bibliography Forensics</h2>
    <div class="sec-sub">Mode: {bib_mode_note}</div>
    {bib_status_html}

    <div class="pro-box">
      <h3>Automate Safe Repairs in Pro ($29)</h3>
      <p>Lite runs strictly read-only. Upgrade once to unlock:</p>
      <ul>
        <li><strong>fix_bib_pro.py</strong>: Byte-preserving `.bib` span patches &amp; idempotent conference upgrades</li>
        <li><strong>latex_guard.py</strong>: Stateful formula/macro/include lock + lossless phrase Diff Studio</li>
        <li><strong>Cell-Bound Audit</strong>: Pinpoint Table [Row, Col] vs prose contradictions (%, GB, ms/tok)</li>
      </ul>
      <a class="btn btn-pro" style="display:block;text-align:center" href="https://zhangx2.gumroad.com/l/paperguard-pro" target="_blank" rel="noopener">Unlock PaperGuard Pro · $29</a>
    </div>
  </aside>
</main>

<script type="application/json" id="pg-fold-store">{fold_json}</script>
<script type="application/json" id="pg-page-store">{page_json}</script>
<script type="application/json" id="pg-finding-index">{finding_index_json}</script>
<script type="application/json" id="pg-bib-more">{bib_more_json}</script>
<script>
(() => {{
  const D = document, $ = id => D.getElementById(id), ae = (e, t, f) => e.addEventListener(t, f);
  const BATCH = 40, PAGE_SIZE = {INITIAL_MOUNT_LIMIT}, MAX_DYN = 80;
  const root = D.documentElement, sheet = $('sheet'), navStatus = $('navStatus'), navReason = $('navReason'), emptyNote = $('emptyFilterNote');
  const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const foldData = JSON.parse($('pg-fold-store').textContent || '{{}}');
  const pageData = JSON.parse($('pg-page-store').textContent || '[]');
  const findingIndex = JSON.parse($('pg-finding-index').textContent || '[]');
  const bibMoreData = JSON.parse($('pg-bib-more').textContent || '[]');
  let bibMoreCursor = 0;
  const topbarEl = document.querySelector('.topbar');
  function syncTopbarHeight() {{
    if (!topbarEl) return 76;
    const h = Math.ceil(topbarEl.getBoundingClientRect().height);
    root.style.setProperty('--topbar-h', `${{h}}px`);
    return h;
  }}
  syncTopbarHeight();
  window.addEventListener('resize', syncTopbarHeight);

  const pageByLine = new Map();
  const foldInPage = new Map();
  for (let i = 0; i < pageData.length; i++) {{
    const o = pageData[i];
    if (o.kind === 'fold') foldInPage.set(o.fid, i);
    else pageByLine.set(o.ln, i);
  }}
  let pageCursor = 0;
  const totalPages = 1 + Math.ceil(pageData.length / PAGE_SIZE);
  let curPage = 1;

  let globalMatches = [];
  let globalCursor = -1;
  let navAnchorLine = null;
  let focusedEl = null;

  const mountedLines = new Set();
  sheet.querySelectorAll('.ms-line[id^="L"]').forEach(el => mountedLines.add(Number(el.id.slice(1))));
  sheet.querySelectorAll('.ms-line[id^="SUB"]').forEach(el => mountedLines.add(-100000 + Number(el.id.slice(3))));
  const mountedFolds = new Set();
  sheet.querySelectorAll('.fold-wrap[data-fold-wrap]').forEach(el => mountedFolds.add(el.dataset.foldWrap));

  function parseTargetLine(targetId) {{
    if (typeof targetId === 'number') return targetId;
    const s = String(targetId || '');
    if (s.startsWith('SUB')) return -100000 + Number(s.slice(3));
    if (s.startsWith('L')) return Number(s.slice(1));
    return Number(s) || 0;
  }}

  function indexMatchesFilter(flags, filt) {{
    if (filt === 'all' || filt === 'issues') return true;
    if (filt === 'bib') return Boolean(flags & 1);
    if (filt === 'slop') return Boolean(flags & 2);
    return true;
  }}

  function objMatchesFilter(obj, filt) {{
    if (obj.kind === 'fold') return filt === 'all';
    if (filt === 'all') return true;
    if (filt === 'issues') return Boolean(obj.bib || obj.slop);
    if (filt === 'slop') return Boolean(obj.slop);
    if (filt === 'bib') return Boolean(obj.bib);
    return true;
  }}

  function getChildLine(el) {{
    if (el.id && el.id.startsWith('SUB')) return -100000 + Number(el.id.slice(3));
    if (el.id && el.id.startsWith('L')) return Number(el.id.slice(1));
    if (el.dataset && el.dataset.firstLine) return Number(el.dataset.firstLine);
    return Infinity;
  }}

  // Strictly ascending DOM insertion across ALL mounts (0 line inversions EVER, P1-03!)
  function insertRowsInOrder(pairs) {{
    if (!pairs.length) return;
    pairs.sort((a, b) => a[0] - b[0]);
    const wrap = document.getElementById('pageMoreWrap');
    const children = Array.from(sheet.children).filter(el => el !== wrap);
    let ci = 0;
    let pi = 0;
    while (pi < pairs.length) {{
      while (ci < children.length && getChildLine(children[ci]) < pairs[pi][0]) ci++;
      const anchor = (ci < children.length) ? children[ci] : wrap;
      const limitLn = (ci < children.length) ? getChildLine(children[ci]) : Infinity;
      const frag = document.createDocumentFragment();
      while (pi < pairs.length && pairs[pi][0] < limitLn) {{
        frag.appendChild(pairs[pi][1]);
        pi++;
      }}
      if (anchor) sheet.insertBefore(frag, anchor);
      else sheet.appendChild(frag);
    }}
  }}

  function makeFoldWrapNode(o) {{
    const wrap = document.createElement('div');
    wrap.className = 'fold-wrap';
    wrap.dataset.foldWrap = o.fid;
    wrap.dataset.firstLine = String(o.ln);
    wrap.dataset.dyn = '1';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'fold-bar';
    btn.dataset.fold = o.fid;
    btn.dataset.label = o.label || 'unflagged lines';
    btn.dataset.range = `L${{o.ln}}–L${{o.end_ln}}`;
    btn.textContent = `▸ ${{o.cnt}} ${{o.label || 'unflagged lines'}} (L${{o.ln}}–L${{o.end_ln}}) — expand`;
    wrap.appendChild(btn);
    return wrap;
  }}

  function clearDynamicRows() {{
    Array.from(sheet.children).forEach(el => {{
      if (el.dataset && el.dataset.dyn === '1') {{
        if (el.classList.contains('fold-wrap')) mountedFolds.delete(el.dataset.foldWrap);
        else mountedLines.delete(getChildLine(el));
        el.remove();
      }}
    }});
  }}

  // Keep dynamically mounted DOM strictly bounded <= MAX_DYN for 60fps across 1,515+ items (never evicts protected target!)
  function pruneDynamicRows(anchorLn, keepEl) {{
    const pinEl = keepEl || focusedEl;
    const keepPinned = pinEl && pinEl.parentNode === sheet && pinEl.dataset && pinEl.dataset.dyn === '1';
    const maxOther = keepPinned ? MAX_DYN - 1 : MAX_DYN;
    const dynEls = Array.from(sheet.children).filter(el => el.dataset && el.dataset.dyn === '1' && el !== pinEl);
    if (dynEls.length <= maxOther) return;
    dynEls.sort((a, b) => Math.abs(getChildLine(b) - anchorLn) - Math.abs(getChildLine(a) - anchorLn));
    const dropCnt = dynEls.length - maxOther;
    for (let i = 0; i < dropCnt; i++) {{
      const el = dynEls[i];
      if (el.classList.contains('fold-wrap')) mountedFolds.delete(el.dataset.foldWrap);
      else mountedLines.delete(getChildLine(el));
      el.remove();
    }}
  }}

  function isPageItemMounted(o) {{
    return o.kind === 'fold' ? mountedFolds.has(o.fid) : mountedLines.has(o.ln);
  }}

  function syncPager() {{
    const wrap = $('pageMoreWrap');
    if (!wrap) return;
    const prevBtn = $('prevPageBtn'), nextBtn = $('loadMorePageBtn'), ind = $('pageInd');
    if (prevBtn) prevBtn.disabled = curPage <= 1;
    if (ind) ind.textContent = `Page ${{curPage}} / ${{totalPages}}`;
    if (nextBtn) {{
      nextBtn.disabled = curPage >= totalPages;
      const nextRem = Math.max(0, pageData.length - (curPage - 1) * PAGE_SIZE);
      nextBtn.textContent = curPage >= totalPages
        ? 'End of document'
        : `Next page (${{Math.min(PAGE_SIZE, nextRem)}} blocks) ▸`;
    }}
  }}
  function updateLoadMoreLabel() {{ syncPager(); }}

  // Mount matching deferred findings in ascending order in ONE pass (P1-03 & P1-12!)
  function mountMatchingDeferred(filt, maxCount) {{
    if (filt === 'all' || pageCursor >= pageData.length || maxCount <= 0) return 0;
    const pairs = [];
    for (let i = pageCursor; i < pageData.length && pairs.length < maxCount; i++) {{
      const o = pageData[i];
      if (o.kind !== 'fold' && !mountedLines.has(o.ln) && objMatchesFilter(o, filt)) {{
        mountedLines.add(o.ln);
        pairs.push([o.ln, makeRichRowNode(o)]);
      }}
    }}
    if (!pairs.length) return 0;
    insertRowsInOrder(pairs);
    pruneDynamicRows(pairs[pairs.length - 1][0]);
    updateLoadMoreLabel();
    return pairs.length;
  }}

  function mountFoldIfDeferred(fid) {{
    let wrap = sheet.querySelector(`.fold-wrap[data-fold-wrap="${{fid}}"]`);
    if (wrap) return wrap;
    const pIdx = foldInPage.get(fid);
    if (pIdx === undefined) return null;
    const o = pageData[pIdx];
    mountedFolds.add(fid);
    wrap = makeFoldWrapNode(o);
    insertRowsInOrder([[o.ln, wrap]]);
    pruneDynamicRows(o.ln);
    updateLoadMoreLabel();
    return wrap;
  }}

  // O(1) direct single-layout mount of any distant target line + tiny context window (P1-03!)
  function mountTargetLineDirect(targetLn) {{
    const numLn = Number(targetLn);
    const idFor = v => (v < 0 ? 'SUB' + (v + 100000) : 'L' + v);
    const existing = document.getElementById(idFor(numLn));
    if (existing) return existing;
    const hitIdx = pageByLine.get(numLn);
    if (hitIdx !== undefined) {{
      const winStart = Math.max(0, hitIdx - 3);
      const winEnd = Math.min(pageData.length, hitIdx + 4);
      const pairs = [];
      let targetEl = null;
      for (let i = winStart; i < winEnd; i++) {{
        const o = pageData[i];
        if (o.kind === 'fold') {{
          if (!mountedFolds.has(o.fid)) {{
            mountedFolds.add(o.fid);
            pairs.push([o.ln, makeFoldWrapNode(o)]);
          }}
          continue;
        }}
        if (mountedLines.has(o.ln)) {{
          if (o.ln === numLn) targetEl = document.getElementById(idFor(o.ln));
          continue;
        }}
        mountedLines.add(o.ln);
        const node = makeRichRowNode(o);
        if (o.ln === numLn) targetEl = node;
        pairs.push([o.ln, node]);
      }}
      if (pairs.length > 0) {{
        insertRowsInOrder(pairs);
        pruneDynamicRows(numLn, targetEl);
        curPage = 2 + Math.floor(hitIdx / PAGE_SIZE);
        pageCursor = (curPage - 1) * PAGE_SIZE;
        syncPager();
      }}
      return targetEl || document.getElementById(idFor(numLn));
    }}
    // O(1) direct ordered fold-line mount for deep search (avoids mounting 3,000 intermediate lines, P1-06/P1-10!)
    for (const [fid, rows] of Object.entries(foldData)) {{
      if (!rows.length || numLn < rows[0][0] || numLn > rows[rows.length - 1][0]) continue;
      const wrap = mountFoldIfDeferred(fid);
      if (!wrap) continue;
      const shown = Number(wrap.dataset.shown || '0');
      const rIdx = rows.findIndex(r => r[0] === numLn);
      if (rIdx === -1) continue;
      if (rIdx >= shown && rIdx < shown + BATCH) {{
        const openBtn = wrap.querySelector('.fold-bar[data-fold]') || wrap.querySelector('[data-fold-more]');
        const lbl = (openBtn && openBtn.dataset.label) || 'unflagged lines';
        const rng = (openBtn && openBtn.dataset.range) || `L${{rows[0][0]}}–L${{rows[rows.length - 1][0]}}`;
        while (!document.getElementById('L' + numLn) && Number(wrap.dataset.shown || '0') < rows.length) {{
          appendFoldBatch(wrap, fid, lbl, rng, true);
        }}
        return document.getElementById('L' + numLn);
      }}
      const [ln, isCode, txt] = rows[rIdx];
      const node = makePlainLineNode(ln, isCode, txt);
      const anchor = wrap.querySelector('.fold-actions') || wrap.querySelector('.fold-bar');
      insertFoldRowOrdered(wrap, node, ln, anchor);
      return node;
    }}
    return null;
  }}

  function insertFoldRowOrdered(wrap, node, ln, fallback) {{
    for (const ch of wrap.children) {{
      if (ch.id && ch.id.startsWith('L')) {{
        if (Number(ch.id.slice(1)) > ln) {{ wrap.insertBefore(node, ch); return; }}
      }}
    }}
    if (fallback && fallback.parentNode === wrap) wrap.insertBefore(node, fallback);
    else wrap.appendChild(node);
  }}

  function mountTargetById(targetId) {{
    const s = String(targetId || '');
    if (s.startsWith('SUB')) return mountTargetLineDirect(-100000 + Number(s.slice(3)));
    if (s.startsWith('L')) return mountTargetLineDirect(Number(s.slice(1)));
    return mountTargetLineDirect(Number(s));
  }}

  function countMountedMatching(filt) {{
    if (filt === 'all') return sheet.querySelectorAll('.ms-line').length;
    if (filt === 'issues') return sheet.querySelectorAll('.ms-line[data-flagged="1"]').length;
    if (filt === 'slop') return sheet.querySelectorAll('.ms-line[data-has-slop="1"]').length;
    if (filt === 'bib') return sheet.querySelectorAll('.ms-line[data-has-bib="1"]').length;
    return 0;
  }}

  function refreshVisibleTargets(preserveCurrent) {{
    const prevId = (preserveCurrent && globalCursor >= 0) ? globalMatches[globalCursor] : null;
    if (!preserveCurrent && focusedEl) {{
      focusedEl.classList.remove('is-focused');
      focusedEl = null;
    }}
    const filt = sheet.dataset.filter || 'all';
    globalMatches = [];
    for (let i = 0; i < findingIndex.length; i++) {{
      if (indexMatchesFilter(findingIndex[i][1], filt)) globalMatches.push(String(findingIndex[i][0]));
    }}
    if (prevId !== null) {{
      globalCursor = globalMatches.indexOf(prevId);
    }} else {{
      globalCursor = -1;
      navReason.hidden = true;
    }}
    const totalMatches = globalMatches.length;
    emptyNote.hidden = totalMatches > 0 || filt === 'all';
    if (globalCursor >= 0) {{
      navStatus.textContent = `${{globalCursor + 1}} / ${{totalMatches}}`;
    }} else {{
      navStatus.textContent = totalMatches ? `${{totalMatches}} matching` : '0 matching';
    }}
  }}

  // Strictly idempotent filter switching — clicking the active chip 19x mutates 0 DOM nodes (P1-12!)
  function setFilter(filt, force) {{
    if (!force && (sheet.dataset.filter || 'all') === filt) return;
    document.querySelectorAll('#filterBar .chip').forEach(c => c.setAttribute('aria-pressed', String(c.dataset.filter === filt)));
    sheet.dataset.filter = filt;
    navAnchorLine = null;
    if (filt !== 'all') {{
      const need = Math.max(0, PAGE_SIZE - countMountedMatching(filt));
      if (need > 0) mountMatchingDeferred(filt, need);
    }}
    refreshVisibleTargets(false);
  }}

  function syncFocusedRow(el, gIdx) {{
    if (focusedEl && focusedEl !== el) focusedEl.classList.remove('is-focused');
    globalCursor = gIdx;
    navAnchorLine = null;
    focusedEl = el;
    el.classList.add('is-focused');
    navStatus.textContent = `${{globalCursor + 1}} / ${{globalMatches.length}}`;
    const rsn = el.dataset.reason || '';
    if (rsn) {{
      navReason.textContent = `${{el.id.startsWith('SUB') ? 'Subfile' : el.id}} · ${{rsn}}`;
      navReason.hidden = false;
    }} else {{
      navReason.hidden = true;
    }}
  }}

  // Scrolls the ACTUAL highlighted <mark class="slop-mark"> (or margin-note) into the visible viewport on 390x850 (P1-11!)
  function focusGlobalIndex(idx, instant) {{
    if (idx < 0 || idx >= globalMatches.length) return;
    const targetId = globalMatches[idx];
    const el = mountTargetById(targetId);
    if (!el) return;
    syncFocusedRow(el, idx);
    el.tabIndex = -1;
    el.focus({{ preventScroll: true }});
    const topH = syncTopbarHeight();
    const hitMark = el.querySelector('.slop-mark');
    const hitNote = el.querySelector('.margin-note');
    const scrollTarget = hitMark || hitNote || el;
    const availH = Math.max(140, window.innerHeight - topH - 24);
    const blockMode = (scrollTarget === el && el.getBoundingClientRect().height > availH) ? 'start' : 'center';
    scrollTarget.scrollIntoView({{ behavior: (instant || reducedMotion) ? 'auto' : 'smooth', block: blockMode }});
  }}

  function stepFinding(delta) {{
    if (!globalMatches.length) return;
    let nextIdx;
    if (globalCursor === -1) {{
      if (navAnchorLine !== null) {{
        if (delta >= 0) {{
          const foundIdx = globalMatches.findIndex(tid => parseTargetLine(tid) >= navAnchorLine);
          nextIdx = foundIdx >= 0 ? foundIdx : 0;
        }} else {{
          let prevIdx = -1;
          for (let i = globalMatches.length - 1; i >= 0; i--) {{
            if (parseTargetLine(globalMatches[i]) < navAnchorLine) {{ prevIdx = i; break; }}
          }}
          nextIdx = prevIdx >= 0 ? prevIdx : globalMatches.length - 1;
        }}
      }} else {{
        nextIdx = delta >= 0 ? 0 : globalMatches.length - 1;
      }}
    }} else {{
      nextIdx = (globalCursor + delta + globalMatches.length) % globalMatches.length;
    }}
    focusGlobalIndex(nextIdx, false);
  }}

  function toggleTheme() {{
    const apply = () => {{
      const next = root.getAttribute('data-theme') === 'paper' ? 'obsidian' : 'paper';
      root.setAttribute('data-theme', next);
      try {{ localStorage.setItem('pg-theme', next); }} catch (_) {{}}
    }};
    if (document.startViewTransition && !reducedMotion) document.startViewTransition(apply);
    else apply();
  }}

  const mk = (tag, cls, txt) => {{ const n = document.createElement(tag); if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n; }};

  function makePlainLineNode(ln, isCode, txt) {{
    const row = mk('div', 'ms-line');
    row.id = 'L' + ln;
    const lcEl = mk('div', 'lc');
    lcEl.appendChild(mk('div', isCode ? 'code-line' : 'prose-line', txt || '\u00a0'));
    row.append(mk('span', 'ln', String(ln)), lcEl);
    return row;
  }}

  function makeNoteNode(kind, title, msg) {{
    const nEl = mk('div', 'margin-note ' + (kind === 'bib' ? 'note-vermilion' : 'note-ochre'));
    nEl.append(mk('strong', '', title), document.createTextNode(' — ' + msg));
    return nEl;
  }}

  function makeRichRowNode(obj) {{
    if (obj.kind === 'sub') {{
      const row = mk('div', 'ms-line finding-row is-critical is-subfile-row');
      row.id = 'SUB' + obj.sid; row.tabIndex = -1; row.dataset.dyn = '1';
      row.dataset.flagged = '1'; row.dataset.hasBib = '1'; row.dataset.subIdx = String(obj.sid);
      if (obj.reason) row.dataset.reason = obj.reason;
      const lcEl = mk('div', 'lc'), cl = mk('div', 'code-line'), lw = mk('span', 'loc-wrap');
      lw.append(mk('span', 'loc-file', obj.file), mk('span', 'loc-ln', 'L' + obj.bline));
      cl.append(D.createTextNode('Included subfile '), lw, D.createTextNode(' — unresolved citation '), mk('code', '', obj.key));
      const mn = mk('div', 'margin-note note-vermilion'), jl = mk('a', 'jump-link', 'Bib Forensics ↓');
      jl.href = '#bib-panel';
      mn.append(mk('strong', '', `Missing in .bib (${{obj.key}})`), D.createTextNode(' — ' + obj.msg + ' '), jl);
      lcEl.append(cl, mn); row.append(mk('span', 'ln', 'Sub'), lcEl);
      return row;
    }}
    const cls = ['ms-line'];
    if (obj.primary) cls.push('finding-row');
    if (obj.bib) cls.push('is-critical');
    else if (obj.slop) cls.push('is-slop');
    const row = mk('div', cls.join(' '));
    row.id = 'L' + obj.ln; row.dataset.dyn = '1';
    if (obj.primary) row.tabIndex = -1;
    if (obj.bib || obj.slop) row.dataset.flagged = '1';
    if (obj.bib) row.dataset.hasBib = '1';
    if (obj.slop) row.dataset.hasSlop = '1';
    if (obj.reason) row.dataset.reason = obj.reason;

    const lcEl = mk('div', 'lc');
    const bodyEl = mk('div', obj.code ? 'code-line' : 'prose-line');
    const txt = obj.txt || '';
    if (!txt) {{
      bodyEl.textContent = '\u00a0';
    }} else if (obj.segs && obj.segs.length) {{
      for (const [isMark, piece] of obj.segs) {{
        bodyEl.appendChild(isMark ? mk('mark', 'slop-mark', piece) : document.createTextNode(piece));
      }}
    }} else {{
      bodyEl.textContent = txt;
    }}
    lcEl.appendChild(bodyEl);

    const bibNotes = [], slopNotes = [];
    for (const item of (obj.notes || [])) (item[0] === 'bib' ? bibNotes : slopNotes).push(item);
    for (const [k, t, m] of bibNotes) lcEl.appendChild(makeNoteNode(k, t, m));
    if (slopNotes.length <= 2) {{
      for (const [k, t, m] of slopNotes) lcEl.appendChild(makeNoteNode(k, t, m));
    }} else {{
      for (let i = 0; i < 2; i++) lcEl.appendChild(makeNoteNode(slopNotes[i][0], slopNotes[i][1], slopNotes[i][2]));
      const det = mk('details', 'more-notes');
      det.appendChild(mk('summary', '', `+${{slopNotes.length - 2}} more style suggestions on L${{obj.ln}}`));
      for (let i = 2; i < slopNotes.length; i++) det.appendChild(makeNoteNode(slopNotes[i][0], slopNotes[i][1], slopNotes[i][2]));
      lcEl.appendChild(det);
    }}

    row.append(mk('span', 'ln', String(obj.ln)), lcEl);
    return row;
  }}

  function showDeferredPage(targetPage, fromKeyboardStep) {{
    const p = Math.max(1, Math.min(totalPages, targetPage));
    curPage = p;
    pageCursor = (p - 1) * PAGE_SIZE;
    clearDynamicRows();
    let firstAdded = null, firstAddedFinding = null, firstAddedLn = null;
    if (p >= 2) {{
      const start = (p - 2) * PAGE_SIZE, end = Math.min(pageData.length, start + PAGE_SIZE);
      const pairs = [];
      for (let i = start; i < end; i++) {{
        const o = pageData[i];
        if (o.kind === 'fold') {{
          if (mountedFolds.has(o.fid)) continue;
          mountedFolds.add(o.fid);
          const node = makeFoldWrapNode(o);
          if (!firstAdded) {{ firstAdded = node; firstAddedLn = o.ln; }}
          pairs.push([o.ln, node]);
          continue;
        }}
        if (mountedLines.has(o.ln)) continue;
        mountedLines.add(o.ln);
        const node = makeRichRowNode(o);
        if (!firstAdded) {{ firstAdded = node; firstAddedLn = o.ln; }}
        if (!firstAddedFinding && o.primary) firstAddedFinding = node;
        pairs.push([o.ln, node]);
      }}
      if (pairs.length > 0) insertRowsInOrder(pairs);
    }} else {{
      firstAddedFinding = sheet.querySelector('.ms-line.finding-row:not([data-dyn])');
      firstAdded = firstAddedFinding || sheet.querySelector('.ms-line:not([data-dyn])');
      if (firstAdded) firstAddedLn = getChildLine(firstAdded);
    }}
    syncPager();
    refreshVisibleTargets(Boolean(fromKeyboardStep));
    if (!fromKeyboardStep) {{
      const gIdx = firstAddedFinding ? globalMatches.indexOf(firstAddedFinding.id) : -1;
      if (firstAddedFinding && gIdx >= 0) {{
        syncFocusedRow(firstAddedFinding, gIdx);
        firstAddedFinding.tabIndex = -1;
        firstAddedFinding.focus({{ preventScroll: true }});
        firstAddedFinding.scrollIntoView({{ block: 'nearest' }});
      }} else if (firstAdded) {{
        navAnchorLine = firstAddedLn;
        firstAdded.tabIndex = -1;
        firstAdded.focus({{ preventScroll: true }});
        firstAdded.scrollIntoView({{ block: 'nearest' }});
      }}
    }}
  }}

  function mountNextPage(fromKeyboardStep) {{
    if (curPage >= totalPages) return;
    showDeferredPage(curPage + 1, fromKeyboardStep);
  }}

  function appendFoldBatch(wrap, fid, labelKind, rangeText, silent) {{
    const rows = foldData[fid] || [], prevShown = Number(wrap.dataset.shown || '0'), nextShown = Math.min(prevShown + BATCH, rows.length);
    let actBar = wrap.querySelector('.fold-actions');
    if (!actBar) {{ const b = wrap.querySelector('.fold-bar'); if (b) b.remove(); actBar = mk('div', 'fold-actions'); wrap.appendChild(actBar); }}
    let firstAdded = null;
    for (let i = prevShown; i < nextShown; i++) {{
      const [ln, isCode, txt] = rows[i];
      if (document.getElementById('L' + ln)) continue;
      const node = makePlainLineNode(ln, isCode, txt);
      if (!firstAdded) firstAdded = node;
      insertFoldRowOrdered(wrap, node, ln, actBar);
    }}
    wrap.dataset.shown = String(nextShown);
    actBar.textContent = '';
    const rem = rows.length - nextShown;
    if (rem > 0) {{
      const mb = mk('button', 'fold-act-btn', `Show ${{Math.min(BATCH, rem)}} more (${{rem}} hidden)`);
      mb.type = 'button'; mb.dataset.foldMore = fid; mb.dataset.label = labelKind; mb.dataset.range = rangeText; actBar.appendChild(mb);
    }}
    const cb = mk('button', 'fold-act-btn', `▾ Collapse (${{rangeText}})`);
    cb.type = 'button'; cb.dataset.foldCollapse = fid; cb.dataset.label = labelKind; cb.dataset.range = rangeText; actBar.appendChild(cb);
    if (!silent && firstAdded) {{ firstAdded.tabIndex = -1; firstAdded.focus({{ preventScroll: true }}); firstAdded.scrollIntoView({{ block: 'nearest' }}); }}
  }}
  function collapseFold(wrap, fid, labelKind, rangeText) {{
    const rows = foldData[fid] || [];
    wrap.textContent = ''; wrap.dataset.shown = '0';
    const b = mk('button', 'fold-bar', `▸ ${{rows.length}} ${{labelKind}} (${{rangeText}}) — expand`);
    b.type = 'button'; b.dataset.fold = fid; b.dataset.label = labelKind; b.dataset.range = rangeText; wrap.appendChild(b);
    b.focus({{ preventScroll: true }});
  }}

  function mkBibCard(b) {{
    const art = mk('article', 'forensic-card tone-' + b[0]), hd = mk('div', 'fc-head'), loc = mk('span', 'fc-loc'), lw = mk('span', 'loc-wrap');
    lw.append(mk('span', 'loc-file', b[2]), mk('span', 'loc-ln', b[3])); loc.appendChild(lw);
    hd.append(mk('strong', 'fc-key', b[1]), loc);
    art.append(hd, mk('p', 'fc-msg', b[4]), mk('div', 'fc-meta', b[5]));
    return art;
  }}
  let prevPrintFocus = null;
  function mountAllForPrint() {{
    prevPrintFocus = D.activeElement;
    const old = $('printRoot'); if (old) old.remove();
    const pr = mk('div'), ws = mk('div', 'print-workspace'), pC = mk('section', 'canvas'), pS = mk('div', 'manuscript-sheet');
    pr.id = 'printRoot'; pS.dataset.filter = 'all';
    // Preserve Diagnostic Summary KPIs, Provenance Notice & Top Finding in Print Full / PDF (P1-10!)
    const ss = D.querySelector('body > .summary-strip'); if (ss) pr.appendChild(ss.cloneNode(true));
    ['.stale-banner', '.priority-banner', '.canvas-head'].forEach(sel => {{ const e = D.querySelector('.workspace .canvas > ' + sel); if (e) pC.appendChild(e.cloneNode(true)); }});
    const entries = [], seen = new Set();
    const pushFold = fid => (foldData[fid] || []).forEach(([ln, ic, t]) => {{ if (!seen.has(ln)) {{ seen.add(ln); entries.push([ln, makePlainLineNode(ln, ic, t)]); }} }});
    for (const ch of sheet.children) {{
      if (ch.id === 'pageMoreWrap' || (ch.dataset && ch.dataset.dyn === '1')) continue;
      if (ch.classList.contains('fold-wrap')) pushFold(ch.dataset.foldWrap);
      else if (ch.id && (ch.id[0] === 'L' || ch.id.startsWith('SUB'))) {{
        const ln = getChildLine(ch);
        if (!seen.has(ln)) {{ seen.add(ln); const cl = ch.cloneNode(true); cl.querySelectorAll('details').forEach(d => {{ d.open = true; }}); entries.push([ln, cl]); }}
      }}
    }}
    for (const o of pageData) {{
      if (o.kind === 'fold') pushFold(o.fid);
      else if (!seen.has(o.ln)) {{ seen.add(o.ln); const nd = makeRichRowNode(o); nd.querySelectorAll('details').forEach(d => {{ d.open = true; }}); entries.push([o.ln, nd]); }}
    }}
    entries.sort((a, b) => a[0] - b[0]);
    const frag = D.createDocumentFragment();
    entries.forEach(([, nd]) => frag.appendChild(nd));
    pS.appendChild(frag); pC.appendChild(pS); ws.appendChild(pC);
    const bp = $('bib-panel');
    if (bp) {{
      const bc = bp.cloneNode(true), bw = bc.querySelector('#bibMoreWrap'), bl = bc.querySelector('#bibList');
      if (bw) bw.remove();
      if (bl) for (let i = bibMoreCursor; i < bibMoreData.length; i++) bl.appendChild(mkBibCard(bibMoreData[i]));
      ws.appendChild(bc);
    }}
    pr.appendChild(ws); D.body.appendChild(pr); root.dataset.printAll = '1';
  }}
  function cleanupPrintRoot() {{
    const pr = $('printRoot'); if (pr) pr.remove();
    delete root.dataset.printAll;
    if (prevPrintFocus && prevPrintFocus.focus) prevPrintFocus.focus({{ preventScroll: true }});
    prevPrintFocus = null;
  }}
  ae(window, 'beforeprint', mountAllForPrint);
  ae(window, 'afterprint', cleanupPrintRoot);
  const loadBibBtn = $('loadMoreBibBtn');
  if (loadBibBtn) ae(loadBibBtn, 'click', () => {{
    const bl = $('bibList'); if (!bl) return;
    const frag = D.createDocumentFragment();
    for (let n = 0; bibMoreCursor < bibMoreData.length && n < BATCH; n++) frag.appendChild(mkBibCard(bibMoreData[bibMoreCursor++]));
    bl.appendChild(frag);
    const rem = bibMoreData.length - bibMoreCursor;
    if (rem <= 0) {{ const w = $('bibMoreWrap'); if (w) w.remove(); }}
    else loadBibBtn.textContent = `▸ Load next ${{Math.min(BATCH, rem)}} bibliography issue(s) (${{rem}} remaining)`;
  }});

  const on = (id, fn) => {{ const e = $(id); if (e) ae(e, 'click', fn); }};
  ae(sheet, 'click', e => {{
    if (e.target.closest('#loadMorePageBtn')) return mountNextPage(false);
    if (e.target.closest('#prevPageBtn')) return showDeferredPage(curPage - 1, false);
    const o = e.target.closest('.fold-bar[data-fold]'); if (o) return appendFoldBatch(o.closest('.fold-wrap'), o.dataset.fold, o.dataset.label || 'lines', o.dataset.range || '', false);
    const m = e.target.closest('[data-fold-more]'); if (m) return appendFoldBatch(m.closest('.fold-wrap'), m.dataset.foldMore, m.dataset.label || 'lines', m.dataset.range || '', false);
    const c = e.target.closest('[data-fold-collapse]'); if (c) return collapseFold(c.closest('.fold-wrap'), c.dataset.foldCollapse, c.dataset.label || 'lines', c.dataset.range || '');
    if (!e.target.closest('button,a,summary')) {{
      const row = e.target.closest('.finding-row');
      if (row) {{ const gi = globalMatches.indexOf(row.id); if (gi >= 0) syncFocusedRow(row, gi); }}
    }}
  }});
  D.querySelectorAll('.pb-jump[data-jump-target]').forEach(link => link.addEventListener('click', e => {{
    const tid = link.dataset.jumpTarget; if (!tid) return;
    e.preventDefault();
    const filt = sheet.dataset.filter || 'all', kind = link.dataset.jumpKind || '';
    if ((kind === 'bib' && filt === 'slop') || (kind === 'slop' && filt === 'bib') || globalMatches.indexOf(tid) === -1) setFilter('all', true);
    const gi = globalMatches.indexOf(tid);
    if (gi !== -1) focusGlobalIndex(gi, true);
    else {{ const t = mountTargetById(tid); if (t) t.scrollIntoView({{ behavior: 'auto', block: 'center' }}); }}
  }}));
  const searchBar = $('searchBar'), searchInput = $('liteSearch'), searchMeta = $('searchMeta');
  let searchHits = [], searchCursor = -1;
  function buildSearchHits(qRaw) {{
    const q = (qRaw || '').trim().toLowerCase();
    searchHits = []; searchCursor = -1;
    if (!q) {{ searchMeta.textContent = ''; return; }}
    const seen = new Set();
    // Canonical full-document search across prose AND rendered/deferred margin notes — invariant before/after Load More (P1-07!)
    sheet.querySelectorAll('.ms-line:not([data-dyn])').forEach(el => {{ if ((el.textContent || '').toLowerCase().includes(q)) {{ seen.add(el.id); searchHits.push(el.id); }} }});
    for (const o of pageData) if (o.kind !== 'fold') {{
      const tid = o.kind === 'sub' ? ('SUB' + o.sid) : ('L' + o.ln);
      const hay = o.kind === 'sub'
        ? `Included subfile ${{o.file || ''}} L${{o.bline || ''}} unresolved citation ${{o.key || ''}} Missing in .bib (${{o.key || ''}}) ${{o.msg || ''}} ${{o.reason || ''}}`
        : `${{o.txt || ''}} ${{o.reason || ''}} ${{(o.notes || []).map(n => n.join(' ')).join(' ')}}`;
      if (!seen.has(tid) && hay.toLowerCase().includes(q)) {{ seen.add(tid); searchHits.push(tid); }}
    }}
    for (const rows of Object.values(foldData)) for (const r of rows) {{ const tid = 'L' + r[0]; if (!seen.has(tid) && String(r[2] || '').toLowerCase().includes(q)) {{ seen.add(tid); searchHits.push(tid); }} }}
    searchHits.sort((a, b) => parseTargetLine(a) - parseTargetLine(b));
    searchMeta.textContent = searchHits.length ? `${{searchHits.length}} match(es)` : '0 matches';
  }}
  function jumpSearchHit(delta) {{
    if (!searchHits.length) return;
    if ((sheet.dataset.filter || 'all') !== 'all') setFilter('all', true);
    searchCursor = searchCursor === -1 ? (delta >= 0 ? 0 : searchHits.length - 1) : (searchCursor + delta + searchHits.length) % searchHits.length;
    const tid = searchHits[searchCursor], el = mountTargetById(tid);
    if (el) {{
      const gi = globalMatches.indexOf(tid);
      if (gi >= 0) syncFocusedRow(el, gi);
      else {{ if (focusedEl && focusedEl !== el) focusedEl.classList.remove('is-focused'); focusedEl = el; el.classList.add('is-focused'); }}
      el.tabIndex = -1; el.focus({{ preventScroll: true }});
      el.scrollIntoView({{ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' }});
    }}
    searchMeta.textContent = `${{searchCursor + 1}}/${{searchHits.length}}`;
  }}
  const openSearchBar = () => {{ searchBar.hidden = false; syncTopbarHeight(); searchInput.focus(); searchInput.select(); }};
  const closeSearchBar = () => {{ searchBar.hidden = true; syncTopbarHeight(); }};
  on('findToggleBtn', () => searchBar.hidden ? openSearchBar() : closeSearchBar());
  on('searchCloseBtn', closeSearchBar);
  on('searchNextBtn', () => jumpSearchHit(1));
  on('searchPrevBtn', () => jumpSearchHit(-1));
  searchInput.addEventListener('input', () => buildSearchHits(searchInput.value));
  searchInput.addEventListener('keydown', e => {{
    if (e.key === 'Enter') {{ e.preventDefault(); if (!searchHits.length) buildSearchHits(searchInput.value); jumpSearchHit(e.shiftKey ? -1 : 1); }}
    else if (e.key === 'Escape') {{ e.preventDefault(); closeSearchBar(); }}
  }});
  on('printAllBtn', () => {{ mountAllForPrint(); if (typeof window.print === 'function') window.print(); }});
  $('filterBar').addEventListener('click', e => {{ const b = e.target.closest('.chip'); if (b) setFilter(b.dataset.filter, false); }});
  on('nextBtn', () => stepFinding(1));
  on('prevBtn', () => stepFinding(-1));
  on('themeBtn', toggleTheme);

  window.addEventListener('keydown', (e) => {{
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.metaKey || e.ctrlKey || e.altKey) return;
    if (!searchBar.hidden && e.key === 'Escape') {{ e.preventDefault(); closeSearchBar(); return; }}
    if (!searchBar.hidden && e.key === 'Enter' && !e.target.closest('button, a, summary')) {{
      e.preventDefault();
      if (!searchHits.length) buildSearchHits(searchInput.value);
      jumpSearchHit(e.shiftKey ? -1 : 1);
      return;
    }}
    if (e.key === 'j') {{ e.preventDefault(); stepFinding(1); }}
    else if (e.key === 'k') {{ e.preventDefault(); stepFinding(-1); }}
    else if (e.key === '/') {{ e.preventDefault(); openSearchBar(); }}
    else if (e.key === 'd') {{ e.preventDefault(); toggleTheme(); }}
  }});

  refreshVisibleTargets();
}})();
</script>
</body>
</html>
""")


def _is_slop_cache_valid(slop_res: dict[str, Any], manuscript_path: pathlib.Path, slop_mod: Any) -> bool:
    prov = slop_res.get("provenance") if isinstance(slop_res, dict) else None
    if not isinstance(prov, dict):
        return False
    if prov.get("tool") != "scan_ai_slop_lite" or prov.get("schema_version") != slop_mod.SCHEMA_VERSION:
        return False
    expected_sha = prov.get("manuscript_sha256")
    if not (expected_sha and expected_sha == _sha256_file(manuscript_path)):
        return False
    # P1-05: Verify full recursive manuscript include tree digests (not just root file!)
    rec_files = prov.get("manuscript_files")
    if isinstance(rec_files, dict) and rec_files:
        cur_paths = slop_mod.collect_slop_protected_paths(manuscript_path)
        cur_map = {str(p.resolve()): _sha256_file(p) for p in cur_paths if p.is_file()}
        if rec_files != cur_map:
            return False
    return True


def _validate_bib_cache_lite(
    bib_res: dict[str, Any],
    manuscript_path: pathlib.Path,
    bib_path: pathlib.Path | None,
    ms_tree: list[pathlib.Path],
    audit_mod: Any,
    requested_online: bool = False,
) -> tuple[str, str]:
    """Return ('fresh' | 'historical' | 'stale', reason) verifying schema, source .bib, .aux & manuscript tree (P0-06, P1-04, P1-09!)."""
    prov = bib_res.get("provenance") if isinstance(bib_res, dict) else None
    if not isinstance(prov, dict):
        return "stale", f"Cached --bib-json lacks v{audit_mod.SCHEMA_VERSION} provenance digests"
    if prov.get("tool") != "audit_bib_lite" or prov.get("schema_version") != audit_mod.SCHEMA_VERSION:
        return (
            "stale",
            f"Cached --bib-json uses schema v{prov.get('schema_version') or 'unknown'} (expected v{audit_mod.SCHEMA_VERSION})",
        )
    mode_low = str(bib_res.get("mode", "")).strip().lower()
    is_cached_offline = (
        prov.get("offline") is True
        or str(prov.get("network_mode", "")).strip().lower() == "offline"
        or mode_low.startswith("offline")
        or int(bib_res.get("unqueried_count", 0) or 0) > 0
    )
    if requested_online and is_cached_offline:
        return "stale", "Cached --bib-json was generated offline or has unqueried entries, but --online was requested"
    rec_aux_map = prov.get("aux_files")
    if isinstance(rec_aux_map, dict) and rec_aux_map:
        for aux_p_str, aux_sha in rec_aux_map.items():
            ap = pathlib.Path(str(aux_p_str))
            if not ap.is_file() or _sha256_file(ap) != str(aux_sha):
                return "stale", f"Compiler artifact '{ap.name}' changed or missing since --bib-json was generated"
    rec_extra_bibs = prov.get("extra_bib_files")
    if isinstance(rec_extra_bibs, dict) and rec_extra_bibs:
        for eb_p_str, eb_sha in rec_extra_bibs.items():
            ep = pathlib.Path(str(eb_p_str))
            if not ep.is_file() or _sha256_file(ep) != str(eb_sha):
                return "stale", f"Sibling bibliography '{ep.name}' changed or missing since --bib-json was generated"
    rec_bib_for_disc = bib_path or (pathlib.Path(str(prov.get("bib_path"))) if prov.get("bib_path") else None)
    if rec_bib_for_disc is not None and rec_bib_for_disc.is_file():
        rec_aux_for_disc = pathlib.Path(str(prov.get("aux_oracle"))) if prov.get("aux_oracle") else None
        live_extra = pg_oracles.discover_declared_bib_files(rec_bib_for_disc, ms_tree, rec_aux_for_disc).get("extra_bib_paths", [])
        live_extra_map = {str(p.resolve()): _sha256_file(p) for p in live_extra if p.is_file()}
        recorded_extra_map = dict(rec_extra_bibs) if isinstance(rec_extra_bibs, dict) else {}
        if live_extra_map != recorded_extra_map:
            return "stale", "Declared sibling bibliography database(s) changed on disk since --bib-json was generated"
    ms_map = prov.get("manuscript_files")
    if not isinstance(ms_map, dict) or not ms_map:
        return "stale", "Cached --bib-json lacked manuscript citation alignment"
    cur_map = {str(p.resolve()): _sha256_file(p) for p in ms_tree}
    if ms_map != cur_map:
        return "stale", "Cached --bib-json does not match current manuscript tree SHA-256"

    rec_bib_sha = prov.get("bib_sha256")
    if bib_path is not None:
        if rec_bib_sha != _sha256_file(bib_path):
            return "stale", "Cached --bib-json does not match current --bib SHA-256"
        return "fresh", ""

    rec_bib_p_str = prov.get("bib_path") or bib_res.get("bib_path")
    if rec_bib_p_str and rec_bib_sha:
        rec_bib_p = pathlib.Path(str(rec_bib_p_str))
        if rec_bib_p.is_file():
            if _sha256_file(rec_bib_p) != rec_bib_sha:
                return "stale", f"Source bibliography '{rec_bib_p.name}' changed on disk since --bib-json was generated"
            return "fresh", ""
        gen_ts = prov.get("generated_at") or "unknown time"
        mode_s = str(bib_res.get("mode", "recorded"))
        return (
            "historical",
            f"Historical --bib-json snapshot (source '{rec_bib_p.name}' unavailable on disk; recorded at {gen_ts}, mode: {mode_s}) — pass --bib to verify current file.",
        )
    return "fresh", ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render PaperGuard Lite's accessible HTML diagnostic cockpit (offline by default)."
    )
    parser.add_argument("--manuscript", "-m", type=pathlib.Path, required=True, help="Path to .tex or .md")
    parser.add_argument("--bib", "-b", type=pathlib.Path, default=None, help="Optional .bib file")
    parser.add_argument("--bib-json", type=pathlib.Path, default=None, help="Precomputed audit_bib_lite JSON")
    parser.add_argument("--slop-json", type=pathlib.Path, default=None, help="Precomputed scan_ai_slop_lite JSON")
    parser.add_argument(
        "--offline", action="store_true", help="Explicitly run offline (now the default unless --online is passed)"
    )
    parser.add_argument(
        "--online", action="store_true", help="Allow live Crossref/DataCite/arXiv lookups when --bib is passed"
    )
    parser.add_argument(
        "--out", "-o", type=pathlib.Path, default=pathlib.Path("paperguard_audit.html"), help="Output HTML path"
    )
    parser.add_argument("--open", action="store_true", help="Open generated report in default browser")
    args = parser.parse_args(argv)

    if not args.manuscript.exists():
        print(f"Error: manuscript not found: {args.manuscript}", file=sys.stderr)
        return 2
    for opt_in in (args.bib, args.bib_json, args.slop_json):
        if opt_in is not None and not opt_in.exists():
            print(f"Error: specified input file not found: {opt_in}", file=sys.stderr)
            return 2

    audit_mod = _load_sibling("audit_bib_lite")
    slop_mod = _load_sibling("scan_ai_slop_lite")
    try:
        ms_tree = audit_mod.collect_manuscript_paths(args.manuscript)
        slop_tree = slop_mod.collect_slop_protected_paths(args.manuscript)
    except pg_tex_ast.SourceEncodingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    raw_slop_cache: Any = None
    raw_bib_cache: Any = None
    cached_paths: list[pathlib.Path] = []
    try:
        for _flag_lbl, fpath, slot in (("--slop-json", args.slop_json, "slop"), ("--bib-json", args.bib_json, "bib")):
            if fpath is None:
                continue
            raw_b = fpath.read_bytes()
            enc = (
                "utf-32"
                if raw_b.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"))
                else ("utf-16" if raw_b.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig" if raw_b.startswith(b"\xef\xbb\xbf") else "utf-8"))
            )
            parsed = json.loads(raw_b.decode(enc))
            if slot == "slop":
                raw_slop_cache = parsed
            else:
                raw_bib_cache = parsed
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Error: malformed JSON cache input: {exc}", file=sys.stderr)
        return 2
    for cdata in (raw_slop_cache, raw_bib_cache):
        if isinstance(cdata, dict):
            for k in ("bib_path", "manuscript_path"):
                if isinstance(cdata.get(k), str) and cdata[k]:
                    cached_paths.append(pathlib.Path(cdata[k]))
            prov = cdata.get("provenance")
            if isinstance(prov, dict):
                for k in ("bib_path", "manuscript_path", "aux_oracle"):
                    if isinstance(prov.get(k), str) and prov[k]:
                        cached_paths.append(pathlib.Path(prov[k]))
                for mk in ("manuscript_files", "aux_files", "extra_bib_files"):
                    mf = prov.get(mk)
                    if isinstance(mf, dict):
                        cached_paths.extend(pathlib.Path(str(k)) for k in mf)

    declared_bib_protected = pg_oracles.discover_manuscript_bib_files([*ms_tree, *slop_tree], None)
    extra_bib_protected = (
        pg_oracles.discover_declared_bib_files(args.bib, ms_tree, None).get("extra_bib_paths", [])
        if (args.bib is not None and args.bib.is_file())
        else []
    )
    for inp in (*ms_tree, *slop_tree, *declared_bib_protected, *extra_bib_protected, *cached_paths, args.bib, args.bib_json, args.slop_json):
        if inp is not None and _same_path(inp, args.out):
            print(f"Error: --out '{args.out}' collides with protected input/cached file '{inp}'.", file=sys.stderr)
            return 2

    use_offline = not args.online
    cache_warnings: list[str] = []

    try:
        if args.slop_json:
            slop_res = raw_slop_cache
            if not _is_slop_cache_valid(slop_res, args.manuscript, slop_mod):
                cache_warnings.append(
                    f"Cached --slop-json did not match current v{slop_mod.SCHEMA_VERSION} manuscript provenance; recomputed live."
                )
                slop_res = slop_mod.scan_manuscript_slop(args.manuscript).to_dict()
        else:
            slop_res = slop_mod.scan_manuscript_slop(args.manuscript).to_dict()

        bib_res: dict[str, Any] | None = None
        bib_stale_withheld = False
        bib_historical_snapshot = False
        if args.bib_json:
            loaded_bib = raw_bib_cache
            bib_state, bib_reason = _validate_bib_cache_lite(
                loaded_bib, args.manuscript, args.bib, ms_tree, audit_mod, requested_online=args.online
            )
            if bib_state == "fresh":
                bib_res = loaded_bib
            elif bib_state == "historical":
                bib_res = loaded_bib
                bib_historical_snapshot = True
                cache_warnings.append(bib_reason)
            else:
                cache_warnings.append(f"Stale --bib-json rejected ({bib_reason}).")
                if args.bib is not None:
                    rec_prov_b = loaded_bib.get("provenance") if isinstance(loaded_bib, dict) else None
                    rec_aux_str = rec_prov_b.get("aux_oracle") if isinstance(rec_prov_b, dict) else None
                    rec_aux_p = pathlib.Path(str(rec_aux_str)) if rec_aux_str else None
                    if rec_aux_p is not None and not rec_aux_p.is_file():
                        rec_aux_p = None
                    bib_res = audit_mod.audit_bibliography(
                        bib_path=args.bib,
                        manuscript_path=args.manuscript,
                        offline=use_offline,
                        aux_path=rec_aux_p,
                    ).to_dict()
                else:
                    bib_stale_withheld = True
        elif args.bib:
            bib_res = audit_mod.audit_bibliography(
                bib_path=args.bib, manuscript_path=args.manuscript, offline=use_offline
            ).to_dict()

        html_doc = render_lite_html(
            args.manuscript,
            bib_res,
            slop_res,
            cache_warnings=cache_warnings,
            bib_stale_withheld=bib_stale_withheld,
            bib_historical_snapshot=bib_historical_snapshot,
        )
    except (FileNotFoundError, ValueError, pg_tex_ast.SourceEncodingError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    _atomic_write_text(args.out, html_doc)
    print(f"Wrote PaperGuard Lite Editorial Report ({len(html_doc.encode('utf-8'))} bytes) -> {args.out}")

    if args.open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
