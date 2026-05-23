# gamma-anchor

Multi-oracle V4 cross-system anchor for the **Gamma family** (19 corpus
heads x 8 tiers x 377 cases) at arb-prec.  Ported from
`scientist-workbench/bench/gamma-anchor/` (workbench HEAD `af1baa3`,
ADR-0042, Phase-1 GATE PASS).  Sibling to `erf-anchor` (B18) and
`besselj-anchor` (B19): same 5-oracle architecture (wolfram + mpmath +
arb gold, scipy bronze, boost silver), with two structural deltas:

1. **+3 corpus heads vs special-eval's ADMITTED_HEADS** — 4 corpus
   heads (`GammaPDerivative`, `IncompleteBeta`, `InverseIncompleteGammaP`,
   `InverseIncompleteGammaQ`, ~22 inputs total) have NO `tools/special-eval`
   backing.  The scientist-workbench candidate honestly returns
   `special-eval/unknown-head` on these inputs; the verifier's
   `refusal_scope_honest` check explicitly admits `unknown-head` for
   these 4 heads with no region restriction.
2. **Per-case `landmine_flags` field** in `expected.json` — encodes
   the 6+ documented L12-L18 landmines + the T7 Temme carve-out
   as data-driven flags the verifier reads, rather than hardcoded
   conditionals.  Additive to the besselj-anchor schema.

## Purpose

Establish a **V4 verification-lattice anchor** (CLAUDE.md Rule 7) for
the Gamma family's 15 `tools/special-eval`-admitted heads (`Gamma`,
`LogGamma`, `Digamma`, `Trigamma`, `Polygamma`, `Pochhammer`,
`IncompleteGammaUpper`, `IncompleteGammaLower`, `IncompleteGammaP`,
`IncompleteGammaQ`, `Beta`, `LogBeta`, `BarnesG`, `GammaRatio`,
`GammaDeltaRatio`) at the arb-prec lane, with a 3-of-3-gold consensus
drawn from `wolfram`, `mpmath`, and `arb` (python-flint).

This bench is the *static record* of the workbench's cross-oracle
verification campaign at the time of porting.  The 5 x `results.json`
files (id-enriched per case so the on-disk records are self-describing)
travel with the repo under `adapters/<oracle>/data/`.  Grade-time does
**not** re-invoke Wolfram, mpmath, FLINT, SciPy, or Boost on the host
- the corpus is a self-contained artefact.

## Oracles (5)

| oracle    | tier   | version                                | precision claim | ok / refused | notes                                                                |
|-----------|--------|----------------------------------------|-----------------|--------------|----------------------------------------------------------------------|
| `wolfram` | gold   | WolframScript 1.13.0 / 14.3.0          | 60 dp           | 369 / 8      | 8 ComplexInfinity at L17 poles                                       |
| `mpmath`  | gold   | 1.3.0 / Python 3.12.3                  | 55 dp (60 dps)  | 357 / 20     | 12 unsupported (no native InverseIncompleteGamma{P,Q}); 8 pole refused |
| `arb`     | gold   | python-flint 0.8.0 / FLINT 3.0.1       | 55 dp           | 357 / 20     | same 12 InverseIncompleteGamma{P,Q} + 8 pole                          |
| `scipy`   | bronze | 1.17.0 / NumPy 1.26.4                  | 53 bits (f64)   | 342 / 35     | 24 refused (8 polygamma-complex + 16 gammainc-complex TypeError); 11 BarnesG unsupported |
| `boost`   | silver | Boost.Math 1_83 cpp_bin_float<50>      | 50 dp           | 295 / 82     | 71 unsupported (all complex T4 + BarnesG + Pochhammer absences); 11 refused at L17 / overflow |

## Consensus rule (3-gold, with 2-of-3 fallback + L13 special case)

Per ADR-0042 §Decision 8 (recon spec scientist-workbench-corpus-bmj):

- `consensus.gold_agree = true` iff at least two of `{wolfram, mpmath,
  arb}` produced finite values that agree to >=48 decimal digits
  pairwise.  When all three are present, all three pairs must clear
  the threshold (strict 3-of-3); when one gold refused, the remaining
  2-of-3 pair must clear it.
