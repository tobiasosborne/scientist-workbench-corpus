# HANDOFF — scientist-workbench-corpus

For the next agent (or future-self) landing in this repo. Read after
`CLAUDE.md`. Reflects state at end of session **2026-05-23**
(post gamma-sync, worklog 007 — same-day follow-up to 006 drift-sync).

## State

- **582 capabilities, 22 benchmark suites, 44 adapters, 17 mappings;
  SCHEMA_VERSION = 3.** Validate clean; DuckDB built.
- **V4 cross-system oracle anchors landed for three special-function
  families.** `benchmarks/erf-anchor/` (271 cases × 4 oracles, B18),
  `benchmarks/besselj-anchor/` (1766 cases × 5 oracles incl. `arb`
  arbitrary-precision, B19), and the gamma-sync addition
  `benchmarks/gamma-anchor/` (377 cases × 5 oracles × 19 heads, G6).
  Gold-tier consensus rules baked into each suite's `expected.json`.
  The per-head substrate pattern (one anchor per head-family) is now
  fully exercised on the corpus side.
- **special-eval lane wired end-to-end.** `special-eval-smoke`
  (10 cases, 6 Erf + 4 Bessel heads, 60/60 invariants green at
  float64) is the first user of the v3 `tool_flags` schema. The
  three -anchor suites use the same schema.
- **17 mappings** now cover the Bessel family (B-, K, I, Y, J),
  Erf, D, PowerMod, and the Gamma family (Gamma, Beta, Pochhammer,
  PolyGamma single-head + IncompleteGammaUpper/Lower on Gamma.toml +
  Digamma/Trigamma on PolyGamma.toml as multi-head flags).
- **LP / SDP Phase 0 still green.** `lp-netlib` (21 cases,
  252/252 invariants), `lp-small` (29 cases, 348/348),
  `sdp-sdplib` (5/6 cases, 64/66; the one shortfall is the known
  `hinf2` primal-feasibility miss, not drift).
- **Parallel candidate target** `scientist-workbench-cone-solve`
  grades the convex-cone solver side-by-side with `lp-solve`
  on both LP suites at the 1e-6 vs 1e-8 precision-tier split
  (ADR-0037).
- **9 worklog shards.** 001 (grading tracer) · 002 (data archives) ·
  003 (wolfram-v2 ingestor) · 004 (LP tracer-bullet) · 005 (LP
  Phase 0) · 006 (drift-sync, 22 beads) · 007 (gamma-sync, 9 beads) ·
  008 (coverage-shape post-gamma analysis).

Pushed to `origin/main`. Recent commits:
- `4278a43` — worklog 007 gamma-sync shard
- `c74bcfa` / `bfe037f` / `570bd71` — gamma-anchor trio (G6)
- `5ac4aaf` / `554cb5a` — multi-head + cheap-win mappings (G4, G2)
- `7d3ec45` — Beta + Pochhammer arity 1 → 2 (G1)
- `658bcd5` / `478a77c` — wolfram-v1 → wolfram-v2 honest rename (9mz)
- `26cc1b7` — HANDOFF rewrite post drift-sync
- `fae868c` — worklog 006 drift-sync shard
- `15bde65` / `2dec887` / `a169a44` — besselj-anchor trio (B19)
- `5197bff` / `20cd2f8` / `6238bd1` — erf-anchor trio (B18)

## What's the next agent likely doing?

Genuinely ambiguous after 31 beads across two same-day sessions: the
corpus is current with workbench HEAD `af1baa3` (advanced from
`9efb8d7` mid-gamma-sync per user pull). Five plausible directions,
pick by appetite:

1. **FLINT integration epic (bead `jly`, P3, multi-session).** Filed
   2026-05-23 mid-gamma-sync per user request: "compare everything
   against FLINT as well at some point". Scope: poly-factor, mod-pow,
   linsolve-q, groebner-basis (via msolve), special-functions (via
   `acb_hypgeom`). Partially-done already because FLINT 3+ absorbed
   Arb as a module and Arb is the besselj/gamma anchor arb-prec
   oracle. Largest forward direction; deserves a dedicated session.

2. **Resume workbench tool implementation.** The Gamma per-head
   substrate epic (ADR-0042) shipped during gamma-sync
   (`tools/special-eval` ADMITTED_HEADS grew 12 → 28). Next workbench
   epic per `docs/CATALOG.md` (ADR-0043) is open; check the registry
   first.

