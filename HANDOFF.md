# HANDOFF — scientist-workbench-corpus

For the next agent (or future-self) landing in this repo. Read after
`CLAUDE.md`. Reflects state at end of session **2026-05-23**
(post drift-sync, worklog 006).

## State

- **582 capabilities, 21 benchmark suites, 38 adapters, 9 mappings;
  SCHEMA_VERSION = 3.** Validate clean; DuckDB built.
- **V4 cross-system oracle anchors landed** for the Erf and Bessel
  families (ADR-0028 mega-bench port): `benchmarks/erf-anchor/`
  (271 cases × 4 oracles) and `benchmarks/besselj-anchor/` (1766
  cases × 5 oracles, including the new `arb` arbitrary-precision
  target). Gold-tier consensus rules baked into each suite's
  `expected.json`.
- **special-eval lane wired end-to-end.** New `special-eval-smoke`
  suite (10 cases, 6 Erf + 4 Bessel heads, 60/60 invariants green
  at float64) is the first user of the v3 `tool_flags` schema.
- **LP / SDP Phase 0 still green.** `lp-netlib` (21 cases,
  252/252 invariants), `lp-small` (29 cases, 348/348),
  `sdp-sdplib` (5/6 cases, 64/66; the one shortfall is the known
  `hinf2` primal-feasibility miss, not drift). Phase 0 baselines
  hold; no silent drift across 12 days of workbench HEAD movement.
- **Parallel candidate target** `scientist-workbench-cone-solve`
  now grades the convex-cone solver side-by-side with `lp-solve`
  on both LP suites at the 1e-6 vs 1e-8 precision-tier split
  (ADR-0037).
- **7 worklog shards.** 001 (grading tracer) · 002 (data archives) ·
  003 (wolfram-v1 ingestor) · 004 (LP tracer-bullet) · 005 (LP
  Phase 0) · 006 (drift-sync, 22 beads).

Pushed to `origin/main`. Recent commits:
- `fae868c` — worklog 006 drift-sync shard
- `15bde65` / `2dec887` / `a169a44` — besselj-anchor trio (B19)
- `5197bff` / `20cd2f8` / `6238bd1` — erf-anchor trio (B18)
- `135469a` — special-eval mappings + special-eval-smoke (B14-B16)
- `2a4dc76` — schema + DuckDB v3 amendments (B8-B12)
- `ecf7f86` — ingest_wolfram_v1 arity fix (B7) [pre-rename; script is now `ingest_wolfram_v2.ts`]
- `20e986c` — adapter reactivations + cone-solve target (B3-B6)

## What's the next agent likely doing?

Genuinely ambiguous after 22 beads of catch-up: the corpus is
roughly current with workbench HEAD. Five plausible directions,
pick by appetite:

1. **Resume workbench tool implementation.** The original
   2026-05-11 HANDOFF pointed at the LP/cone-solver epic (`eg9j`);
   that shipped on the workbench side (`tools/lp-solve`,
   `tools/cone-solve`, `tools/sdp-solve` all present). The
   *current* next workbench epic is **Gamma per-head substrate
   (ADR-0042)**, Phase 0 only — no code yet. The corpus already
   has the `tool_flags` schema to receive it.

2. **Broader wolfram-v2 arity sweep (child bead 3pu.698).** 401
   TOMLs still carry the original ingestor's hard-coded `arity=1`.
   Bessel + D + PowerMod were fixed in this session; the rest is a
   single focused mechanical pass with provenance rows per row.

3. **Extend special-eval bench coverage (seu.1, seu.2).** HankelH1,
   HankelH2, SphericalBesselJ, SphericalBesselY are admitted by
   cas-core (ADR-0041) but not yet by `tools/special-eval`'s
   ADMITTED_HEADS. **Blocked on workbench-side admission.**

4. **Wire `grade.ts` to forward `adapter.tool_flags` to
   candidates.** Today the field is informational only;
   `run-candidate.ts` hard-codes precision (e.g. `precision = 10n`
   for float64, `200n` for arb-prec). Removing the workaround is a
   small, well-scoped substrate task — no bead filed yet.