- **L13 special case**: for `InverseIncompleteGamma{P,Q}` (12 inputs
  in T1) mpmath and arb have no native implementation and refuse;
  wolfram is the definitionally authoritative single gold and the
  consensus is `gold_agree = true, value = <wolfram>, digits_agreed = 60`.
- When `gold_agree=true`, `consensus.value = <wolfram's value>`
  (primary gold; falls back to mpmath then arb when wolfram unavailable).
- When `gold_agree=false`, `consensus.value = null`; the verifier's
  `value_matches_consensus_within_tolerance` check is **skipped** (the
  `applies_when` predicate evaluates false) and `consensus_exists`
  emits a **warn** so the absence of consensus is surfaced.

### Tolerance ladder (per tier; T7 per-case)

| tier  | description                                                   | tolerance_rel       |
|-------|---------------------------------------------------------------|---------------------|
| T1    | real positive z+a (all heads; series/CF crossovers)           | `1e-48`             |
| T2    | real negative z (poles, LogGamma branch, Digamma reflection)  | `1e-48`             |
| T3    | near-poles (delta-stress); poles emit null consensus          | `1e-48` (non-pole)  |
| T4    | complex Q1-Q4, \|z\| in [0.5,12]                              | `1e-46`             |
| T5    | half-integer a (DLMF §5.4-5.5 closed forms)                   | `1e-46`             |
| T6    | large \|z\| in (100,1000] (Stirling / Poincare)               | `1e-44`             |
| T7    | near a~z (Temme uniform-asymptotic) -- v0.1 carve-out          | per-case `1e-(48-ceil(log2(\|a\|)))` |
| T8    | digamma near negative integers (reflection cancellation)      | `1e-44`             |

## Null-consensus accounting (8 of 377)

All 8 null-consensus cases are **L17 exact poles** (T3 delta=0 inputs
for Gamma and Digamma at z=0,-1,-2,-3):

```
T3-gamma-001 / T3-digamma-001    z = 0
T3-gamma-006 / T3-digamma-006    z = -1
T3-gamma-011 / T3-digamma-011    z = -2
T3-gamma-016 / T3-digamma-016    z = -3
```

Wolfram returns `ComplexInfinity`; mpmath / arb refuse.  This is
**not** a candidate-vs-oracle finding; it's the four-way oracle-pole
behaviour landmine documented at ADR-0042 §L17.  The verifier admits
`Infinity` / `NaN` / `ComplexInfinity` (or any honest refusal) from
the candidate on these 8 cases via the `landmine_flags: [L17]` data
field.

The 12 `InverseIncompleteGamma{P,Q}` cases are *not* null-consensus
in this port: per the G5 spec the L13 single-wolfram-gold fallback
yields `gold_agree=true, digits_agreed=60`.  These cases carry
`landmine_flags: [L13]` for downstream provenance only.

## Landmine flags (per-case, additive to besselj-anchor schema)

| flag           | count | meaning                                                                              |
|----------------|-------|--------------------------------------------------------------------------------------|
| `L12`          | 26    | P/Q/Upper/Lower 4-way confusion (IncompleteGamma* T1 cases)                          |
| `L13`          | 12    | arb+mpmath no native InverseIncompleteGamma{P,Q}; wolfram-only gold                  |
| `L14`          | 8     | SciPy polygamma complex TypeError (T4 Polygamma/Trigamma)                            |
| `L16`          | 11    | BarnesG absent from boost & scipy (all 11 BarnesG inputs)                            |
| `L17`          | 8     | Gamma/Digamma at non-positive integer pole (T3 delta=0)                              |
| `L18`          | 2     | Boost digamma negative-half-integer bug (T2 z=-1/2 cases)                            |
| `T7-carve-out` | 40    | Temme uniform-asymptotic transition; per-case relaxed tolerance                      |

**L15** (SciPy `loggamma` real-negative -> NaN) is **not** encoded as
a flag: the workbench's scipy adapter fixed it upstream by passing
`x + 0j`; the committed scipy results.json carries correct values.
Doc-only landmine.