3. **Broader wolfram-v2 arity sweep (child bead `698`).** Now ~½ done
   piecemeal: BesselJ/Y/I/K + D + PowerMod (B7) + Beta + Pochhammer
   (G1) — 8 fixed, ~393 TOMLs remain. A single mechanical pass with
   provenance rows per row would close it.

4. **Workbench-side ADMITTED_HEADS expansion** for the 4 gamma-anchor
   heads currently yielding honest `unknown-head`: `GammaPDerivative`,
   `IncompleteBeta`, `InverseIncompleteGammaP`,
   `InverseIncompleteGammaQ` (22 of 377 inputs). No corpus bead
   (Rule 8). Sister to `seu.1`/`seu.2`.

5. **Wire `grade.ts` to forward `adapter.tool_flags` to candidates**
   and/or **fix `lp-solve`'s achieved_precision overclaim (`1av.1`).**
   Both small substrate tasks; the first is unfiled, the second is
   workbench-side.

6. **Cluster cheap-wins session (recommended highest-leverage).**
   Per worklog 008's post-gamma coverage analysis: linalg + algebra +
   discrete + constants clusters carry ~32 high-confidence mapping
   candidates (Inverse, LinearSolve, Det, Factor, Solve, GCD, LCM,
   Pi, E, ...) where workbench tools exist and v2 has the canonical
   names. Wiring these alone would lift in-scope coverage from
   ~7% → ~26%, faster and at lower complexity than another per-head
   epic. **Apply G3 discipline:** file a recon bead per cluster
   confirming each candidate's v2-presence via `data/wolfram-v2/raw/contents/B.8.html`
   before assuming any single estimate.

## What's also not done (priority order)

### Open beads at session close

- **`1av.1`** (P2) — `tools/lp-solve` overclaims `achieved_precision`
  on NETLIB by ~138×. Workbench-side.
- **`698`** (P2) — ~393 wolfram-v2 TOMLs still hold ingestor `arity=1`.
  8 fixed across two sessions (BesselJ/Y/I/K + D + PowerMod + Beta +
  Pochhammer).
- **`seu.1`** (P2) — admit HankelH1 / HankelH2 into
  `tools/special-eval`. Workbench-side.
- **`seu.2`** (P2) — admit SphericalBesselJ / SphericalBesselY into
  `tools/special-eval`. Workbench-side.
- **`jly`** (P3) — FLINT integration epic. Multi-session scope; see
  direction (1) above.

### Workbench-side follow-ups from gamma-sync (no corpus bead)

The four gamma-anchor heads above (`GammaPDerivative`, `IncompleteBeta`,
`InverseIncompleteGammaP`, `InverseIncompleteGammaQ`) are admitted via
`refusal_scope_honest` today. Future workbench-side ADMITTED_HEADS
expansion target; no corpus bead filed because the corpus side is
already correct.

### Out-of-corpus (noted for clarity, no action needed)

8 Gamma-family head names (LogGamma, LogBeta, IncompleteGammaP/Q,
BarnesG, Hyperfactorial, GammaRatio, GammaDeltaRatio) are
workbench-internal naming or post-v2 functions. **Correctly absent
from `capabilities/wolfram-v2/`** — they're not standalone Mathematica
v2 names. If a future Mathematica edition (v3+) ingest lands, these
may gain TOMLs organically.

### Carried over from older HANDOFFs (still open)

- **matlab-v1 ingestor (MEDIUM).** ~71 functions in
  `data/matlab-v1/raw/cleve-pc-matlab-v1.0.html`. Line-oriented,
  simpler than wolfram-v2. Ship as `scripts/ingest_matlab_v1.ts`.
- **macsyma ingestor (LOWER).**
  `data/macsyma-v9/raw/MACSYMA_RefMan_V9_Dec77.pdf` (14 MB scanned
  1977 PDF). Less typeset-clean than wolfram-v2.
- **Aliases for cross-system equivalence groups (LOW).**
  `aliases/<concept>.toml`. Meaningful once ≥2 systems have populated
  capabilities; needs matlab-v1 ingest first to be load-bearing.