5. **Investigate `lp-solve`'s achieved_precision overclaim
   (1av.1).** Workbench-side, ~138× under-claim on NETLIB. The
   corpus verifier behaviour is correct (Rule 8); this just needs
   somebody on the workbench side to actually fix the tool.

## What's also not done (priority order)

### Open beads (this session's deferred items)

- **`bsk`** — final validate + build + commit + push (closes in
  this session, after `0a4` lands).
- **`1av.1`** (P2) — `tools/lp-solve` overclaims
  `achieved_precision` on NETLIB by ~138×. Workbench-side.
- **`3pu.698`** (P2) — 401 wolfram-v2 TOMLs with wrong arity from
  the original ingestor bug.
- **`seu.1`** (P2) — admit HankelH1 / HankelH2 into
  `tools/special-eval`. Workbench-side.
- **`seu.2`** (P2) — admit SphericalBesselJ / SphericalBesselY
  into `tools/special-eval`. Workbench-side.

### Carried over from 2026-05-11 HANDOFF (still open)

- **matlab-v1 ingestor (MEDIUM).** ~71 functions in MATLAB v1's
  HELP listing. Source on disk:
  `data/matlab-v1/raw/cleve-pc-matlab-v1.0.html`. Line-oriented,
  simpler than wolfram-v2. Ship as
  `scripts/ingest_matlab_v1.ts` mirroring the wolfram-v2 ingestor.
- **macsyma ingestor (LOWER).**
  `data/macsyma-v9/raw/MACSYMA_RefMan_V9_Dec77.pdf` (14 MB scanned
  1977 PDF). Less typeset-clean than wolfram-v2.
- **Aliases for cross-system equivalence groups (LOW).**
  `aliases/<concept>.toml` files (e.g. `aliases/determinant.toml`
  listing `mathematica-1/Det`, `matlab-1.0/det`,
  `macsyma/determinant`). Meaningful once ≥2 systems have
  populated capabilities. Mappings table currently has 9 rows;
  needs the matlab-v1 ingest first to be load-bearing.
- **Mappings sweep for the remaining ~573 capabilities.** This
  session wired 7 (Bessels + Erf + D + PowerMod) on top of the 2
  inherited; the bulk of wolfram-v2 still has no `[[mapping]]`.

### Other deferred work (older HANDOFFs)

- **v0.2 sparse wire format for the convex-cone tier** (ADR-0030
  §"Open questions #5"). CSR/COO for `A` in `Ax_eq_b`, schema
  amendment, oracle/verifier updates. Unlocks the remaining 88
  NETLIB problems (80bau3b, fit2p, ken-\*, pds-\*, osa-\*, cre-\*).
- **`benchmarks/qp-maros-meszaros`.** Phase 0 sibling for the QP
  specialist (`tools/qp-solve`, bead `psuw`). Same dual-witness
  pattern as `lp-*`.

## Quick orientation

```sh
# Bun lives at ~/.bun/bin/bun on this device — not on PATH.
# Use the ABSOLUTE PATH; snap-bun (if PATH-resolved) has a
# mount-namespace bug that breaks python3-verifier benches.
# Install via curl: curl -fsSL https://bun.sh/install | bash
BUN=~/.bun/bin/bun

# Seven commands you'll run all the time:
$BUN src/cli.ts validate                                  # JSON-Schema check every TOML
$BUN src/cli.ts list                                      # what's in the corpus
$BUN src/cli.ts build                                     # rebuild build/corpus.duckdb
$BUN src/cli.ts grade <target> <suite>                    # candidate × cases × verifier
$BUN src/cli.ts query grade-vs-corpus                     # the scoreboard
$BUN src/cli.ts query lp-bench-overview                   # per-check LP pass rates
$BUN src/cli.ts query-sql "SELECT ..."                    # ad-hoc SQL

# Special-function lanes (B14-B19):
$BUN src/cli.ts grade scientist-workbench special-eval-smoke
$BUN src/cli.ts grade scientist-workbench erf-anchor
$BUN src/cli.ts grade scientist-workbench besselj-anchor
$BUN src/cli.ts grade wolfram erf-anchor                  # per-oracle replay grading
$BUN src/cli.ts grade arb besselj-anchor                  # arb-prec lane

# LP / cone parallel candidates:
$BUN src/cli.ts grade scientist-workbench lp-netlib
$BUN src/cli.ts grade scientist-workbench-cone-solve lp-netlib

# Per-suite regeneration (LP):
python3 benchmarks/lp-netlib/golden/generate.py           # ~5 min wall, downloads 110 MPS
python3 benchmarks/lp-small/golden/generate.py            # ~30 sec, parametric
```

