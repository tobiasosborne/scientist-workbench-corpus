# 003 — wolfram-v1 ingestor end-to-end (2026-05-06)

## Context

Shard 002 landed the bytes; this shard turns them into capabilities.
The first ingestor in the corpus, smoke-tested on `Eigenvalues`
(deliberately picked as the alias-mate of the existing `matlab-v1/eig`
tracer), then hardened against six surfaced frictions, then bulk-run
across all 581 v1-Mathematica entries from Appendix B.8 of the
2nd-edition reference.

End state: **582 capabilities validate** (581 mathematica + 1 matlab),
DuckDB rebuilt, the cross-system intersection query is now
non-trivial — `mathematica-1/Eigenvalues` and `matlab-1.0/eig` both
map to scientist-workbench's `linalg-eigh`, which is the planning
surface the README's roadmap called out as the priority queue for
the workbench's tool catalog.

## What changed

- **`scripts/ingest_wolfram_v1.ts`** (~340 lines, two CLI modes):
  - `--name <Function>` or `--pdf <listN.pdf>` for one-off ingest;
  - `--bulk [--max-n N]` to iterate the full B.8 index.
  - Resolution order for hand-fields: explicit CLI flag > existing
    TOML > heuristic (categorize) > default. CLI = override; existing
    = round-trip preserve; heuristic = bulk auto-categorisation.
  - Deterministic font-artifact patches in description body
    (control-char scrub, `:::` → `…`, `n × n` matrix recovery,
    `Name X]` → `Name[X]`).
  - 20-category lookup table (~470 explicit name matches + 2 regex
    patterns) for auto-categorisation during bulk.
- **`capabilities/wolfram-v1/`** — 581 TOMLs, one per v1-Mathematica
  built-in object.
- **One hand-mapping** wired post-bulk:
  `mathematica-1/Eigenvalues` → `scientist-workbench/linalg-eigh`,
  matching the existing `matlab-1.0/eig` mapping.

## Why these choices

- **Pipeline = pdftotext → parse → emit.** The Wolfram legacy PDFs are
  typeset (not scanned), so `pdftotext -layout` gives clean prose plus
  predictable artifacts — no OCR, no LLM, no judgment per-function.

- **Round-trip preservation** is the load-bearing decision for an
  ingestor against an evolving repo. Auto fields (`[meta]`,
  `[signature]`, `[description]`, primary `[[provenance]]` row) get
  fully regenerated from the PDF on every run; hand fields
  (`[category]`, `[[mapping]]`, `[verification]`, additional
  `[[provenance]]` rows) are read back from the existing TOML and
  preserved. The "auto" provenance row is identified by source_url
  matching `legacy/v1/contents/list*.pdf`; everything else is hand.

- **Three-tier hand-field resolution** makes both modes work. In
  single mode with CLI flags, the CLI wins (active override). In
  re-run with no flags, existing wins (round-trip preserve). In bulk
  ingest of a fresh capability with no existing TOML, the heuristic
  wins. Default `"uncategorized"` only when all three miss.

- **20 categories, 470 explicit matches.** Hand-curated taxonomy
  rather than LLM categorisation — every choice is reviewable, and
  the table is editable in one place if it's wrong. Match by exact
  name (e.g. `Eigenvalues` → linalg) plus regex patterns for two
  programmatic groups (`^\$/` for system globals, `^Character` for
  strings).

- **Literal triple-quote `'''…'''` for description.md, not basic.**
  TOML basic strings process backslash escapes, but PDF descriptions
  may contain literal `\#` or other non-TOML escape sequences (Splice
  was the trip-wire). Literal strings don't process escapes.

## Frictions surfaced (six structural, two TOML hazards)

The smoke test surfaced six structural frictions explicitly enumerated
to the user before bulk; all six were addressed before the bulk run.
Two further TOML hazards surfaced *during* the bulk and were patched
in-line.

| # | Friction | Fix |
|---|---|---|
| 1 | Page-header line poisons `meta.name` for some PDFs | Strip running header lines (`Listing of Built-in Mathematica Objects …`); use B.8 index name as authoritative |
| 2 | Hand-coded `[[mapping]]` in single mode | Default empty; opt-in via `--map-tool / --map-status / --map-notes` |
| 3 | Hand-coded `[category]` in single mode | Default `"uncategorized"`; opt-in via `--category-primary / --category-secondary` |
| 4 | Re-ingestion clobbers hand edits | Round-trip merge: parse existing TOML, preserve hand fields |
| 5 | `×` becomes whitespace in `n n matrix` | Regex on single-letter dim pairs |
| 6 | No bulk mode + no name-resolution | Parse B.8.html → name ↔ filename index; `--bulk` iterates it |
| 7 | Control character (U+0002) in Quit description | Scrub U+0000..U+001F (except tab) and U+007F before emit |
| 8 | Backslash-hash `\#` in Splice description not a TOML escape | Switch description block to literal triple-quote `'''…'''` |