- **v0.2 sparse wire format for the convex-cone tier** (ADR-0030
  §"Open questions #5"). Unlocks the remaining 88 NETLIB problems.
- **`benchmarks/qp-maros-meszaros`.** Phase 0 sibling for `tools/qp-solve`
  (bead `psuw`).

## Quick orientation

```sh
# Bun lives at ~/.bun/bin/bun — NOT on PATH. snap-bun (PATH-resolved)
# has a mount-namespace bug that breaks python3-verifier benches.
# Install: curl -fsSL https://bun.sh/install | bash
BUN=~/.bun/bin/bun

# Core commands:
$BUN src/cli.ts validate                                  # every TOML against schema
$BUN src/cli.ts list / build / grade <target> <suite>
$BUN src/cli.ts query grade-vs-corpus | lp-bench-overview
$BUN src/cli.ts query-sql "SELECT ..."

# Special-function lanes (B14-B19 + G6):
$BUN src/cli.ts grade scientist-workbench special-eval-smoke
$BUN src/cli.ts grade scientist-workbench erf-anchor
$BUN src/cli.ts grade scientist-workbench besselj-anchor
$BUN src/cli.ts grade scientist-workbench gamma-anchor    # 22/377 expected refused
$BUN src/cli.ts grade wolfram erf-anchor                  # per-oracle replay
$BUN src/cli.ts grade arb besselj-anchor                  # arb-prec lane

# LP / cone parallel candidates:
$BUN src/cli.ts grade scientist-workbench lp-netlib
$BUN src/cli.ts grade scientist-workbench-cone-solve lp-netlib

# Regenerate LP goldens:
python3 benchmarks/lp-netlib/golden/generate.py           # ~5 min, 110 MPS
python3 benchmarks/lp-small/golden/generate.py            # ~30 sec, parametric

# Mapping-head distribution (handy for the corpus dashboard):
$BUN src/cli.ts query-sql \
  "SELECT dispatch_head, count(*) FROM mappings GROUP BY dispatch_head ORDER BY 2 DESC"
```

A per-head/per-tier dashboard query across the three anchor suites
would be a productive follow-up bead — `grade-vs-corpus` lumps every
oracle and head into one row.

## Where the bodies are buried

### Gamma-sync session (007):

- **G3 honesty-first recon overturned D2's 6-8 TOMLs projection.**
  D2's drift catalogue estimated 6-8 of 12 "C-minus" Gamma-family
  heads would land as new wolfram-v2 TOMLs. G3 cross-checked against
  `data/wolfram-v2/raw/B.8.html` (the canonical 581-function v2
  index) and found **none** of the 12 are standalone Mathematica v2
  names. Four (IncompleteGammaUpper/Lower, Digamma, Trigamma) get
  expressed as multi-head mapping flags on existing parent TOMLs
  (Gamma + PolyGamma); the other 8 are out-of-corpus. **Pattern:
  file a recon bead BEFORE any ingest bead whose scope is derived
  from a drift catalogue.** Otherwise you risk the fundingscape
  anti-pattern (fabricated stub TOMLs). TOML-is-truth.
- **gamma-anchor admits 4 heads workbench does NOT yet ADMIT.**
  `GammaPDerivative`, `IncompleteBeta`, `InverseIncompleteGammaP`,
  `InverseIncompleteGammaQ` (22 of 377 inputs) yield honest
  `unknown-head` refusals from the candidate. The verifier's
  `refusal_scope_honest` admits this. **Don't be alarmed when
  grading shows "22 cases out of 377 with unknown-head" — it's by
  design, not a regression.**
