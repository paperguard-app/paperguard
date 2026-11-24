#!/usr/bin/env python3
"""PaperGuard Lite — Multi-Registrar Read-Only BibTeX & Citation Auditor (v2.0).

Powered by vendored MIT `pylatexenc` (`LatexNodes2Text` Unicode accent decoding),
multi-registrar verification (Crossref, DataCite, OpenAlex CC0 retraction/errata guard,
DBLP, arXiv), and optional local `bibtex` / `.aux` / `pandoc` compiler oracles.
Zero external pip dependencies.
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

_VENDOR_DIR = pathlib.Path(__file__).resolve().parent / "_vendor"
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

import pg_oracles  # noqa: E402
import pg_tex_ast  # noqa: E402

SCHEMA_VERSION = "2.5"
USER_AGENT = (
    "PaperGuardLite/2.1 (https://github.com/paperguard/paperguard-lite; "
    "mailto:audit@paperguard.dev)"
)
PRO_UPGRADE_URL = "https://zhangx2.gumroad.com/l/paperguard-pro"

TEX_CITE_COMMANDS = {
    "cite",
    "citep",
    "citet",
    "citealt",
    "citealp",
    "citeauthor",
    "citeyear",
    "citeyearpar",
    "nocite",
    "parencite",
    "Parencite",
    "autocite",
    "Autocite",
    "textcite",
    "Textcite",
    "footcite",
    "fullcite",
    "supercite",
    "smartcite",
    "cites",
    "Cites",
    "parencites",
    "Parencites",
    "autocites",
    "Autocites",
    "textcites",
    "Textcites",
    "footcites",
    "Footcites",
    "fullcites",
    "Fullcites",
    "supercites",
    "Supercites",
    "smartcites",
    "Smartcites",
}
CITE_CMD_RE = re.compile(
    r"\\(cite|citep|citet|citealt|citealp|citeauthor|citeyear|citeyearpar|nocite|"
    r"parencite|Parencite|autocite|Autocite|textcite|Textcite|footcite|fullcite|supercite|smartcite)\*?"
    r"(?:\s*\[[^\]]*\])*\s*\{([^}]*)\}",
    re.DOTALL,
)
INPUT_CMD_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}|\\input[ \t]+([^\s%{}\\]+)")
MD_BRACKET_BLOCK_RE = re.compile(r"(?<!!)\[([^\[\]]+)\](?![(\[])")
MD_INNER_CITE_KEY_RE = re.compile(r"(?:^|[\s;(])-?@([a-zA-Z0-9_][\w:./-]*)")

DOI_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Advisory heuristic only — NEVER skips online API verification or flags IDENTITY_CONFLICT
SUSPECT_DOI_HINT_RE = re.compile(
    r"(?:^10\.5555/ghost|\.9999999\b|\.8888888\b|\.0000000\b)",
    re.IGNORECASE,
)
ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?$", re.I)

STANDARD_MONTH_MACROS: dict[str, str] = {
    "jan": "January",
    "feb": "February",
    "mar": "March",
    "apr": "April",
    "may": "May",
    "jun": "June",
    "jul": "July",
    "aug": "August",
    "sep": "September",
    "oct": "October",
    "nov": "November",
    "dec": "December",
}

MARKDOWN_SUFFIXES = {".md", ".markdown", ".rmd", ".qmd"}


class LineIndex:
    def __init__(self, text: str) -> None:
        self.starts = [0]
        for idx, ch in enumerate(text):
            if ch == "\n":
                self.starts.append(idx + 1)

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)


@dataclass
class FieldToken:
    name: str
    kind: str  # braced | quoted | number | bare_macro | concat
    raw_value: str
    value: str
    val_start: int
    val_end: int
    has_unresolved_macro: bool = False


DUPLICATE_IDENTITY_FIELDS = frozenset(
    {"title", "author", "editor", "doi", "eprint", "year", "journal", "booktitle", "url"}
)


@dataclass
class BibEntry:
    key: str
    entry_type: str
    fields: dict[str, str]
    field_tokens: dict[str, FieldToken]
    line: int
    start_pos: int
    end_pos: int
    unresolved_macro_fields: list[str] = field(default_factory=list)
    duplicate_fields: dict[str, list[tuple[int, str]]] = field(default_factory=dict)


@dataclass
class BibIssue:
    severity: str  # CRITICAL | WARNING | INFO
    code: str
    key: str
    file: str
    line: int
    message: str
    evidence: str = ""


@dataclass
class EntryVerification:
    key: str
    status: str  # VERIFIED | IDENTITY_CONFLICT | UNVERIFIED_MISS | LOOKUP_FAILED | UNQUERIED_OFFLINE
    source: str
    detail: str = ""


@dataclass
class BibAuditReport:
    bib_path: str
    manuscript_path: str | None
    mode: str
    total_entries: int
    cited_unique_keys: int
    verified_count: int
    conflict_count: int
    unverified_count: int
    lookup_failed_count: int
    unqueried_count: int
    critical_count: int
    warning_count: int
    info_count: int
    has_nocite_star: bool = False
    included_files: list[str] = field(default_factory=list)
    unresolved_includes: list[str] = field(default_factory=list)
    entry_statuses: list[EntryVerification] = field(default_factory=list)
    issues: list[BibIssue] = field(default_factory=list)
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


def _guard_readonly_output(inputs: list[pathlib.Path | None], out_path: pathlib.Path | None) -> None:
    if out_path is None:
        return
    for inp in inputs:
        if inp is not None and _same_path(inp, out_path):
            raise ValueError(
                f"Read-only safety block: output '{out_path}' collides with input '{inp}'."
            )


def _atomic_write_text(target: pathlib.Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".pg_lite_", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def strip_latex_markup(text: str) -> str:
    """Decode LaTeX accents, diacritics & macros to clean Unicode via vendored MIT pylatexenc."""
    if not text:
        return ""
    if "\\" in text or "{" in text:
        cleaned = pg_tex_ast.latex_to_plain_text(text)
    else:
        cleaned = text
    cleaned = unicodedata.normalize("NFKC", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_title_unicode(title: str) -> str:
    plain = strip_latex_markup(title)
    decomposed = unicodedata.normalize("NFKD", plain)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = stripped.casefold()
    chars = [ch if (ch.isalnum() or ch.isspace()) else " " for ch in folded]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def normalize_surname(raw_surname: str) -> str:
    """Shared Unicode accent-folding surname normalizer for local and remote authors."""
    plain = strip_latex_markup(raw_surname)
    decomposed = unicodedata.normalize("NFKD", plain)
    base = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in base.casefold() if ch.isalnum())


def extract_surnames(author_field: str) -> set[str]:
    plain = strip_latex_markup(author_field)
    if not plain:
        return set()
    surnames: set[str] = set()
    for person in re.split(r"\band\b", plain, flags=re.IGNORECASE):
        p = person.strip()
        if not p or p.lower() == "others":
            continue
        if "," in p:
            last = p.split(",", 1)[0].strip()
        else:
            parts = p.split()
            last = parts[-1] if parts else ""
        norm = normalize_surname(last)
        if norm:
            surnames.add(norm)
    return surnames


def _extract_crossref_surnames(authors: Any) -> set[str]:
    if not isinstance(authors, list):
        return set()
    out: set[str] = set()
    for a in authors:
        if not isinstance(a, dict):
            continue
        fam = a.get("family") or ""
        if fam:
            norm = normalize_surname(str(fam))
            if norm:
                out.add(norm)
        elif a.get("name") or a.get("literal"):
            out |= extract_surnames(str(a.get("name") or a.get("literal")))
    return out


def _extract_datacite_surnames(creators: Any) -> set[str]:
    if not isinstance(creators, list):
        return set()
    out: set[str] = set()
    for c in creators:
        if not isinstance(c, dict):
            continue
        fam = c.get("familyName") or ""
        if fam:
            norm = normalize_surname(str(fam))
            if norm:
                out.add(norm)
        elif c.get("name"):
            out |= extract_surnames(str(c["name"]))
    return out


ROMAN_MAP = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def _extract_series_markers(title: str) -> set[str]:
    norm = normalize_title_unicode(title)
    markers: set[str] = set()
    for m in re.finditer(r"\b(?:part|pt|vol|volume|book|section)\s+([ivx]{1,4}|\d{1,2})\b", norm):
        val = ROMAN_MAP.get(m.group(1), m.group(1))
        markers.add(f"part:{val}")
    for m in re.finditer(r"\b(?:v|ver|version)\s*(\d{1,2})\b", norm):
        markers.add(f"ver:{m.group(1)}")
    # Trailing standalone Roman numeral (e.g. "Methods II" vs "Methods I")
    tail_m = re.search(r"(?:^|\s)([ivx]{1,4})$", norm)
    if tail_m and tail_m.group(1) in ROMAN_MAP:
        markers.add(f"part:{ROMAN_MAP[tail_m.group(1)]}")
    return markers


def _series_markers_conflict(local_title: str, remote_title: str) -> bool:
    lm = _extract_series_markers(local_title)
    rm = _extract_series_markers(remote_title)
    return bool((lm or rm) and lm != rm)


def _is_archival_published_entry(entry_type: str, fields: dict[str, str]) -> bool:
    if entry_type in {"inproceedings", "incollection", "proceedings", "book"}:
        return True
    if fields.get("booktitle", "").strip():
        return True
    journal = fields.get("journal", "").strip().lower()
    return bool(journal and "arxiv" not in journal and "preprint" not in journal)


def _resolve_tex_include(
    root_dir: pathlib.Path, current_dir: pathlib.Path, raw_inc: str
) -> pathlib.Path | None:
    inc_clean = raw_inc.strip()
    if not inc_clean or "\\" in inc_clean:
        return None
    for base in (root_dir, current_dir):
        cand = base / inc_clean
        if cand.is_file():
            return cand
        if not cand.suffix:
            cand_tex = cand.with_suffix(".tex")
            if cand_tex.is_file():
                return cand_tex
    return None


TITLE_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "to", "with", "and", "or",
    "by", "from", "at", "via", "using", "towards", "toward", "its", "their",
    "as", "is", "are", "be", "we", "that", "this", "into", "over", "under",
}


def _words_roughly_same(w1: str, w2: str) -> bool:
    if w1 == w2:
        return True
    if w1 + "s" == w2 or w2 + "s" == w1 or w1 + "es" == w2 or w2 + "es" == w1:
        return True
    if len(w1) >= 5 and len(w2) >= 5:
        if w1[:5] == w2[:5] and abs(len(w1) - len(w2)) <= 2:
            return True
        if w1[0] == w2[0] and difflib.SequenceMatcher(None, w1, w2).ratio() >= 0.82:
            return True
    return False


def _title_core_words_conflict(local_title: str, remote_title: str) -> bool:
    ltoks = {w for w in normalize_title_unicode(local_title).split() if len(w) >= 3 and w not in TITLE_STOPWORDS}
    rtoks = {w for w in normalize_title_unicode(remote_title).split() if len(w) >= 3 and w not in TITLE_STOPWORDS}
    if not ltoks or not rtoks:
        return False
    lo = ltoks - rtoks
    ro = rtoks - ltoks
    if not lo or not ro:
        return False
    unmatched_lo = [w for w in lo if not any(_words_roughly_same(w, rw) for rw in ro)]
    unmatched_ro = [w for w in ro if not any(_words_roughly_same(w, lw) for lw in lo)]
    return bool(unmatched_lo and unmatched_ro)


def _norm_given_tokens(raw_given: str) -> list[str]:
    plain = strip_latex_markup(raw_given)
    decomposed = unicodedata.normalize("NFKD", plain)
    base = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return [t for t in re.findall(r"[a-z0-9]+", base) if t]


def _extract_author_records(author_field: str) -> list[tuple[str, list[str]]]:
    plain = strip_latex_markup(author_field)
    if not plain:
        return []
    recs: list[tuple[str, list[str]]] = []
    for person in re.split(r"\band\b", plain, flags=re.IGNORECASE):
        p = person.strip()
        if not p or p.lower() == "others":
            continue
        if "," in p:
            last_raw, given_raw = p.split(",", 1)
            sur = normalize_surname(last_raw)
            giv = _norm_given_tokens(given_raw)
        else:
            parts = p.split()
            sur = normalize_surname(parts[-1]) if parts else ""
            giv = _norm_given_tokens(" ".join(parts[:-1])) if len(parts) > 1 else []
        if sur:
            recs.append((sur, giv))
    return recs


def _extract_crossref_author_records(authors: Any) -> list[tuple[str, list[str]]]:
    if not isinstance(authors, list):
        return []
    recs: list[tuple[str, list[str]]] = []
    for a in authors:
        if not isinstance(a, dict):
            continue
        fam = str(a.get("family") or "").strip()
        giv = str(a.get("given") or "").strip()
        if fam:
            sur = normalize_surname(fam)
            if sur:
                recs.append((sur, _norm_given_tokens(giv)))
        elif a.get("name") or a.get("literal"):
            recs.extend(_extract_author_records(str(a.get("name") or a.get("literal"))))
    return recs


def _extract_datacite_author_records(creators: Any) -> list[tuple[str, list[str]]]:
    if not isinstance(creators, list):
        return []
    recs: list[tuple[str, list[str]]] = []
    for c in creators:
        if not isinstance(c, dict):
            continue
        fam = str(c.get("familyName") or "").strip()
        giv = str(c.get("givenName") or "").strip()
        if fam:
            sur = normalize_surname(fam)
            if sur:
                recs.append((sur, _norm_given_tokens(giv)))
        elif c.get("name"):
            recs.extend(_extract_author_records(str(c["name"])))
    return recs


def _given_compatible(lg: list[str], rg: list[str]) -> bool:
    if not lg or not rg:
        return True
    g1, g2 = lg[0], rg[0]
    if g1[0] != g2[0]:
        return False
    if len(g1) == 1 or len(g2) == 1:
        return True
    if g1 == g2 or g1.startswith(g2) or g2.startswith(g1):
        return True
    if len(g1) >= 5 and len(g2) >= 5 and difflib.SequenceMatcher(None, g1, g2).ratio() >= 0.80:
        return True
    return False


def _authors_given_conflict(
    local_recs: list[tuple[str, list[str]]], remote_recs: list[tuple[str, list[str]]]
) -> bool:
    loc_by_sur: dict[str, list[list[str]]] = {}
    for sur, giv in local_recs:
        loc_by_sur.setdefault(sur, []).append(giv)
    rem_by_sur: dict[str, list[list[str]]] = {}
    for sur, giv in remote_recs:
        rem_by_sur.setdefault(sur, []).append(giv)
    shared = set(loc_by_sur) & set(rem_by_sur)
    if not shared:
        return False
    for sur in shared:
        for lg in loc_by_sur[sur]:
            for rg in rem_by_sur[sur]:
                if _given_compatible(lg, rg):
                    return False
    return True


def _extract_all_arxiv_ids(fields: dict[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    eprint = fields.get("eprint", "").strip()
    clean_ep = re.sub(r"^arXiv:", "", eprint, flags=re.I).strip()
    if re.match(r"^(\d{4}\.\d{4,5}|[a-z-]+/\d{7})(v\d+)?$", clean_ep, re.I):
        found["eprint"] = re.sub(r"v\d+$", "", clean_ep, flags=re.I)
    doi = fields.get("doi", "").strip()
    dm = ARXIV_DOI_RE.match(doi)
    if dm:
        found["doi"] = re.sub(r"v\d+$", "", dm.group(1), flags=re.I)
    journal = fields.get("journal", "")
    jm = re.search(r"arXiv:(\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?", journal, re.I)
    if jm:
        found["journal"] = jm.group(1)
    url = fields.get("url", "")
    um = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?", url, re.I)
    if um:
        found["url"] = um.group(1)
    return found


def title_similarity(a: str, b: str) -> float:
    na = normalize_title_unicode(a)
    nb = normalize_title_unicode(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _parse_value_expr(
    full_text: str,
    start: int,
    end: int,
    string_macros: dict[str, str],
) -> tuple[str, str, str, int, bool]:
    """Parse BibTeX value expression supporting `#` concatenation.

    Returns (kind, raw_value, resolved_value, new_pos, has_unresolved_macro).
    """
    i = start
    atoms: list[tuple[str, str]] = []  # (atom_kind, inner_text)
    unresolved_macro = False

    while i < end:
        while i < end and full_text[i] in " \t\r\n":
            i += 1
        if i >= end:
            break

        if full_text[i] == "{":
            depth = 1
            i += 1
            s = i
            while i < end and depth > 0:
                if full_text[i] == "{":
                    depth += 1
                elif full_text[i] == "}":
                    depth -= 1
                i += 1
            atoms.append(("braced", full_text[s : i - 1]))
        elif full_text[i] == '"':
            i += 1
            s = i
            q_depth = 0
            while i < end:
                ch = full_text[i]
                if ch == "{":
                    q_depth += 1
                elif ch == "}" and q_depth > 0:
                    q_depth -= 1
                elif ch == '"' and q_depth == 0:
                    bs = 0
                    k = i - 1
                    while k >= s and full_text[k] == "\\":
                        bs += 1
                        k -= 1
                    if bs % 2 == 0:
                        break
                i += 1
            atoms.append(("quoted", full_text[s:i]))
            if i < end and full_text[i] == '"':
                i += 1
        else:
            s = i
            while i < end and full_text[i] not in ",# \t\r\n}":
                i += 1
            tok = full_text[s:i]
            if not tok:
                break
            if tok.isdigit():
                atoms.append(("number", tok))
            else:
                atoms.append(("bare_macro", tok))

        # Check if `#` follows (string concatenation)
        j = i
        while j < end and full_text[j] in " \t\r\n":
            j += 1
        if j < end and full_text[j] == "#":
            i = j + 1
            continue
        break

    val_end = i
    raw_value = full_text[start:val_end]

    resolved_parts: list[str] = []
    for akind, aval in atoms:
        if akind == "bare_macro":
            low = aval.lower()
            if low in string_macros:
                resolved_parts.append(string_macros[low])
            else:
                unresolved_macro = True
                resolved_parts.append(aval)
        else:
            resolved_parts.append(aval)

    resolved = re.sub(r"\s+", " ", "".join(resolved_parts)).strip()
    if len(atoms) > 1:
        kind = "concat"
    elif atoms:
        kind = atoms[0][0]
    else:
        kind = "bare_macro"

    return kind, raw_value, resolved, val_end, unresolved_macro


def _parse_bib_fields_spans(
    full_text: str,
    start: int,
    end: int,
    string_macros: dict[str, str],
    lidx: LineIndex | None = None,
) -> tuple[dict[str, str], dict[str, FieldToken], list[str], dict[str, list[tuple[int, str]]]]:
    """Parse BibTeX fields keeping FIRST occurrence per BibTeX 0.99e and recording duplicates (P0-01)."""
    fields: dict[str, str] = {}
    tokens: dict[str, FieldToken] = {}
    unresolved_fields: list[str] = []
    duplicate_fields: dict[str, list[tuple[int, str]]] = {}
    i = start
    while i < end:
        while i < end and full_text[i] in " \t\r\n,":
            i += 1
        if i >= end:
            break
        if full_text[i] == "%":
            nl = full_text.find("\n", i, end)
            i = end if nl == -1 else nl + 1
            continue
        stmt_start = i
        eq_pos = full_text.find("=", i, end)
        if eq_pos == -1:
            break
        fname = full_text[i:eq_pos].strip().lower()
        i = eq_pos + 1
        while i < end and full_text[i] in " \t\r\n":
            i += 1
        if i >= end:
            break

        val_start = i
        kind, raw_tok, resolved, val_end, has_unres = _parse_value_expr(
            full_text, val_start, end, string_macros
        )
        i = val_end
        if fname:
            ln_no = lidx.line_of(stmt_start) if lidx is not None else 1
            if fname in fields:
                if fname not in duplicate_fields:
                    first_ln = lidx.line_of(tokens[fname].val_start) if lidx is not None else 1
                    duplicate_fields[fname] = [(first_ln, fields[fname])]
                duplicate_fields[fname].append((ln_no, resolved))
            else:
                fields[fname] = resolved
                tokens[fname] = FieldToken(
                    name=fname,
                    kind=kind,
                    raw_value=raw_tok,
                    value=resolved,
                    val_start=val_start,
                    val_end=val_end,
                    has_unresolved_macro=has_unres,
                )
            if has_unres and fname not in unresolved_fields:
                unresolved_fields.append(fname)
    return fields, tokens, unresolved_fields, duplicate_fields


def parse_bibtex(
    content: str, bib_name: str = "references.bib"
) -> tuple[list[BibEntry], list[BibIssue], bool]:
    """Lossless BibTeX parser returning (entries, syntax_issues, has_fatal_syntax_error)."""
    entries: list[BibEntry] = []
    issues: list[BibIssue] = []
    seen_keys: dict[str, int] = {}
    string_macros: dict[str, str] = dict(STANDARD_MONTH_MACROS)
    lidx = LineIndex(content)
    fatal_syntax = False

    idx = 0
    n = len(content)
    while idx < n:
        at_pos = content.find("@", idx)
        if at_pos == -1:
            break
        # Classic BibTeX 0.99e treats top-level text outside @entries as comments — `%` does NOT suppress
        # a later `@entry{...}` on the same line (P1-02). Do not skip `@` after `%`.

        header_m = re.match(r"@([a-zA-Z]+)\s*([({])", content[at_pos:])
        if not header_m:
            idx = at_pos + 1
            continue

        entry_type = header_m.group(1).lower()
        open_delim = header_m.group(2)
        body_start = at_pos + header_m.end()
        line_no = lidx.line_of(at_pos)

        brace_depth = 1 if open_delim == "{" else 0
        paren_depth = 1 if open_delim == "(" else 0
        pos = body_start
        in_quotes = False
        swallowed_at: int | None = None

        while pos < n and (brace_depth > 0 or paren_depth > 0):
            ch = content[pos]
            prev_bs = 0
            k = pos - 1
            while k >= 0 and content[k] == "\\":
                prev_bs += 1
                k -= 1
            escaped = (prev_bs % 2) == 1

            if ch == '"' and not escaped and brace_depth <= (1 if open_delim == "{" else 0):
                in_quotes = not in_quotes
            elif not in_quotes and not escaped:
                at_entry_root = (
                    (brace_depth == 1 and paren_depth == 0)
                    if open_delim == "{"
                    else (paren_depth == 1 and brace_depth == 0)
                )
                if ch == "@" and at_entry_root and re.match(r"@[a-zA-Z]+\s*[({]", content[pos:]):
                    prev_nl = content.rfind("\n", body_start, pos)
                    if prev_nl != -1 and content[prev_nl:pos].strip() == "":
                        swallowed_at = pos
                        break
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    if brace_depth > 0:
                        brace_depth -= 1
                elif open_delim == "(" and brace_depth == 0:
                    if ch == "(":
                        paren_depth += 1
                    elif ch == ")":
                        paren_depth -= 1
            pos += 1

        if swallowed_at is not None or brace_depth != 0 or paren_depth != 0:
            fatal_syntax = True
            issues.append(
                BibIssue(
                    severity="CRITICAL",
                    code="MALFORMED_BIBTEX_ENTRY",
                    key=f"@line_{line_no}",
                    file=bib_name,
                    line=line_no,
                    message=(
                        f"Unclosed BibTeX entry starting at line {line_no} "
                        "(missing closing delimiter or swallows subsequent @entry)."
                    ),
                )
            )
            if swallowed_at is not None:
                idx = swallowed_at
                continue
            break

        inner_end = pos - 1
        idx = pos
        if entry_type == "string":
            s_fields, _, _, _ = _parse_bib_fields_spans(content, body_start, inner_end, string_macros, lidx)
            for mk, mv in s_fields.items():
                string_macros[mk.lower()] = mv
            continue
        if entry_type in {"comment", "preamble"}:
            continue

        comma_pos = content.find(",", body_start, inner_end)
        if comma_pos == -1:
            fatal_syntax = True
            issues.append(
                BibIssue(
                    severity="CRITICAL",
                    code="MALFORMED_BIBTEX_ENTRY",
                    key=f"@line_{line_no}",
                    file=bib_name,
                    line=line_no,
                    message=f"Malformed BibTeX entry at line {line_no} (missing citation key comma).",
                )
            )
            continue

        cite_key = content[body_start:comma_pos].strip()
        if not cite_key or any(c.isspace() for c in cite_key):
            fatal_syntax = True
            issues.append(
                BibIssue(
                    severity="CRITICAL",
                    code="MALFORMED_BIBTEX_KEY",
                    key=cite_key or f"@line_{line_no}",
                    file=bib_name,
                    line=line_no,
                    message=f"Invalid BibTeX citation key '{cite_key}' at line {line_no}.",
                )
            )
            continue

        if cite_key in seen_keys:
            issues.append(
                BibIssue(
                    severity="CRITICAL",
                    code="DUPLICATE_KEY",
                    key=cite_key,
                    file=bib_name,
                    line=line_no,
                    message=f"Duplicate BibTeX key '{cite_key}' (first defined at line {seen_keys[cite_key]}).",
                )
            )
        else:
            seen_keys[cite_key] = line_no

        f_vals, f_toks, unres_f, dup_f = _parse_bib_fields_spans(
            content, comma_pos + 1, inner_end, string_macros, lidx
        )
        for df, occs in sorted(dup_f.items()):
            is_id = df in DUPLICATE_IDENTITY_FIELDS
            ln_desc = ", ".join(f"L{ln}" for ln, _ in occs)
            issues.append(
                BibIssue(
                    severity="CRITICAL" if is_id else "WARNING",
                    code="DUPLICATE_IDENTITY_FIELD" if is_id else "DUPLICATE_FIELD",
                    key=cite_key,
                    file=bib_name,
                    line=occs[-1][0],
                    message=(
                        f"Duplicate {'identity ' if is_id else ''}field '{df}' in '{cite_key}' ({ln_desc}); "
                        f"BibTeX uses first ('{occs[0][1][:45]}') and ignores extra value(s)."
                    ),
                )
            )
        entries.append(
            BibEntry(
                key=cite_key,
                entry_type=entry_type,
                fields=f_vals,
                field_tokens=f_toks,
                line=line_no,
                start_pos=at_pos,
                end_pos=pos,
                unresolved_macro_fields=unres_f,
                duplicate_fields=dup_f,
            )
        )

    return entries, issues, fatal_syntax


def _mask_tex_literals_and_comments(tex: str) -> str:
    out = list(tex)
    i = 0
    n = len(tex)
    while i < n:
        ch = tex[i]
        bs = 0
        k = i - 1
        while k >= 0 and tex[k] == "\\":
            bs += 1
            k -= 1
        escaped = (bs % 2) == 1

        if ch == "%" and not escaped:
            j = i
            while j < n and tex[j] != "\n":
                out[j] = " "
                j += 1
            i = j
            continue

        if ch == "\\" and not escaped:
            vm = re.match(r"\\verb\*?([^\s\w])", tex[i:])
            if vm:
                delim = vm.group(1)
                end_d = tex.find(delim, i + vm.end())
                end_p = n if end_d == -1 else end_d + 1
                for p in range(i, end_p):
                    if out[p] != "\n":
                        out[p] = " "
                i = end_p
                continue
            lm = re.match(r"\\(?:lstinline|mintinline)(?:\s*\[[^\]]*\])*\s*([^\s\w{])", tex[i:])
            if lm:
                delim = lm.group(1)
                end_d = tex.find(delim, i + lm.end())
                end_p = n if end_d == -1 else end_d + 1
                for p in range(i, end_p):
                    if out[p] != "\n":
                        out[p] = " "
                i = end_p
                continue
            bm = re.match(r"\\begin\{\s*(verbatim|Verbatim|lstlisting|minted)\s*\}", tex[i:])
            if bm:
                end_m = re.search(
                    r"\\end\{\s*" + re.escape(bm.group(1)) + r"\s*\}", tex[i + bm.end() :]
                )
                end_p = n if not end_m else i + bm.end() + end_m.end()
                for p in range(i, end_p):
                    if out[p] != "\n":
                        out[p] = " "
                i = end_p
                continue
        i += 1
    return "".join(out)


def _mask_markdown_code(md: str) -> str:
    """Mask variable-length fenced code blocks, 4-space/tab indented code blocks, and inline code spans.

    Preserves exact character length and newline positions.
    """
    lines = md.splitlines(keepends=True)
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
        # Mask inline backtick code spans (supports ``code`` and `code`)
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
                    end_span = closer + tick_len
                    for p in range(tick_start, end_span):
                        chars[p] = " "
                    i = end_span
                    continue
            else:
                i += 1
        out_lines.append("".join(chars) + nl)

    masked = "".join(out_lines)
    return re.sub(
        r"<!--.*?-->",
        lambda m: "".join("\n" if ch == "\n" else " " for ch in m.group(0)),
        masked,
        flags=re.DOTALL,
    )


MD_INTEXT_CITE_RE = re.compile(
    r"(?:^|(?<=[\s(\[{;]))-?@([a-zA-Z0-9_][\w:./-]*)(?!\s*\{)",
    re.MULTILINE,
)


def _display_rel_label(path: pathlib.Path, root_dir: pathlib.Path) -> str:
    try:
        return pathlib.Path(os.path.relpath(path.resolve(), root_dir.resolve())).as_posix()
    except ValueError:
        return path.name


def _extract_md_yaml_frontmatter_and_mask(raw: str) -> tuple[str, list[tuple[str, int]], bool]:
    """Parse YAML frontmatter `nocite` (incl. '@*' and explicit '@key') and space-mask header preserving newlines (P1-05!)."""
    lines = raw.splitlines(keepends=True)
    if not lines:
        return raw, [], False
    first_stripped = lines[0].lstrip("\ufeff").rstrip("\r\n")
    if first_stripped != "---":
        return raw, [], False
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n").strip() in {"---", "..."}:
            end_idx = idx
            break
    if end_idx == -1:
        return raw, [], False

    nocite_keys: list[tuple[str, int]] = []
    has_star = False
    i = 1
    while i < end_idx:
        ln_body = lines[i].rstrip("\r\n")
        m_nc = re.match(r"^\s*nocite\s*:\s*(.*)$", ln_body, re.IGNORECASE)
        if m_nc:
            chunk_lines: list[tuple[int, str]] = [(i + 1, m_nc.group(1))]
            j = i + 1
            while j < end_idx:
                nxt = lines[j].rstrip("\r\n")
                if nxt.strip() == "" or nxt.startswith((" ", "\t", "-")):
                    chunk_lines.append((j + 1, nxt))
                    j += 1
                else:
                    break
            for ln_no, val_line in chunk_lines:
                if re.search(r"@\*", val_line):
                    has_star = True
                for km in re.finditer(r"(?<![A-Za-z0-9_.+%\\])@([a-zA-Z0-9_][\w:./-]*)", val_line):
                    k = km.group(1).strip().rstrip(".,;:?!'\"")
                    if k:
                        nocite_keys.append((k, ln_no))
            i = j
            continue
        i += 1

    masked_lines: list[str] = []
    for idx, raw_ln in enumerate(lines):
        if idx <= end_idx:
            nl = "\r\n" if raw_ln.endswith("\r\n") else ("\n" if raw_ln.endswith("\n") else "")
            body_len = len(raw_ln) - len(nl)
            masked_lines.append((" " * body_len) + nl)
        else:
            masked_lines.append(raw_ln)
    return "".join(masked_lines), nocite_keys, has_star


QUARTO_XREF_PREFIXES = (
    "fig-",
    "tbl-",
    "sec-",
    "eq-",
    "lst-",
    "thm-",
    "lem-",
    "cor-",
    "prp-",
    "cnj-",
    "def-",
    "exm-",
    "exr-",
    "alg-",
    "apx-",
)

_NOTICE_TITLE_RE = re.compile(
    r"^(?:correction|erratum|corrigendum|retraction|retracted|publisher\s+correction|"
    r"author\s+correction|expression\s+of\s+concern|addendum)\s*(?:(?:to|for|on)\b(?:\s*:)?|:)\s*",
    re.IGNORECASE,
)
_NOTICE_TYPE_RE = re.compile(
    r"(?:correction|erratum|corrigendum|retraction|withdrawal|expression[- ]of[- ]concern|addendum|partial[- ]retraction)",
    re.IGNORECASE,
)


def _detect_remote_update_notice(msg: dict[str, Any], remote_title: str) -> tuple[str, str] | None:
    updates = msg.get("update-to")
    if isinstance(updates, list):
        for item in updates:
            if not isinstance(item, dict):
                continue
            u_type = str(item.get("type") or item.get("label") or "").strip()
            target_doi = str(item.get("DOI") or "").strip()
            if u_type and _NOTICE_TYPE_RE.search(u_type):
                return u_type.lower(), target_doi

    work_type = str(msg.get("type") or "").strip().lower()
    if work_type in {"erratum", "correction", "retraction", "corrigendum"}:
        return work_type, ""

    relation = msg.get("relation")
    if isinstance(relation, dict):
        for rel_k in ("is-correction-of", "is-retraction-of", "updates"):
            rel_items = relation.get(rel_k)
            if isinstance(rel_items, list) and rel_items:
                t_doi = str(rel_items[0].get("id") or "") if isinstance(rel_items[0], dict) else ""
                return rel_k, t_doi

    # P1-01: Retracted primary articles keep their own DOI + updated-by retraction (and publisher-added "Retracted:" title)
    if _detect_crossref_retraction(msg) is not None:
        return None

    m_rem = _NOTICE_TITLE_RE.match(remote_title.strip())
    if m_rem:
        return m_rem.group(0).split(":")[0].strip().lower(), ""

    return None


def _detect_crossref_update_notice(
    msg: dict[str, Any], local_title: str, remote_title: str
) -> tuple[str, str, str] | None:
    """Symmetric 2x2 notice-vs-original check (P0-07!)."""
    local_clean = strip_latex_markup(local_title).strip()
    local_is_notice = bool(_NOTICE_TITLE_RE.match(local_clean))
    remote_notice = _detect_remote_update_notice(msg, remote_title)
    remote_is_notice = remote_notice is not None
    if not local_is_notice and remote_is_notice:
        assert remote_notice is not None
        return "REMOTE_IS_NOTICE", remote_notice[0], remote_notice[1]
    if local_is_notice and not remote_is_notice:
        m_loc = _NOTICE_TITLE_RE.match(local_clean)
        loc_kind = m_loc.group(0).split(":")[0].strip().lower() if m_loc else "correction"
        return "LOCAL_IS_NOTICE", loc_kind, ""
    return None


def _detect_crossref_retraction(msg: dict[str, Any]) -> tuple[str, str] | None:
    """Detect retracted status on Crossref primary article via `updated-by` or `relation` (P0-06!)."""
    updated_by = msg.get("updated-by")
    if isinstance(updated_by, list):
        for item in updated_by:
            if not isinstance(item, dict):
                continue
            u_type = str(item.get("type") or "").strip()
            u_label = str(item.get("label") or "").strip()
            combined = f"{u_type} {u_label}".lower()
            if "retract" in combined or "withdraw" in combined:
                return (u_label or u_type or "retraction"), str(item.get("DOI") or "").strip()
    relation = msg.get("relation")
    if isinstance(relation, dict):
        for rel_k in ("is-retracted-by", "has-retraction"):
            rel_items = relation.get(rel_k)
            if isinstance(rel_items, list) and rel_items:
                t_doi = str(rel_items[0].get("id") or "") if isinstance(rel_items[0], dict) else ""
                return "retraction", t_doi
    return None


def _extract_quarto_xref_labels(raw: str) -> set[str]:
    """Extract declared Quarto/Pandoc cross-reference target IDs (#fig-acc, #sec-results, #| label: fig-acc, P1-07!)."""
    labels: set[str] = set()
    for m in re.finditer(r"\{[^{}\n]*#([a-zA-Z0-9_][\w:.-]*)", raw):
        labels.add(m.group(1))
    for m in re.finditer(r"^\s*(?:#\||%%\||//\|)\s*label\s*:\s*['\"]?([a-zA-Z0-9_][\w:.-]*)['\"]?", raw, re.MULTILINE):
        labels.add(m.group(1))
    for m in re.finditer(r'\bid\s*=\s*["\']([a-zA-Z0-9_][\w:.-]*)["\']', raw):
        labels.add(m.group(1))
    return labels


def _is_quarto_xref_key(key: str, is_qmd: bool, declared_xrefs: set[str]) -> bool:
    if not is_qmd:
        return key in declared_xrefs and key.startswith(QUARTO_XREF_PREFIXES)
    if key in declared_xrefs:
        return True
    if key.startswith(QUARTO_XREF_PREFIXES):
        return True
    return False


def _extract_pandoc_keys_from_clean_md(
    clean_md: str,
    is_qmd: bool = False,
    declared_xrefs: set[str] | None = None,
) -> list[tuple[str, int]]:
    xrefs = declared_xrefs or set()
    lidx = LineIndex(clean_md)
    found: list[tuple[str, int]] = []
    outside_chars = list(clean_md)
    for bm in MD_BRACKET_BLOCK_RE.finditer(clean_md):
        inner = bm.group(1)
        if "\n\n" in inner:
            continue
        base_pos = bm.start(1)
        matched_any = False
        for km in MD_INNER_CITE_KEY_RE.finditer(inner):
            key = km.group(1).strip().rstrip(".,;:?!")
            if key:
                matched_any = True
                if not _is_quarto_xref_key(key, is_qmd, xrefs):
                    ln = lidx.line_of(base_pos + km.start(1))
                    found.append((key, ln))
        if matched_any:
            for p in range(bm.start(), bm.end()):
                if outside_chars[p] != "\n":
                    outside_chars[p] = " "

    outside = "".join(outside_chars)

    def _blank_keep_nl(m: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in m.group(0))

    outside = re.sub(r"\]\([^)\n]*\)", _blank_keep_nl, outside)
    outside = re.sub(r"<(?:https?://|mailto:)[^>\n]+>", _blank_keep_nl, outside, flags=re.IGNORECASE)
    outside = re.sub(r"(?:https?://|mailto:)[^\s)\]>]+", _blank_keep_nl, outside, flags=re.IGNORECASE)

    for im in MD_INTEXT_CITE_RE.finditer(outside):
        key = im.group(1).strip().rstrip(".,;:?!")
        if key and not _is_quarto_xref_key(key, is_qmd, xrefs):
            ln = lidx.line_of(im.start(1))
            found.append((key, ln))
    return found


def _read_delim_group(text: str, start: int, open_ch: str, close_ch: str) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != open_ch:
        return None
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        bs = 0
        k = i - 1
        while k >= 0 and text[k] == "\\":
            bs += 1
            k -= 1
        if (bs % 2) == 0:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start + 1 : i], i + 1
        i += 1
    return None


