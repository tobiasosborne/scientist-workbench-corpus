# erf-anchor

Multi-oracle V4 cross-system anchor for the **Erf family** (6 heads ×
8 tiers × 271 cases). Ported from
`scientist-workbench/bench/erf-anchor/` (workbench commit `9efb8d7`,
generator bead `scientist-workbench-9sqr`, ADR-0040). This is the per-head
mega-bench Phase-0 referenced by ADR-0028 §"Per-head bench corpus" and the
companion to `besselj-anchor` (B19).

## Purpose

Establish a **V4 verification-lattice anchor** (CLAUDE.md Rule 7) for
`tools/special-eval`'s six Erf-family heads (`Erf`, `Erfc`, `Erfcx`,
`Erfi`, `InverseErf`, `InverseErfc`) at the arb-prec lane. Four
independent oracles pre-compute every case once; the cross-agreement
matrix (committed below) becomes the ground truth, and candidate
implementations are graded against the gold-tier consensus rather than
against any single oracle.

This bench is the *static record* of the workbench's cross-oracle
verification campaign at the time of porting. The 4 × `results.json`
files copied into the per-oracle adapter `data/` directories travel with
the repo — grade-time does **not** re-invoke Wolfram, mpmath, Boost, or
SciPy on the host. The corpus is a self-contained artefact (recon spec,
constraint #2: no dynamic dependency on oracle installations).

## Oracles (4)

| oracle    | tier   | version                       | precision claim | ok / refused |
|-----------|--------|-------------------------------|-----------------|--------------|
| `wolfram` | gold   | WolframScript 1.13.0 / 14.3   | 60 dp           | 271 / 0      |
| `mpmath`  | gold   | 1.3.0                         | 55 dp (60 dps)  | 269 / 2      |
| `boost`   | silver | 1_83                          | 50 dp           | 149 / 122    |
| `scipy`   | bronze | 1.17.0                        | 53 bits (f64)   | 271 / 0      |

`Julia.SpecialFunctions` + `Arb` adapters were specified in the original
ADR-0040 design space but are **deferred** in v0.1 (mirrors workbench
state — no Julia/Arb adapter exists upstream yet).

## Consensus rule (gold-only)

Per the recon spec (`bd show scientist-workbench-corpus-fpn`) and
ADR-0040 §"Decision 8":

- `consensus.gold_agree = true`  ⟺  `wolfram` + `mpmath` agree to ≥48
  decimal digits on this case.
- When `gold_agree=true`, `consensus.value = <wolfram's value>` (the
  primary gold per recon-spec choice).
- When `gold_agree=false`, `consensus.value = null` and the verifier's
  `value_matches_consensus_within_tolerance` check is **skipped** (the
  check's `applies_when` predicate evaluates false on the case); the
  `consensus_exists` check emits a **warn** (not a fail) so the absence
  of consensus is surfaced without blocking the grade.

The 48-digit threshold is calibrated against the gold oracles' shared
target precision (50 dps for mpmath at 60-dp request; Wolfram's `N[…,
60]` returns ≥55 dp reliably). Tier-specific relative tolerances for
the `value_matches` check follow the recon-spec ladder:

| tier   | description                          | tolerance_rel |
|--------|--------------------------------------|----------------|
| T1–T3  | real, well-conditioned               | `1e-48`        |
| T4–T5  | imaginary + complex (Faddeeva)       | `1e-46`        |
| T6     | edge (±0, ±∞, NaN, subnormal)        | structural     |
| T7     | Stokes band (Berry smoothing)        | `1e-44`        |
| T8     | inverses (Newton iteration)          | `1e-44`        |

## Tier breakdown (271 cases total)

- **T1** real-small  `|x| ∈ [0, 0.84]` (Maclaurin Borel) — 30
- **T2** real-mid    `|x| ∈ (0.84, 6]` — 39
- **T3** real-large  `|x| ∈ (6, 30]` (asymptotic; Erf saturates) — 30
- **T4** pure-imaginary `z = i·y, y ∈ [0, 30]` (Erfi via Faddeeva) — 30
- **T5** complex Q1–Q4 `|z| ∈ [0.1, 15]` (full Faddeeva) — 45
- **T6** edge `±0, ±∞, NaN, subnormal, denormal` — 32
- **T7** Stokes band `|arg z| ≈ π/2` (Berry-smoothing consumer) — 30
- **T8** inverses `InverseErf(y), InverseErfc(y)` — 35

## Known oracle-side disagreements (7 cases)

Per `agreement-matrix.md` (workbench, 2026-05-16) and the regenerated
`expected.json` here:

| input_id                | head        | pair               | digits agreed | threshold |
|-------------------------|-------------|--------------------|---------------|-----------|
| `T5-erf-003`            | Erf         | wolfram vs mpmath  | 35            | 48        |
| `T5-erf-009`            | Erf         | wolfram vs mpmath  | 31            | 48        |
| `T5-erf-015`            | Erf         | wolfram vs mpmath  | 7             | 48        |
| `T5-erfi-034`           | Erfi        | wolfram vs mpmath  | 31            | 48        |
| `T5-erfi-040`           | Erfi        | wolfram vs mpmath  | 21            | 48        |
| `T5-erfi-045`           | Erfi        | wolfram vs mpmath  | 25            | 48        |
| `T8-inverseerfc-018`    | InverseErfc | wolfram vs mpmath  | 14            | 48        |

All 7 have `consensus.gold_agree = false`, `consensus.value = null`.

The 8-case figure quoted in B17's recon notes counts **8 findings**
across the agreement matrix's row-set — the 7 inputs above plus a
second `T8-inverseerfc-018` row (boost-vs-mpmath, 14-digit silver-gold
disagreement on the same input). Our `expected.json` consensus is
gold-only, so the 8 findings collapse to 7 unique input IDs.

### `T8-inverseerfc-018` — oracle-side adjudication open

This case has **multi-oracle disagreement at the 14-digit level**
between every oracle pair examined (wolfram vs mpmath, wolfram vs boost,
mpmath vs boost). The well below-threshold agreement suggests an
oracle-side rather than candidate-side bug — most likely numerical
sensitivity of `InverseErfc` near its argument singularity at `y ≈ 0`.
The case ships in the bench (it's a legitimate stress input) but its
`consensus.value` is `null` and the verifier emits a warn rather than
adjudicating. Child bead recommended for upstream investigation.

## Refusal accounting (Boost ~45%)

Boost's `cpp_bin_float<50>` erf has **no `std::complex` specialisation**
(R5 §1, confirmed by compile test on 2026-05-16). Boost honestly
refuses all 105 complex cases (T4 pure-imag + T5 complex + complex T7
Stokes) plus 17 additional non-finite edge cases — 122/271 total.

The verifier **must not** count these honest refusals as candidate
failures. The implementation: every per-oracle check has an
`applies_when` predicate scoped to "this oracle has a non-null value
for this case" (machine-checkable via the `expected.json` entry). When
the predicate is false, the check reports `pass: true, detail: "N/A
(oracle refused)"` rather than failing.

mpmath refused 2 cases (`T6-erfc-015`, `T6-erfcx-023` — both
`MAX_DOUBLE` overflow); scipy refused 0; wolfram refused 0.

## File layout

```
benchmarks/erf-anchor/
├── DESCRIPTION.md          ← this file
├── manifest.toml           ← suite manifest (7 verifier.checks)
├── run-candidate.ts        ← scientist-workbench / special-eval bridge
└── golden/
    ├── inputs.json         ← 271 cases × {id, input: {head, z}, tags}
    ├── expected.json       ← per-case oracle values + consensus block
    └── verify.ts           ← tournament-protocol verifier
```

Per-oracle replay adapters live under
`adapters/{wolfram,mpmath,boost,scipy}/erf-anchor.toml` with the
committed `results.json` files at
`adapters/<name>/data/erf-anchor-results.json`. The replay script at
`adapters/<name>/oracles/erf-anchor-replay.ts` reads its own
`results.json` and emits the recorded value for the requested case ID.

## References

- **ADR-0040** — per-head substrate, Erf prototype
- **ADR-0028** — bench-to-corpus migration
- **B17** (`scientist-workbench-corpus-fpn`) — recon / port spec
- **B18** (this bead, `scientist-workbench-corpus-m9t`) — the port
- **workbench** `bench/erf-anchor/cross-agreement.ts` — agreement matrix
  generator (the source of the 48-digit gold threshold)
- **CLAUDE.md** Rule 7 — V4 cross-system consensus
