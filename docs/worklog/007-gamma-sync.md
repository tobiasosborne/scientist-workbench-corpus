# 007 — gamma-sync (2026-05-23)

## Context

Same-day follow-up to worklog 006. Between the drift-sync close and
this session opening, the user pulled `scientist-workbench` and
discovered the pre-drift-sync pull had been stale: workbench HEAD
had advanced 10 commits past `9efb8d7` (the baseline the prior
session pinned) to `af1baa3`. Two epics had landed in that window:

- **Gamma epic close** (workbench shards 174-178): Phase 1 + round 1,
  epic close, HurwitzZeta CVZ lane, IncompleteGamma Lentz CF, v0.2
  hardening. `tools/special-eval` ADMITTED_HEADS grew 12 → 28
  (+16 Gamma-family heads). bench/gamma-anchor materialised
  (377 inputs × 8 tiers × 19 heads × 5 oracles).
- **ixnv registry epic** (workbench shards 179-182, ADR-0043):
  `docs/CATALOG.md` as a 55-row machine-readable manifest of the
  workbench tool surface — single source of truth for tool scope.

Drift recon: `scientist-workbench-corpus-x0y` (D1, delta catalogue)
+ `scientist-workbench-corpus-e4a` (D2, refreshed coverage shape).
Plan: 9 in-session G-beads (G0-G6 + the recon split G3/G4) + 1
FLINT-future parent (`scientist-workbench-corpus-jly`, P3, deferred
per user request "compare everything against FLINT as well at some
point"). All 9 G-beads closed in-session. This shard is bead
`scientist-workbench-corpus-5n0`; HANDOFF rewrite (`d80`) + final
commit/push remain inline after the shard.

## What shipped (organised by phase)

### Phase 0 — drift gate (G0 / 0qt)

Regraded the three corpus benches that exercise `special-eval`
against the post-Gamma workbench: `special-eval-smoke` (10 cases),
`erf-anchor` (271 cases × 4 oracles), `besselj-anchor`
(1766 cases × 5 oracles). All three green at baseline counts.
D1's "no wire-shape change to `special-eval`; additive only" claim
confirmed empirically. No regressions; G-beads cleared to proceed.

### Phase 1 — arity bugs (G1 / 0gu)

`capabilities/wolfram-v2/Beta.toml` and `Pochhammer.toml` both had
`arity = 1` from the original ingest — pre-existing transcription
bugs (same class as B7's BesselJ-family bug from worklog 006).
Beta is arity-2 (`Beta[x,y]`), Pochhammer is arity-2
(`Pochhammer[a,n]`). Fixed via one-off `scripts/fix_wolfram_v2_arity_0gu.ts`
(mirrors B7's `3pu` pattern, tagged to `0gu` for distinguishable
provenance). Rule 6 honoured: append-only `[[provenance]]` row per
TOML (`source_kind=manual`, `field_path=signature.arity`,
`source_url=bead:0gu`). Joins the broader 401-TOML sweep tracked
as bead `698` (still open). Commit `7d3ec45`. Validate clean
(582/21/38, unchanged at this point).

### Phase 2 — cheap-win mappings (G2 / eak)

Four bucket-C Gamma-family mappings wired as new `[[mapping]]`
blocks on existing TOMLs:

- `Gamma.toml → special-eval` (flags `head=Gamma`)
- `Beta.toml → special-eval` (flags `head=Beta`)
- `Pochhammer.toml → special-eval` (flags `head=Pochhammer`)
- `PolyGamma.toml → special-eval` (flags `head=Polygamma`)

All four `status=implemented`, `precision_tier=both`, per workbench
ADR-0042 close (shard 175). **Naming friction surfaced:** the corpus
TOML name `PolyGamma` (Mathematica's spelling) maps to workbench's
`Polygamma` (lower-case `g`). Documented in the mapping's `notes`
field for future-reader clarity rather than renaming either side.
Mappings table 9 → 13. Commit `554cb5a`.

### Phase 3 — honesty-first recon + multi-head mappings (G3 / 4r2, G4 / 0ea)

**G3 recon overturned D2's projection.** D2 had estimated 6-8 of
the 12 "C-minus" heads (LogGamma, Digamma, Trigamma,
IncompleteGammaUpper/Lower/P/Q, LogBeta, BarnesG, Hyperfactorial,
GammaRatio, GammaDeltaRatio) would land as new wolfram-v2 TOMLs.
G3 cross-checked against `data/wolfram-v2/raw/B.8.html` (the
canonical 581-function v2 index) and found that **none** of the 12
are standalone Mathematica v2 names. The honest answer per Rule 1
(TOML is truth, no fabrication) is **zero new TOMLs**.

Instead: the four heads with v2 backing under a different dispatch
shape get expressed as additional `[[mapping]]` rows on the two
existing parent TOMLs:

- `Gamma.toml` gains `IncompleteGammaUpper` (v2's 2-arg `Gamma[a,z]`)
  and `IncompleteGammaLower` (v2's 3-arg `Gamma[a,z0,z1]`);
- `PolyGamma.toml` gains `Digamma` (v2's 1-arg `PolyGamma[z]`) and
  `Trigamma` (v2's `PolyGamma[1,z]`, with `order=1` flag).

The other 8 heads (LogGamma, LogBeta, IncompleteGammaP/Q, BarnesG,
Hyperfactorial, GammaRatio, GammaDeltaRatio) are workbench-internal
naming or post-v2 functions — correctly **not** created as stub
TOMLs (would have been the fundingscape anti-pattern: silently
fabricated ingest output).

**G4 implemented G3's findings.** Mappings table 13 → 17. Spot-check
SELECT returned the 6 expected rows (4 single-head + 4 multi-head)
on the two TOMLs. Validate clean (582/21/38, unchanged — no caps
added). Commit `5ac4aaf`.

### Phase 4 — gamma-anchor bench migration (G5 / bmj, G6 / 59p)

B20 analogue, parallel to B18 (erf-anchor) and B19 (besselj-anchor).

**G5 port spec** mirrors B17's structure. Headline decisions:

- **3-of-3 strict + 2-of-3 fallback gold consensus** (same as
  besselj-anchor B19; was 2-of-2 in erf-anchor B18).
- **L13 single-wolfram fallback** for the 12 `InverseIncompleteGamma{P,Q}`
  cases where arb + mpmath have no native implementation:
  `gold_agree=true`, `digits_agreed=60`, wolfram-only (definitionally
  authoritative).
- **Per-case `landmine_flags: string[]`** added to `expected.json`
  case shape — drives data-driven verifier branches for
  L12-L18 + the T7 Temme-saddle carve-out rather than hardcoded
  tier/head checks in `verify.ts`.
- **Honesty gap:** 19 corpus heads vs 16 `special-eval`
  ADMITTED_HEADS. The 4 unimplemented heads (`GammaPDerivative`,
  `IncompleteBeta`, `InverseIncompleteGammaP`, `InverseIncompleteGammaQ`
  — 22 inputs total) yield honest `unknown-head` refusals from the
  candidate, admitted via `refusal_scope_honest`. Hyperfactorial is
  in tool.ts ADMITTED_HEADS but has 0 corpus cases (asymmetric the
  other way).
- **No sampling needed:** 377 cases × 5 oracles fits well under the
  50 MB git limit (estimate ~1.3 MB).

**G6 implemented the port.** 22 files across 3 themed commits
(same shape as B18/B19):

| commit  | content |
|---------|---------|
| `570bd71` | `benchmarks/gamma-anchor/` (manifest + golden trio + verify + run-candidate + DESCRIPTION) |
| `bfe037f` | 5 oracle adapter TOMLs + replay shims + snapshotted `data/gamma-anchor-results.json` |
| `c74bcfa` | `adapters/scientist-workbench/gamma-anchor.toml` (arb-prec lane, `tool_flags.precision = "200"`) |

Validate 582/21/38 → **582/22/44** (+1 suite, +6 adapters as
projected). Smoke: wolfram 5/5, mpmath 5/5, arb 5/5, boost 5/5,
scipy 4/5 (`T1-gamma-005` expected fail — scipy float64 1e-17
cannot meet gold 1e-48; structural bronze-tier ceiling, same class
as B19's scipy on besselj). Candidate (scientist-workbench) 10/10
on a mixed smoke covering T1 (gamma/loggamma/incompletegammap/
pochhammer/beta), T1-`IncompleteBeta` (`unknown-head` pass),
T1-`InverseIncompleteGammaP` (L13 `unknown-head` pass),
T3-gamma-001 (L17 pole), T4-gamma-001 (complex `cgamma` method
tag added mid-port), T6-gamma-001.

Total git-tracked: **~1.85 MB** (812K bench + 1.0M oracle data) —
within the spec's 1.3 MB estimate. Decisions on top of G5:

1. `landmine_flags` computed in build-script from `corpus.json` notes
   + per-head/tier rules (L17 detection: `head ∈ {Gamma, Digamma}` +
   `Number(z)` non-positive integer; L13 for all
   `InverseIncompleteGamma{P,Q}`; L14 for `Polygamma`/`Trigamma` T4;
   L16 for all `BarnesG`; L18 for `Digamma` at `2z` odd-negative-int).
2. L13 single-wolfram fallback adopted as G5 recommended.
3. `input.id` carried per-case so replay shims look up by case-id
   (cleaner than reconstructing composite `(head, a.value, z)` keys
   under collision risk).
4. `ARBPREC_METHOD_COMPLEX` tags (`cgamma-stirling-recurrence-reflection`)
   added to `ADMITTED_METHODS` mid-port after initial smoke surfaced
   them (catch-and-fix; documented in `DESCRIPTION.md`).
5. Boost L16/L18 absences handled within the boost replay shim
   using existing `oracle/boost-{refused,unsupported}` tags — no
   verifier change needed. No child beads filed.

Minor CLI friction: `--case-id` is singular (multi-pass for several
cases), not `--case-ids`. Worth noting for future bench smokes.

## Adapter and suite counts (gamma-sync delta)

| metric             | pre-session | post-session | delta |
|--------------------|-------------|--------------|-------|
| capabilities       | 582         | 582          | 0     |
| benchmark suites   | 21          | 22           | +1    |
| adapters           | 38          | 44           | +6    |
| mapping rows       | 9           | 17           | +8    |
| SCHEMA_VERSION     | 3           | 3            | 0     |

Suite delta: `+gamma-anchor`.

Adapter delta (+6): 5 oracle (wolfram/mpmath/arb/boost/scipy) + 1
scientist-workbench candidate for gamma-anchor.

Mapping delta (+8): G2's 4 single-head Gamma-family mappings (Gamma,
Beta, Pochhammer, PolyGamma) + G4's 4 multi-head additions on Gamma
+ PolyGamma (IncompleteGammaUpper/Lower + Digamma/Trigamma).

## Frictions worth knowing

- **G3 honesty discipline overrode D2's ingest projection.** D2
  projected 6-8 new TOMLs; G3 cross-check found 0. The drift
  catalogue is sometimes wrong about ingest opportunities; the
  recon-before-ingest step is load-bearing. Pattern to adopt:
  **file a recon bead before any ingest bead** whose scope is
  derived from a drift catalogue.

- **4 special-eval heads in the gamma-anchor corpus but not in
  `tools/special-eval` ADMITTED_HEADS:** `GammaPDerivative`,
  `IncompleteBeta`, `InverseIncompleteGammaP`, `InverseIncompleteGammaQ`.
  The candidate adapter yields honest `unknown-head` for these
  22 inputs; the verifier admits via `refusal_scope_honest`. Future
  workbench-side ADMITTED_HEADS expansion target; no corpus bead
  filed (the corpus side is already correct per Rule 8).

- **8 Gamma-family heads (LogGamma, LogBeta, IncompleteGammaP/Q,
  BarnesG, Hyperfactorial, GammaRatio, GammaDeltaRatio)** are
  workbench-internal naming or post-v2 functions. Correctly absent
  from `capabilities/wolfram-v2/`; no follow-up bead. If a future
  Mathematica edition (v3+) ingest lands, these may gain TOMLs
  organically.

- **`PolyGamma` vs `Polygamma` capitalisation.** Corpus follows
  Mathematica's casing; workbench's `tools/special-eval` uses
  lower-case `g`. Captured in the mapping's `notes` field rather
  than renamed either side — both spellings are intentional.

## What's not done after gamma-sync

- **Bead 698** (broader 401-TOML arity sweep) still open. Beta +
  Pochhammer joined the BesselJ-family + D + PowerMod in being
  hand-corrected; ~395 TOMLs still hold the wrong arity.
- **Beads `seu.1` + `seu.2`** (HankelH1/H2 + Spherical Bessel
  ADMITTED_HEADS expansion on the workbench side) — still open
  from worklog 006.
- **Bead `1av.1`** (workbench-side `lp-solve` `achieved_precision`
  overclaim on NETLIB) — still open from worklog 006.
- **Bead `jly`** (FLINT integration epic) — filed as P3 parent for
  a future dedicated session. Multi-target scope: poly-factor,
  mod-pow, linsolve-q, groebner-basis (via msolve), special-functions
  (via `acb_hypgeom`). Arb is already covered as the
  besselj-anchor/gamma-anchor arb-prec oracle; FLINT 3+ absorbed Arb
  as a module, so the integration starts partially-done.
- **Bead `d80`** (HANDOFF rewrite) and the final commit/push — both
  immediate next steps after this shard lands.

## References

- Workbench shards 174-178 (Gamma epic close), 179-182 (ixnv
  registry epic close), plus the prior 089-173 already cited in
  worklog 006.
- Workbench ADR-0042 (Gamma-anchor consensus rule), ADR-0043
  (registry-single-source-of-truth via `docs/CATALOG.md`).
- Workbench HEAD pin: `af1baa3` (advanced from `9efb8d7` mid-session
  per user pull).
- Corpus beads: `scientist-workbench-corpus-{x0y (D1), e4a (D2),
  0qt (G0), 0gu (G1), eak (G2), 4r2 (G3 recon), 0ea (G4),
  bmj (G5 port spec), 59p (G6 port), 5n0 (this shard),
  d80 (HANDOFF rewrite), jly (FLINT future)}`.
- Commits this session: `7d3ec45` (G1 arity fix), `554cb5a` (G2
  cheap-win mappings), `5ac4aaf` (G4 multi-head mappings), `570bd71`
  + `bfe037f` + `c74bcfa` (G6 gamma-anchor trio). Plus earlier
  housekeeping: `658bcd5` + `478a77c` (wolfram-v1 → wolfram-v2
  honest rename, bead `9mz`), `817429f` (gitignore for
  lean4-skills plugin staging). Final shard commit: this one.
