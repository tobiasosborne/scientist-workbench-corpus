# HANDOFF — scientist-workbench-corpus

For the next agent (or future-self) landing in this repo. Read after
`CLAUDE.md`. Reflects state at end of session 2026-05-06.

## State

- **582 capabilities, 1 benchmark suite, 1 adapter** validate clean;
  DuckDB built. Cross-system intersection has a real row pair
  (Eigenvalues ↔ eig both → linalg-eigh).
- **3 ground-truth archives committed** (`data/wolfram-v1/`,
  `data/matlab-v1/`, `data/macsyma-v9/`), 64 MB total. Per-source
  `MANIFEST.toml` + `sha256.sum` ledgers, all verify.
- **1 working ingestor** at `scripts/ingest_wolfram_v1.ts` with
  single + bulk modes, round-trip preservation, 20-category
  auto-classification.
- **3 worklog shards**: 001 (grading tracer), 002 (data archives),
  003 (wolfram-v1 ingestor).

The repo has been pushed to `origin/main` at the end of this session.

## What's not done (in roughly priority order)

The README's roadmap and the workbench's bead `scientist-workbench-rll`
together name the remaining work. None of it is started.

### 1. Bench migration from scientist-workbench (HIGH — endorsed by rll)

The closing commit of `scientist-workbench-rll` says: *"Future
bench/<tool>/ work migrates into benchmarks/<tool>/ in the corpus
repo."* Six benches in `../scientist-workbench/bench/` are waiting:

- `linalg-qr` — closest sibling to `linalg-eigh`; smallest port.
- `linalg-svd` — dual-algorithm (Jacobi + Golub-Reinsch); 56 cases.
- `linalg-solve` — first numerical-tier tool.
- `integrate-ode-ivp` — Dormand-Prince 5(4); first time the corpus
  adapter handles trajectory I/O.
- `integrate-ode-stiff` — Radau-IIA; Jacobian bookkeeping.
- `integrate-ode-symplectic` — Verlet/Yoshida; energy-drift invariants.

Each migration: copy `bench/<tool>/{golden,manifest}` → `benchmarks/<tool>/`,
port the Python verifier to `verify.ts`, write the adapter
`adapters/scientist-workbench/<tool>.toml`, validate, build, grade.
Worklog 001's tracer-bullet for `linalg-eigh` is the template.

Track each migration with a closing-out `[[mapping]]` against the
relevant capabilities (matlab-v1/qr, mathematica-1/QRDecomposition,
etc.) once the suite is in.

### 2. Mappings for the existing 581 capabilities (MEDIUM)

The bulk ingest set `[[mapping]]` only for `Eigenvalues`. The
load-bearing intersections waiting to be wired:

- `Det` ↔ matlab-v1/det (no workbench tool yet)
- `Inverse` ↔ matlab-v1/inv (no workbench tool yet)
- `Sum` ↔ no current workbench tool, but a future symbolic summation
- `Integrate` ↔ workbench `integrate-1d` (numerical) — partial
- `D` ↔ workbench `cas-diff` — partial (vocab-bounded)
- `Solve` ↔ workbench `linalg-solve` — partial (linear-only)
- `BesselJ`, `Gamma`, etc. — no workbench tool yet; alias-only

Every mapping is a one-liner CLI invocation:

```sh
bun scripts/ingest_wolfram_v1.ts --name Integrate \
    --map-tool integrate-1d --map-status partial \
    --map-notes 'Mathematica is symbolic; integrate-1d is numerical Gauss-Kronrod.'
```

The round-trip preserves these across re-bulks.

### 3. matlab-v1 ingestor (MEDIUM — README roadmap calls it out as the smallest)

~71 functions in MATLAB v1's HELP listing. Source already on disk:
`data/matlab-v1/raw/cleve-pc-matlab-v1.0.html` (190 KB, the listing is
inline). Pattern is line-oriented (`<command-name>  short description`),
much simpler to parse than the wolfram-v1 PDFs. Should ship as
`scripts/ingest_matlab_v1.ts` mirroring the wolfram-v1 ingestor's
structure — round-trip preservation, opt-in category, etc.

After matlab-v1 ingests, the cross-system intersection query
(`SELECT name, COUNT(DISTINCT system) AS n_systems FROM capabilities
GROUP BY name HAVING n_systems >= 2`) will return tens of rows — every
one a planning candidate.

