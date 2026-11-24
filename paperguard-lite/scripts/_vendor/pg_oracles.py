#!/usr/bin/env python3
"""PaperGuard Optional Local Compiler & Tool Oracles (v2.1).

100% Python 3.11+ standard library. Auto-detects user-installed open-source TeX/Pandoc
tools via subprocess or existing build artifacts (`.aux` / `.bcf` / `.fls`), degrading cleanly
when not installed on the user's machine:
1. `verify_bib_with_bibtex_oracle`: Runs real `bibtex` in a temporary directory against
   a synthetic `.aux` (respecting `has_nocite_star`) to verify `.bbl` output, syntax exit
   codes, and `crossref` integrity.
2. `parse_tex_fls_details` / `parse_tex_fls_recorder`: Reads TeX `-recorder` `.fls` files to
   recover the compiler's exact active `.tex` AND project-local `.sty`/`.cls`/`.def` files
   along with `PWD` tree ownership.
3. `parse_tex_aux_file`: Recursively parses `.aux` (`\\@input{...}`), BibLaTeX 2-arg
   `\\abx@aux@cite{<refsec>}{<key>}`, and Biber `.bcf` XML (`<bcf:citekey>`), returning
   SHA-256 + mtime digests for freshness validation.
4. `extract_pandoc_ast_citations`: Runs `pandoc --to=json` for `.md`/`.qmd`/`.rmd` to
   extract Pandoc's official AST `Cite` nodes cleanly separated from code/YAML/comments.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Any

_EXTRA_TEX_DIRS = (
    "/Library/TeX/texbin",
    "/usr/local/texlive/2026/bin/universal-darwin",
    "/usr/local/texlive/2025/bin/universal-darwin",
    "/usr/local/texlive/2024/bin/universal-darwin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)

LOCAL_MACRO_SUFFIXES = frozenset({".sty", ".cls", ".def", ".cfg", ".clo"})
TEX_INPUT_SUFFIXES = frozenset({".tex", ".ltx"})


def _sha256_path(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def find_tool_binary(name: str) -> str | None:
    """Locate an installed external CLI binary (`bibtex`, `pdflatex`, `pandoc`) without modifying PATH."""
    direct = shutil.which(name)
    if direct:
        return direct
    for d in _EXTRA_TEX_DIRS:
        cand = pathlib.Path(d) / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    for match in sorted(glob.glob(f"/usr/local/texlive/*/bin/*/{name}"), reverse=True):
        if os.path.isfile(match) and os.access(match, os.X_OK):
            return match
    return None


def verify_bib_with_bibtex_oracle(
    bib_text: str,
    cited_keys: list[str] | None = None,
    *,
    has_nocite_star: bool = False,
    bibtex_bin: str | None = None,
    timeout: float = 10.0,
    ordered_bib_sources: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Run real `bibtex` in a temp workspace to verify `.bbl` keys, syntax, and `crossref` graph (P1-04, v2.5 multi-DB)."""
    exe = bibtex_bin or find_tool_binary("bibtex")
    if not exe:
        return {
            "available": False,
            "binary_found": False,
            "status": "UNAVAILABLE",
            "binary": None,
            "exit_code": None,
            "style": "plain",
            "has_nocite_star": bool(has_nocite_star),
            "requested_keys": list(cited_keys or []),
            "bbl_keys": [],
            "errors": [],
            "warnings": [],
        }

    try:
        with tempfile.TemporaryDirectory(prefix="pg_bibtex_") as td:
            tdir = pathlib.Path(td)
            bibdata_names: list[str] = []
            if ordered_bib_sources:
                for idx, (raw_stem, src_txt) in enumerate(ordered_bib_sources):
                    clean_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw_stem)).strip("_") or f"db{idx}"
                    safe_name = f"db{idx}_{clean_stem}"
                    (tdir / f"{safe_name}.bib").write_text(src_txt, encoding="utf-8")
                    bibdata_names.append(safe_name)
            else:
                (tdir / "refs.bib").write_text(bib_text, encoding="utf-8")
                bibdata_names.append("refs")
            aux_lines = ["\\relax", "\\bibstyle{plain}"]
            req_keys: list[str] = []
            if cited_keys:
                for k in cited_keys:
                    aux_lines.append(f"\\citation{{{k}}}")
                    req_keys.append(k)
            if has_nocite_star or not cited_keys:
                aux_lines.append("\\citation{*}")
                if "*" not in req_keys:
                    req_keys.append("*")
            aux_lines.append(f"\\bibdata{{{','.join(bibdata_names)}}}")
            (tdir / "check.aux").write_text("\n".join(aux_lines) + "\n", encoding="utf-8")

            proc = subprocess.run(
                [exe, "check"],
                cwd=str(tdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            errors: list[str] = []
            warnings: list[str] = []
            duplicate_field_warnings: list[str] = []
            for ln in combined.splitlines():
                st = ln.strip()
                if not st:
                    continue
                low = st.lower()
                if re.search(r"ignoring\s+.+?'s\s+extra\s+\"[^\"]+\"\s+field", low):
                    duplicate_field_warnings.append(st)
                    warnings.append(st)
                    errors.append(f"Duplicate BibTeX field warning (P0-01): {st}")
                elif (
                    "error message" in low
                    or "bad cross reference" in low
                    or "syntax error" in low
                    or st.startswith("I couldn't")
                    or st.startswith("Illegal")
                    or st.startswith("Repeated entry")
                    or st.startswith("You're missing")
                    or st.startswith("You are missing")
                ):
                    errors.append(st)
                elif st.startswith("Warning--"):
                    warnings.append(st)

            bbl_path = tdir / "check.bbl"
            bbl_keys: list[str] = []
            if bbl_path.is_file():
                bbl_txt = bbl_path.read_text(encoding="utf-8", errors="replace")
                bbl_keys = re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", bbl_txt)

            bbl_ci = {k.lower() for k in bbl_keys}
            missing_req = [k for k in req_keys if k != "*" and k.lower() not in bbl_ci]
            coverage_complete = len(missing_req) == 0
            compiler_passed = (
                proc.returncode == 0
                and not duplicate_field_warnings
                and not any("bad cross reference" in e.lower() for e in errors)
            )
            return {
                "available": True,
                "binary_found": True,
                "status": "PASSED" if compiler_passed else "FAILED",
                "compiler_passed": compiler_passed,
                "coverage_complete": coverage_complete,
                "missing_requested_keys": missing_req,
                "binary": exe,
                "exit_code": proc.returncode,
                "style": "plain",
                "has_nocite_star": bool(has_nocite_star or not cited_keys),
                "requested_keys": req_keys,
                "bbl_keys": bbl_keys,
                "errors": errors,
                "warnings": warnings,
                "duplicate_field_warnings": duplicate_field_warnings,
            }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": True,
            "binary_found": True,
            "status": "ERROR",
            "binary": exe,
            "exit_code": None,
            "style": "plain",
            "has_nocite_star": bool(has_nocite_star),
            "requested_keys": list(cited_keys or []),
            "bbl_keys": [],
            "errors": [str(exc)],
            "warnings": [],
        }


BUILD_ARTIFACT_SUFFIXES = frozenset({
    ".aux", ".log", ".out", ".toc", ".lof", ".lot", ".fls", ".fdb_latexmk",
    ".synctex", ".gz", ".dvi", ".ps", ".blg", ".bcf", ".xml",
    ".nav", ".snm", ".vrb", ".idx", ".ilg", ".ind", ".glo", ".gls", ".acn",
    ".acr", ".alg", ".ist", ".xdy", ".tfm", ".vf", ".pk", ".pfb", ".otf",
    ".ttf", ".ttc", ".enc", ".map", ".fd", ".fmt", ".cnf",
})
ASSET_INPUT_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".bbl"})
_SYSTEM_TEX_PREFIXES = (
    "/usr/share/texlive/",
    "/usr/local/texlive/",
    "/library/texlive/",
    "/opt/homebrew/share/texmf",
    "/usr/share/texmf",
    "/var/lib/texmf",
    "/etc/texmf/",
)
_SYSTEM_TEX_SEGMENTS = ("/texmf-dist/", "/texmf-var/", "/texmf-config/", "/texlive/")


