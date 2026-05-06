# `linalg-eigh` verifier protocol

The verifier reads `{input, candidate, id?}` on stdin and emits
`{pass, reason, checks: {<name>: {pass, detail}}}`. Every check
is computed independently; a case is overall `pass: true` iff
every check passes.

## Constants

- `EPS` = `2.220446049250313e-16` (IEEE-754 double machine epsilon).
- `SAFETY` = `100` (multiplicative slack on Higham 2002 §20.6 bounds).
- `SELF_REPORT_REL_TOL` = `1e-6` — tolerance for self-reported
  diagnostic fields agreeing with verifier recomputation.
- `SYMMETRY_TOL_FACTOR` = `100 · EPS` — relative tolerance for
  considering input symmetric (`max|A − Aᵀ| ≤ SYMMETRY_TOL_FACTOR ·
  max|A|`).

## The 7 checks

### 1. `shape`

Pure structural. Given input `n × n`:
- `Q` is `n × n` (nested list of n lists of n JSON numbers).
- `eigenvalues` is a list of length `n` of JSON numbers.
- Required output fields present with correct types: `Q`,
  `eigenvalues`, `reconstruction_error`, `orthogonality_error`,
  `condition_number`, `method`, `warnings`.
- `method` is a string; `warnings` is `list[str]`.

### 2. `finite_entries`

Every entry of `Q`, `eigenvalues`, `reconstruction_error`,
`orthogonality_error`, `condition_number` is a finite double.

### 3. `eigenvalues_ascending`

For all `i ∈ [0, n − 1)`: `λ[i] ≤ λ[i+1] + tol_struct` where
`tol_struct = SAFETY · EPS · max(|λ_max|, 1)`. Allows LSB ties
to satisfy non-decreasing.

(numpy / LAPACK convention is ascending; we mirror that.)

### 4. `Q_orthonormal`

`||QᵀQ − I_n||_F ≤ tol_orth` where
`tol_orth = SAFETY · EPS · n · √n`.

Independent of `κ(A)` for both Jacobi and tridiag+QR (Wilkinson 1965;
Higham 2002 §20.6). Failure here means the algorithm lost
orthogonality during sweeps — typically a Givens-rotation accumulation
bug, or convergence accepted before off-diagonal Frobenius norm
reached the tolerance.

### 5. `eigendecomp_residual`

`||A · Q − Q · diag(λ)||_F ≤ tol_recon · ||A||_F` with
`tol_recon = SAFETY · EPS · n · √n`.

For `A = 0`, requires exact zero residual (trivially satisfied since
`0 · Q = 0` and `Q · diag(0) = 0`).

This is the *defining* equation of the eigendecomposition — failure
here is a failure of the eigh contract regardless of how nice `Q`
and `λ` look individually.

### 6. `self_reported_residual`

Candidate's `reconstruction_error` agrees with verifier
recomputation:

  `recomputed = ||A·Q − Q·diag(λ)||_F / max(||A||_F, 1)`
  `|reported − recomputed| ≤ SELF_REPORT_REL_TOL · max(recomputed, EPS)`

### 7. `self_reported_orthogonality`

Candidate's `orthogonality_error` agrees with verifier recomputation
to `1e-6` relative.

## Tagged-boundary checks

For tagged outputs (`linalg-eigh/non-symmetric-input`,
`linalg-eigh/non-finite-input`, `linalg-eigh/degenerate-shape`),
the verifier accepts the boundary iff:
- The input *is* the relevant boundary case (e.g. `A` is
  asymmetric beyond `SYMMETRY_TOL_FACTOR · max|A|` for
  `non-symmetric-input`).
- The tagged payload has the documented shape (e.g. for
  `non-symmetric-input`: `{row, col, value}` plus
  `max_asymmetry`).

Tagged outputs that don't match the input's actual boundary
category fail with reason `"misclassified boundary"`.

## Failure-reason format

When a check fails, `detail` includes the tolerance, the violating
value, and (for multi-element checks) the index of the first
violating element. Standard verifier diagnostic floor.

## What's NOT checked

- **Exact match against LAPACK eigenvalues.** Same reasoning as the
  dropped `singular_values_match` from `bench/linalg-qr` (worklog
  043 friction): for a backward-stable algorithm, eigenvalue
  forward error is `O(κ · ε)`. On `H_50` with `κ > 10¹⁸`, that
  exceeds 1 — so an honest tolerance band would also admit
  incorrect implementations. The 5-invariant numerical core
  (`shape`, `finite`, `eigenvalues_ascending`, `Q_orthonormal`,
  `eigendecomp_residual`) is already tight: a passing candidate
  *is* a valid symmetric eigh of `A`.
- **Exact match against LAPACK eigenvectors.** Eigenvectors are
  unique only up to: (a) sign flip on each column; (b) for repeated
  eigenvalues, arbitrary rotation in the eigenspace. The
  orthogonality + residual checks already encode the meaningful
  constraints.
- **`condition_number` exactness.** Self-report; sanity-checked as
  finite by `finite_entries`. Computing `|λ_max| / |λ_min|` from
  the candidate's eigenvalues is so cheap that exact comparison
  isn't useful — any differences are LSB roundoff in the division.
