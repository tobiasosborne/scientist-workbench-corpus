# `linalg-svd` — design notes and rationale

Companion to `PROMPT.md`. This document defends the design.

## Why SVD specifically (vs eigendecomposition or just QR)

SVD is the *complete* linear-algebraic factorisation of a
rectangular matrix:

  `A = U · diag(S) · Vᵀ`

with `U`, `V` orthogonal and `S ≥ 0` non-increasing. Every other
factorisation can be derived from it: rank from the count of
nonzero `S_i`; least-squares solution from `V · diag(1/S) · Uᵀ b`
(pseudo-inverse); spectral decomposition of `AᵀA` from `V`,
`S²`; principal components, low-rank approximations, condition
number, all of these read off directly.

This is what makes SVD the *general-purpose rank-revealing tool*
in the numerical tier:

- `linalg-solve` solves `A x = b` for square non-singular `A`;
  fails (correctly, with `tagged "linalg-solve/singular"`) on
  singular `A`.
- `linalg-qr` factorises any `A` but doesn't *reveal* rank — it
  produces a triangular `R` whose diagonal entries are pivot
  magnitudes, not singular values.
- `linalg-svd` factorises any `A` *and* reveals rank as the count
  of singular values above the LAPACK-standard threshold.

So `linalg-svd`'s output record is richer than `linalg-qr`'s: it
includes `condition_number` and `rank_estimate` as first-class
fields, not derivable post-hoc by the agent.

## Why the agent-honest output

`linalg-solve` set the precedent (ADR-0014, worklog 031);
`linalg-qr` followed (worklog 043). The output of a numerical-tier
tool is *not* just the answer, it is a record that lets a planner
decide whether to trust the answer:

```
{
  U, S, Vt, mode,
  reconstruction_error,        // ||U·diag(S)·Vt − A||_F / max(||A||_F, 1)
  orthogonality_error_U,       // ||UᵀU − I||_F
  orthogonality_error_Vt,      // ||Vt·Vtᵀ − I||_F
  condition_number,            // S[0] / max(S[k-1], EPS·S[0])
  rank_estimate,               // count of S_i > max(m,n)·EPS·S[0]
  method,                      // "golub-reinsch" or "one-sided-jacobi"
  warnings                     // soft strings
}
```

A planner reading this output decides:

- "Reconstruction below `1e-12`, both orthogonality errors below
  `1e-13` — trust the SVD."
- "`condition_number = 1e15` — `A` is numerically singular; the
  least-squares solve via `V · diag(1/S) · Uᵀ b` will amplify
  noise by 15 orders of magnitude. Truncate `S` at the noise
  floor before pseudo-inverting."
- "`rank_estimate = 7` for an `8 × 4` input — `A` is full row rank.
  Good." Or "`rank_estimate = 3` for an `8 × 4` — there's a
  near-collinearity between two rows; the user's data may have a
  duplicate."
- "`warnings` includes `'condition number 4e15 near machine
  precision'` — escalate the precision warning to the caller."

The two principles say a TS expert would type `svd(A)` and want
all of this back. So that's what we ship.

## Why these specific test tiers

The seven tiers map to specific failure modes the algorithm class
admits:

### A. Shape edges (10 cases)

Indexing failures (off-by-one in the bidiagonalization, wrong
`U` columns retained for `m < n`, etc.) surface here without
numerical noise. `1×1`, `2×1`, `1×2`, identities, all-zero `2×2`,
`5×3` (tall), `3×5` (fat), `100×100` and `200×200` identities
(boundary).

### B. Random well-conditioned (8 cases)

Happy-path. Failure here means a bug in the core algorithm itself.
Sized to also exercise the `n ≤ 200` cap.

### C. Hilbert (7 cases)

`H_ij = 1 / (i + j − 1)`. Famously ill-conditioned: `κ(H_n) ≈
(1 + √2)^{4n} / √(πn)`. For `n ∈ {4, 6, 8, 10, 12, 20, 50}`,
`κ` ranges from `~10^4` to `~10^{18}`. The orthogonality bounds
on `U` and `Vᵀ` are independent of `κ` for both Golub-Reinsch
and Jacobi, so this tier is about whether the algorithm *converges*
under extreme conditioning — buggy implicit-shift sweeps drift on
ill-conditioned bidiagonals; one-sided Jacobi sweeps just take
more iterations.

### D. Vandermonde (4 cases)

`V_ij = x_i^{j-1}` for nodes `x_i = (i − 1) / (n − 1)`. Also
exponentially ill-conditioned, structurally distinct from Hilbert
(diagonal-dominance pattern is different). An SVD that hard-codes
column-norm assumptions will fail Vandermonde even after passing
Hilbert.

### E. Wilkinson / Pei / Frank (5 cases)