### 4. macsyma ingestor (LOWER — PDF text extraction is harder)

`data/macsyma-v9/raw/MACSYMA_RefMan_V9_Dec77.pdf` is 14 MB scanned PDF
from 1977. Less typeset-clean than wolfram-v1; expect more font
artifacts. Defer until matlab-v1 done and the cross-system
intersection-of-2 work has surfaced what we actually need from
macsyma.

### 5. Aliases (LOW — needs ≥2 systems first)

`aliases/<concept>.toml` files grouping cross-system equivalents
(e.g. `aliases/determinant.toml` listing `mathematica-1/Det`,
`matlab-1.0/det`, `macsyma/determinant`). Only meaningful once two or
three systems have populated capabilities to intersect. README
roadmap mentions this as a Phase-2 item.

### 6. Quality polish on the ingestor (LOW)

- 177/581 capabilities are `"uncategorized"` — most are graphics
  options and obscure format names. Expand the category map in
  `scripts/ingest_wolfram_v1.ts:CATEGORIES` if any downstream wants
  them.
- 132/581 signatures are `any → any` — same population. Could try
  more verb forms or a structural pass over the description.
- Font artifacts in description text (`R ` for ∫, `P ` for Σ, `j…j`
  for `|…|`, `@@x` for `∂/∂x`) — described in worklog 003. Cosmetic
  only; description is informational, not contract.

## Quick orientation

```sh
# Seven commands you'll run all the time:
PATH=/home/tobias/.amp/bin:$PATH bun src/cli.ts validate              # JSON-Schema check
PATH=/home/tobias/.amp/bin:$PATH bun src/cli.ts list                  # what's in the corpus
PATH=/home/tobias/.amp/bin:$PATH bun src/cli.ts build                 # rebuild build/corpus.duckdb
PATH=/home/tobias/.amp/bin:$PATH bun src/cli.ts query grade-vs-corpus # scoreboard
PATH=/home/tobias/.amp/bin:$PATH bun src/cli.ts query-sql "..."       # ad-hoc SQL
PATH=/home/tobias/.amp/bin:$PATH bun scripts/ingest_wolfram_v1.ts --name <Function>  # one capability
PATH=/home/tobias/.amp/bin:$PATH bun scripts/ingest_wolfram_v1.ts --bulk             # all 581
```

`bun` lives at `/home/tobias/.amp/bin/bun` on this device — not on the
default PATH. Memory entry `env_bun_path.md` has the detail.

## Where the bodies are buried

- **Two TOML escape hazards** patched in 003: control chars (U+0002 in
  Quit), backslash-hash (`\#` in Splice). Description block uses
  literal triple-quote `'''…'''` to neutralise the second class.
- **Page-header bug**: ~half the per-function PDFs have a running
  header that pdftotext puts as the first line. The B.8 index name is
  authoritative; never trust the PDF's first line on a fresh ingest.
- **Snap-Bun mount-namespace** (inherited from workbench ADR-0001):
  `cmd === "bun"` resolves via `process.execPath` in `src/grade.ts`.
  Don't replace with raw `Bun.spawn(["bun", ...])`.
- **Wayback for Cleve's Corner**: `blogs.mathworks.com` 403s scripted
  clients. WebFetch and `curl` both fail; Wayback succeeds. The
  matlab-v1 archives reflect this.
- **Wolfram legacy URL is "v1" but the PDFs are 2nd-edition (1991).**
  Documented in `data/wolfram-v1/MANIFEST.toml` description.

## Sister repos

- `../scientist-workbench/` — the candidate-implementation repo. Bead
  `scientist-workbench-rll` is closed and references this corpus by
  path. Bead `scientist-workbench-71f` (linalg-decompose qr/svd/eig
  epic) is open and gets its eig-leg closed by this repo's tracer.
- `../tstournament/` — the origin of the brutal-and-punishing
  golden-master protocol the corpus inherits.

Memory at
`~/.claude/projects/-home-tobias-Projects-scientist-workbench-corpus/memory/`
has the full set: `sibling_repos.md`, `data_layout.md`,
`feedback_long_running_progress.md`, `env_bun_path.md`,
`project_status.md`. Read `MEMORY.md` index first.