def _is_system_tex_path(p: pathlib.Path, root_dir: pathlib.Path | None = None) -> bool:
    """Distinguish real system TeX installations from project-local directories (even project `texmf/`, P0-07)."""
    if root_dir is not None:
        try:
            if p.resolve().is_relative_to(root_dir.resolve()):
                return False
        except OSError:
            pass
    posix = p.as_posix().lower()
    if any(posix.startswith(pref) for pref in _SYSTEM_TEX_PREFIXES):
        return True
    if any(seg in posix for seg in _SYSTEM_TEX_SEGMENTS):
        return True
    return False


def parse_tex_fls_details(fls_path: pathlib.Path, root_dir: pathlib.Path) -> dict[str, Any]:
    """Parse TeX `-recorder` `.fls` returning ALL non-system INPUTs, OUTPUTs, local assets, external inputs & digest (P0-03, P1-02)."""
    if not fls_path.is_file():
        return {
            "exists": False,
            "fls_path": str(fls_path),
            "fls_sha256": None,
            "fls_mtime_ns": None,
            "pwd": None,
            "input_line_count": 0,
            "tex_files": [],
            "macro_files": [],
            "data_files": [],
            "asset_files": [],
            "external_files": [],
            "output_files": [],
            "all_files": [],
            "raw_inputs": [],
            "unresolved_inputs": [],
        }

    fls_res = fls_path.resolve()
    root_res = root_dir.resolve()
    pwd_dir: pathlib.Path | None = None
    raw_lines = fls_res.read_text(encoding="utf-8", errors="replace").splitlines()
    for raw_ln in raw_lines:
        st = raw_ln.strip()
        if st.startswith("PWD "):
            cand_pwd = pathlib.Path(st[4:].strip())
            if cand_pwd.exists():
                pwd_dir = cand_pwd.resolve()
            break

    base_dir = pwd_dir or fls_res.parent

    # Pass 1: collect compiler OUTPUT files so pure INPUT assets (panel.pdf, standalone .bbl)
    # are cleanly separated from build outputs (main.pdf, main.aux, main.log, P1-02).
    output_set: set[pathlib.Path] = set()
    output_files: list[pathlib.Path] = []
    for raw_ln in raw_lines:
        st = raw_ln.strip()
        if not st.startswith("OUTPUT "):
            continue
        rel_or_abs = st[7:].strip()
        if not rel_or_abs:
            continue
        cand_out = pathlib.Path(rel_or_abs)
        cand_out = (base_dir / cand_out).resolve() if not cand_out.is_absolute() else cand_out.resolve()
        if cand_out not in output_set:
            output_set.add(cand_out)
            output_files.append(cand_out)

    seen_tex: set[pathlib.Path] = set()
    seen_macro: set[pathlib.Path] = set()
    seen_data: set[pathlib.Path] = set()
    seen_asset: set[pathlib.Path] = set()
    seen_external: set[pathlib.Path] = set()
    seen_raw: set[pathlib.Path] = set()
    tex_files: list[pathlib.Path] = []
    macro_files: list[pathlib.Path] = []
    data_files: list[pathlib.Path] = []
    asset_files: list[pathlib.Path] = []
    external_files: list[pathlib.Path] = []
    raw_inputs: list[pathlib.Path] = []
    unresolved_inputs: list[str] = []
    input_line_count = 0

    for raw_ln in raw_lines:
        st = raw_ln.strip()
        if not st.startswith("INPUT "):
            continue
        rel_or_abs = st[6:].strip()
        if not rel_or_abs:
            continue
        input_line_count += 1
        cand = pathlib.Path(rel_or_abs)
        if not cand.is_absolute():
            cand = (base_dir / cand).resolve()
        else:
            cand = cand.resolve()
        if not cand.is_file():
            if not _is_system_tex_path(cand, root_res):
                unresolved_inputs.append(rel_or_abs)
            continue
        if _is_system_tex_path(cand, root_res):
            continue

        # Never silently drop deep relative parent inputs like ../../../datasets/value.dat (P0-03)!
        # Always include every non-system INPUT in raw_inputs for write protection, and track
        # external (out-of-root) inputs explicitly.
        try:
            is_inside_root = cand.is_relative_to(root_res)
        except OSError:
            is_inside_root = False

        if cand not in seen_raw:
            seen_raw.add(cand)
            raw_inputs.append(cand)
        if not is_inside_root and cand not in seen_external:
            seen_external.add(cand)
            external_files.append(cand)

        suf = cand.suffix.lower()
        if suf in TEX_INPUT_SUFFIXES:
            if cand not in seen_tex:
                seen_tex.add(cand)
                tex_files.append(cand)
        elif suf in LOCAL_MACRO_SUFFIXES:
            if cand not in seen_macro:
                seen_macro.add(cand)
                macro_files.append(cand)
        elif cand in output_set:
            # Compiler-generated build output (e.g. main.aux, main.toc, main.pdf); not a user source asset
            continue
        elif suf in ASSET_INPUT_SUFFIXES:
            # Pure INPUT local graphic (panel.pdf, fig.png) or pre-generated .bbl (P1-02)
            if cand not in seen_asset:
                seen_asset.add(cand)
                asset_files.append(cand)
        elif suf not in BUILD_ARTIFACT_SUFFIXES:
            # Non-TeX data input (e.g. results.dat, ../../../datasets/value.dat, table.csv, P0-03/P0-07)
            if cand not in seen_data:
                seen_data.add(cand)
                data_files.append(cand)

    return {
        "exists": True,
        "fls_path": str(fls_res),
        "fls_sha256": _sha256_path(fls_res),
        "fls_mtime_ns": fls_res.stat().st_mtime_ns,
        "pwd": str(pwd_dir) if pwd_dir else str(fls_res.parent),
        "input_line_count": input_line_count,
        "tex_files": tex_files,
        "macro_files": macro_files,
        "data_files": data_files,
        "asset_files": asset_files,
        "external_files": external_files,
        "output_files": output_files,
        "all_files": tex_files + macro_files + data_files + asset_files,
        "raw_inputs": raw_inputs,
        "unresolved_inputs": unresolved_inputs,
    }