def _extract_tex_citations(
    clean: str, file_name: str, lidx: LineIndex
) -> tuple[dict[str, list[tuple[str, int]]], bool]:
    """Balanced citation scanner supporting natbib + BibLaTeX multi-cites (\\parencites[2]{a}[5]{b}, P1-02)."""
    cited_locs: dict[str, list[tuple[str, int]]] = {}
    has_star = False
    i = 0
    n = len(clean)
    while i < n:
        if clean[i] == "\\":
            m = re.match(r"\\([a-zA-Z]+)\*?", clean[i:])
            if m and m.group(1) in TEX_CITE_COMMANDS:
                cmd = m.group(1)
                ln = lidx.line_of(i)
                cur = i + m.end()
                is_multi = cmd.endswith("cites")
                while cur < n and clean[cur].isspace():
                    cur += 1
                if is_multi:
                    while cur < n and clean[cur] == "(":
                        pg = _read_delim_group(clean, cur, "(", ")")
                        if pg is None:
                            break
                        cur = pg[1]
                        while cur < n and clean[cur].isspace():
                            cur += 1
                parsed_any = False
                while cur < n:
                    while cur < n and clean[cur].isspace():
                        cur += 1
                    while cur < n and clean[cur] == "[":
                        og = _read_delim_group(clean, cur, "[", "]")
                        if og is None:
                            break
                        cur = og[1]
                        while cur < n and clean[cur].isspace():
                            cur += 1
                    if cur < n and clean[cur] == "{":
                        kg = _read_delim_group(clean, cur, "{", "}")
                        if kg is None:
                            break
                        parsed_any = True
                        for part in kg[0].split(","):
                            k = part.strip()
                            if k == "*" and cmd == "nocite":
                                has_star = True
                            elif k and k != "*":
                                cited_locs.setdefault(k, []).append((file_name, ln))
                        cur = kg[1]
                        if not is_multi:
                            break
                        continue
                    break
                if parsed_any:
                    i = cur
                    continue
        i += 1
    return cited_locs, has_star