Structural test cases:
- **Wilkinson `W^+_n`**: tridiagonal with clustered eigenvalues
  near the centre. SVD of a symmetric matrix gives `S = |λ|` with
  `U = V`; this checks the algorithm doesn't break on symmetry.
- **Pei matrix `αI + eeᵀ`**: rank-one update of identity. `S`
  should have `n − 1` values clustered at `1` and one at `n + 1`.
- **Frank matrix**: lower-Hessenberg, one famously small singular
  value. Stress-tests accurate computation of small `S`.

### F. Rank-deficient (5 cases)

The defining test for SVD: it must produce *exactly* zero (or below
the noise floor) singular values for rank-deficient inputs, and
`rank_estimate` must reflect the true rank. This is what
distinguishes `linalg-svd` from `linalg-qr` — QR survives
rank-deficiency but doesn't reveal it; SVD reveals it.

The five cases:
- Rank-1 outer product `u vᵀ` (`u`, `v` random `5×1`); should
  give exactly one nonzero singular value.
- `5 × 5` identity with one column zeroed (rank 4); should give
  4 ones and 1 zero.
- `8 × 4` matrix with two near-equal rows (numerical rank 3);
  should give one tiny singular value below the threshold.
- `6 × 6` zero matrix (rank 0); all singular values zero.
- `H_8` with a zero column appended (`8 × 9`, full row rank but
  with a zero column — `S[7]` should be a true zero).

### G. Tall and skinny / short and fat (6 cases)

Asymmetric shapes. Tall `(50, 3) (100, 5) (200, 10)` exercise the
common least-squares regime; fat `(3, 50) (5, 100) (10, 200)` the
underdetermined regime. The `m ≥ n` vs `m < n` shape arithmetic in
SVD is more involved than QR (you must decide whether to bidiagonalize
`A` or `Aᵀ`); this tier flushes those off-by-one bugs.

### H. Complete-mode (4 cases)

A representative case from each of A/B/C/G with `mode: "complete"`.
The extra columns of `U` (for `m > n`) span the orthogonal complement
of `A`'s column space; the extra rows of `Vᵀ` (for `n > m`) span the
null space.

## Why these specific tolerances

Demmel-Kahan 1990 and Higham 2002 §20.3 give the backward-stability
bounds for accurate-singular-value SVD:

  `‖Ũ Σ̃ Ṽᵀ − A‖_F ≤ c_1(m, n) · ε · ‖A‖_F`
  `‖Ũᵀ Ũ − I‖_F ≤ c_2(m, n) · ε`
  `‖Ṽ Ṽᵀ − I‖_F ≤ c_2(m, n) · ε`

with `c_1, c_2` low-degree polynomials in `m, n`. Concretely, the
bench uses:

- `tol_recon = 100 · EPS · max(m, n) · √(min(m, n))` — Higham's
  bound with empirical 100× safety.
- `tol_orth = 100 · EPS · m · √q` (where `q` is the column count
  of the orthogonal factor).
- `tol_struct = 100 · EPS · S[0]` for the singular-value
  monotonicity check (allows LSB ties to satisfy `S[i] ≥ S[i+1]`).

The 100× safety factor is empirically chosen: SciPy LAPACK clears
each bound by ≥1 order of magnitude on the bench.

## Why no `singular_values_match` check

SVD is unique only up to:
- column-sign flips on `U` (which transfer to row-sign flips on
  `Vᵀ`),
- (for repeated singular values) arbitrary rotation in the
  corresponding subspace of `U` and `Vᵀ`.

The singular *values* `S` are unique up to LSB rounding. So in
principle a `singular_values_match` check (compare candidate `S`
to LAPACK's `S` element-wise) would be admissible. Why we don't
include it:

The relative tolerance band needed to admit honest LSB
disagreement is `O(EPS · κ(A))` for the smallest singular values.
On `H_50` with `κ > 10^{18}`, this exceeds `1`, which is the
singular value itself. So the check would either:
- Be tight enough to fail honest implementations on `H_50`, or
- Be slack enough to admit incorrect implementations that fail to
  compute the smallest singular values.

The factorisation residual + orthonormality + monotonicity trio
already characterises a valid SVD — any candidate passing those is
*a* valid SVD of `A`. Adding `singular_values_match` is
overconstraint, not extra signal. (Same reasoning as `linalg-qr`'s
dropped `singular_values_match`, see worklog 043 / Friction 2.)

## Why no NIST Matrix Market for the path-finder

Same reasoning as `linalg-qr` (worklog 043, "Why no NIST Matrix
Market"). The 49 hand-crafted cases span every known failure mode
for the algorithm class. Adding 50 more random matrices doesn't
increase signal. If a future bench version adds harwell-boeing,
the verifier doesn't change — only `inputs.json` grows.