def parse_tex_fls_recorder(fls_path: pathlib.Path, root_dir: pathlib.Path) -> list[pathlib.Path]:
    """Backwards-compatible wrapper returning ordered unique project `.tex` files from `.fls`."""
    return list(parse_tex_fls_details(fls_path, root_dir)["tex_files"])


def _parse_biber_bcf_file(bcf_path: pathlib.Path) -> dict[str, Any]:
    """Parse Biber `.bcf` XML control file for exact `<bcf:citekey>` entries."""
    res = bcf_path.resolve()
    citations: list[str] = []
    seen: set[str] = set()
    has_star = False
    try:
        tree = ET.parse(res)
        root = tree.getroot()
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "citekey" and el.text:
                k = el.text.strip()
                if k == "*":
                    has_star = True
                elif k and k not in seen:
                    seen.add(k)
                    citations.append(k)
    except (OSError, ET.ParseError):
        pass
    stat_r = res.stat()
    return {
        "citations": citations,
        "has_nocite_star": has_star,
        "labels": [],
        "bibdata": [],
        "aux_files": [str(res)],
        "aux_digests": [{"path": str(res), "sha256": _sha256_path(res), "mtime_ns": stat_r.st_mtime_ns}],
    }


def parse_tex_aux_file(aux_path: pathlib.Path, _visited: set[pathlib.Path] | None = None) -> dict[str, Any]:
    """Recursively parse TeX `.aux` (`\\@input{...}`, BibLaTeX 2-arg `\\abx@aux@cite`) or Biber `.bcf` (P1-02/P1-03)."""
    visited = _visited if _visited is not None else set()
    res = aux_path.resolve()
    if res in visited or not aux_path.is_file():
        return {
            "citations": [],
            "has_nocite_star": False,
            "labels": [],
            "bibdata": [],
            "aux_files": [],
            "aux_digests": [],
        }
    visited.add(res)

    if res.suffix.lower() == ".bcf":
        return _parse_biber_bcf_file(res)

    citations: list[str] = []
    seen_cites: set[str] = set()
    has_star = False
    labels: list[str] = []
    seen_labels: set[str] = set()
    bibdata: list[str] = []
    stat_r = res.stat()
    aux_files: list[str] = [str(res)]
    aux_digests: list[dict[str, Any]] = [
        {"path": str(res), "sha256": _sha256_path(res), "mtime_ns": stat_r.st_mtime_ns}
    ]

    def _add_cite_csv(csv_str: str) -> None:
        nonlocal has_star
        for part in csv_str.split(","):
            k = part.strip()
            if k == "*":
                has_star = True
            elif k and k not in seen_cites:
                seen_cites.add(k)
                citations.append(k)

    raw = aux_path.read_text(encoding="utf-8", errors="replace")
    for ln in raw.splitlines():
        st = ln.strip()
        # 1. BibLaTeX 2-arg commands: \abx@aux@cite{<refsection>}{<key>} and \abx@aux@nocite{<refsection>}{<key>} (P1-02!)
        m_abx2 = re.match(r"^\\abx@aux@(?:cite|nocite)\{[^}]*\}\{([^}]+)\}", st)
        if m_abx2:
            _add_cite_csv(m_abx2.group(1))
            continue
        # 2. Classic \citation{k1,k2} or legacy 1-arg \abx@aux@cite[opt]{key}
        m_cite = re.match(r"^\\(?:citation|abx@aux@(?:cite|nocite)(?:\[[^\]]*\])?)\{([^}]+)\}", st)
        if m_cite:
            _add_cite_csv(m_cite.group(1))
            continue
        m_lbl = re.match(r"^\\newlabel\{([^}]+)\}", st)
        if m_lbl:
            lk = m_lbl.group(1).strip()
            if lk and not lk.endswith("@cref") and lk not in seen_labels:
                seen_labels.add(lk)
                labels.append(lk)
            continue
        m_bib = re.match(r"^\\bibdata\{([^}]+)\}", st)
        if m_bib:
            for b in m_bib.group(1).split(","):
                if b.strip() and b.strip() not in bibdata:
                    bibdata.append(b.strip())
            continue
        m_inc = re.match(r"^\\@input\{([^}]+)\}", st)
        if m_inc:
            sub_aux = aux_path.parent / m_inc.group(1).strip()
            if sub_aux.is_file():
                sub = parse_tex_aux_file(sub_aux, visited)
                for ck in sub["citations"]:
                    if ck not in seen_cites:
                        seen_cites.add(ck)
                        citations.append(ck)
                has_star = has_star or sub["has_nocite_star"]
                for lk in sub["labels"]:
                    if lk not in seen_labels:
                        seen_labels.add(lk)
                        labels.append(lk)
                for bd in sub["bibdata"]:
                    if bd not in bibdata:
                        bibdata.append(bd)
                aux_files.extend(sub["aux_files"])
                aux_digests.extend(sub["aux_digests"])

    return {
        "citations": citations,
        "has_nocite_star": has_star,
        "labels": labels,
        "bibdata": bibdata,
        "aux_files": aux_files,
        "aux_digests": aux_digests,
    }


