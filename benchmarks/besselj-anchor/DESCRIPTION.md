# besselj-anchor

Multi-oracle V4 cross-system anchor for the **Bessel family** (6 heads ×
10 tiers × 3 ν-classes × 1766 cases) at arb-prec. Ported from
`scientist-workbench/bench/besselj-anchor/` (corpus bead
`scientist-workbench-qccc`; ADR-0041; agreement-matrix.md
2026-05-17 Phase-1 GATE PASS). This is the sibling and scale-up of
`erf-anchor` (B18): same architecture, one additional gold oracle
(`arb`), two additional tiers (T9 zero-crossing band, T10 large-ν
integer), and a ν parameter threaded through the input wire because
Bessel is arity-2 vs Erf's arity-1.

## Purpose

Establish a **V4 verification-lattice anchor** (CLAUDE.md Rule 7) for
`tools/special-eval`'s six Bessel-family heads (`BesselJ`, `BesselY`,
`BesselI`, `BesselK`, `BesselIScaled`, `BesselKScaled`) at the arb-prec
lane, with a 3-of-3-gold consensus drawn from `wolfram`, `mpmath`, and
`arb` (python-flint).

This bench is the *static record* of the workbench's cross-oracle
verification campaign at the time of porting. The 5 × `results.json`
files (id-enriched per case so the on-disk records are self-describing)
travel with the repo under `adapters/<oracle>/data/`. Grade-time does
**not** re-invoke Wolfram, mpmath, Boost, SciPy, or FLINT on the host
— the corpus is a self-contained artefact.

## Oracles (5)

| oracle    | tier   | version                            | precision claim    | ok / refused | notes                                              |
|-----------|--------|------------------------------------|--------------------|--------------|----------------------------------------------------|
| `wolfram` | gold   | WolframScript 1.13.0 / 14.3        | 60 dp              | 1721 / 0     | 45 "limit" (unevaluated symbolic returns); 0 hard refusals |
| `mpmath`  | gold   | 1.3.0                              | 55 dp (60 dps)     | 1729 / 0     | 36 honest-special-token (T6 sentinels), 1 timeout (T7-besselk-020) |
| `arb`     | gold   | python-flint 0.8.0 / FLINT 3.x     | 55 dp (acc-bits)   | 1718 / 48    | 48 refused at non-finite z (T6 NaN/Inf cases) |
| `boost`   | silver | 1_83 cpp_bin_float<50>             | 50 dp              | 1578 / 188   | no std::complex specialisation; refuses all 128 T5 + ~60 large-ν / overflow boundary |
| `scipy`   | bronze | 1.17.0 / NumPy 1.26.4              | 53 bits (f64)      | 1667 / 0     | 99 "limit" annotations (NaN at L5 transition / L9 underflow boundaries) |