def _norm_tex_include_key(raw_name: str) -> str:
    cleaned = os.path.normpath(raw_name.strip().replace("\\", "/")).replace("\\", "/")
    if cleaned.lower().endswith(".tex"):
        cleaned = cleaned[:-4]
    return cleaned


def extract_manuscript_citations_full(
    manuscript_path: pathlib.Path,
    _visited: set[pathlib.Path] | None = None,
    _root_dir: pathlib.Path | None = None,
    _includeonly: list[set[str] | None] | None = None,
    _inherited_encoding: str | None = None,
) -> tuple[dict[str, list[tuple[str, int]]], bool, list[str], list[str], list[pathlib.Path]]:
    """Returns (key -> [(filename, line)], has_nocite_star, included_names, unresolved_includes, scanned_paths)."""
    visited = _visited if _visited is not None else set()
    root_dir = _root_dir if _root_dir is not None else manuscript_path.parent
    inc_only = _includeonly if _includeonly is not None else [None]
    resolved = manuscript_path.resolve()
    if resolved in visited or not manuscript_path.exists():
        return {}, False, [], [], []
    visited.add(resolved)

    rel_lbl = _display_rel_label(manuscript_path, root_dir)
    raw, enc_meta = pg_tex_ast.decode_tex_bytes(
        manuscript_path.read_bytes(), resolved, inherited_encoding=_inherited_encoding
    )
    eff_enc = enc_meta.get("declared_encoding") or _inherited_encoding
    is_md = manuscript_path.suffix.lower() in MARKDOWN_SUFFIXES
    key_locs: dict[str, list[tuple[str, int]]] = {}
    has_nocite_star = False
    included_files: list[str] = []
    unresolved_includes: list[str] = []
    scanned_paths: list[pathlib.Path] = [manuscript_path]

    if is_md:
        is_qmd = manuscript_path.suffix.lower() == ".qmd"
        declared_xrefs = _extract_quarto_xref_labels(raw)
        yaml_masked, yaml_nocite, yaml_star = _extract_md_yaml_frontmatter_and_mask(raw)
        has_nocite_star = has_nocite_star or yaml_star
        for yk, yln in yaml_nocite:
            if not _is_quarto_xref_key(yk, is_qmd, declared_xrefs):
                key_locs.setdefault(yk, []).append((rel_lbl, yln))
        clean = _mask_markdown_code(yaml_masked)
        lidx = LineIndex(clean)
        for k, ln in _extract_pandoc_keys_from_clean_md(clean, is_qmd=is_qmd, declared_xrefs=declared_xrefs):
            key_locs.setdefault(k, []).append((rel_lbl, ln))
        md_tex_cites, md_star = _extract_tex_citations(clean, rel_lbl, lidx)
        for ck, clocs in md_tex_cites.items():
            key_locs.setdefault(ck, []).extend(clocs)
        has_nocite_star = has_nocite_star or md_star
    else:
        clean = _mask_tex_literals_and_comments(raw)
        # Respect \endinput, \end{document}, \iffalse & \includeonly identically to TeX (P0-01, P1-05!)
        m_end = re.search(r"\\endinput\b", clean)
        if m_end:
            nl = clean.find("\n", m_end.end())
            clean = clean[: (len(clean) if nl == -1 else nl + 1)]
        if _root_dir is None:
            clean, _ = pg_tex_ast.truncate_after_end_document(clean)
        clean = pg_tex_ast.strip_iffalse_blocks(clean)
        for io_m in re.finditer(r"\\includeonly\s*\{([^}]+)\}", clean):
            inc_only[0] = {_norm_tex_include_key(x) for x in io_m.group(1).split(",") if x.strip()}
        lidx = LineIndex(clean)
        tex_cites, tex_star = _extract_tex_citations(clean, rel_lbl, lidx)
        for ck, clocs in tex_cites.items():
            key_locs.setdefault(ck, []).extend(clocs)
        has_nocite_star = has_nocite_star or tex_star

        for cmd_name, inc_raw in pg_tex_ast.iter_tex_include_directives(clean):
            if not inc_raw:
                continue
            if cmd_name == "include" and inc_only[0] is not None and _norm_tex_include_key(inc_raw) not in inc_only[0]:
                continue
            cand = _resolve_tex_include(root_dir, manuscript_path.parent, inc_raw)
            if cand is not None:
                included_files.append(_display_rel_label(cand, root_dir))
                sub_locs, sub_star, sub_inc, sub_unres, sub_paths = (
                    extract_manuscript_citations_full(cand, visited, root_dir, inc_only, eff_enc)
                )
                if sub_star:
                    has_nocite_star = True
                included_files.extend(sub_inc)
                unresolved_includes.extend(sub_unres)
                scanned_paths.extend(sub_paths)
                for sk, slist in sub_locs.items():
                    key_locs.setdefault(sk, []).extend(slist)
            elif cmd_name != "InputIfFileExists":
                unresolved_includes.append(inc_raw)

    return key_locs, has_nocite_star, included_files, unresolved_includes, scanned_paths


