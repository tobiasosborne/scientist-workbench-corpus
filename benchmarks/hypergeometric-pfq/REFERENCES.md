# References

## Primary references for hypergeometric pFq numerics

1. **Pearson, J. W., Olver, S., & Porter, M. A.** (2017). "Numerical
   methods for the computation of the confluent and Gauss
   hypergeometric functions." *Numerical Algorithms* 74, 821–866.
   DOI: [10.1007/s11075-016-0173-0](https://doi.org/10.1007/s11075-016-0173-0)
   — The canonical taxonomy. §3 enumerates the failure modes used
   to organise the bench's tier structure: cancellation, oscillation
   near `|z| → 1`, parameter coalescence, and slow convergence in
   the `p == q + 1` band.

2. **Slater, L. J.** (1966). *Generalized Hypergeometric Functions.*
   Cambridge University Press. — §4.1 (definition and convergence
   regime by `(p, q)` relation), §4.5 (coalescence limits via the
   Slater residue-summation; the analytic foundation that
   `packages/meijer-core::Slater` operationalises). The radius-of-
   convergence facts `p ≤ q ⇒ ℂ`, `p == q + 1 ⇒ |z| < 1`,
   `p > q + 1 ⇒` asymptotic-only.

3. **Olver, F. W. J., Lozier, D. W., Boisvert, R. F., & Clark, C. W.**
   (eds.) (2010). *NIST Handbook of Mathematical Functions.*
   Cambridge University Press. Online: <https://dlmf.nist.gov>
   — §16.2 (hypergeometric function definition and elementary
   reductions — the closed-form anchors for Tier 0), §16.4
   (hypergeometric polynomials when `aⱼ` is a non-positive integer),
   §16.11 (asymptotic expansions for `|z| → 1` and `p > q + 1`).

4. **Johansson, F.** (2009). "Numerical evaluation of hypergeometric
   functions" (mpmath implementation notes), in *mpmath* — a
   high-precision Python library, version 0.15+. Online:
   <https://mpmath.org/doc/current/functions/hypergeometric.html>
   — Documents mpmath's cancellation-control philosophy (the `dps`
   working-precision-bump retry) which the workbench's
   `evaluatePFq` direct-series + outer-driver pair faithfully
   ports. **Permitted reading: API documentation only. Source code
   is forbidden — see CLAUDE.md "Forbidden" section in the bench
   brief.**

## Connection / continuation references (for future extensions)

5. **Bühring, W.** (1987). "Analytic continuation of the
   hypergeometric series and the Pfaff–Kummer transformation."
   *Journal of Approximation Theory* 50(3), 235–268.
   — Connection formulas at `z ≈ 1`. The boundary refusals in
   Tier E (notably `2F1(1, 1; 2; −1) = log 2`) become happy-path
   cases when the analytic-continuation path lands. Bühring's
   formulas are the algorithmic base.

6. **Becken, W., & Schmelcher, P.** (2000). "The analytic
   continuation of the Gaussian hypergeometric function 2F1(a, b;
   c; z) for arbitrary parameters." *Journal of Computational
   and Applied Mathematics* 126(1–2), 449–478.
   — Modern, computationally-oriented account of the connection
   formulas; the algorithm-of-record for a v0.2 analytic-
   continuation extension to this tool.

7. **Kummer, E. E.** (1836). "Über die hypergeometrische Reihe."
   *Journal für die reine und angewandte Mathematik* 15, 39–83.
   — The 24 transformations of `2F1` (Kummer-related transformations).
   Several Tier C cases at `|z| ∈ [0.85, 0.97]` would be reduced to
   smaller-`|z|` happy-path cases via Kummer transformations in a
   v0.2 build; the v0.1 tool refuses no admissible case.