def extract_pandoc_ast_citations(
    md_path: pathlib.Path,
    *,
    pandoc_bin: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Extract citation keys via official `pandoc --to=json` AST when a working Pandoc binary is installed."""
    exe = pandoc_bin or find_tool_binary("pandoc")
    if not exe or not md_path.is_file():
        return {"available": False, "status": "UNAVAILABLE", "keys": [], "binary": None}

    try:
        proc = subprocess.run(
            [exe, "--to=json", str(md_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {"available": False, "status": "ERROR", "keys": [], "binary": exe}
        ast_data = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"available": False, "status": "UNAVAILABLE", "keys": [], "binary": exe}

    keys: list[str] = []
    seen: set[str] = set()

    def _walk_json(node: Any) -> None:
        if isinstance(node, list):
            for it in node:
                _walk_json(it)
            return
        if isinstance(node, dict):
            if node.get("t") == "Cite" and isinstance(node.get("c"), list) and node["c"]:
                cite_objs = node["c"][0]
                if isinstance(cite_objs, list):
                    for co in cite_objs:
                        if isinstance(co, dict):
                            cid = str(co.get("citationId") or "").strip()
                            if cid and cid not in seen:
                                seen.add(cid)
                                keys.append(cid)
            for v in node.values():
                _walk_json(v)

    _walk_json(ast_data)
    return {"available": True, "status": "OK", "keys": keys, "binary": exe}


_MD_SUFFIXES_FOR_BIB = frozenset({".md", ".markdown", ".qmd", ".rmd"})


def discover_declared_bib_files(
    bib_path: pathlib.Path,
    manuscript_paths: list[pathlib.Path] | None = None,
    aux_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Resolve all `.bib` databases declared via `.aux` `\\bibdata{...}` or TeX `\\bibliography{...}` in exact order.

    Returns:
      - declared_names: ordered list of declared DB names (excluding duplicates)
      - ordered_dbs: list of `(db_stem, resolved_path_or_none, is_primary)` preserving TeX lookup order
      - extra_bib_paths: list of resolved sibling `.bib` files on disk (`!= bib_path.resolve()`)
      - missing_bib_dbs: list of declared DB names not found on disk
    """
    import pg_tex_ast

    declared_names: list[str] = []
    if aux_path is not None and aux_path.is_file():
        aux_info = parse_tex_aux_file(aux_path)
        for bd in aux_info.get("bibdata", []):
            st_bd = str(bd).strip()
            if st_bd and st_bd not in declared_names:
                declared_names.append(st_bd)

    ms_list = list(manuscript_paths or [])
    for idx, sp in enumerate(ms_list):
        if sp.suffix.lower() in _MD_SUFFIXES_FOR_BIB or not sp.is_file():
            continue
        try:
            sp_raw, _ = pg_tex_ast.decode_tex_bytes(sp.read_bytes(), sp.resolve())
        except Exception:
            continue
        if idx == 0:
            sp_raw, _ = pg_tex_ast.truncate_after_end_document(sp_raw)
        sp_raw = pg_tex_ast.strip_iffalse_blocks(sp_raw)
        sp_clean = pg_tex_ast.mask_tex_via_ast(sp_raw, mask_comments=True, mask_verbatim=True)
        for bm in re.finditer(r"\\(?:bibliography|addbibresource)\s*\{([^}]+)\}", sp_clean):
            for part in bm.group(1).split(","):
                st_bd = part.strip()
                if st_bd and st_bd not in declared_names:
                    declared_names.append(st_bd)

    cur_bib_res = bib_path.resolve()
    search_dirs: list[pathlib.Path] = [bib_path.parent]
    if ms_list:
        ms_dir = ms_list[0].parent
        if ms_dir not in search_dirs:
            search_dirs.append(ms_dir)
    if aux_path is not None and aux_path.parent not in search_dirs:
        search_dirs.append(aux_path.parent)

    ordered_dbs: list[tuple[str, pathlib.Path | None, bool]] = []
    extra_bib_paths: list[pathlib.Path] = []
    missing_bib_dbs: list[str] = []
    seen_extra: set[pathlib.Path] = set()
    primary_seen = False

    for db_raw in declared_names:
        db_stem = db_raw[:-4] if db_raw.lower().endswith(".bib") else db_raw
        resolved_hit: pathlib.Path | None = None
        for sdir in search_dirs:
            for cand_b in (sdir / db_raw, sdir / f"{db_stem}.bib"):
                if cand_b.is_file():
                    resolved_hit = cand_b.resolve()
                    break
            if resolved_hit is not None:
                break
        is_primary = (
            (resolved_hit is not None and resolved_hit == cur_bib_res)
            or db_stem == bib_path.stem
            or db_raw == bib_path.name
        )
        if is_primary:
            if not primary_seen:
                ordered_dbs.append((bib_path.stem, cur_bib_res, True))
                primary_seen = True
            continue
        if resolved_hit is not None:
            ordered_dbs.append((resolved_hit.stem, resolved_hit, False))
            if resolved_hit not in seen_extra:
                seen_extra.add(resolved_hit)
                extra_bib_paths.append(resolved_hit)
        else:
            ordered_dbs.append((db_stem, None, False))
            missing_bib_dbs.append(db_raw)

    if not primary_seen:
        ordered_dbs.insert(0, (bib_path.stem, cur_bib_res, True))

    return {
        "declared_names": declared_names,
        "ordered_dbs": ordered_dbs,
        "extra_bib_paths": extra_bib_paths,
        "missing_bib_dbs": missing_bib_dbs,
    }


def discover_manuscript_bib_files(
    manuscript_paths: list[pathlib.Path] | None = None,
    aux_path: pathlib.Path | None = None,
) -> list[pathlib.Path]:
    """Resolve EVERY existing `.bib` file declared by a manuscript tree (or `.aux`) even when `--bib` is omitted."""
    import pg_tex_ast

    declared_names: list[str] = []
    if aux_path is not None and aux_path.is_file():
        aux_info = parse_tex_aux_file(aux_path)
        for bd in aux_info.get("bibdata", []):
            st_bd = str(bd).strip()
            if st_bd and st_bd not in declared_names:
                declared_names.append(st_bd)

    ms_list = [p for p in (manuscript_paths or []) if p is not None and p.is_file()]
    search_dirs: list[pathlib.Path] = []
    for sp in ms_list:
        if sp.parent not in search_dirs:
            search_dirs.append(sp.parent)
    if aux_path is not None and aux_path.parent not in search_dirs:
        search_dirs.append(aux_path.parent)

    for idx, sp in enumerate(ms_list):
        if sp.suffix.lower() in _MD_SUFFIXES_FOR_BIB:
            # Also support Markdown/Quarto YAML frontmatter `bibliography: refs.bib` or `[refs.bib, dataset.bib]`
            try:
                raw_md = sp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m_fm = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", raw_md, flags=re.S)
            if m_fm:
                for bm in re.finditer(r"^\s*bibliography\s*:\s*(.+)$", m_fm.group(1), flags=re.M):
                    val = bm.group(1).strip().strip("[]")
                    for part in val.split(","):
                        st_bd = part.strip().strip("'\"")
                        if st_bd and st_bd not in declared_names:
                            declared_names.append(st_bd)
            continue
        try:
            sp_raw, _ = pg_tex_ast.decode_tex_bytes(sp.read_bytes(), sp.resolve())
        except Exception:
            continue
        if idx == 0:
            sp_raw, _ = pg_tex_ast.truncate_after_end_document(sp_raw)
        sp_raw = pg_tex_ast.strip_iffalse_blocks(sp_raw)
        sp_clean = pg_tex_ast.mask_tex_via_ast(sp_raw, mask_comments=True, mask_verbatim=True)
        for bm in re.finditer(r"\\(?:bibliography|addbibresource)\s*\{([^}]+)\}", sp_clean):
            for part in bm.group(1).split(","):
                st_bd = part.strip()
                if st_bd and st_bd not in declared_names:
                    declared_names.append(st_bd)

    resolved: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for db_raw in declared_names:
        db_stem = db_raw[:-4] if db_raw.lower().endswith(".bib") else db_raw
        for sdir in search_dirs:
            for cand_b in (sdir / db_raw, sdir / f"{db_stem}.bib"):
                if cand_b.is_file():
                    res = cand_b.resolve()
                    if res not in seen:
                        seen.add(res)
                        resolved.append(res)
                    break
    return resolved
