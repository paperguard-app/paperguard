#!/usr/bin/env python3
"""PaperGuard Lite — Academic Prose Cliché & Cadence Scanner (v2.0).

Upgrades:
- Powered by vendored MIT-licensed `pylatexenc` AST (`pg_tex_ast.py`) for exact TeX
  comment, verbatim, `\\verb`/`\\lstinline`/`\\mintinline`, `alltt`, table & math masking
- Unified lowercase Markdown extension set (.md, .markdown, .qmd, .rmd)
- CommonMark-aware Markdown masking: YAML frontmatter, HTML comments, variable-length
  fenced code blocks (``` / ```` / ~~~), 4-space/tab indented code blocks, variable-length
  inline backticks, and math — preserving '%' in prose!
- Cross-line phrase matching ('It is worth\\nnoting that') with $O(\\log N)$ bisect
  mapping back to exact (line, col_start, col_end)
- Strict same-file write collision block + SHA-256 provenance digest

0 external pip dependencies (Python 3.11+ stdlib + vendored MIT pylatexenc).
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any

_VENDOR_DIR = pathlib.Path(__file__).resolve().parent / "_vendor"
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

import pg_oracles  # noqa: E402
import pg_tex_ast  # noqa: E402

SCHEMA_VERSION = "2.5"
PRO_UPGRADE_URL = "https://zhangx2.gumroad.com/l/paperguard-pro"
MARKDOWN_SUFFIXES = {".md", ".markdown", ".qmd", ".rmd"}

# (pattern_regex, canonical_label, tier, weight, editorial_hint)
SLOP_PATTERNS: list[tuple[re.Pattern[str], str, int, int, str]] = [
    (re.compile(r"\bdelve(?:s|d|ing)?\s+into\b", re.I), "delve into", 1, 6, "Use 'examine', 'analyze', or state the technical action directly."),
    (re.compile(r"\bintricate\s+tapestry\b|\btapestry\s+of\b", re.I), "tapestry of", 1, 7, "Delete metaphor; specify the exact components or coupling."),
    (re.compile(r"\b(?:stands\s+as\s+a\s+)?testament\s+to\b", re.I), "testament to", 1, 6, "Replace with 'demonstrates' or cite the specific empirical table."),
    (re.compile(r"\bparadigm\s+shift\b", re.I), "paradigm shift", 1, 6, "Avoid overclaiming; state the concrete reformulation instead."),
    (re.compile(r"\bunderscores?\s+the\s+(?:importance|need|significance)\b", re.I), "underscores the importance", 1, 5, "Explain the mechanism or cite the ablation delta."),
    (re.compile(r"\bpivotal\s+role\b|\bplays?\s+a\s+pivotal\b", re.I), "pivotal role", 1, 5, "Prefer 'governs', 'controls', or state the direct dependency."),
    (re.compile(r"\bin\s+the\s+realm\s+of\b", re.I), "in the realm of", 1, 5, "Replace with 'in' or the specific task setting."),
    (re.compile(r"\brapidly\s+evolving\s+landscape\b", re.I), "rapidly evolving landscape", 1, 6, "Cut generic opening cliché; open directly with the technical bottleneck."),
    (re.compile(r"\bseamlessly\s+integrat(?:e|es|ed|ing)\b", re.I), "seamlessly integrates", 1, 5, "Drop 'seamlessly'; specify how the operators connect."),
    (re.compile(r"\bsheds?\s+light\s+on\b", re.I), "shed light on", 1, 4, "Use 'clarifies', 'quantifies', or 'isolates'."),
    (re.compile(r"\bpaves?\s+the\s+way\s+for\b", re.I), "pave the way for", 1, 4, "Name the concrete extension rather than a closing cliché."),
    (re.compile(r"\bmultifaceted\s+challenges?\b", re.I), "multifaceted challenges", 1, 5, "Enumerate the specific competing constraints."),
    (re.compile(r"\bholistic\s+(?:approach|framework|mechanism|view)\b", re.I), "holistic framework", 1, 4, "Specify 'joint', 'end-to-end', or the coupled objective."),
    (re.compile(r"\bit\s+is\s+(?:important|crucial)\s+to\s+note\s+that\b", re.I), "it is important to note that", 2, 4, "Delete filler prefix; lead with the grammatical subject."),
    (re.compile(r"\bit\s+is\s+worth\s+(?:noting|mentioning)\s+that\b", re.I), "it is worth noting that", 2, 4, "Delete throat-clearing clause."),
    (re.compile(r"\bcrucial\s+aspect\b", re.I), "crucial aspect", 2, 3, "Name the exact bottleneck or parameter."),
    (re.compile(r"\bremarkable\b|\bremarkably\b", re.I), "remarkable", 2, 3, "Remove promotional intensifier; let reported table values speak."),
    (re.compile(r"\bfurthermore,\s+it\s+is\b|\bmoreover,\s+it\s+is\b", re.I), "furthermore/moreover, it is", 2, 3, "Simplify stacked conjunctive transition."),
]

TEX_SECTION_RE = re.compile(r"\\(?:section|subsection)\*?\{([^}]+)\}|\\begin\{(abstract)\}")
MD_SECTION_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

TEX_SKIP_ENV_RE = re.compile(
    r"\\begin\{\s*(equation\*?|align\*?|alignat\*?|flalign\*?|gather\*?|multline\*?|eqnarray\*?|"
    r"IEEEeqnarray\*?|tabular\*?|tabularx|thebibliography|lstlisting|verbatim|Verbatim|minted)\s*\}"
    r".*?\\end\{\s*\1\s*\}",
    re.DOTALL,
)


class LineIndex:
    def __init__(self, text: str) -> None:
        self.starts = [0]
        for idx, ch in enumerate(text):
            if ch == "\n":
                self.starts.append(idx + 1)
        self.total_len = len(text)

    def pos_to_lc(self, pos: int) -> tuple[int, int]:
        line_idx = bisect.bisect_right(self.starts, pos) - 1
        return line_idx + 1, pos - self.starts[line_idx] + 1

    def line_end_offset(self, line_1based: int) -> int:
        if line_1based < len(self.starts):
            return self.starts[line_1based] - 1
        return self.total_len


@dataclass
class SlopHit:
    line: int
    end_line: int
    column: int
    end_column: int
    section: str
    phrase: str
    matched_text: str
    tier: int
    weight: int
    hint: str
    snippet: str
    last_line_end_column: int = 0
    file: str = ""


@dataclass
class SectionSlopStats:
    name: str
    start_line: int
    word_count: int
    hit_count: int
    sentence_cv: float
    monotonous_rhythm: bool
    section_score: int
    file: str = ""


@dataclass
class SlopScanReport:
    manuscript_path: str
    format_mode: str
    total_words: int
    total_hits: int
    overall_score: int
    grade: str
    metric_note: str
    hits_per_1k_words: float = 0.0
    sections: list[SectionSlopStats] = field(default_factory=list)
    hits: list[SlopHit] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    fd, tmp_name = tempfile.mkstemp(prefix=".pg_slop_", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _preserve_newlines(match: re.Match[str]) -> str:
    return "".join("\n" if ch == "\n" else " " for ch in match.group(0))


def _is_escaped(text: str, pos: int) -> bool:
    bs = 0
    k = pos - 1
    while k >= 0 and text[k] == "\\":
        bs += 1
        k -= 1
    return (bs % 2) == 1


def _read_balanced_brace_end(text: str, start: int) -> int | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if not _is_escaped(text, i):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return None


def _skip_inline_code_end(raw_text: str, start: int) -> int | None:
    """Return end index for \\verb, \\lstinline, or \\mintinline (both braced {code} and |code| delimiters, P2-03!)."""
    n = len(raw_text)
    vm = re.match(r"\\verb\*?([^\s\w])", raw_text[start:])
    if vm:
        end_d = raw_text.find(vm.group(1), start + vm.end())
        return n if end_d == -1 else end_d + 1

    cm = re.match(r"\\(lstinline|mintinline)\b", raw_text[start:])
    if not cm:
        return None
    cmd = cm.group(1)
    cur = start + cm.end()
    # Skip optional [...] arguments
    while True:
        while cur < n and raw_text[cur] in " \t":
            cur += 1
        if cur < n and raw_text[cur] == "[":
            close_b = raw_text.find("]", cur + 1)
            if close_b == -1:
                return n
            cur = close_b + 1
        else:
            break
    # \mintinline requires mandatory {language} before the inline code argument
    if cmd == "mintinline":
        while cur < n and raw_text[cur] in " \t":
            cur += 1
        if cur >= n or raw_text[cur] != "{":
            return None
        lang_end = _read_balanced_brace_end(raw_text, cur)
        if lang_end is None:
            return n
        cur = lang_end
        while cur < n and raw_text[cur] in " \t":
            cur += 1
    if cur >= n:
        return n
    if raw_text[cur] == "{":
        brace_end = _read_balanced_brace_end(raw_text, cur)
        return n if brace_end is None else brace_end
    if not raw_text[cur].isalnum() and not raw_text[cur].isspace():
        end_d = raw_text.find(raw_text[cur], cur + 1)
        return n if end_d == -1 else end_d + 1
    return None


def mask_tex_non_prose(raw_text: str) -> tuple[str, str]:
    """Returns (comment_free_tex_for_sections, fully_masked_prose_same_length) via vendored pylatexenc AST."""
    no_comments = pg_tex_ast.mask_tex_via_ast(
        raw_text,
        mask_comments=True,
        mask_verbatim=True,
        mask_math=False,
        mask_tables=False,
        mask_cite_ref_macros=False,
        mask_bib_env=False,
        mask_alltt=False,
    )
    masked = pg_tex_ast.mask_tex_via_ast(
        raw_text,
        mask_comments=True,
        mask_verbatim=True,
        mask_math=True,
        mask_tables=True,
        mask_cite_ref_macros=True,
        mask_bib_env=True,
        mask_alltt=True,
    )
    return no_comments, masked


def _mask_markdown_code_blocks_and_inline(raw_text: str) -> str:
    """CommonMark-aware line-by-line masker for variable fences, 4-space/tab indented code, and inline code."""
    lines = raw_text.splitlines(keepends=True)
    out_lines: list[str] = []
    in_fence_char: str | None = None
    in_fence_len = 0
    prev_blank_or_indented = True

    for raw_line in lines:
        nl = "\r\n" if raw_line.endswith("\r\n") else ("\n" if raw_line.endswith("\n") else "")
        body = raw_line[: len(raw_line) - len(nl)] if nl else raw_line

        if in_fence_char is not None:
            close_m = re.match(r"^ {0,3}(" + re.escape(in_fence_char) + r"{3,})[ \t]*$", body)
            out_lines.append((" " * len(body)) + nl)
            if close_m and len(close_m.group(1)) >= in_fence_len:
                in_fence_char = None
                in_fence_len = 0
                prev_blank_or_indented = False
            continue

        open_m = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", body)
        if open_m and not (open_m.group(1).startswith("`") and "`" in open_m.group(2)):
            in_fence_char = open_m.group(1)[0]
            in_fence_len = len(open_m.group(1))
            out_lines.append((" " * len(body)) + nl)
            prev_blank_or_indented = False
            continue

        if body.strip() == "":
            out_lines.append(raw_line)
            prev_blank_or_indented = True
            continue

        if prev_blank_or_indented and (body.startswith("    ") or body.startswith("\t")):
            out_lines.append((" " * len(body)) + nl)
            prev_blank_or_indented = True
            continue

        prev_blank_or_indented = False
        chars = list(body)
        i = 0
        n = len(chars)
        while i < n:
            if chars[i] == "`":
                tick_start = i
                while i < n and chars[i] == "`":
                    i += 1
                tick_len = i - tick_start
                closer = body.find("`" * tick_len, i)
                if closer != -1:
                    for p in range(tick_start, closer + tick_len):
                        chars[p] = " "
                    i = closer + tick_len
                    continue
            else:
                i += 1
        out_lines.append("".join(chars) + nl)

    return "".join(out_lines)


def mask_markdown_non_prose(raw_text: str) -> tuple[str, str]:
    """Masks YAML frontmatter, HTML comments, variable-length fenced/indented/inline code, and math."""
    masked = raw_text
    if masked.startswith("---\n") or masked.startswith("---\r\n"):
        masked = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", _preserve_newlines, masked, count=1, flags=re.DOTALL)
    masked = re.sub(r"<!--.*?-->", _preserve_newlines, masked, flags=re.DOTALL)
    no_code = _mask_markdown_code_blocks_and_inline(masked)
    masked = re.sub(r"\$\$.*?\$\$", _preserve_newlines, no_code, flags=re.DOTALL)
    masked = re.sub(r"(?<!\\)\$(?!\s)[^$\n]+?(?<!\s)(?<!\\)\$", _preserve_newlines, masked)
    return no_code, masked


def _compute_sentence_cv(prose_chunk: str) -> tuple[float, bool]:
    clean = re.sub(r"\\[a-zA-Z]+\*?", " ", prose_chunk)
    clean = clean.replace("{", " ").replace("}", " ")
    sentences = [s.strip() for s in re.split(r"[.!?]+(?:\s+|$)", clean) if len(s.split()) >= 5]
    if len(sentences) < 4:
        return 0.35, False
    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    if mean <= 0:
        return 0.0, False
    variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
    cv = math.sqrt(variance) / mean
    return round(cv, 3), cv < 0.18


_TEX_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}|\\input[ \t]+([^\s%{}\\]+)")


def _norm_inc_key(raw_name: str) -> str:
    cleaned = os.path.normpath(raw_name.strip().replace("\\", "/")).replace("\\", "/")
    if cleaned.lower().endswith(".tex"):
        cleaned = cleaned[:-4]
    return cleaned


def _resolve_tex_child(root_dir: pathlib.Path, cur_dir: pathlib.Path, target: str) -> pathlib.Path | None:
    raw = target.strip()
    if not raw or "\\" in raw:
        return None
    for base in (cur_dir, root_dir):
        cand = base / raw
        if cand.is_file():
            return cand
        if not cand.suffix:
            cand_tex = cand.with_suffix(".tex")
            if cand_tex.is_file():
                return cand_tex
    return None


def _truncate_top_level_endinput(text: str) -> str:
    if "\\endinput" not in text:
        return text
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{" and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif ch == "}" and (i == 0 or text[i - 1] != "\\"):
            depth = max(0, depth - 1)
        elif (
            depth == 0
            and ch == "\\"
            and text.startswith("\\endinput", i)
            and (i + 9 >= n or not text[i + 9].isalpha())
        ):
            nl = text.find("\n", i + 9)
            return text[: (n if nl == -1 else nl + 1)]
        i += 1
    return text


def _collect_tex_project_files(
    manuscript_path: pathlib.Path,
    _visited: set[pathlib.Path] | None = None,
    _root_dir: pathlib.Path | None = None,
    _includeonly: list[set[str] | None] | None = None,
    _inherited_encoding: str | None = None,
) -> tuple[list[tuple[pathlib.Path, str, str, dict[str, Any]]], list[str]]:
    """Traverse active LaTeX `\\input`/`\\include` tree honoring `\\includeonly`, `\\endinput` & encoding (P1-04/P2-03)."""
    visited = _visited if _visited is not None else set()
    root_dir = _root_dir if _root_dir is not None else manuscript_path.parent
    inc_only = _includeonly if _includeonly is not None else [None]
    res = manuscript_path.resolve()
    if res in visited or not manuscript_path.is_file():
        return [], []
    visited.add(res)

    raw_text, enc_meta = pg_tex_ast.decode_tex_bytes(
        manuscript_path.read_bytes(), res, inherited_encoding=_inherited_encoding
    )
    eff_enc = enc_meta.get("declared_encoding") or _inherited_encoding
    # P0-01 & P1-05: Honor \end{document}, \endinput & \iffalse...\fi identically for scan, counts & write-protection!
    if _root_dir is None:
        raw_text, _ = pg_tex_ast.truncate_after_end_document(raw_text)
    raw_text = pg_tex_ast.strip_iffalse_blocks(raw_text)
    comment_free, _ = mask_tex_non_prose(raw_text)
    active_comment_free = _truncate_top_level_endinput(comment_free)
    active_raw = raw_text[: len(active_comment_free)]
    for io_m in re.finditer(r"\\includeonly\s*\{([^}]+)\}", active_comment_free):
        inc_only[0] = {_norm_inc_key(x) for x in io_m.group(1).split(",") if x.strip()}

    try:
        disp = pathlib.Path(os.path.relpath(res, root_dir.resolve())).as_posix()
    except ValueError:
        disp = manuscript_path.name
    files: list[tuple[pathlib.Path, str, str, dict[str, Any]]] = [(manuscript_path, disp, active_raw, enc_meta)]
    unresolved: list[str] = []

    for cmd_name, inc_raw in pg_tex_ast.iter_tex_include_directives(active_comment_free):
        if not inc_raw:
            continue
        if cmd_name == "include" and inc_only[0] is not None and _norm_inc_key(inc_raw) not in inc_only[0]:
            continue
        child = _resolve_tex_child(root_dir, manuscript_path.parent, inc_raw)
        if child is not None:
            sub_f, sub_u = _collect_tex_project_files(child, visited, root_dir, inc_only, eff_enc)
            files.extend(sub_f)
            unresolved.extend(sub_u)
        elif cmd_name != "InputIfFileExists":
            unresolved.append(inc_raw)

    return files, unresolved


def collect_slop_protected_paths(manuscript_path: pathlib.Path) -> list[pathlib.Path]:
    """Return manuscript + all recursively included `.tex` files for read-only write collision guard (P0-08)."""
    if manuscript_path.suffix.lower() in MARKDOWN_SUFFIXES:
        return [manuscript_path]
    try:
        files, _ = _collect_tex_project_files(manuscript_path)
        return [f[0] for f in files] or [manuscript_path]
    except pg_tex_ast.SourceEncodingError:
        return [manuscript_path]


def scan_manuscript_slop(manuscript_path: pathlib.Path) -> SlopScanReport:
    is_md = manuscript_path.suffix.lower() in MARKDOWN_SUFFIXES
    if is_md:
        raw_md, enc_meta = pg_tex_ast.decode_tex_bytes(manuscript_path.read_bytes(), manuscript_path.resolve())
        source_entries: list[tuple[pathlib.Path, str, str, dict[str, Any]]] = [
            (manuscript_path, manuscript_path.name, raw_md, enc_meta)
        ]
        unresolved_includes: list[str] = []
    else:
        source_entries, unresolved_includes = _collect_tex_project_files(manuscript_path)

    hits: list[SlopHit] = []
    section_stats: list[SectionSlopStats] = []
    encoding_notices: list[dict[str, Any]] = []

    for fpath, flbl, raw_text, enc_meta in source_entries:
        if enc_meta.get("transcoded"):
            encoding_notices.append(enc_meta)
        if is_md:
            sec_source, masked_text = mask_markdown_non_prose(raw_text)
        else:
            sec_source, masked_text = mask_tex_non_prose(raw_text)

        lidx = LineIndex(raw_text)
        raw_lines = raw_text.splitlines()
        masked_lines = masked_text.splitlines()

        default_sec = "Opening" if fpath.resolve() == manuscript_path.resolve() else flbl
        boundaries: list[tuple[int, str]] = [(1, default_sec)]
        if is_md:
            for m in MD_SECTION_RE.finditer(sec_source):
                ln, _ = lidx.pos_to_lc(m.start())
                boundaries.append((ln, m.group(2).strip()))
        else:
            for m in TEX_SECTION_RE.finditer(sec_source):
                ln, _ = lidx.pos_to_lc(m.start())
                sec_title = m.group(1) or ("Abstract" if m.group(2) else "Section")
                boundaries.append((ln, sec_title.strip()))

        b_lines = [b[0] for b in boundaries]

        candidate_hits: list[tuple[int, int, str, int, int, str]] = []
        for regex, label, tier, weight, hint in SLOP_PATTERNS:
            for m in regex.finditer(masked_text):
                candidate_hits.append((m.start(), m.end(), label, tier, weight, hint))

        candidate_hits.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        hits_by_sec_idx: dict[int, list[SlopHit]] = {k: [] for k in range(len(boundaries))}
        last_end = -1

        for s_off, e_off, label, tier, weight, hint in candidate_hits:
            if s_off < last_end:
                continue
            last_end = e_off
            start_ln, start_col = lidx.pos_to_lc(s_off)
            end_ln, end_col_excl = lidx.pos_to_lc(max(s_off, e_off - 1))
            end_col = end_col_excl + 1
            first_line_end_col = (
                len(raw_lines[start_ln - 1]) + 1 if end_ln > start_ln and start_ln <= len(raw_lines) else end_col
            )

            sec_idx = bisect.bisect_right(b_lines, start_ln) - 1
            sec_name = boundaries[sec_idx][1]
            matched_clean = re.sub(r"\s+", " ", raw_text[s_off:e_off]).strip()
            orig_line = raw_lines[start_ln - 1].strip() if start_ln <= len(raw_lines) else matched_clean

            hit = SlopHit(
                line=start_ln,
                end_line=end_ln,
                column=start_col,
                end_column=first_line_end_col,
                section=sec_name,
                phrase=label,
                matched_text=matched_clean,
                tier=tier,
                weight=weight,
                hint=hint,
                snippet=orig_line[:160],
                last_line_end_column=end_col,
                file=flbl,
            )
            hits.append(hit)
            hits_by_sec_idx[sec_idx].append(hit)

        total_lines = max(len(masked_lines), 1)
        for idx, (start_ln, sec_name) in enumerate(boundaries):
            end_ln = boundaries[idx + 1][0] - 1 if idx + 1 < len(boundaries) else total_lines
            chunk = "\n".join(masked_lines[start_ln - 1 : end_ln])
            words = re.findall(r"\b[a-zA-Z]{2,}\b", re.sub(r"\\[a-zA-Z]+", " ", chunk))
            sec_hits = hits_by_sec_idx[idx]
            if not words and not sec_hits and idx == 0 and len(boundaries) > 1:
                continue
            cv, monotonous = _compute_sentence_cv(chunk)
            weight_sum = sum(h.weight for h in sec_hits) + (6 if monotonous else 0)
            denom = max(len(words), 45)
            raw_score = min(100, int(round((weight_sum / denom) * 420)))
            section_stats.append(
                SectionSlopStats(
                    name=sec_name,
                    start_line=start_ln,
                    word_count=len(words),
                    hit_count=len(sec_hits),
                    sentence_cv=cv,
                    monotonous_rhythm=monotonous,
                    section_score=raw_score,
                    file=flbl,
                )
            )

    total_words = sum(s.word_count for s in section_stats) or 1
    total_weight = sum(h.weight for h in hits) + sum(6 for s in section_stats if s.monotonous_rhythm)
    overall_score = min(100, int(round((total_weight / max(total_words, 80)) * 420)))
    hits_per_1k = round((len(hits) / max(total_words, 1)) * 1000.0, 1)

    if overall_score >= 65:
        grade = "HIGH_CLICHE_LOAD"
    elif overall_score >= 35:
        grade = "ELEVATED"
    elif overall_score >= 15:
        grade = "MODERATE"
    else:
        grade = "CONCISE"

    provenance = {
        "schema_version": SCHEMA_VERSION,
        "tool": "scan_ai_slop_lite",
        "parser_engine": "pylatexenc-ast",
        "manuscript_path": str(manuscript_path.resolve()),
        "manuscript_sha256": _sha256_file(manuscript_path),
        "manuscript_files": {str(fp.resolve()): _sha256_file(fp) for fp, _, _, _ in source_entries},
        "scanned_files": [flbl for _, flbl, _, _ in source_entries],
        "unresolved_includes": unresolved_includes,
        "encoding_notices": encoding_notices,
    }

    return SlopScanReport(
        manuscript_path=str(manuscript_path),
        format_mode="markdown" if is_md else "latex",
        total_words=total_words,
        total_hits=len(hits),
        overall_score=overall_score,
        hits_per_1k_words=hits_per_1k,
        grade=grade,
        metric_note=(
            "Cliché density (hits per 1,000 prose words; lower is cleaner) and concentration index (0-100); "
            "not an AI-authorship classifier or desk-reject probability."
        ),
        sections=section_stats,
        hits=hits,
        provenance=provenance,
    )


def format_markdown_report(report: SlopScanReport) -> str:
    lines = [
        "# PaperGuard Lite — Academic Style & Cliché Diagnostic (Read-Only)",
        "",
        f"- **Manuscript**: `{report.manuscript_path}` (`{report.format_mode}`, {report.total_words} prose words; code/math/comments masked)",
        f"- **Cliché Density**: **{report.hits_per_1k_words:.1f}/1k words** (concentration index {report.overall_score}/100, `{report.grade}`; lower is cleaner)",
        f"- **Flagged Phrases**: **{report.total_hits}** occurrences",
        f"- *Note*: {report.metric_note}",
        "",
        "## Section Breakdown",
        "| Section | Start Line | Prose Words | Cliché Hits | Sentence CV | Index |",
        "| :--- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sec in report.sections:
        rhythm_flag = f"{sec.sentence_cv:.2f} (Uniform)" if sec.monotonous_rhythm else f"{sec.sentence_cv:.2f}"
        lines.append(
            f"| {sec.name} | {sec.start_line} | {sec.word_count} | {sec.hit_count} | {rhythm_flag} | **{sec.section_score}/100** |"
        )

    if report.hits:
        lines.extend(
            [
                "",
                "## Line & Column Findings",
                "| Location | Section | Tier | Matched Phrase | Editorial Guidance |",
                "| :--- | :--- | :---: | :--- | :--- |",
            ]
        )
        for h in report.hits:
            loc_core = f"L{h.line}:C{h.column}" if h.line == h.end_line else f"L{h.line}-L{h.end_line}"
            loc = f"{h.file}:{loc_core}" if (h.file and len(report.provenance.get("scanned_files", [])) > 1) else loc_core
            lines.append(
                f"| `{loc}` | {h.section} | T{h.tier} | `{h.matched_text}` | {h.hint} |"
            )

    if report.total_hits > 0:
        lines.extend(
            [
                "",
                "---",
                "> **Formula-Safe Polishing**: Pasting raw LaTeX into chat windows risks mutating `$...$`, `\\eqref{}`, and `\\cite{}`.",
                f"> Unlock stateful `latex_guard.py` + 156-entry Style Bank + Word-Diff Studio in **PaperGuard Pro ($29)**: {PRO_UPGRADE_URL}",
            ]
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PaperGuard Lite: Scan LaTeX/Markdown manuscripts for stock LLM clichés."
    )
    parser.add_argument("manuscript", type=pathlib.Path, help="Path to .tex or .md file")
    parser.add_argument("--json", type=pathlib.Path, default=None, help="Export structured JSON report")
    args = parser.parse_args(argv)

    if not args.manuscript.exists():
        print(f"Error: manuscript file not found: {args.manuscript}", file=sys.stderr)
        return 2

    try:
        protected = collect_slop_protected_paths(args.manuscript)
        protected.extend(pg_oracles.discover_manuscript_bib_files(protected))
        if args.json is not None:
            for inp in protected:
                if _same_path(inp, args.json):
                    print(
                        f"Error: read-only safety block — --json '{args.json}' collides with input '{inp}'.",
                        file=sys.stderr,
                    )
                    return 2

        report = scan_manuscript_slop(args.manuscript)
    except (FileNotFoundError, ValueError, pg_tex_ast.SourceEncodingError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _atomic_write_text(args.json, json.dumps(report.to_dict(), indent=2))

    print(format_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