def collect_manuscript_paths(manuscript_path: pathlib.Path | None) -> list[pathlib.Path]:
    if manuscript_path is None or not manuscript_path.exists():
        return []
    _, _, _, _, scanned_paths = extract_manuscript_citations_full(manuscript_path)
    return scanned_paths


def extract_manuscript_citations(text: str, is_markdown: bool = False) -> dict[str, list[int]]:
    """In-memory helper retained for unit tests."""
    clean = _mask_markdown_code(text) if is_markdown else _mask_tex_literals_and_comments(text)
    lidx = LineIndex(clean)
    out: dict[str, list[int]] = {}
    for cm in CITE_CMD_RE.finditer(clean):
        ln = lidx.line_of(cm.start())
        for part in cm.group(2).split(","):
            k = part.strip()
            if k and k != "*":
                out.setdefault(k, []).append(ln)
    if is_markdown:
        for k, ln in _extract_pandoc_keys_from_clean_md(clean):
            out.setdefault(k, []).append(ln)
    return out


def default_http_fetcher(url: str, timeout: float = 10.0) -> tuple[int, str]:
    accept = "application/atom+xml, application/xml" if "export.arxiv.org" in url else "application/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def _parse_arxiv_atom(xml_body: str) -> tuple[str, int | None, set[str]] | None:
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry_el = root.find("a:entry", ns)
    if entry_el is None:
        return None
    id_el = entry_el.find("a:id", ns)
    title_el = entry_el.find("a:title", ns)
    if title_el is None or id_el is None or not (title_el.text or "").strip():
        return None
    if (title_el.text or "").strip().lower() == "error":
        return None
    pub_el = entry_el.find("a:published", ns)
    pub_year: int | None = None
    if pub_el is not None and pub_el.text and len(pub_el.text) >= 4 and pub_el.text[:4].isdigit():
        pub_year = int(pub_el.text[:4])
    authors: set[str] = set()
    for a_el in entry_el.findall("a:author/a:name", ns):
        if a_el.text:
            authors |= extract_surnames(a_el.text)
    return (title_el.text.strip(), pub_year, authors)


def _search_crossref_bibliographic(
    title: str,
    bib_surnames: set[str],
    fetch: Callable[[str, float], tuple[int, str]],
    timeout: float,
) -> tuple[str, float, str, bool]:
    """Search Crossref by title+author.

    Returns (state: MATCH|MISS|ERROR, best_sim, best_title, author_overlap).
    """
    q = urllib.parse.quote(strip_latex_markup(title))
    status, body = fetch(f"https://api.crossref.org/works?query.bibliographic={q}&rows=5", timeout)
    if status != 200 or not body:
        return "ERROR", 0.0, "", False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "ERROR", 0.0, "", False
    if not isinstance(parsed, dict) or not isinstance(parsed.get("message"), dict):
        return "ERROR", 0.0, "", False
    items = parsed["message"].get("items")
    if not isinstance(items, list) or not items:
        return "MISS", 0.0, "", False

    best_sim = 0.0
    best_title = ""
    best_overlap = False
    for item in items:
        if not isinstance(item, dict):
            continue
        cand_titles = item.get("title") or []
        if not (isinstance(cand_titles, list) and cand_titles):
            continue
        ct = str(cand_titles[0])
        if _detect_crossref_update_notice(item, title, ct) is not None:
            continue
        sim = title_similarity(title, ct)
        cr_authors = _extract_crossref_surnames(item.get("author"))
        if bib_surnames:
            overlap = bool(cr_authors) and bool(bib_surnames & cr_authors)
        else:
            overlap = True
        if (overlap and not best_overlap and sim >= 0.78) or (
            overlap == best_overlap and sim > best_sim
        ) or (not best_overlap and sim > best_sim):
            best_sim = sim
            best_title = ct
            best_overlap = overlap

    if best_sim >= 0.78 and best_overlap:
        return "MATCH", best_sim, best_title, True
    return "MISS", best_sim, best_title, best_overlap


