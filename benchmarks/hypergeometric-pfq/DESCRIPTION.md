# `hypergeometric-pfq` — design notes and rationale

This document is the longer-form companion to `PROMPT.md`. It explains
*why* the bench has the shape it does — what the tiers discriminate,
why each tolerance, why the corpus stops where it does.

## Why pFq is load-bearing

The generalised hypergeometric function is the workhorse of special-
function computation: every Bessel, Whittaker, parabolic-cylinder,
Coulomb, Jacobi-theta value is a `pFq` value at specific
parameter / argument combinations. The Slater residue-summation of the
Meijer G-function (the workbench's headline `tstournament` problem-13
campaign target) reduces every `MeijerG[..., z]` to a finite sum of
`pFq` values. The Adamchik–Marichev symbolic dispatch path
(`packages/meijer-core::meijergSymbolic`, ADR-0025) emits closed
forms in the cas-core special-function vocabulary that *include*
`HypergeometricPFQ` heads — those heads are then evaluated by this
tool. The campaign's planned asymptotic Braaksma path (hv0.9, in
flight by a parallel agent) and the top-level dispatcher (hv0.10)
both consume `evaluatePFq`.

Therefore: a regression in `pFq` is a regression in everything
downstream. The bench is the discriminator that the inner-loop unit
tests (`tools/hypergeometric-pfq/tool.test.ts`, 15 cases) cannot be
on their own — the unit tests verify the closed-form fast paths and
a handful of identities; the bench is *graded* test discrimination
across the four documented failure modes (Pearson-Olver-Porter 2017
§3): cancellation, oscillation near `|z| → 1`, parameter coalescence,
slow-convergence in the `p == q + 1` band.

## The four failure modes

Pearson-Olver-Porter 2017 ("Numerical methods for the computation of
the confluent and Gauss hypergeometric functions", *Numerical
Algorithms* 74, 821–866) is the canonical taxonomy. The 2F1 case
covers most modes:

1. **Cancellation in the direct power series.** Alternating signs at
   similar magnitudes — e.g., `2F1(1, 1; 2; −1) = log 2` summed by
   `1 − 1/2 + 1/3 − …`; or large parameters producing leading-digit
   cancellation between the Pochhammer numerator/denominator. The
   tool's `cancellationLoss` tracker (in
   `packages/hypergeometric::pFqDirectSeries`) measures
   `log₂(maxₖ |termₖ|) − log₂(|sum|)`; the outer driver
   (`evaluatePFq`) re-runs at higher working precision when the
   loss exceeds the request margin. **Tier B** (large parameters)
   and **Tier D** (parameter coalescence) probe this path. The
   `t0-2F1-log-zneg-half` case is the cancellation reduction of
   `2 log(3/2)` from the alternating series.

2. **Oscillation near `|z| → 1`.** For `p == q + 1`, the radius of
   convergence is the unit disc; the term ratio is `|z|` per step,
   so to drive `|term_k| < 2^{-N}` we need `k > N · ln 2 / ln(1/|z|)`.
   At `|z| = 0.99` and `N = 196` bits (≈ 50 dps), that's `~13 600`
   summands; the tool's iteration cap is sized analytically (see
   `pfq.ts` `analyticCount`), but the cancellation across alternating
   signs in this slow-convergence regime is what the bench checks.
   **Tier C** is dedicated to this regime, with cases at
   `|z| ∈ {0.85, 0.90, 0.95, 0.97, 0.98}`. The boundary at
   `|z| ≥ 0.99` is the tool's documented refusal — see Tier E.

3. **Parameter coalescence.** When a numerator parameter `aⱼ` equals
   a denominator parameter `bⱼ` (or differs by a non-positive
   integer), the corresponding Pochhammer ratios cancel exactly in
   the limit — but the direct series, evaluated *without* taking
   that limit, exhibits enormous cancellation as the recurrence
   approaches it. The tool's bumped-precision retry must surmount
   this. The Slater residue-summation handles the limit analytically;
   we don't go that route in v0.1 (deferred to a `Slater` shard).
   **Tier D** has 5 cases. The `tD-2F1-coalesce-a-eq-c` case is
   `2F1(1, 3/2; 3/2; z)` where `a₂ = b₁` exactly — the tool reduces
   to `1F0(1;;z) = (1−z)^{−1}`, but only via the bumped-precision
   *retry* (the closed-form fast path doesn't trigger because
   `p ≠ 1`).

4. **Slow series convergence in the `p == q + 1` band.** Over and
   above the oscillation, the term magnitude itself decays only as
   `|z|^k`, not factorially — so achieving 50 dps at `|z| = 0.95`
   requires summing thousands of terms. **Tier C** probes this; the
   workshop's iteration-cap sizing must be honest. A tool that
   defaults to a small `maxTerms` cap and silently truncates would
   pass Tier A but fail every Tier C case beyond `|z| = 0.85`.