A Bessel/Erf dashboard query (per-head/per-tier pass rates
across the per-oracle replay adapters) would be a productive
follow-up bead — the existing `grade-vs-corpus` query lumps every
oracle into one row.

## Where the bodies are buried

### Drift-sync session (006):

- **snap-bun mount-namespace strips `/usr/lib/python3/dist-packages`.**
  Snap-confined bun (`/snap/bin/bun`, `confinement=strict`,
  `base=core22`) spawns python3 subprocesses that cannot `import
  sympy`. Workaround: install curl-bun at `~/.bun/bin/bun`
  (v1.3.14 installed 2026-05-23) and **invoke that absolute path
  explicitly** in every `bun src/cli.ts grade` invocation. `bun`
  via PATH may re-resolve to snap. Surfaced by `chg.1`; mandatory
  going forward.
- **`adapter.tool_flags` is informational only today.**
  `src/grade.ts` does not forward the field to spawned
  candidates. `run-candidate.ts` files hard-code the precision
  flags they actually need (B15 friction). Removing the
  workaround is a clean substrate task; until then, the
  precedent is "lane decision lives in run-candidate".
- **wolfram-v2 arity sweep is partial.** Only Bessel family +
  D + PowerMod were fixed this session (with bead-provenance
  rows). The other ~401 TOMLs still carry the ingestor's
  hard-coded `arity=1`. See `3pu.698`.
- **`tools/lp-solve` overclaims `achieved_precision` on NETLIB**
  (~138× under-claim vs verifier-recomputed `|x^Ts|`). The corpus
  verifier behaviour is correct per Rule 8. See `1av.1`.
- **Adapter version pin convention:** `version =
  "git@<workbench-HEAD-SHA>"`. Bump when re-grading after
  workbench updates. Common pin across reactivated adapters in
  this session: `git@9efb8d7`.
- **`CANDIDATE_TOOL` env override** still works on lp-netlib /
  lp-small adapters for the `scientist-workbench` target, but the
  cleaner pattern is the parallel target directory
  (`adapters/scientist-workbench-cone-solve/`). Use the parallel
  target for any new alt-candidate; the env override is legacy.
- **Validator cross-reference gotcha.** `src/validator.ts:50-55`
  requires every `adapter.capability_id` to match an existing
  benchmark suite. New adapters cannot validate clean until the
  matching suite lands (B14 → B15 ordering was forced by this).

### Special-function anchors (B17-B19):

- **Gold-tier consensus rule.** `expected.json` carries
  `consensus.value` only when ≥2 gold oracles (wolfram + mpmath,
  plus arb for Bessel) agree to ≥48 digits. The 8 erf cases and
  21 besselj cases that fail consensus are flagged in-band as
  T6 edge / refusal-in-scope, not silently dropped.
- **Per-oracle expected.json shape**:
  `oracles.{wolfram,mpmath,boost,scipy[,arb]}.{value,
  achieved_precision, method}` + `consensus.{gold_agree, value,
  digits_agreed, tolerance_rel}`. Replay shims read
  committed `data/<bench>-results.json` rather than re-running
  python3/wolframscript/arb at grade time.
- **Per-oracle status normaliser.** besselj-anchor's build script
  collapses 5 divergent per-oracle status taxonomies to
  `{success, refused, limit, timeout, error}` on disk.
- **scipy bronze-tier failures are expected.** float64 scipy
  fails T1 well-defined cases on `rel_err` (1e-19 vs 1e-48 gold
  tolerance). Documented in the candidate adapter TOML.

### Phase 0 LP-specific (still load-bearing):