def _query_openalex_doi(
    doi: str, fetch: Callable[[str, float], tuple[int, str]], timeout: float
) -> tuple[str, str, int | None, set[str], list[tuple[str, list[str]]], bool, str]:
    """Query OpenAlex (CC0) DOI endpoint for fallback confirmation, retraction & erratum detection (v2.0)."""
    enc = urllib.parse.quote(f"https://doi.org/{doi}", safe=":/")
    status, body = fetch(f"https://api.openalex.org/works/{enc}", timeout)
    if status == 404:
        return "NOT_FOUND", "", None, set(), [], False, ""
    if status != 200 or not body:
        return "ERROR", "", None, set(), [], False, ""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "ERROR", "", None, set(), [], False, ""
    if not isinstance(parsed, dict) or not parsed.get("id"):
        return "ERROR", "", None, set(), [], False, ""

    oa_title = str(parsed.get("title") or parsed.get("display_name") or "").strip()
    pub_yr = parsed.get("publication_year")
    is_retracted = bool(parsed.get("is_retracted"))
    work_type = str(parsed.get("type") or "").strip().lower()
    authorships = parsed.get("authorships") or []
    names: list[str] = []
    if isinstance(authorships, list):
        for au in authorships:
            if isinstance(au, dict):
                a_obj = au.get("author") or {}
                dname = str(a_obj.get("display_name") or au.get("raw_author_name") or "").strip()
                if dname:
                    names.append(dname)
    joined_authors = " and ".join(names)
    oa_surnames = extract_surnames(joined_authors) if joined_authors else set()
    oa_recs = _extract_author_records(joined_authors) if joined_authors else []
    if not oa_title and not oa_surnames:
        return "ERROR", "", None, set(), [], is_retracted, work_type
    return (
        "FOUND",
        oa_title,
        (pub_yr if isinstance(pub_yr, int) else None),
        oa_surnames,
        oa_recs,
        is_retracted,
        work_type,
    )


def _search_dblp_bibliographic(
    title: str,
    bib_surnames: set[str],
    fetch: Callable[[str, float], tuple[int, str]],
    timeout: float,
) -> tuple[str, float, str, bool]:
    """Query DBLP public JSON API as CS conference bibliographic fallback when Crossref misses (v2.0)."""
    q = urllib.parse.quote(strip_latex_markup(title))
    status, body = fetch(f"https://dblp.org/search/publ/api?q={q}&format=json&h=5", timeout)
    if status != 200 or not body:
        return "ERROR", 0.0, "", False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "ERROR", 0.0, "", False
    hits_wrap = (((parsed.get("result") or {}).get("hits") or {}).get("hit")) if isinstance(parsed, dict) else None
    if not isinstance(hits_wrap, list) or not hits_wrap:
        return "MISS", 0.0, "", False

    best_sim = 0.0
    best_title = ""
    best_overlap = False
    for hit in hits_wrap:
        if not isinstance(hit, dict) or not isinstance(hit.get("info"), dict):
            continue
        info = hit["info"]
        ct = str(info.get("title") or "").strip().rstrip(".")
        if not ct:
            continue
        sim = title_similarity(title, ct)
        raw_aus = (info.get("authors") or {}).get("author")
        if isinstance(raw_aus, dict):
            raw_aus = [raw_aus]
        dblp_names: list[str] = []
        if isinstance(raw_aus, list):
            for a in raw_aus:
                nm = a.get("text") if isinstance(a, dict) else str(a)
                if nm:
                    dblp_names.append(re.sub(r"\s+\d{4}$", "", str(nm).strip()))
        dblp_surnames = extract_surnames(" and ".join(dblp_names)) if dblp_names else set()
        ov = (bool(dblp_surnames) and bool(bib_surnames & dblp_surnames)) if bib_surnames else True
        if (ov and not best_overlap and sim >= 0.78) or (ov == best_overlap and sim > best_sim) or (not best_overlap and sim > best_sim):
            best_sim = sim
            best_title = ct
            best_overlap = ov

    if best_sim >= 0.78 and best_overlap:
        return "MATCH", best_sim, best_title, True
    return "MISS", best_sim, best_title, best_overlap


def _check_datacite_doi(
    doi: str,
    entry: BibEntry,
    eff_fields: dict[str, str],
    bib_surnames: set[str],
    bib_name: str,
    fetch: Callable[[str, float], tuple[int, str]],
    timeout: float,
) -> tuple[EntryVerification, list[BibIssue]]:
    enc = urllib.parse.quote(doi, safe="")
    status, body = fetch(f"https://api.datacite.org/dois/{enc}", timeout)
    if status == 404:
        return (
            EntryVerification(entry.key, "UNVERIFIED_MISS", "DataCite", f"DOI {doi} 404 on DataCite"),
            [],
        )
    if status != 200 or not body:
        return (
            EntryVerification(entry.key, "LOOKUP_FAILED", "DataCite", f"HTTP {status}"),
            [],
        )
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return EntryVerification(entry.key, "LOOKUP_FAILED", "DataCite", "Invalid JSON"), []
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), dict):
        return EntryVerification(entry.key, "LOOKUP_FAILED", "DataCite", "Unexpected payload root"), []

    attrs = parsed["data"].get("attributes")
    if not isinstance(attrs, dict) or not attrs:
        return EntryVerification(entry.key, "LOOKUP_FAILED", "DataCite", "Empty attributes"), []

    titles = attrs.get("titles") or []
    dc_title = ""
    if isinstance(titles, list) and titles and isinstance(titles[0], dict):
        dc_title = str(titles[0].get("title", "")).strip()
    dc_authors = _extract_datacite_surnames(attrs.get("creators"))
    dc_recs = _extract_datacite_author_records(attrs.get("creators"))
    bib_author_recs = _extract_author_records(eff_fields.get("author", "") or eff_fields.get("editor", ""))

    bib_title = eff_fields.get("title", "")
    issues: list[BibIssue] = []

    if not dc_title and not dc_authors:
        return EntryVerification(entry.key, "LOOKUP_FAILED", "DataCite", "Empty DataCite metadata"), []

    if dc_title and bib_title:
        sim = title_similarity(bib_title, dc_title)
        if sim < 0.60 or _series_markers_conflict(bib_title, dc_title):
            issues.append(
                BibIssue(
                    severity="CRITICAL",
                    code="DOI_TITLE_MISMATCH",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=f"DataCite DOI '{doi}' resolves to '{dc_title[:90]}', conflicting with BibTeX title.",
                    evidence=f"Title similarity={sim:.2f}",
                )
            )
            return EntryVerification(entry.key, "IDENTITY_CONFLICT", "DataCite", "Title mismatch"), issues

    if bib_surnames and dc_authors and not (bib_surnames & dc_authors):
        issues.append(
            BibIssue(
                severity="CRITICAL",
                code="DOI_AUTHOR_MISMATCH",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=f"DataCite DOI '{doi}' author list conflicts with BibTeX authors.",
                evidence=f"Local={sorted(bib_surnames)[:3]} vs DataCite={sorted(dc_authors)[:3]}",
            )
        )
        return EntryVerification(entry.key, "IDENTITY_CONFLICT", "DataCite", "Author mismatch"), issues

    if not (bib_title and dc_title and bib_surnames and dc_authors):
        issues.append(
            BibIssue(
                severity="WARNING",
                code="INSUFFICIENT_IDENTITY_EVIDENCE",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=(
                    f"DataCite DOI '{doi}' resolved, but local or remote entry lacks title/author metadata "
                    "required for full identity confirmation."
                ),
            )
        )
        return EntryVerification(entry.key, "UNVERIFIED_MISS", "DataCite", "DOI resolved; missing local/remote title or author"), issues

    if _title_core_words_conflict(bib_title, dc_title) or _authors_given_conflict(bib_author_recs, dc_recs):
        issues.append(
            BibIssue(
                severity="WARNING",
                code="CANDIDATE_NEEDS_REVIEW",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=(
                    f"DataCite DOI '{doi}' candidate ('{dc_title[:75]}') differs in core title words or author given names; "
                    "confirm manually."
                ),
            )
        )
        return EntryVerification(entry.key, "UNVERIFIED_MISS", "DataCite", "Candidate requires confirmation"), issues

    pub_yr = attrs.get("publicationYear")
    bib_yr = eff_fields.get("year", "").strip()
    is_preprint_doi_on_archival = bool(
        ARXIV_DOI_RE.match(doi) and _is_archival_published_entry(entry.entry_type, eff_fields)
    )
    if (
        not is_preprint_doi_on_archival
        and isinstance(pub_yr, int)
        and bib_yr.isdigit()
        and abs(pub_yr - int(bib_yr)) >= 1
    ):
        issues.append(
            BibIssue(
                severity="WARNING",
                code="YEAR_MISMATCH",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=f"Year mismatch: BibTeX has {bib_yr}, DataCite record for '{doi}' states {pub_yr}.",
            )
        )
    return EntryVerification(entry.key, "VERIFIED", "DataCite", f"Confirmed {doi}"), issues