The bench discriminates: a tool that handles all four modes passes;
one that handles three fails ≥3 cases.

## Why the tier sizes

Per ADR-0019 §7 the seven-tier skeleton is for solve-class problems;
`hypergeometric-pfq` is a numerical-tier tool, so the tier structure
deviates and is documented here.

| Tier | Cases | Rationale |
|------|-------|-----------|
| 0    | 11    | One case per closed-form anchor reduction (`exp`, `(1−z)^{−a}`, `−log(1−z)/z`, `(eᶻ−1)/z`, `cos(z)`, `sinc(z)`). Two values per anchor probes the closed-form path *across* the parameter sub-space — `exp(1)` vs `exp(5)` vs `exp(−5)` all flow through the closed-form-0F0 branch but exercise different bigfloat-arithmetic regimes (small/medium/large). |
| A    | 15    | The most-populated tier because it's the union of `0F1, 1F1, 2F1, 3F2` happy-path probes — pFq's complete admitted shape. Includes one `precision = 100` case (`tA-pfq-precision100`) to verify the `--precision` flag actually flows through and the higher-precision result is faithful. |
| B    | 10    | Calibrated to expose the cancellation-driven precision-bump retry: parameters `a, b ∈ [10, 100]` with mid-magnitude `z`. `tB-2F1-largeneg-z` (`2F1(20, 30; 25; −0.5)`) is the cancellation hot spot — alternating signs at similar magnitudes; the retry must fire and surmount. |
| C    | 8     | Eight `|z| ∈ [0.85, 0.98]` cases including one negative-real (`tC-2F1-neg-095`) and one complex (`tC-2F1-complex-085`). The complex case probes the cancellation regime in 2D (two coordinates can each be near unity in magnitude even if `|z|` is moderate). |
| D    | 5     | Five integer-spaced or near-integer-spaced parameter combinations. The discriminator is whether the bumped-precision retry succeeds within the default 4 retries. `tD-2F1-near-coalesce` (`2F1(2, 3; 3.01; 0.4)`) is the near-coalescence stress: a tool that doesn't honour the cancellation tracker fails this case. |
| E    | 4     | Four refusal envelopes: `p > q + 1`, `\|z\| ≥ 0.99` with `p == q + 1`, `1F0(a;;1)` (closed-form singular), and the boundary `2F1(1, 1; 2; −1) = log 2` case. The last is documented as a v0.2-followup analytic-continuation target; the tool's conservative refusal is the *correct* v0.1 behaviour. |

Total **53 cases**. Larger than the bead spec's "~50" — the extra 3
come from the sub-anchor coverage in Tier 0 (multiple `z` values per
identity).

## Why these tolerances

Per the structure of mpmath's own evaluator (Johansson 2009 — mpmath
internal-precision conventions): a `dps = N` evaluation produces
relative error `≤ 10^{−(N − ε(input))}` where `ε(input)` is the
case-specific cancellation overhead in dps. The bench's
`tolerance_rel` field encodes that overhead per tier:

  * Tier 0, A — `1e-(precision − 2)` — well-conditioned cases lose
    only 2 dps to roundoff in mpmath's own arithmetic at the truth
    layer.
  * Tier B — `1e-(precision − 4)` to `1e-(precision − 6)` — large-
    parameter cancellation can eat 4-6 dps; the precision-bump retry
    surmounts but the residual roundoff is real.
  * Tier C — `1e-(precision − 6)` at `|z| = 0.85`, relaxing to
    `1e-(precision − 10)` at `|z| = 0.98` — slow convergence
    accumulates 6-10 dps of summand-roundoff noise across thousands
    of summed terms.
  * Tier D — `1e-(precision − 4)` to `1e-(precision − 6)` —
    coalescence cancellation is bounded by the retry's safety margin.

