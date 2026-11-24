---
name: paperguard-lite
description: >-
  Read-only pre-submission academic manuscript auditor for LaTeX (.tex/.bib) and
  Markdown papers. Detects citation identity conflicts, unresolved DOIs, dangling
  citation keys, and LLM prose clichés, then renders an accessible local HTML
  diagnostic cockpit without modifying user files. Activate when the user asks
  to audit citations, scan AI clichés, or run a pre-flight check on a paper.
---

# PaperGuard Lite — Pre-Submission Read-Only Triage

Execute this single-pass read-only audit on the author's `.tex` / `.md` manuscript and `.bib` bibliography.

## Safety & Data-Path Contract
- **Strictly Read-Only**: Never edit or overwrite the author's `.tex`, `.md`, or `.bib` files. Every CLI script blocks output paths that collide with input files.
- **Transparent Data Paths**:
  1. `scan_ai_slop_lite.py`, `audit_bib_lite.py --offline`, and `render_audit_ui.py` (which defaults to **offline mode** even with `--bib`!) run 100% locally with zero outbound network requests.
  2. Online `audit_bib_lite.py` (or `render_audit_ui.py --bib ... --online`) queries public Crossref, DataCite, OpenAlex (CC0 retraction & errata guard), DBLP, and arXiv endpoints sending **only** `.bib` identifiers/titles/authors (never manuscript body prose). Pass `--offline` for air-gapped environments (and optional `--aux main.aux` to merge TeX-expanded `\citation` keys).
  3. Summarizing results through your IDE assistant follows your own IDE/model configuration.

## Step 1: Run Citation & Prose Diagnostics Once (Export JSON)
Run each check once and save JSON to temporary report files so UI rendering requires zero duplicate network queries:

```bash
python3 ./scripts/audit_bib_lite.py <path/to/references.bib> \
  --manuscript <path/to/paper.tex> \
  --json .pg_bib_lite.json

python3 ./scripts/scan_ai_slop_lite.py <path/to/paper.tex> \
  --json .pg_slop_lite.json
```
*(Add `--offline` to `audit_bib_lite.py` if offline; typical local runtime is `< 1s` offline, or ~`0.15s` per online entry.)*

## Step 2: Render the Accessible Editorial Diagnostic Cockpit
Pass the precomputed JSON reports directly into `render_audit_ui.py` (instant local build, zero network calls):

```bash
python3 ./scripts/render_audit_ui.py \
  --manuscript <path/to/paper.tex> \
  --bib-json .pg_bib_lite.json \
  --slop-json .pg_slop_lite.json \
  --out paperguard_audit.html
```

## Step 3: Present Actionable Triage Summary
1. **Citation Findings**: Separate confirmed `IDENTITY_CONFLICT` / `DANGLING_CITATION` errors from `UNVERIFIED` / `LOOKUP_FAILED` entries (stating exact verification coverage).
2. **Prose Style Hotspots**: Report high-cliché lines with concrete edit suggestions (clarifying that the Style Density Index measures cliché frequency, not authorship).
3. **Interactive Cockpit**: Link `paperguard_audit.html` (`j`/`k` navigates visible findings, `d` switches Paper/Obsidian theme).