- **Corpus capitalisation can differ from workbench's.** `PolyGamma`
  (corpus, mirroring Mathematica's spelling) vs `Polygamma`
  (workbench dispatch head, lower-case `g`). G2's mapping uses the
  workbench spelling in `flags.head`. **Pattern: capability NAMES
  preserve source spelling; mapping FLAGS use workbench's dispatch
  spelling.** Documented in the mapping's `notes` field rather than
  renaming either side.
- **Workbench `@workbench/bigfloat` import had a 6.4s → 0.07s perf
  bug fixed in workbench shard 178 (bead `eoei`).** Any corpus
  consumer should pin workbench >= `af1baa3` (or accept slow imports
  on older SHAs). Surfaced under the gamma-anchor candidate smoke.
- **`landmine_flags: string[]` in gamma-anchor `expected.json`
  case shape.** Computed in the build script from `corpus.json`
  notes + per-head/tier rules (L17 pole detection, L13 single-wolfram
  fallback, L14 Polygamma/Trigamma T4, L16 BarnesG, L18 Digamma at
  odd-negative-int half-integers). Drives data-driven verifier
  branches rather than hardcoded tier/head checks in `verify.ts`.
  Future per-head anchors should mirror this shape.
- **`--case-id` is singular** in `grade.ts`, not `--case-ids`. Multi-
  pass for several cases during smoke. Worth noting for future bench
  smokes.

### Drift-sync session (006):

- **snap-bun mount-namespace strips `/usr/lib/python3/dist-packages`.**
  Snap-confined bun (`/snap/bin/bun`, `confinement=strict`,
  `base=core22`) spawns python3 subprocesses that cannot `import
  sympy`. Workaround: install curl-bun at `~/.bun/bin/bun`
  (v1.3.14) and **invoke that absolute path explicitly** in every
  grade invocation. PATH-resolved `bun` may re-resolve to snap.
  Surfaced by `chg.1`; mandatory.
- **`adapter.tool_flags` is informational only today.**
  `src/grade.ts` does not forward the field to spawned candidates;
  `run-candidate.ts` files hard-code precision (B15 friction).
  Until removed, the precedent is "lane decision lives in
  run-candidate".
- **wolfram-v2 arity sweep is partial.** BesselJ/Y/I/K + D + PowerMod
  (B7) and Beta + Pochhammer (G1) fixed; ~393 TOMLs remain. See `698`.
- **`tools/lp-solve` overclaims `achieved_precision` on NETLIB**
  (~138× under-claim vs verifier-recomputed `|x^Ts|`). Corpus
  verifier is correct per Rule 8. See `1av.1`.
- **Adapter version pin convention:** `version =
  "git@<workbench-HEAD-SHA>"`. Current pin post-gamma-sync:
  `git@af1baa3`. Prior through drift-sync: `git@9efb8d7`.
- **`CANDIDATE_TOOL` env override** still works on lp-netlib /
  lp-small adapters but the cleaner pattern is the parallel target
  directory (`adapters/scientist-workbench-cone-solve/`). The env
  override is legacy.
- **Validator cross-reference gotcha.** `src/validator.ts:50-55`
  requires every `adapter.capability_id` to match an existing
  benchmark suite — new adapters can't validate clean until the
  matching suite lands.

### Special-function anchors (B17-B19, G5-G6):

- **Gold-tier consensus rule.** `expected.json` carries
  `consensus.value` only when ≥2 (erf-anchor) or ≥3 strict / 2-of-3
  fallback (besselj-anchor + gamma-anchor) gold oracles agree to ≥48
  digits. Cases that fail consensus are flagged in-band as T6 edge /
  refusal-in-scope, not silently dropped.
- **Per-oracle expected.json shape:** `oracles.{wolfram,mpmath,boost,
  scipy[,arb]}.{value, achieved_precision, method}` +
  `consensus.{gold_agree, value, digits_agreed, tolerance_rel}`.
  Replay shims read committed `data/<bench>-results.json` rather
  than re-running python3/wolframscript/arb at grade time.
- **L13 single-wolfram fallback** in gamma-anchor for the 12
  `InverseIncompleteGamma{P,Q}` cases where arb + mpmath have no
  native implementation (wolfram is definitionally authoritative).
- **`input.id` carried per-case** in gamma-anchor so replay shims
  look up by case-id (cleaner than composite-key reconstruction under
  collision risk). Pattern for future anchors.
- **Per-oracle status normaliser.** besselj/gamma anchor build
  scripts collapse divergent taxonomies to
  `{success, refused, limit, timeout, error}` on disk.
- **scipy bronze-tier failures are expected.** float64 scipy fails
  T1 well-defined cases on `rel_err` (1e-17 vs 1e-48 gold).
  Structural ceiling, documented in adapter TOML, applies to all three.

### Phase 0 LP-specific (still load-bearing):

- **Field-absence semantics for `objective` / `achieved_precision`**:
  candidate records omit these fields entirely when `status !==
  "optimal"`. JSON.parse rejects raw `Infinity`; widening
  `number → number | "Infinity"` widens every TS consumer.
- **`s = c − Aᵀy` formula in both oracles**: Mosek's basic solution
  doesn't expose `snx`; Gurobi has `RC`. Computing from stationarity
  directly removes per-vendor sign-convention drift.
- **`basis_tol_s = 1e-9` in Mosek**: default 1e-7 fails the
  verifier's 1e-8 `dual_feasibility` check on multiple NETLIB
  problems. Tightened at the adapter, not the verifier.
- **`self_reported_precision` 2× slack** in `verify.ts`: float64
  summation-order between Python `sum()` and JS loop introduces
  ~1.5-2× drift at the 1e-13 machine-precision floor. Catches
  order-of-magnitude lies (Rule 8) while tolerating bit-noise.
- **DENSE_LIMIT = 100,000 entries** in `lp-netlib/golden/generate.py`:
  21 of 109 fetchable NETLIB problems fit. Cases above are skipped
  (canonical wire is dense per ADR-0030 §C). Raw .mps files stay in
  `data/lp-netlib/raw/` (gitignored) for v0.2 regeneration.

### Earlier bodies (still load-bearing):

- **Two TOML escape hazards** patched in 003: control chars (U+0002
  in Quit), backslash-hash (`\#` in Splice). Description block uses
  literal triple-quote `'''…'''` to neutralise the second class.
- **Page-header bug**: ~half the per-function PDFs have a running
  header that pdftotext puts as the first line. The B.8 index name
  is authoritative; never trust the PDF's first line on a fresh ingest.
- **Snap-Bun mount-namespace** (inherited from workbench ADR-0001;
  reconfirmed by `chg.1`): `cmd === "bun"` resolves via
  `process.execPath` in `src/grade.ts`. Don't replace with raw
  `Bun.spawn(["bun", ...])`.
- **Wayback for Cleve's Corner**: `blogs.mathworks.com` 403s scripted
  clients. WebFetch and `curl` both fail; Wayback succeeds.
- **Wolfram legacy URL is "v1" but the PDFs are 2nd-edition (1991).**
  Renamed to `wolfram-v2` throughout the corpus (bead `9mz`); the
  public URL source is still labelled `v1` and is preserved in one
  line of `data/wolfram-v2/MANIFEST.toml` for grep-ability.

## Sister repos

- `../scientist-workbench/` — the candidate-implementation repo.
  HEAD pinned to **`af1baa3`** as of 2026-05-23 (post Gamma epic
  close + ixnv registry epic). LP epic `eg9j` shipped; the v0.2
  special-function arc (Erf + Bessel + Gamma) is in. `docs/CATALOG.md`
  (ADR-0043) is now the registry single-source-of-truth for tool
  scope. Workbench worklog shards 089-182 are the source of drift
  this corpus has absorbed across worklogs 006 + 007.
- `../tstournament/` — origin of the brutal-and-punishing
  golden-master protocol the corpus inherits.

## Beads

Beads in use as of 2026-05-23 (`bd init` at `d46e299`; tracker prefix
`scientist-workbench-corpus-<hash>`). Hooks auto-call `bd prime` at
session start.

- `bd ready` / `bd list --status=open` / `bd show <id>`
- `bd remember "..."` — persistent knowledge, supersedes `MEMORY.md`

**Closed this session (gamma-sync):** `x0y` (D1 delta catalogue),
`e4a` (D2 coverage shape), `0qt` (G0 drift gate), `0gu` (G1 arity),
`eak` (G2 cheap-win mappings), `4r2` (G3 recon), `0ea` (G4 multi-head),
`bmj` (G5 port spec), `59p` (G6 gamma-anchor port), `5n0` (worklog
007), `d80` (this HANDOFF). Plus housekeeping `9mz` (wolfram-v1 → v2
rename).

**5 open beads at session close** (see "What's also not done"):
`1av.1`, `698`, `seu.1`, `seu.2`, `jly`.

Memory at
`~/.claude/projects/-home-tobias-Projects-scientist-workbench-corpus/memory/`
still has the legacy shard set (`sibling_repos.md`, `data_layout.md`,
`feedback_long_running_progress.md`, `env_bun_path.md`,
`project_status.md`). `MEMORY.md` is the index; `bd remember` is the
new write path.