- **Field-absence semantics for `objective` / `achieved_precision`**:
  candidate records omit these fields entirely when `status !==
  "optimal"`. JSON.parse rejects raw `Infinity` tokens; widening
  `number → number | "Infinity"` widens every TS consumer. Both
  oracle adapters, the generator, and the bridge all conform.
- **`s = c − Aᵀy` formula in both oracles**: Mosek's basic solution
  doesn't expose `snx`; Gurobi has `RC`. Computing from the
  stationarity equation directly removes per-vendor sign-convention
  drift and uses one formula on both sides.
- **`basis_tol_s = 1e-9` in Mosek**: Mosek's default 1e-7 fails the
  verifier's 1e-8 `dual_feasibility` check on multiple NETLIB
  problems. Tightened at the adapter, not the verifier.
- **`self_reported_precision` 2× slack** in `verify.ts`: float64
  summation-order between Python `sum()` and JS loop accumulator
  introduces ~1.5-2× drift at the 1e-13 machine-precision floor.
  Slack catches order-of-magnitude lies (CLAUDE.md Rule 8) while
  tolerating bit-noise.
- **DENSE_LIMIT = 100,000 entries** in `lp-netlib/golden/generate.py`:
  21 of 109 fetchable NETLIB problems fit. Cases above are *skipped*
  (not stored sparsely; canonical wire is dense per ADR-0030 §C).
  Raw .mps files stay in `data/lp-netlib/raw/` (gitignored) for
  v0.2 regeneration.

### Earlier bodies (still load-bearing):

- **Two TOML escape hazards** patched in 003: control chars (U+0002
  in Quit), backslash-hash (`\#` in Splice). Description block uses
  literal triple-quote `'''…'''` to neutralise the second class.
- **Page-header bug**: ~half the per-function PDFs have a running
  header that pdftotext puts as the first line. The B.8 index name
  is authoritative; never trust the PDF's first line on a fresh
  ingest.
- **Snap-Bun mount-namespace** (inherited from workbench ADR-0001;
  reconfirmed and extended by chg.1 this session): `cmd === "bun"`
  resolves via `process.execPath` in `src/grade.ts`. Don't replace
  with raw `Bun.spawn(["bun", ...])`.
- **Wayback for Cleve's Corner**: `blogs.mathworks.com` 403s scripted
  clients. WebFetch and `curl` both fail; Wayback succeeds.
- **Wolfram legacy URL is "v1" but the PDFs are 2nd-edition (1991).**
  Renamed to `wolfram-v2` throughout the corpus (bead 9mz) so the
  in-corpus label reflects the content's documentation edition;
  the public URL source is still labelled `v1` and is preserved in
  one line of `data/wolfram-v2/MANIFEST.toml` for grep-ability.

## Sister repos

- `../scientist-workbench/` — the candidate-implementation repo.
  As of 2026-05-23 the workbench LP epic `eg9j` has shipped
  (`lp-solve`, `cone-solve`, `sdp-solve` all present), and the
  v0.2 special-function arc (Erf + Bessel, partial Gamma) is
  also in. Active workbench epic going forward is **Gamma
  per-head substrate (ADR-0042)** — Phase 0 only at session
  close. Workbench worklog shards 089-173 are the source of
  drift that this session absorbed.
- `../tstournament/` — origin of the brutal-and-punishing
  golden-master protocol the corpus inherits.

## Beads

Beads is in use in this repo as of 2026-05-23
(`bd init` committed at `d46e299`; tracker prefix
`scientist-workbench-corpus-<hash>`).

- `bd ready` — next actionable beads (no open blockers).
- `bd list --status=open` — everything open.
- `bd show <id>` — detail.
- `bd remember "..."` — persistent knowledge; supersedes
  `MEMORY.md` for new institutional facts.
- The bd hooks committed at `d46e299` auto-call `bd prime` at
  session start; no need to read MEMORY.md by hand any more.

5 open beads at session close (see "What's also not done" above).

Memory at
`~/.claude/projects/-home-tobias-Projects-scientist-workbench-corpus/memory/`
still has the legacy shard set: `sibling_repos.md`,
`data_layout.md`, `feedback_long_running_progress.md`,
`env_bun_path.md`, `project_status.md`. `MEMORY.md` is the
index; `bd remember` is the new write path.