def _verify_entry_online(
    entry: BibEntry,
    eff_fields: dict[str, str],
    bib_name: str,
    fetch: Callable[[str, float], tuple[int, str]],
    timeout: float,
) -> tuple[EntryVerification, list[BibIssue]]:
    found: list[BibIssue] = []
    title = eff_fields.get("title", "")
    doi = eff_fields.get("doi", "").strip()
    year = eff_fields.get("year", "").strip()
    eprint = eff_fields.get("eprint", "").strip()
    bib_surnames = extract_surnames(eff_fields.get("author", "") or eff_fields.get("editor", ""))

    if any(f in {"title", "author", "editor"} for f in entry.unresolved_macro_fields):
        found.append(
            BibIssue(
                severity="WARNING",
                code="UNRESOLVED_MACRO_IDENTITY",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=(
                    f"Entry '{entry.key}' uses undefined BibTeX macro in identity field(s) "
                    f"{', '.join(entry.unresolved_macro_fields)}; marked unverified."
                ),
            )
        )
        return EntryVerification(entry.key, "UNVERIFIED_MISS", "BibTeX-Macro", "Unresolved identity macro"), found

    # Route 1: DataCite / arXiv DOI (10.48550/arXiv.*)
    if doi and ARXIV_DOI_RE.match(doi):
        ver, dc_issues = _check_datacite_doi(doi, entry, eff_fields, bib_surnames, bib_name, fetch, timeout)
        return ver, dc_issues

    # Route 2: Standard DOI -> Crossref first, DataCite fallback on 404, bibliographic title+author fallback on dual-404
    if doi:
        enc = urllib.parse.quote(doi, safe="")
        status, body = fetch(f"https://api.crossref.org/works/{enc}", timeout)
        if status == 404:
            dc_ver, dc_issues = _check_datacite_doi(
                doi, entry, eff_fields, bib_surnames, bib_name, fetch, timeout
            )
            if dc_ver.status in {"VERIFIED", "IDENTITY_CONFLICT"}:
                return dc_ver, dc_issues
            if dc_ver.status == "LOOKUP_FAILED":
                found.append(
                    BibIssue(
                        severity="WARNING",
                        code="DOI_UNVERIFIED_SINGLE_REGISTRAR",
                        key=entry.key,
                        file=bib_name,
                        line=entry.line,
                        message=(
                            f"DOI '{doi}' was not found on Crossref and secondary DataCite check failed; "
                            "manual verification required."
                        ),
                    )
                )
                return EntryVerification(entry.key, "LOOKUP_FAILED", "Crossref+DataCite"), found

            # OpenAlex (CC0) DOI fallback on dual Crossref+DataCite 404 (incl. retraction & erratum guard, v2.0)
            oa_state, oa_title, oa_yr, oa_authors, oa_recs, oa_retracted, oa_type = _query_openalex_doi(doi, fetch, timeout)
            if oa_state == "FOUND":
                if oa_retracted:
                    found.append(
                        BibIssue(
                            severity="CRITICAL",
                            code="RETRACTED_WORK",
                            key=entry.key,
                            file=bib_name,
                            line=entry.line,
                            message=f"OpenAlex marks DOI '{doi}' ('{oa_title[:80]}') as RETRACTED.",
                            evidence=f"DOI: {doi}",
                        )
                    )
                    return EntryVerification(entry.key, "UNVERIFIED_MISS", "OpenAlex", "Retracted work"), found
                if oa_type in {"erratum", "retraction", "paratext"}:
                    found.append(
                        BibIssue(
                            severity="WARNING",
                            code="UPDATE_NOTICE_DOI_MISMATCH",
                            key=entry.key,
                            file=bib_name,
                            line=entry.line,
                            message=f"OpenAlex classifies DOI '{doi}' as '{oa_type}', not the primary research article.",
                        )
                    )
                    return EntryVerification(entry.key, "UNVERIFIED_MISS", "OpenAlex", f"DOI is {oa_type}"), found
                sim_oa = title_similarity(title, oa_title) if (title and oa_title) else 0.0
                if (title and oa_title and (sim_oa < 0.60 or _series_markers_conflict(title, oa_title))) or (
                    bib_surnames and oa_authors and not (bib_surnames & oa_authors)
                ):
                    found.append(
                        BibIssue(
                            severity="CRITICAL",
                            code="DOI_TITLE_MISMATCH",
                            key=entry.key,
                            file=bib_name,
                            line=entry.line,
                            message=f"OpenAlex DOI '{doi}' resolves to '{oa_title[:85]}', conflicting with BibTeX entry.",
                            evidence=f"Similarity={sim_oa:.2f}",
                        )
                    )
                    return EntryVerification(entry.key, "IDENTITY_CONFLICT", "OpenAlex", "DOI mismatch"), found
                bib_author_recs_oa = _extract_author_records(eff_fields.get("author", "") or eff_fields.get("editor", ""))
                if title and oa_title and bib_surnames and oa_authors and not (
                    _title_core_words_conflict(title, oa_title) or _authors_given_conflict(bib_author_recs_oa, oa_recs)
                ):
                    if isinstance(oa_yr, int) and year.isdigit() and abs(oa_yr - int(year)) >= 1:
                        found.append(
                            BibIssue(
                                severity="WARNING",
                                code="YEAR_MISMATCH",
                                key=entry.key,
                                file=bib_name,
                                line=entry.line,
                                message=f"Publication year mismatch: BibTeX has {year}, OpenAlex record states {oa_yr}.",
                            )
                        )
                    return EntryVerification(entry.key, "VERIFIED", "OpenAlex", f"Confirmed {doi}"), found

            # Dual 404 -> fallback to bibliographic title + author search (Crossref then DBLP, v2.0)
            if title:
                fb_state, fb_sim, fb_title, _ = _search_crossref_bibliographic(
                    title, bib_surnames, fetch, timeout
                )
                fb_source = "Crossref-TitleFallback"
                if fb_state == "MISS":
                    db_state, db_sim, db_title, _ = _search_dblp_bibliographic(title, bib_surnames, fetch, timeout)
                    if db_state == "MATCH":
                        fb_state, fb_sim, fb_title, fb_source = db_state, db_sim, db_title, "DBLP-TitleFallback"
                if fb_state == "MATCH":
                    found.append(
                        BibIssue(
                            severity="WARNING",
                            code="DOI_DEAD_PAPER_EXISTS",
                            key=entry.key,
                            file=bib_name,
                            line=entry.line,
                            message=(
                                f"DOI '{doi}' returned 404 on Crossref & DataCite, but {fb_source} "
                                f"confirms the paper exists ('{fb_title[:80]}'); update the DOI."
                            ),
                            evidence=f"Title similarity={fb_sim:.2f}",
                        )
                    )
                    return (
                        EntryVerification(
                            entry.key,
                            "UNVERIFIED_MISS",
                            fb_source,
                            f"DOI 404, paper confirmed (sim={fb_sim:.2f})",
                        ),
                        found,
                    )
                if fb_state == "ERROR":
                    return (
                        EntryVerification(
                            entry.key, "LOOKUP_FAILED", "Crossref-TitleFallback", "Fallback query failed"
                        ),
                        found,
                    )

            found.append(
                BibIssue(
                    severity="WARNING",
                    code="UNREGISTERED_DOI_404",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=(
                        f"DOI '{doi}' returned 404 Not Found on Crossref & DataCite, "
                        "and bibliographic fallback found no matching record — verify manually."
                    ),
                    evidence=strip_latex_markup(title)[:100],
                )
            )
            return EntryVerification(entry.key, "UNVERIFIED_MISS", "Crossref+DataCite", "404 in both"), found

        if status != 200 or not body:
            return EntryVerification(entry.key, "LOOKUP_FAILED", "Crossref", f"HTTP {status}"), found

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return EntryVerification(entry.key, "LOOKUP_FAILED", "Crossref", "Invalid JSON"), found
        if not isinstance(parsed, dict) or not isinstance(parsed.get("message"), dict):
            return EntryVerification(entry.key, "LOOKUP_FAILED", "Crossref", "Unexpected JSON shape"), found

        payload = parsed["message"]
        if not payload:
            return EntryVerification(entry.key, "LOOKUP_FAILED", "Crossref", "Empty Crossref message"), found

        cr_titles = payload.get("title") or []
        cr_title = str(cr_titles[0]).strip() if (isinstance(cr_titles, list) and cr_titles) else ""
        cr_authors = _extract_crossref_surnames(payload.get("author"))
        cr_recs = _extract_crossref_author_records(payload.get("author"))
        bib_author_recs = _extract_author_records(eff_fields.get("author", "") or eff_fields.get("editor", ""))

        if not cr_title and not cr_authors:
            return EntryVerification(entry.key, "LOOKUP_FAILED", "Crossref", "Empty metadata fields"), found

        author_conflict = bool(bib_surnames and cr_authors and not (bib_surnames & cr_authors))
        local_clean = strip_latex_markup(title).strip()
        local_is_notice = bool(_NOTICE_TITLE_RE.match(local_clean))
        local_base = _NOTICE_TITLE_RE.sub("", local_clean).strip() or local_clean
        cr_title_stripped = _NOTICE_TITLE_RE.sub("", cr_title).strip() or cr_title
        remote_is_retracted_primary = (
            _detect_crossref_retraction(payload) is not None
            and _detect_remote_update_notice(payload, cr_title) is None
        )
        remote_is_notice = _detect_remote_update_notice(payload, cr_title) is not None
        strip_both = (local_is_notice and remote_is_notice) or remote_is_retracted_primary
        cmp_local_title = local_base if strip_both else title
        cmp_remote_title = cr_title_stripped if strip_both else cr_title
        sim_raw = title_similarity(cmp_local_title, cmp_remote_title) if (cmp_local_title and cmp_remote_title) else 0.0
        sim_notice = (
            max(sim_raw, title_similarity(local_base, cr_title_stripped)) if (local_base and cr_title_stripped) else 0.0
        )

        update_notice = None if remote_is_retracted_primary else _detect_crossref_update_notice(payload, title, cr_title)
        if update_notice is not None and not author_conflict and (not title or not cr_title or sim_notice >= 0.60):
            direction, notice_type, target_doi = update_notice
            if direction == "LOCAL_IS_NOTICE":
                msg_txt = (
                    f"BibTeX entry cites a '{notice_type}' notice, but Crossref DOI '{doi}' points to the primary "
                    "research article; verify and replace with the correction/erratum DOI."
                )
                ev_txt = f"Primary DOI: {doi}"
                det_txt = f"Notice entry paired with primary article DOI ({doi})"
            else:
                target_hint = f" (points to original DOI '{target_doi}')" if target_doi else ""
                msg_txt = (
                    f"Crossref DOI '{doi}' is a '{notice_type}' notice{target_hint}, not the primary research "
                    "article; verify and replace with the original article DOI."
                )
                ev_txt = f"Notice DOI: {doi}" + (f" -> Target: {target_doi}" if target_doi else "")
                det_txt = f"DOI is a {notice_type} notice{target_hint}"
            found.append(
                BibIssue(
                    severity="WARNING",
                    code="UPDATE_NOTICE_DOI_MISMATCH",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=msg_txt,
                    evidence=ev_txt,
                )
            )
            return (
                EntryVerification(entry.key, "UNVERIFIED_MISS", "Crossref-UpdateNotice", det_txt),
                found,
            )

        if cmp_remote_title and cmp_local_title:
            sim = sim_raw
            if sim < 0.60 or _series_markers_conflict(cmp_local_title, cmp_remote_title):
                found.append(
                    BibIssue(
                        severity="CRITICAL",
                        code="DOI_TITLE_MISMATCH",
                        key=entry.key,
                        file=bib_name,
                        line=entry.line,
                        message=f"DOI '{doi}' resolves to '{cr_title[:90]}', conflicting with BibTeX title.",
                        evidence=f"Similarity={sim:.2f}",
                    )
                )
                return EntryVerification(entry.key, "IDENTITY_CONFLICT", "Crossref", "DOI title mismatch"), found

        if author_conflict:
            found.append(
                BibIssue(
                    severity="CRITICAL",
                    code="DOI_AUTHOR_MISMATCH",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=f"DOI '{doi}' resolves to authors conflicting with BibTeX entry.",
                    evidence=f"Local={sorted(bib_surnames)[:3]} vs Crossref={sorted(cr_authors)[:3]}",
                )
            )
            return EntryVerification(entry.key, "IDENTITY_CONFLICT", "Crossref", "DOI author mismatch"), found

        if not (title and cr_title and bib_surnames and cr_authors):
            found.append(
                BibIssue(
                    severity="WARNING",
                    code="INSUFFICIENT_IDENTITY_EVIDENCE",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=(
                        f"Crossref DOI '{doi}' resolved, but local or remote entry lacks title/author metadata "
                        "required to confirm identity."
                    ),
                )
            )
            return (
                EntryVerification(entry.key, "UNVERIFIED_MISS", "Crossref", "DOI resolved; missing local/remote title or author"),
                found,
            )

        if _title_core_words_conflict(cmp_local_title, cmp_remote_title) or _authors_given_conflict(bib_author_recs, cr_recs):
            found.append(
                BibIssue(
                    severity="WARNING",
                    code="CANDIDATE_NEEDS_REVIEW",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=(
                        f"Crossref DOI '{doi}' candidate ('{cr_title[:75]}') differs in core title words or author given names; "
                        "verify manually."
                    ),
                )
            )
            return EntryVerification(entry.key, "UNVERIFIED_MISS", "Crossref", "Candidate requires confirmation"), found

        # P0-06: Check editorial retraction status even when Crossref DOI succeeds (Crossref updated-by + OpenAlex overlay)!
        retract_hit = _detect_crossref_retraction(payload)
        retract_source = "Crossref"
        if retract_hit is None:
            oa_st, _, _, _, _, oa_retracted, _ = _query_openalex_doi(doi, fetch, timeout)
            if oa_st == "FOUND" and oa_retracted:
                retract_hit = ("retraction", "")
                retract_source = "OpenAlex"
        if retract_hit is not None:
            r_label, r_doi = retract_hit
            r_hint = f" (notice DOI '{r_doi}')" if r_doi else ""
            found.append(
                BibIssue(
                    severity="CRITICAL",
                    code="RETRACTED_WORK",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=f"DOI '{doi}' identity matched, but {retract_source} marks this work as RETRACTED ({r_label}{r_hint}).",
                    evidence=f"DOI: {doi}",
                )
            )
            return EntryVerification(entry.key, "RETRACTED", retract_source, f"Identity confirmed; RETRACTED{r_hint}"), found

        pub_parts = (
            (payload.get("published-print") or {}).get("date-parts")
            or (payload.get("published-online") or {}).get("date-parts")
            or (payload.get("issued") or {}).get("date-parts")
        )
        if isinstance(pub_parts, list) and pub_parts and pub_parts[0] and year.isdigit():
            cr_year = int(pub_parts[0][0])
            if abs(cr_year - int(year)) >= 1:
                found.append(
                    BibIssue(
                        severity="WARNING",
                        code="YEAR_MISMATCH",
                        key=entry.key,
                        file=bib_name,
                        line=entry.line,
                        message=f"Publication year mismatch: BibTeX has {year}, Crossref DOI record states {cr_year}.",
                        evidence=f"DOI: {doi}",
                    )
                )
        return EntryVerification(entry.key, "VERIFIED", "Crossref", f"Confirmed {doi}"), found

    # Route 3: arXiv eprint without DOI -> query arXiv Atom API
    if eprint:
        clean_id = re.sub(r"^arXiv:", "", eprint, flags=re.I).strip()
        enc_id = urllib.parse.quote(clean_id, safe="")
        status, xml_body = fetch(f"https://export.arxiv.org/api/query?id_list={enc_id}", timeout)
        if status == 200 and xml_body:
            ax = _parse_arxiv_atom(xml_body)
            if ax is None:
                found.append(
                    BibIssue(
                        severity="WARNING",
                        code="ARXIV_ID_NOT_FOUND",
                        key=entry.key,
                        file=bib_name,
                        line=entry.line,
                        message=f"arXiv identifier '{eprint}' was not found on export.arxiv.org.",
                    )
                )
                return EntryVerification(entry.key, "UNVERIFIED_MISS", "arXiv"), found
            ax_title, _, ax_authors = ax
            sim = title_similarity(title, ax_title) if (title and ax_title) else 0.0
            if (title and ax_title and (sim < 0.60 or _series_markers_conflict(title, ax_title))) or (
                bib_surnames and ax_authors and not (bib_surnames & ax_authors)
            ):
                found.append(
                    BibIssue(
                        severity="CRITICAL",
                        code="ARXIV_IDENTITY_MISMATCH",
                        key=entry.key,
                        file=bib_name,
                        line=entry.line,
                        message=f"arXiv:{clean_id} belongs to '{ax_title[:85]}', conflicting with BibTeX entry.",
                    )
                )
                return EntryVerification(entry.key, "IDENTITY_CONFLICT", "arXiv"), found
            if not (title and ax_title and bib_surnames and ax_authors):
                return (
                    EntryVerification(entry.key, "UNVERIFIED_MISS", "arXiv", "Missing local/remote title or authors"),
                    found,
                )
            return EntryVerification(entry.key, "VERIFIED", "arXiv", f"Confirmed arXiv:{clean_id}"), found
        return EntryVerification(entry.key, "LOOKUP_FAILED", "arXiv", f"HTTP {status}"), found

    # Route 4: Bibliographic title + author search on Crossref
    if title:
        fb_state, best_sim, best_title, _ = _search_crossref_bibliographic(title, bib_surnames, fetch, timeout)
        if fb_state == "ERROR":
            return EntryVerification(entry.key, "LOOKUP_FAILED", "Crossref-Search", "Search failed"), found
        if fb_state == "MATCH":
            return EntryVerification(entry.key, "VERIFIED", "Crossref-Search", f"sim={best_sim:.2f}"), found
        found.append(
            BibIssue(
                severity="WARNING",
                code="UNVERIFIED_BIBLIOGRAPHIC_SEARCH",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message="No matching title+author record confirmed on Crossref — verify manually.",
                evidence=f"Closest candidate ({best_sim:.2f}): {best_title[:85]}",
            )
        )
        return EntryVerification(entry.key, "UNVERIFIED_MISS", "Crossref-Search", f"best_sim={best_sim:.2f}"), found

    return EntryVerification(entry.key, "UNVERIFIED_MISS", "none", "No identifier or title"), found