The `arb` oracle is new in B19 (B18's erf-anchor had only 4 oracles);
it's the third gold leg per ADR-0041 §Decision 8.

## Consensus rule (3-gold, with 2-of-3 fallback)

Per the recon spec extension (B17 spec, scaled to 3 golds) and
ADR-0041 §"Decision 8":

- `consensus.gold_agree = true`  ⟺  at least two of `{wolfram, mpmath,
  arb}` produced finite values that agree to ≥48 decimal digits
  pairwise. When all three are present, all three pairs must clear
  the threshold (strict 3-of-3); when one gold refused/limited/timed
  out, the remaining 2-of-3 pair must clear it.
- When `gold_agree=true`, `consensus.value = <wolfram's value>`
  (primary gold, B18 precedent — falls back to mpmath then arb when
  wolfram is unavailable).
- When `gold_agree=false`, `consensus.value = null`, the verifier's
  `value_matches_consensus_within_tolerance` check is **skipped**
  (the `applies_when` predicate evaluates false on the case), and
  `consensus_exists` emits a **warn** so the absence of consensus is
  surfaced without blocking grade.

Tier-specific relative tolerances:

| tier  | description                                        | tolerance_rel |
|-------|----------------------------------------------------|----------------|
| T1–T3 | small-z series + mid-z + large-z asymptotic, real | `1e-48`        |
| T4–T5 | transition |z|≈ν + complex Q1-Q4                  | `1e-46`        |
| T6    | edges (±0, ±∞, NaN, subnormal, 700-boundary)       | structural     |
| T7    | high-ν Debye band                                  | `1e-44`        |
| T8    | negative-real-ν Y/K connection-formula             | `1e-44`        |
| T9    | Bessel zeros (zero-crossing tolerance band)        | `1e-40`        |
| T10   | large-ν integer (overflow/underflow boundary)      | `1e-44`        |

## Tier breakdown (1766 cases total)

- **T1** small-z series  `|z| ∈ (0, 8]` — 468
- **T2** mid-z  `|z| ∈ (8, 60]` (cancellation retry) — 364
- **T3** large-z asymptotic  `|z| ∈ (60, 300]` (Hankel) — 336
- **T4** transition  `|z| ≈ ν` (algorithmically hardest) — 96
- **T5** complex Q1–Q4  `|z| ∈ [0.5, 30]` (Amos-rotation path) — 128
- **T6** edges  `±0, ±∞, NaN, subnormal, 700-boundary` — 96
- **T7** high-ν Debye  `ν ∈ [50, 500] × |z| ∈ ν·[0.5, 2]` — 80
- **T8** negative-real-ν `Y/K` connection-formula branch — 30
- **T9** Bessel zeros (zero-crossing band) — 120
- **T10** large-ν integer (overflow/underflow) — 48

## ν-class taxonomy (3-axis)

The `nu_kind` tag on every case slices the corpus along ν-class — a
new tagging dimension B19 introduces (B18's erf-anchor had no analogous
parameter).

| ν class        | count | meaning |
|----------------|-------|---------|
| `integer`      | 976   | ν ∈ ℤ — exercises integer-recurrence + closed-form-at-0 paths |
| `half-integer` | 458   | ν = (2k+1)/2 — exercises J_{n+1/2} ↔ spherical-Bessel closures |
| `decimal`      | 332   | general real non-integer non-half — general-ν algorithm |

The corpus carries ν in three on-disk encodings: integer string (`"0"`,
`"5"`), fraction string (`"-3/2"`, `"7/2"`), and 60-char decimal string
(`"1.699..."`). The `run-candidate.ts` parses all three into a single
`Number(nu)` before encoding as `args = list<float64>([nu, z])`.

## Disagreement accounting (21 of 1766)

Per the regenerated `expected.json`, 21 cases carry
`consensus.gold_agree=false`. **All 21 are T6 edge cases where every
gold oracle (wolfram, mpmath, arb) refused, limited, or emitted a
sentinel-only response** (wolfram: `Indeterminate`; mpmath: `NaN` /
`0` honest-special-token; arb: `refused`).

| input_id              | head    | tier | wolfram | mpmath | arb     |
|-----------------------|---------|------|---------|--------|---------|
| T6-besselj-008/016/024| BesselJ | T6   | limit   | sentinel | refused |
| T6-bessely-008/016/024| BesselY | T6   | limit   | sentinel | refused |
| T6-besseli-003/004/008/011/012/016/019/020/024 | BesselI | T6 | limit | sentinel | refused |
| T6-besselk-004/008/012/016/020/024            | BesselK | T6 | limit | sentinel | refused |

This is *not* a candidate-vs-oracle finding; it's an oracle-side
artefact of float64-limit sentinel-spelling drift (Wolfram returns
`Indeterminate` for unevaluated `BesselI[0, Infinity]` etc; mpmath
emits `NaN`/`0` annotations; arb refuses). The agreement-matrix's
Phase-1 GATE PASS classifies all of these under landmine class
**L9-L10-overflow-underflow-boundary** (downgraded warn → info).

The one non-T6 candidate for disagreement, `T7-besselk-020` (mpmath
30-second timeout), is **resolved** by the 2-of-3 fallback: wolfram +
arb both produced values agreeing to ≥48 dp.

## Refusal accounting

- **boost (188/1766 = 10.6%):** no std::complex Bessel specialisation
  in 1_83 → all 128 T5 complex cases refused; also refuses ~60
  large-ν / overflow-boundary T7/T10 cases. Refusals carry
  `method="boost-refused"` and a `reason` field; verifier surfaces
  them as `oracle/boost-refused` tagged envelopes scoped via
  `applies_when`.
- **arb (48/1766 = 2.7%):** refuses on non-finite z (T6 NaN/Inf cases).
- **mpmath (37/1766 ≈ 2.1%):** 36 honest-special-token responses on T6
  edge cases (`NaN`, `0` for `BesselJ(ν, +∞)→0` etc), plus 1 timeout
  on `T7-besselk-020`. Normalised to `output=null` in the corpus copy.
- **wolfram (45/1766 = 2.5%):** "limit" responses on T6 edge cases
  where Mathematica returns unevaluated symbolic form
  (`BesselI[0, Infinity]`).
- **scipy (0/1766):** never refuses; 99 cases annotated as "limit"
  (NaN at L5 transition / L9 underflow boundaries) are normalised to
  `output=null` for the verifier (scipy emitted a NaN/0 sentinel; not
  a finite value to compare).

The verifier **must not** conflate honest oracle refusals with
candidate failures. Every per-oracle check has an `applies_when`
predicate scoped so the verifier emits `pass: true, detail: "N/A
(candidate is refusal envelope)"` rather than failing.

## File layout

```
benchmarks/besselj-anchor/
├── DESCRIPTION.md          ← this file
├── manifest.toml           ← suite manifest (7 verifier.checks)
├── run-candidate.ts        ← scientist-workbench / special-eval bridge
└── golden/
    ├── inputs.json         ← 1766 cases × {id, input: {head, nu, z}, tags}
    ├── expected.json       ← per-case oracle values + 3-gold consensus block
    └── verify.ts           ← tournament-protocol verifier
```

Per-oracle replay adapters live under
`adapters/{wolfram,mpmath,arb,boost,scipy}/besselj-anchor.toml` with the
id-enriched `results.json` files at
`adapters/<name>/data/besselj-anchor-results.json`. The replay script
at `adapters/<name>/oracles/besselj-anchor-replay.ts` reads its own
`results.json` and emits the recorded value keyed by `(head, nu, z)`.

## References

- **ADR-0041** — Bessel-family substrate + cross-oracle Phase-1 GATE
- **ADR-0040** — per-head substrate (erf-anchor prototype)
- **ADR-0028** — bench-to-corpus migration
- **B17** (`scientist-workbench-corpus-fpn`) — recon / port spec
  (erf-anchor; B19 extends pattern to 3 golds + arity-2 inputs)
- **B18** (`scientist-workbench-corpus-m9t`) — erf-anchor port (B19's
  immediate template)
- **B19** (this bead, `scientist-workbench-corpus-9bz`) — the port
- **workbench** `bench/besselj-anchor/agreement-matrix.md` — 0
  unexplained findings; Phase-1 GATE PASS
- **CLAUDE.md** Rule 7 — V4 cross-system consensus