Resolution-order bug surfaced mid-test: my first round-trip rule was
`existing > CLI > default`, but that meant CLI `--category-primary
linalg` got ignored if the existing TOML already had
`"uncategorized"`. Flipped to `CLI > existing > heuristic > default` —
explicit CLI flag is an override signal; no flag is preserve.
Verified by the four-step Eigenvalues sequence (fresh → override →
confirm overridden → no-flag re-run preserves linalg).

Signature-extraction recall jumped from 36% → 77% by widening the
verb list. First version only matched `Name[args] (gives|returns|
finds|yields) <result>.`; v1 functions also open with
`is|represents|specifies|sets|tests|applies|prints|reads|writes|
opens|closes`. Plus a bare-name fallback (`Name (verb) <result>`)
catches constants and options like `Pi`, `True`, `AspectRatio`. Final:
304/581 bracketed, 145/581 bare-name, 132/581 honestly `any → any`.

Font artifacts that *don't* get patched:
- `R ` (uppercase R glyph) for `∫` (integral) — context-dependent;
  `R` is also a legitimate identifier.
- `P ` for `Σ` (summation) — same reason.
- `j…j` for `|…|` (absolute value) — `j` is a legitimate identifier.
- `@@x` for `∂/∂x` (partial deriv) and similar.
- `xn;1` for `x^(n−1)` and similar exponent collapses.

These appear in display-equation regions of descriptions. Living
with them: `description.md` is *informational*, not contract surface
(the canonical name lives in `meta.name`, the signature in
`[signature]`, the bytes in `[[provenance]]`). A future LLM-cleaning
pass could fix them; not load-bearing now.

## Acceptance

```sh
$ time bun scripts/ingest_wolfram_v1.ts --bulk
… [575/581] $PrePrint
bulk done. 581 processed.
real    0m14.628s

$ bun src/cli.ts validate
OK — 582 caps, 1 suites, 1 adapters.

$ bun src/cli.ts build
built build/corpus.duckdb
  {"capabilities":582,"suites":1,"adapters":1,"grade_runs":0,"grade_results":0}

$ bun src/cli.ts query-sql "
    SELECT capability_id, tool, status FROM mappings ORDER BY tool, capability_id"
capability_id              tool          status
mathematica-1/Eigenvalues  linalg-eigh   partial
matlab-1.0/eig             linalg-eigh   partial
```

Category distribution across the 581 mathematica entries:

| primary | n |   | primary | n |
|---|---|---|---|---|
| uncategorized | 177 |   | system | 21 |
| graphics | 43 |   | patterns | 19 |
| special-functions | 34 |   | linalg | 18 |
| structure | 29 |   | rules | 17 |
| logic | 29 |   | io | 17 |
| discrete | 29 |   | constants | 15 |
| lists | 27 |   | calculus | 14 |
| algebra | 26 |   | assignment | 10 |
| trigonometric | 24 |   | numerical | 9 |
| | |   | arithmetic | 9 |
| | |   | strings | 8 |
| | |   | complex | 6 |

177 uncategorized = 30% — most are graphics options, format names
(`CForm`, `FortranForm`, `TeXForm`), and obscure system bits that
don't fit any of the 20 buckets. Cheap to expand the table iteratively
when a downstream consumer needs them.

## Pointers

- `scripts/ingest_wolfram_v1.ts` — the ingestor itself; literate
  comments describe the round-trip discipline and font-artifact
  patches.
- `capabilities/wolfram-v1/Eigenvalues.toml` — the load-bearing
  alias-mate, hand-mapped to `linalg-eigh`.
- `capabilities/matlab-v1/eig.toml` — the corresponding matlab-v1
  side.
- `data/wolfram-v1/raw/contents/B.8.html` — the master function index
  (581 entries).
- `data/wolfram-v1/raw/contents/list136.pdf` — the Eigenvalues source.
- shard 001 (`001-tracer-bullet.md`) — the grading-side tracer; this
  shard is the ingestion-side tracer.
