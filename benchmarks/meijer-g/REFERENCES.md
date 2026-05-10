# References — `bench/meijer-g`

The truth values for this bench come from a curated set of primary
sources, with cross-validation by independent oracles per
[`tstournament/.../problem-13/ORACLE-STRATEGY.md`](../../../tstournament/ts-bench-infra/problems/13-meijer-g/ORACLE-STRATEGY.md).

## Truth-value oracles

* **mpmath** (Johansson & contributors). `mpmath.meijerg([[an], [ap]],
  [[bm], [bq]], z)` at `mp.dps = 110`. The primary oracle for tiers
  A–F. Internal algorithm: Slater residue summation with
  `mpmath.hyper` / `hypercomb`, parameter perturbation (`hmag`) for
  coalescent inputs, automatic Series-1/Series-2 selection.
  https://mpmath.org/doc/current/functions/hypergeometric.html
* **wolframscript** (Wolfram Research). `MeijerG[{{an}, {ap}}, {{bm},
  {bq}}, z]` evaluated at 110 dps via `N[..., 110]` inside
  `Block[{$MaxExtraPrecision = 5000}, …]`. The independent witness for
  Wolfram + mpmath consensus.

## Tier-0 closed-form anchors

The Tier-0 anchor RHS values are computed *directly from the
elementary closed form* at `mp.dps = 200`, bypassing any MeijerG
codepath in either oracle. Sources for the identities:

* **Erdélyi A. et al. (1953)**, *Higher Transcendental Functions* Vol. I
  (the "Bateman manuscript project"). §5.6 — elementary reductions of
  the Meijer G-function: `G^{1,0}_{0,1}(_; b | z) = z^b · e^{-z}`,
  `G^{1,1}_{1,1}(a; b | z) = Γ(1+b−a) · z^b · (1+z)^{a−b−1}`, etc.
  Cited per-anchor in `reference/generate-truth.py` and in the local
  rule files
  [`packages/meijer-core/src/dispatch-rules/bateman-5-6.ts`](../../packages/meijer-core/src/dispatch-rules/bateman-5-6.ts).
* **DLMF (2024)**, NIST Digital Library of Mathematical Functions.
  §16.17 (Definition), §16.18 (Reductions to elementary and
  named-special functions); especially §16.18.E1 (general pFq via the
  one-pole reduction). Cited per-anchor; mirrored in
  [`packages/meijer-core/src/dispatch-rules/dlmf-16-18.ts`](../../packages/meijer-core/src/dispatch-rules/dlmf-16-18.ts).
* **Wolfram Functions Site**,
  http://functions.wolfram.com/HypergeometricFunctions/MeijerG/
  — independent index of the Bateman / DLMF reductions plus
  contemporary cross-references; used as a cross-check on the rule
  identities (not on the numerical values).

## Tier-A / Tier-B / Tier-C / Tier-D / Tier-E / Tier-F numerical truths

Per the two-oracle consensus protocol, each non-Tier-0 numerical case
has its truth pinned to the consensus of mpmath at 110 dps and
Wolfram at 110 dps.  The two values must agree to ≥ 80 significant
figures (the threshold absorbs Wolfram's variable trailing-digit guard
and mpmath's `~10-bit hextra` margin); cases where they disagree get
flagged in `golden/oracle-disagreements.log` and skipped (no
silently-pinned single-oracle truth).

When Wolfram is unavailable / times out / refuses (e.g. on the negative
real axis where the convention may differ), the truth falls back to
mpmath-only at 110 dps.  These cases are flagged with
`truth_method = "mpmath-only@110dps"` in `expected.json`; the bench's
tolerance is unchanged but the lack of independent witness is honest.

## Algorithmic references (for the implementation under test)

* **Slater LJ (1966)**, *Generalized Hypergeometric Functions*. Ch. 5
  — residue-summation derivation of `MeijerG` from the Mellin–Barnes
  contour integral.  The reference for the dispatcher's Slater lane.
* **Johansson F (2009)**, "Computing hypergeometric functions
  rigorously" (mpmath blog). The `hmag` perturbation trick for
  parameter coalescence; the cancellation-driven precision-bump.
* **Braaksma BLJ (1964)**, "Asymptotic expansions and analytic
  continuations for a class of Barnes integrals". *Compositio Math.*
  15, 239–341. The asymptotic-lane reference for `|z| → ∞`.
* **Adamchik VS, Marichev OI (1990)**, "The algorithm for calculating
  integrals of hypergeometric type functions and its realization in
  REDUCE system". *Proc. ISSAC '90*, 212–224. The pattern-table-
  reduction approach the dispatcher's symbolic lane implements.
* **Olver FWJ (1974)**, *Asymptotics and Special Functions*. §3.7 —
  superasymptotic truncation (stop when `|t_{k+1}| ≥ |t_k|`); the
  Braaksma lane's truncation rule.
* **Pearson J, Olver S, Porter MA (2017)**, "Numerical methods for the
  computation of the confluent and Gauss hypergeometric functions".
  *Numerical Algorithms* 74, 821–866.  Inner-pFq taxonomy that the
  bench's Tier C/D inherits.

## Bench discipline

* **ADR-0019**, [`docs/adr/0019-solve-bench-discipline.md`](../../docs/adr/0019-solve-bench-discipline.md)
  — bench shape (golden master directory, two-oracle consensus,
  mutation-prove discipline).
* **ADR-0020**, [`docs/adr/0020-arbprec-determinism-tier.md`](../../docs/adr/0020-arbprec-determinism-tier.md)
  — `arbprec: true` determinism contract;  `--precision=<int>` standard
  flag; bit-identical cross-platform forever.
* **ADR-0027**, [`docs/adr/0027-meijerg-dispatcher.md`](../../docs/adr/0027-meijerg-dispatcher.md)
  — dispatcher design pin; the bench validates this contract.

## Sibling benches

* [`bench/hypergeometric-pfq/`](../hypergeometric-pfq/) — the inner
  pFq path that Slater + asymptotic + dispatcher all consume.
  `bench/meijer-g`'s shape is modelled after it.
* [`bench/linalg-qr/`](../linalg-qr/) — the canonical numerical-tier
  bench shape.
* [`bench/integrate-ode-ivp/`](../integrate-ode-ivp/) — the canonical
  long-batch verifier shape with per-tier tolerance ladders.