8. **Kummer, E. E.** (1837). "De integralibus quibusdam definitis et
   seriebus infinitis." *Journal für die reine und angewandte
   Mathematik* 17, 228–242.
   — The 1F1 transformations: `1F1(a; b; z) = e^z 1F1(b−a; b; −z)`.
   Some Tier B 1F1 cases at moderate `|z|` would go through a Kummer
   transformation in a v0.2 build to reduce cancellation.

## Oracle references

9. **Wolfram Research, Inc.** *Mathematica.* `Hypergeometric2F1`,
   `HypergeometricPFQ`, `Hypergeometric0F1`, `Hypergeometric1F1`,
   `MeijerG`. Online:
   <https://reference.wolfram.com/language/ref/HypergeometricPFQ.html>
   — Cross-validation oracle accessed via `wolframscript -code
   'ToString[N[HypergeometricPFQ[{a...}, {b...}, z], dps], InputForm]'`
   on the TIB-Hannover-VPN host. Block-scoped
   `$MaxExtraPrecision = 5000` is required for moderate-or-large
   parameters; otherwise Wolfram emits a closed-form Stirling-tower
   that overflows the default 50-extra-precision budget.

10. **mpmath.** `hyper(a, b, z)`. Online:
    <https://mpmath.org/doc/current/functions/hypergeometric.html>
    — Primary high-precision oracle. The bench runs mpmath at
    `dps = max(80, precision + 30)` so the truth values clear the
    tolerance contracts by ≥ 20 orders of magnitude. **API only;
    source-code reading is forbidden.**

## Bench protocol references

- `docs/adr/0019-solve-bench-discipline.md` — the bench-shape ADR;
  the `DESCRIPTION.md`/`PROMPT.md`/`REFERENCES.md`/`golden/`/`reference/`
  layout this directory mirrors. §1 mathematical-invariant verification,
  §3 triple-witness oracle protocol, §4 mutation-prove requirement,
  §7 tier structure (per-tool customised — see `DESCRIPTION.md` for
  this bench's tier rationale).
- `docs/adr/0020-arbprec-determinism-tier.md` — the `arbprec: true`
  annotation, `--precision=<int>` standard flag, BigInt-substrate
  cross-platform-determinism contract.
- `docs/adr/0007-numerical-precision-field.md` — the precedent for
  per-output precision-field conditioning; the bench's value
  comparison is precision-conditioned.
- `docs/adr/0012-execute-tool-def.md` — the work-case helper
  (`executeToolDef`) that both subprocess and in-process surfaces
  fan out to. The bench bypasses `compose.runWorkbench` and calls
  `executeToolDef` directly because compose doesn't yet merge
  arbprec's standard `--precision` flag.

## sci-wb internal references

- `tools/hypergeometric-pfq/tool.ts` — the candidate, ~190 LOC.
- `tools/hypergeometric-pfq/tool.test.ts` — 15 inner-loop unit tests
  exercising closed-form fast paths, Kummer identities, and the
  refusal envelopes.
- `packages/hypergeometric/src/pfq.ts` — the algorithmic substrate;
  ~410 LOC of literate exposition + direct-series implementation +
  cancellation-driven outer driver.
- `packages/bigfloat/src/{complex,arithmetic,transcendental}.ts` —
  the BigInt substrate.
- `bench/linalg-qr/` — the bench shape this directory mirrors;
  148-LOC `run-candidate.ts`, hand-curated `golden/inputs.json`,
  per-check verifier.
- `bench/integrate-ode-ivp/` — sibling for the closed-vocabulary
  numerical-tier shape.
- `docs/worklog/069-bigfloat-and-pfq-shipped.md` — context for
  hv0.3 (the tool's initial ship).
- `docs/worklog/070-meijer-core-slater.md` — the refactor that
  extracted `@workbench/hypergeometric` from the tool, making this
  bench possible.
- `docs/worklog/077-tanh-sinh-fixed.md` — adjacent arbprec work;
  the integrand contract pitfalls documented there inform the
  bench wire-format design.