def _collect_crossref_deps(entries: list[BibEntry], cited_keys: set[str]) -> set[str]:
    """Transitively collect BibTeX `crossref` parent keys required by cited entries (P1-06!)."""
    by_key_ci: dict[str, BibEntry] = {e.key.lower(): e for e in entries}
    required: set[str] = set()
    queue = [k.lower() for k in cited_keys if k.lower() in by_key_ci]
    visited: set[str] = set(queue)
    while queue:
        cur_ci = queue.pop()
        ent = by_key_ci.get(cur_ci)
        if ent is None:
            continue
        xref = ent.fields.get("crossref", "").strip().lower()
        if xref and xref in by_key_ci:
            parent = by_key_ci[xref]
            required.add(parent.key)
            if xref not in visited:
                visited.add(xref)
                queue.append(xref)
    return required


def _resolve_effective_fields(
    entry: BibEntry, by_key_ci: dict[str, BibEntry]
) -> tuple[dict[str, str], bool]:
    """Resolve BibTeX `crossref` inheritance without mutating the entry's raw fields."""
    eff = dict(entry.fields)
    xref = entry.fields.get("crossref", "").strip().lower()
    missing_xref = False
    if xref:
        parent = by_key_ci.get(xref)
        if parent is not None:
            for k, v in parent.fields.items():
                eff.setdefault(k, v)
        else:
            missing_xref = True
    return eff, missing_xref


def _validate_entry_fields(
    entry: BibEntry, eff_fields: dict[str, str], missing_xref: bool, bib_name: str
) -> list[BibIssue]:
    out: list[BibIssue] = []
    et = entry.entry_type
    if missing_xref:
        out.append(
            BibIssue(
                severity="WARNING",
                code="UNRESOLVED_CROSSREF",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=f"Entry '{entry.key}' references crossref='{entry.fields.get('crossref')}' not found in {bib_name}.",
            )
        )
    if not eff_fields.get("title"):
        out.append(
            BibIssue(
                severity="WARNING",
                code="MISSING_REQUIRED_FIELD",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=f"Entry '{entry.key}' (@{et}) is missing 'title'.",
            )
        )
    if et in {"book", "proceedings", "periodical"}:
        if not (eff_fields.get("author") or eff_fields.get("editor")):
            out.append(
                BibIssue(
                    severity="WARNING",
                    code="MISSING_REQUIRED_FIELD",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=f"Entry '{entry.key}' (@{et}) requires 'author' or 'editor'.",
                )
            )
    elif et not in {"misc", "online", "manual", "standard"}:
        if not eff_fields.get("author"):
            out.append(
                BibIssue(
                    severity="WARNING",
                    code="MISSING_REQUIRED_FIELD",
                    key=entry.key,
                    file=bib_name,
                    line=entry.line,
                    message=f"Entry '{entry.key}' (@{et}) is missing 'author'.",
                )
            )
    if not (eff_fields.get("year") or eff_fields.get("date")):
        out.append(
            BibIssue(
                severity="WARNING",
                code="MISSING_REQUIRED_FIELD",
                key=entry.key,
                file=bib_name,
                line=entry.line,
                message=f"Entry '{entry.key}' (@{et}) is missing 'year'.",
            )
        )
    return out


