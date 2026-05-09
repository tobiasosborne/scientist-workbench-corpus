# `linalg-svd` verifier protocol

The verifier reads `{input, candidate, id?}` on stdin and emits
`{pass, reason, checks: {<name>: {pass, detail}}}`. Every check is
computed independently; a case is overall `pass: true` iff every
check passes.

## Constants

- `EPS` = `2.220446049250313e-16` (IEEE-754 double machine epsilon).
- `SAFETY` = `100` (multiplicative slack on Higham 2002 §20.3 bounds).
- `SELF_REPORT_REL_TOL` = `1e-6` — tolerance for self-reported
  diagnostic fields agreeing with the verifier's recomputation.

## The 8 checks

### 1. `shape`

Pure structural. Given input `m × n` and `mode`, with `k = min(m, n)`:
- `mode = "reduced"`: `U` is `m × k`, `S` has length `k`,
  `Vt` is `k × n`.
- `mode = "complete"`: `U` is `m × m`, `S` has length `k`,
  `Vt` is `n × n`.

Plus required output fields present with correct types: `U`, `S`,
`Vt`, `mode`, `reconstruction_error`, `orthogonality_error_U`,
`orthogonality_error_Vt`, `condition_number`, `rank_estimate`,
`method`, `warnings`. `mode` echoes input (or defaults to
`"reduced"`); `method` is `"golub-reinsch"` or `"one-sided-jacobi"`
or another self-described value (the verifier accepts any string,
but the catalog row constrains to documented choices).

### 2. `finite_entries`

Every entry of `U`, `S`, `Vt`, and the scalar diagnostic fields
(`reconstruction_error`, `orthogonality_error_U`,
`orthogonality_error_Vt`, `condition_number`) is finite. `Infinity`
or `NaN` anywhere fails. (`rank_estimate` must be a non-negative
integer.)

### 3. `S_nonneg_descending`

For all `i ∈ [0, k)`: `S[i] ≥ -tol_struct` where
`tol_struct = 100 · EPS · max(S[0], 1)`. (Allows tiny negatives from
roundoff to count as zero, but no genuinely negative singular values.)

For all `i ∈ [0, k − 1)`: `S[i] + tol_struct ≥ S[i + 1]`.
(Allows LSB ties to satisfy non-increasing.)

### 4. `U_orthonormal`

`||UᵀU − I_q||_F ≤ tol_orth` where `q = U.shape[1]`,
`tol_orth = SAFETY · EPS · m · √q`.

Backward stability: both Golub-Reinsch and Jacobi achieve
`O(EPS)` orthogonality independent of `κ(A)` (Demmel-Kahan 1990;
Demmel-Veselić 1992).

### 5. `Vt_orthonormal`

`||Vt · Vtᵀ − I_q||_F ≤ tol_orth` where `q = Vt.shape[0]`.

(`Vᵀ` has *orthonormal rows*, so the check is `Vt · Vtᵀ ≈ I`,
not `Vtᵀ · Vt ≈ I`. The latter would be `n × n`, which is
larger and only the identity for `mode = "complete"`.)

### 6. `factorisation_residual`

`||U · diag(S) · Vt − A||_F ≤ tol_recon · ||A||_F` with
`tol_recon = SAFETY · EPS · max(m, n) · √(min(m, n))`.

For `A = 0`, requires exact zero residual. (Trivially satisfied
since `U · 0 · Vt = 0`.)

### 7. `self_reported_residual`

Candidate's `reconstruction_error` agrees with the verifier's
recomputation:

  `recomputed = ||U · diag(S) · Vt − A||_F / max(||A||_F, 1)`
  `|reported − recomputed| ≤ SELF_REPORT_REL_TOL · max(recomputed, EPS)`

### 8. `self_reported_orthogonality`

Both `orthogonality_error_U` and `orthogonality_error_Vt` agree
with the verifier's recomputation to `1e-6` relative. Either
failure fails the combined check.

## Failure-reason format

When a check fails, `detail` includes the tolerance, the actual
violating value, and (for multi-element checks) the index of the
first violating element.

## What's deliberately NOT checked

- **Exact match against LAPACK `S`.** The honest tolerance band is
  `O(EPS · κ(A))`, which exceeds `1` for `H_50`. Including such
  a check would either reject correct implementations on
  ill-conditioned cases or admit incorrect ones. The 6-invariant
  numerical core (`shape`, `finite`, `S_nonneg_descending`,
  `U_orthonormal`, `Vt_orthonormal`, `factorisation_residual`)
  characterises a valid SVD; that's enough.
- **Exact match against LAPACK `U`/`Vᵀ`.** Even more freedom: SVD
  is unique only up to column-sign flip and (for repeated singular
  values) subspace rotation. The orthogonality + factorisation
  checks already encode the meaningful constraints.
- **`condition_number` exactness.** Self-report; cross-checked by
  the verifier as part of `finite_entries` (must be a finite
  positive number) but not exactly compared to `S[0] / S[k-1]`
  (numerical roundoff in the division leaves a small honest
  disagreement).
- **`rank_estimate` exactness.** Self-report; cross-checked
  against the LAPACK threshold but with a one-step slack to
  accommodate cases where a singular value sits exactly on the
  threshold.