The verifier reads `landmine_flags` per-case and:
- L17 -> `value_finite` admits `Infinity`/`NaN`/`ComplexInfinity` from
  the candidate; `value_matches` skipped (consensus is null anyway).
- L14 -> `refusal_scope_honest` admits `oracle/scipy-refused` for the
  scipy replay shim on these cases without region check.
- L13 -> documents the wolfram-sole-gold provenance; no behavioural
  change vs the generic consensus path.
- L18 -> documents the Boost digamma quirk; the candidate is graded
  against the gold consensus, not boost.
- L16 -> documents BarnesG's absence from boost/scipy; honest oracle
  refusals via `oracle/boost-unsupported` / `oracle/scipy-refused`.
- T7-carve-out -> per-case tolerance_rel read from
  `consensus.tolerance_rel` (no hardcoded T7 branch in verify.ts).

## Tier breakdown (377 cases total)

- **T1** real positive z+a       — 92
- **T2** real negative z          — 43
- **T3** near-poles (delta-stress) — 54
- **T4** complex Q1-Q4            — 40
- **T5** half-integer a (closed)  — 40
- **T6** large |z|                — 28
- **T7** Temme transition         — 40
- **T8** digamma near neg int     — 40

## Head taxonomy (19 corpus heads)

```
BarnesG (11), Beta (13), Digamma (60), Gamma (42), GammaDeltaRatio (2),
GammaPDerivative (4) *,  GammaRatio (2), IncompleteBeta (6) *,
IncompleteGammaLower (26), IncompleteGammaP (18),
IncompleteGammaQ (24), IncompleteGammaUpper (36),
InverseIncompleteGammaP (6) *, InverseIncompleteGammaQ (6) *,
LogBeta (4), LogGamma (28), Pochhammer (20), Polygamma (29),
Trigamma (40)
```

(*) = NOT in `tools/special-eval` ADMITTED_HEADS (16 heads).  Total of
4 unimplemented corpus heads (`GammaPDerivative`, `IncompleteBeta`,
`InverseIncompleteGammaP`, `InverseIncompleteGammaQ`; 22 inputs)
yield `special-eval/unknown-head` refusals from the candidate.
These are honest capability gaps, not bugs.  The verifier admits
`special-eval/unknown-head` for these 4 heads without a region
restriction.

Hyperfactorial IS in `tools/special-eval` ADMITTED_HEADS but has 0
cases in the corpus.  `n_heads=19` counts corpus heads.

## File layout

```
benchmarks/gamma-anchor/
+-- DESCRIPTION.md          <- this file
+-- manifest.toml           <- suite manifest (8 verifier.checks)
+-- run-candidate.ts        <- scientist-workbench / special-eval bridge
+-- golden/
    +-- inputs.json         <- 377 cases x {id, input: {head, z?, a?, b?, m?, n?, id}, tags}
    +-- expected.json       <- per-case oracles + 3-gold consensus + landmine_flags
    +-- verify.ts           <- tournament-protocol verifier (8 checks)
```

Per-oracle replay adapters live under
`adapters/{wolfram,mpmath,arb,boost,scipy}/gamma-anchor.toml` with the
id-enriched `results.json` files at
`adapters/<name>/data/gamma-anchor-results.json`.  The replay script
at `adapters/<name>/oracles/gamma-anchor-replay.ts` reads its own
`results.json` and emits the recorded value keyed by `input_id` (the
corpus runner forwards the case id through `input.id`).

## References

- **ADR-0042** — Gamma-family substrate + cross-oracle Phase-1 GATE
- **ADR-0041** — Bessel-family substrate (besselj-anchor prototype)
- **ADR-0040** — Erf per-head substrate (erf-anchor prototype)
- **ADR-0028** — bench-to-corpus migration
- **G5** (`scientist-workbench-corpus-bmj`) — recon / port spec
- **G6** (this bead, `scientist-workbench-corpus-59p`) — the port
- **workbench** `bench/gamma-anchor/agreement-matrix.md` — Phase-1
  GATE PASS, 0 unexplained disagreements
- **CLAUDE.md** Rule 7 — V4 cross-system consensus