def audit_bibliography(
    bib_path: pathlib.Path,
    manuscript_path: pathlib.Path | None = None,
    offline: bool = False,
    timeout: float = 10.0,
    polite_delay: float = 0.05,
    fetcher: Callable[[str, float], tuple[int, str]] | None = None,
    aux_path: pathlib.Path | None = None,
) -> BibAuditReport:
    bib_text = bib_path.read_bytes().decode("utf-8")
    entries, issues, _ = parse_bibtex(bib_text, bib_name=bib_path.name)
    by_key_ci = {e.key.lower(): e for e in entries}

    base_fetch = fetcher or default_http_fetcher
    call_counter = 0

    def throttled_fetch(url: str, t: float) -> tuple[int, str]:
        nonlocal call_counter
        if call_counter > 0 and polite_delay > 0 and fetcher is None:
            time.sleep(polite_delay)
        call_counter += 1
        return base_fetch(url, t)

    entry_map = {e.key: e for e in entries}
    cited_locs: dict[str, list[tuple[str, int]]] = {}
    has_nocite_star = False
    included_files: list[str] = []
    unresolved_includes: list[str] = []
    scanned_ms_paths: list[pathlib.Path] = []

    aux_oracle_info: dict[str, Any] | None = None
    pandoc_oracle_used = False
    if manuscript_path is not None:
        if not manuscript_path.exists():
            raise FileNotFoundError(f"Manuscript file not found: {manuscript_path}")
        is_md_ms = manuscript_path.suffix.lower() in MARKDOWN_SUFFIXES
        if is_md_ms:
            pan_res = pg_oracles.extract_pandoc_ast_citations(manuscript_path)
            if pan_res.get("available"):
                pandoc_oracle_used = True
                raw_md, _ = pg_tex_ast.decode_tex_bytes(manuscript_path.read_bytes(), manuscript_path)
                is_qmd = manuscript_path.suffix.lower() == ".qmd"
                xrefs = _extract_quarto_xref_labels(raw_md)
                md_no_yaml, yaml_nocite, yaml_star = _extract_md_yaml_frontmatter_and_mask(raw_md)
                masked_md_for_lines = _mask_markdown_code(md_no_yaml)
                has_nocite_star = yaml_star
                scanned_ms_paths = [manuscript_path]
                for yk, yln in yaml_nocite:
                    if not _is_quarto_xref_key(yk, is_qmd, xrefs):
                        cited_locs.setdefault(yk, []).append((manuscript_path.name, yln))
                # P1-05 & P1-11: Single-pass O(N) line index backfill intersected with authoritative Pandoc AST keys!
                md_lidx = LineIndex(masked_md_for_lines)
                md_key_lines: dict[str, list[int]] = {}
                for m_ln in re.finditer(r"(?<![\w.])@([A-Za-z0-9_][A-Za-z0-9_:#$%&+?<>~/-]*)", masked_md_for_lines):
                    mk = m_ln.group(1)
                    ln_no = md_lidx.line_of(m_ln.start())
                    lst = md_key_lines.setdefault(mk, [])
                    if not lst or lst[-1] != ln_no:
                        lst.append(ln_no)
                for pk in pan_res.get("keys", []):
                    if _is_quarto_xref_key(pk, is_qmd, xrefs):
                        continue
                    found_lns = md_key_lines.get(pk)
                    if found_lns:
                        for ln_no in found_lns:
                            cited_locs.setdefault(pk, []).append((manuscript_path.name, ln_no))
                    else:
                        cited_locs.setdefault(pk, []).append((manuscript_path.name, 0))
            else:
                (
                    cited_locs,
                    has_nocite_star,
                    included_files,
                    unresolved_includes,
                    scanned_ms_paths,
                ) = extract_manuscript_citations_full(manuscript_path)
        else:
            (
                cited_locs,
                has_nocite_star,
                included_files,
                unresolved_includes,
                scanned_ms_paths,
            ) = extract_manuscript_citations_full(manuscript_path)
        if aux_path is not None and aux_path.is_file():
            aux_oracle_info = pg_oracles.parse_tex_aux_file(aux_path)
            has_nocite_star = has_nocite_star or bool(aux_oracle_info.get("has_nocite_star"))
            for ak in aux_oracle_info.get("citations", []):
                if ak not in cited_locs:
                    cited_locs.setdefault(ak, []).append((aux_path.name, 1))

    # P0-A / P1-01 / P1-02: Unified multi-library bibliography discovery & cross-DB duplicate/crossref checks
    extra_bib_paths: list[pathlib.Path] = []
    unchecked_bib_dbs: list[str] = []
    sibling_bib_keys_ci: dict[str, str] = {}
    sibling_entries_ci: dict[str, BibEntry] = {}
    if manuscript_path is not None or aux_path is not None:
        if aux_oracle_info is None and aux_path is not None and aux_path.is_file():
            aux_oracle_info = pg_oracles.parse_tex_aux_file(aux_path)
        multi_bib = pg_oracles.discover_declared_bib_files(bib_path, scanned_ms_paths, aux_path)
        extra_bib_paths = list(multi_bib["extra_bib_paths"])
        unchecked_bib_dbs = list(multi_bib["missing_bib_dbs"])
        for sib_p in extra_bib_paths:
            sib_entries, _, _ = parse_bibtex(
                sib_p.read_text(encoding="utf-8", errors="replace"), sib_p.name
            )
            for se in sib_entries:
                k_ci = se.key.lower()
                if k_ci in sibling_bib_keys_ci and sibling_bib_keys_ci[k_ci] != sib_p.name:
                    issues.append(
                        BibIssue(
                            severity="CRITICAL",
                            code="CROSS_BIB_DUPLICATE_KEY",
                            key=se.key,
                            file=sib_p.name,
                            line=se.line,
                            message=(
                                f"Key '{se.key}' is defined in both '{sibling_bib_keys_ci[k_ci]}' and '{sib_p.name}' "
                                "(native BibTeX rejects cross-library duplicates)."
                            ),
                        )
                    )
                sibling_bib_keys_ci[k_ci] = sib_p.name
                sibling_entries_ci.setdefault(k_ci, se)

        for entry in entries:
            k_ci = entry.key.lower()
            if k_ci in sibling_bib_keys_ci:
                issues.append(
                    BibIssue(
                        severity="CRITICAL",
                        code="CROSS_BIB_DUPLICATE_KEY",
                        key=entry.key,
                        file=bib_path.name,
                        line=entry.line,
                        message=(
                            f"Key '{entry.key}' exists in both '{bib_path.name}' and sibling database "
                            f"'{sibling_bib_keys_ci[k_ci]}' (native BibTeX fails with Repeated entry)."
                        ),
                    )
                )

    if manuscript_path is not None:
        entry_map_ci = {k.lower(): k for k in entry_map}
        cited_canonical: set[str] = set()
        sib_hits: list[str] = []
        for ckey, locs in sorted(cited_locs.items()):
            canon = entry_map_ci.get(ckey.lower())
            if canon is not None:
                cited_canonical.add(canon)
            elif ckey.lower() in sibling_bib_keys_ci:
                sib_hits.append(sibling_bib_keys_ci[ckey.lower()])
            else:
                first_file, first_ln = locs[0]
                loc_str = ", ".join(f"{fn}:L{ln}" if ln > 0 else f"{fn} (unlocated)" for fn, ln in locs[:4])
                if unchecked_bib_dbs:
                    issues.append(
                        BibIssue(
                            severity="WARNING",
                            code="DANGLING_CITATION_UNCHECKED_DB",
                            key=ckey,
                            file=first_file,
                            line=first_ln,
                            message=(
                                f"Citation key '{ckey}' cited at {loc_str} is absent from {bib_path.name}, "
                                f"but additional database(s) ({', '.join(unchecked_bib_dbs)}) were not inspected."
                            ),
                        )
                    )
                else:
                    issues.append(
                        BibIssue(
                            severity="CRITICAL",
                            code="DANGLING_CITATION",
                            key=ckey,
                            file=first_file,
                            line=first_ln,
                            message=f"Citation key '{ckey}' cited at {loc_str} is missing from {bib_path.name}.",
                        )
                    )
        if sib_hits or unchecked_bib_dbs:
            note_bits: list[str] = []
            if sib_hits:
                note_bits.append(f"{len(sib_hits)} key(s) resolved in sibling DB(s): {', '.join(sorted(set(sib_hits)))}")
            if unchecked_bib_dbs:
                note_bits.append(f"unchecked DB(s) missing on disk: {', '.join(unchecked_bib_dbs)}")
            issues.append(
                BibIssue(
                    severity="INFO",
                    code="MULTI_BIB_SCOPE",
                    key="MULTI_BIB_SCOPE",
                    file=bib_path.name,
                    line=1,
                    message=f"Multi-database bibliography declared ({'; '.join(note_bits)}).",
                )
            )
        for uinc in unresolved_includes:
            issues.append(
                BibIssue(
                    severity="INFO",
                    code="UNRESOLVED_INCLUDE_SCOPE",
                    key=uinc,
                    file=manuscript_path.name,
                    line=1,
                    message=f"Included file '{uinc}' was not found; uncited-entry checks cover scanned files only.",
                )
            )

    statuses: list[EntryVerification] = []
    crossref_required = _collect_crossref_deps(entries, cited_canonical if manuscript_path is not None else set()) if manuscript_path is not None else set()
    combined_by_key_ci = {**sibling_entries_ci, **by_key_ci}

    for entry in entries:
        eff_fields, missing_xref = _resolve_effective_fields(entry, combined_by_key_ci)
        issues.extend(_validate_entry_fields(entry, eff_fields, missing_xref, bib_path.name))
        doi = eff_fields.get("doi", "").strip()
        year = eff_fields.get("year", "").strip()
        journal = eff_fields.get("journal", "")
        eprint = eff_fields.get("eprint", "")
        arxiv_id_map = _extract_all_arxiv_ids(eff_fields)
        arch_pref = (eff_fields.get("archiveprefix") or eff_fields.get("eprinttype") or "").strip()

        if arch_pref and arch_pref.lower() != "arxiv" and arxiv_id_map:
            statuses.append(
                EntryVerification(
                    entry.key,
                    "UNVERIFIED_MISS",
                    "BibTeX-Parser",
                    f"Conflicting archivePrefix '{arch_pref}' with arXiv-like eprint",
                )
            )
            issues.append(
                BibIssue(
                    severity="WARNING",
                    code="ARCHIVE_PREFIX_CONFLICT",
                    key=entry.key,
                    file=bib_path.name,
                    line=entry.line,
                    message=(
                        f"Entry '{entry.key}' declares non-arXiv repository '{arch_pref}' alongside "
                        "arXiv-like eprint; verify identifier manually."
                    ),
                )
            )
        elif len(set(arxiv_id_map.values())) > 1:
            pair_desc = ", ".join(f"{k}={v}" for k, v in arxiv_id_map.items())
            statuses.append(EntryVerification(entry.key, "IDENTITY_CONFLICT", "Cross-Field-arXiv", pair_desc))
            issues.append(
                BibIssue(
                    severity="CRITICAL",
                    code="CONFLICTING_ARXIV_IDS",
                    key=entry.key,
                    file=bib_path.name,
                    line=entry.line,
                    message=f"Entry '{entry.key}' carries conflicting arXiv identifiers ({pair_desc}); resolve mismatch.",
                )
            )
        elif offline:
            statuses.append(EntryVerification(entry.key, "UNQUERIED_OFFLINE", "offline"))
            if doi and SUSPECT_DOI_HINT_RE.search(doi):
                issues.append(
                    BibIssue(
                        severity="WARNING",
                        code="SUSPECT_DOI_PATTERN",
                        key=entry.key,
                        file=bib_path.name,
                        line=entry.line,
                        message=(
                            f"DOI '{doi}' matches placeholder pattern (advisory only; run online verification to confirm)."
                        ),
                        evidence=strip_latex_markup(eff_fields.get("title", ""))[:100],
                    )
                )
            if doi and year.isdigit() and not ARXIV_DOI_RE.match(doi):
                doi_years = [int(y) for y in DOI_YEAR_RE.findall(doi)]
                if doi_years and all(abs(dy - int(year)) >= 2 for dy in doi_years):
                    issues.append(
                        BibIssue(
                            severity="WARNING",
                            code="YEAR_DOI_HINT",
                            key=entry.key,
                            file=bib_path.name,
                            line=entry.line,
                            message=(
                                f"Year '{year}' differs from year token ({doi_years[0]}) embedded in DOI '{doi}' "
                                "(run online verification to confirm authoritative publication year)."
                            ),
                        )
                    )
        else:
            ver, online_issues = _verify_entry_online(
                entry, eff_fields, bib_path.name, throttled_fetch, timeout
            )
            statuses.append(ver)
            issues.extend(online_issues)

        if (eprint or "arxiv" in journal.lower()) and not _is_archival_published_entry(entry.entry_type, eff_fields):
            issues.append(
                BibIssue(
                    severity="INFO",
                    code="PREPRINT_UPGRADE_CANDIDATE",
                    key=entry.key,
                    file=bib_path.name,
                    line=entry.line,
                    message=f"Entry '{entry.key}' cites an arXiv preprint; check if an archival proceedings version exists.",
                    evidence=eprint or journal,
                )
            )

        if (
            manuscript_path is not None
            and not has_nocite_star
            and not unresolved_includes
            and entry.key not in cited_canonical
            and entry.key not in crossref_required
        ):
            issues.append(
                BibIssue(
                    severity="INFO",
                    code="UNCITED_ENTRY",
                    key=entry.key,
                    file=bib_path.name,
                    line=entry.line,
                    message=f"Entry '{entry.key}' in {bib_path.name} is not cited in {manuscript_path.name}.",
                )
            )

    sev_rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    issues.sort(key=lambda item: (sev_rank.get(item.severity, 9), item.file, item.line, item.key))

    verified_cnt = sum(1 for s in statuses if s.status == "VERIFIED")
    retracted_cnt = sum(1 for s in statuses if s.status == "RETRACTED")
    conflict_cnt = sum(1 for s in statuses if s.status == "IDENTITY_CONFLICT")
    unver_cnt = sum(1 for s in statuses if s.status == "UNVERIFIED_MISS")
    failed_cnt = sum(1 for s in statuses if s.status == "LOOKUP_FAILED")
    unqueried_cnt = sum(1 for s in statuses if s.status == "UNQUERIED_OFFLINE")

    if offline:
        mode = "offline"
    elif not entries:
        mode = "empty"
    elif call_counter > 0 and failed_cnt == 0 and unver_cnt == 0 and retracted_cnt == 0 and verified_cnt == len(entries):
        mode = f"online-verified ({verified_cnt}/{len(entries)} confirmed)"
    elif failed_cnt > 0 and verified_cnt == 0 and conflict_cnt == 0 and retracted_cnt == 0:
        mode = f"online-lookup-failed (0/{len(entries)} confirmed, {failed_cnt} service errors)"
    else:
        r_str = f", {retracted_cnt} retracted" if retracted_cnt else ""
        mode = (
            f"online-partial ({verified_cnt}/{len(entries)} confirmed{r_str}, "
            f"{conflict_cnt} conflicts, {unver_cnt} unverified, {failed_cnt} lookup errors)"
        )

    provenance = {
        "schema_version": SCHEMA_VERSION,
        "tool": "audit_bib_lite",
        "parser_engine": "pylatexenc-ast",
        "aux_oracle": str(aux_path.resolve()) if aux_oracle_info else None,
        "aux_files": (
            {str(d["path"]): str(d["sha256"]) for d in aux_oracle_info.get("aux_digests", [])}
            if aux_oracle_info
            else {}
        ),
        "pandoc_ast_oracle": pandoc_oracle_used,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "bib_path": str(bib_path.resolve()),
        "bib_sha256": _sha256_file(bib_path),
        "extra_bib_files": {str(p.resolve()): _sha256_file(p) for p in extra_bib_paths if p.is_file()},
        "missing_bib_dbs": list(unchecked_bib_dbs),
        "manuscript_path": str(manuscript_path.resolve()) if manuscript_path else None,
        "manuscript_files": {str(p.resolve()): _sha256_file(p) for p in scanned_ms_paths},
        "offline": offline,
        "network_mode": "offline" if offline else "online",
        "unqueried_count": unqueried_cnt,
        "lookup_failed_count": failed_cnt,
    }

    return BibAuditReport(
        bib_path=str(bib_path),
        manuscript_path=str(manuscript_path) if manuscript_path else None,
        mode=mode,
        total_entries=len(entries),
        cited_unique_keys=len(cited_locs),
        verified_count=verified_cnt,
        conflict_count=conflict_cnt,
        unverified_count=unver_cnt,
        lookup_failed_count=failed_cnt,
        unqueried_count=unqueried_cnt,
        critical_count=sum(1 for x in issues if x.severity == "CRITICAL"),
        warning_count=sum(1 for x in issues if x.severity == "WARNING"),
        info_count=sum(1 for x in issues if x.severity == "INFO"),
        has_nocite_star=has_nocite_star,
        included_files=included_files,
        unresolved_includes=unresolved_includes,
        entry_statuses=statuses,
        issues=issues,
        provenance=provenance,
    )


def format_markdown_report(report: BibAuditReport) -> str:
    lines = [
        "# PaperGuard Lite — Citation Forensics Triage (Read-Only)",
        "",
        f"- **Bibliography**: `{report.bib_path}` ({report.total_entries} entries)",
        f"- **Verification Status**: `{report.mode}`",
        f"- **Findings**: **{report.critical_count} Critical** | **{report.warning_count} Warnings** | **{report.info_count} Notes**",
        "",
    ]
    if not report.issues:
        lines.append("No citation anomalies detected in scanned scope.")
    else:
        lines.extend(
            [
                "| Severity | Key | Source Location | Code | Finding |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for issue in report.issues:
            lines.append(
                f"| **{issue.severity}** | `{issue.key}` | `{issue.file}:L{issue.line}` | `{issue.code}` | {issue.message} |"
            )

    if report.critical_count > 0 or report.warning_count > 0 or report.info_count > 0:
        lines.extend(
            [
                "",
                "---",
                "> **Read-Only Diagnostic Mode**: PaperGuard Lite never modifies your `.bib` or manuscript files.",
                f"> For lossless evidence-backed `.bib` span patching, identity-verified preprint upgrades, and stateful `latex_guard.py` verification, see **PaperGuard Pro ($29)**: {PRO_UPGRADE_URL}",
            ]
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PaperGuard Lite: Read-only multi-registrar BibTeX & manuscript citation auditor."
    )
    parser.add_argument("bib_file", type=pathlib.Path, help="Path to .bib file")
    parser.add_argument(
        "--manuscript", "-m", type=pathlib.Path, default=None, help="Optional .tex or .md manuscript"
    )
    parser.add_argument(
        "--aux", type=pathlib.Path, default=None, help="Optional TeX .aux compiler oracle for expanded citations"
    )
    parser.add_argument(
        "--offline", action="store_true", help="Skip Crossref/DataCite/arXiv network queries"
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds (default: 10)")
    parser.add_argument("--json", type=pathlib.Path, default=None, help="Export structured JSON report")
    args = parser.parse_args(argv)

    if not args.bib_file.exists():
        print(f"Error: bibliography file not found: {args.bib_file}", file=sys.stderr)
        return 2
    if args.manuscript is not None and not args.manuscript.exists():
        print(f"Error: manuscript file not found: {args.manuscript}", file=sys.stderr)
        return 2

    try:
        aux_protected: list[pathlib.Path] = []
        if args.aux is not None:
            aux_protected.append(args.aux)
            if args.aux.is_file():
                for af in pg_oracles.parse_tex_aux_file(args.aux).get("aux_files", []):
                    aux_protected.append(pathlib.Path(af))
        ms_protected = collect_manuscript_paths(args.manuscript)
        extra_bib_protected = pg_oracles.discover_declared_bib_files(
            args.bib_file, ms_protected, args.aux
        ).get("extra_bib_paths", [])
        protected_paths: list[pathlib.Path | None] = [
            args.bib_file,
            *extra_bib_protected,
            *ms_protected,
            *aux_protected,
        ]
        _guard_readonly_output(protected_paths, args.json)
        report = audit_bibliography(
            bib_path=args.bib_file,
            manuscript_path=args.manuscript,
            offline=args.offline,
            timeout=args.timeout,
            aux_path=args.aux,
        )
        if args.json:
            _atomic_write_text(args.json, json.dumps(report.to_dict(), indent=2))
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(format_markdown_report(report))
    return 1 if report.critical_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