The verifier's reference is **mpmath at `dps = max(80, precision +
30)`**, which clears every case's tolerance by ≥ 20 orders of
magnitude. Cross-validation against Wolfram at the same precision
confirmed agreement on 49 of 49 numerical cases (one case at
`precision = 100` requires the consensus threshold to be conditioned
on `min(precision, wolf_dps - 10)` to absorb Wolfram's residual
roundoff at its own internal precision — see
`reference/generate-mpmath-truth.py::consensus`).

## Why the wire format uses string-decimals (not JSON numbers)

The whole point of `arbprec: true` is that the value's precision is
not bounded by `Number.MAX_SAFE_INTEGER` or `Number`'s 52-bit
mantissa. JSON numbers are 64-bit floats; encoding a 50-dps value as
a JSON number floors it to 16 dps. The bench is no exception — every
real / imaginary part in `inputs.json` and `expected.json` is a
decimal-string (or rational-string `"p/q"`, expanded by the adapter
at the working precision). This matches PRD §0.1 and the wider
workbench's number-bearing-fields-are-strings convention.

## Why we run via `executeToolDef`, not `wb.run(...)`

`@workbench/compose`'s `runWorkbench` validates the caller's `flags`
against `def.flags ?? {}`. For an `arbprec: true` tool that declares
no flags of its own, the runner-merged `--precision` flag (ADR-0020)
is invisible to compose, and passing `{ precision: 50n }` is rejected
as an unknown flag. This is a known gap (filed as a follow-up bead);
until it lands the bench bypasses the wrapper and invokes
`executeToolDef` directly. The seven-step contract — schema validation
in/out, provenance write — is byte-identical (ADR-0012); the in-process
and subprocess surfaces both fan out to `executeToolDef`. So the
bench's results are admissible.

## Why no third-witness adjudication for the disagreement cases

ADR-0019 §3 names three oracles: Wolfram, SymPy, Sage. SymPy's
`mpmath.hyper` is the same evaluator we're already using as primary
oracle (mpmath is mpmath, regardless of via SymPy or directly).
SageMath would be a genuine third witness but is not currently
installed on the workbench host; the workbench's policy is "drop
disagreements rather than pick" (ADR-0019 §3 final paragraph). The
generator records every disagreement with relative magnitude in
`expected.json::cases[i].consensus.rel_disagreement`; the current
build records *zero* disagreements at the `cmp_dps` threshold (49 of
49 mpmath/Wolfram agree; 4 are structural-refusal cases).

If a future precision sweep surfaces a Wolfram/mpmath disagreement
above the bench's tolerance, the run is *aborted* by the generator
(disagreement at `cmp_dps`); the human investigator picks the
witness via Sage when it's available, otherwise the case is
quarantined.

## Mutation-prove discipline (CLAUDE.md Rule 6)

The bench's `golden/test_mutations.py` demonstrates RED on five
characteristic perturbations of the candidate output, proving the
verifier *would have caught* a regression:

  1. flip a Tier 0 truth value to `2 · truth` → confirm the bench
     reports `value_accuracy` failure.
  2. flip a Tier E expected refusal to `value` shape → confirm the
     bench reports failure on the unexpected envelope.
  3. tighten a Tier C tolerance from `1e-44` to `1e-50` → confirm
     the bench reports failure on the (previously passing)
     `|z| = 0.95` case.
  4. set `achieved_precision` to `requested + 5` → confirm the
     `self_reported_precision` check rejects over-reporting.
  5. flip a Tier 0 candidate `method` from `"direct-series"` to
     `"unknown-method"` → confirm `method_admissible` rejects.

These run as part of `bun run check` via `bench/_corpus/run-mutation-
tests.sh` (no — see ADR-0019 §6: this is an ADR-0019 §4 requirement
for solve-tier benches, but the workbench's wider `bun run check`
hasn't yet been wired with a mutation-prove phase. The five mutations
are stored as a Python script that can be run manually; future
workbench gating will pick them up).

## What the bench does not test (deferred)

- **Asymptotic regime** (`p > q + 1`, `|z| ≥ 0.99` with `p = q + 1`).
  These all refuse with `non-convergent` in v0.1; the future
  Braaksma asymptotic path (hv0.9 by parallel agent) and analytic-
  continuation path (hv0.10) will reclaim them. When that lands the
  bench's Tier E will shrink, Tier C grows, and a new Tier F may
  appear with the asymptotic regime.
- **Mellin-Barnes contour evaluation** — `packages/meijer-core` has
  the contour layer (ADR-0022); it composes `pFq` calls but isn't
  itself this tool's responsibility.
- **Performance regressions.** The bench is a correctness gate; the
  cost-vs-precision graph (e.g., 5700 summands at `|z| = 0.98`,
  50 dps) is computed and reported as `n_terms` in each output but
  is not asserted as a tier — performance is a follow-up shard.

## What this bench taught us about the tool

Two things, surfaced during golden generation:

1. **`2F1(1, 1; 2; −1) = log 2` is on the boundary.** The tool's
   `|z| ≥ 0.99` cutoff is correct for v0.1 but loses the famous
   Abel-summation identity. Tier E captures this as documented
   "graceful refusal of an admissible identity"; reclaiming it is a
   v0.2 deliverable filed in `BEADS-TO-FILE.txt`.

2. **`compose.runWorkbench` doesn't merge ADR-0020's standard
   `--precision` flag.** The bench's adapter routes around it via
   `executeToolDef`; long-term the merge needs to land in compose
   so the typed barrel `wb.hypergeometricPfq(input, { precision:
   50n })` works. Filed in `BEADS-TO-FILE.txt`.
