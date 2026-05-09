# `linalg-qr` verifier protocol

The verifier (`verify.py`) reads `{input, candidate, id?}` on stdin
and emits `{pass, reason, checks: {<name>: {pass, detail}}}`. Every
check is computed independently; a case is overall `pass: true` iff
every check passes.

## Constants

- `EPS` = `2.220446049250313e-16` (IEEE-754 double machine epsilon).
- `SAFETY` = `100` (multiplicative slack on Higham's bounds; see
  `DESCRIPTION.md` for the empirical justification).
- `SELF_REPORT_REL_TOL` = `1e-6` — tolerance for the candidate's
  self-reported `reconstruction_error` and `orthogonality_error`
  agreeing with the verifier's recomputation.

All tolerances are documented inline in `verify.py` next to the
check that consumes them.

## The 7 checks

### 1. `shape`

Pure structural. Given input `m × n` and `mode`:
- `mode = "reduced"`: `Q` must be `m × min(m, n)`, `R` must be
  `min(m, n) × n`.
- `mode = "complete"`: `Q` must be `m × m`, `R` must be `m × n`.

Plus: `Q` and `R` are nested lists of JSON numbers; output object
contains the required fields (`Q`, `R`, `mode`, `diagonal_R`,
`reconstruction_error`, `orthogonality_error`, `method`,
`warnings`); `method` equals `"householder"`; `mode` echoes the
input mode (or defaults to `"reduced"` if input omitted it).

### 2. `finite_entries`

Every entry of `Q`, `R`, `diagonal_R`, `reconstruction_error`, and
`orthogonality_error` is a finite IEEE-754 double (no `NaN`, no
`±Inf`). Reported separately because the failure mode is distinct
from "wrong values".

### 3. `R_upper_triangular`

`R` is upper triangular below its diagonal (within the `min(m, n)`
sub-block). The check:

  `for i > j (within min(m,n) block): |R[i,j]| ≤ tol_struct · max(‖A‖_F, 1)`

where `tol_struct = SAFETY · EPS · max(m, n)`.

The `max(‖A‖_F, 1)` is to keep the tolerance sensible for the
all-zeros input.

For `mode = "complete"` with `m > n`, the bottom `m − n` rows of
`R` must be exactly zero (LAPACK convention). Same tolerance applies.

### 4. `Q_orthonormal`

`Q`'s columns are orthonormal:

  `‖QᵀQ − I_k‖_F ≤ tol_orth`

where `k` is `Q`'s column count and
`tol_orth = SAFETY · EPS · m · √k`.

Higham 2002 Thm 19.4: for Householder, `‖Q̃ᵀQ̃ − I‖_F ≤ c(m) · ε`
*independent of `κ(A)`*. This is the discriminator: MGS would have
`O(κ · ε)` here and fail on Hilbert; CGS would have `O(κ² · ε)` and
fail on Hilbert-6.

### 5. `factorisation_residual`

Reconstruction holds:

  `‖Q · R − A‖_F ≤ tol_recon · ‖A‖_F`

where `tol_recon = SAFETY · EPS · max(m, n) · √(min(m, n))`.

For all-zeros `A`, the bound becomes `tol_recon · 0 = 0`, so the
check requires exact zero — but Householder of a zero matrix
produces exact zero `R`, and any orthogonal `Q` works (we accept
identity or any Householder reflector applied to nothing).

### 6. `self_reported_residual`

The candidate's `reconstruction_error` field agrees with the
verifier's recomputation:

  `recomputed = ‖Q · R − A‖_F / max(‖A‖_F, 1)`
  `|reported − recomputed| ≤ SELF_REPORT_REL_TOL · max(recomputed, EPS)`

The `max(recomputed, EPS)` is to handle `recomputed = 0` exactly.

### 7. `self_reported_orthogonality`

The candidate's `orthogonality_error` field agrees with the
verifier's recomputation:

  `recomputed = ‖Qᵀ · Q − I_k‖_F`
  `|reported − recomputed| ≤ SELF_REPORT_REL_TOL · max(recomputed, EPS)`

## Failure-reason format

When a check fails, `detail` includes:
- The check's tolerance value.
- The actual value that was out of tolerance.
- For shape failures: the expected and actual shapes.
- For multi-element checks (R upper triangularity, diagonal
  matching): the index of the first violating element.

This is the diagnostic floor for an agent debugging a failed run.
