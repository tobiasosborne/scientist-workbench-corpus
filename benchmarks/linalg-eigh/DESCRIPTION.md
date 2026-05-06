# `linalg-eigh` — design notes

Companion to `PROMPT.md`. Defends the design.

## Why symmetric eigh specifically

The symmetric eigendecomposition `A = QΛQᵀ` is the spectral
foundation of every quadratic-form-based numerical method in pure
linear algebra:

- Principal component analysis (covariance matrix is symmetric SPD)
- Quadratic optimisation (Hessians are symmetric)
- Linear stability analysis (Jacobian symmetrisation)
- Mass-spring-damper modal analysis (mass and stiffness are symmetric)
- Quantum-mechanical observables (Hermitian operators)

It is also the *easy* case of the general eigenvalue problem: real
symmetric matrices have real eigenvalues, orthogonal eigenvectors,
guaranteed convergence under classical algorithms, and clean
backward-stability bounds (Wilkinson 1965). The non-symmetric eigen-
problem (`linalg-eig`) is harder — possible complex eigenvalues,
defective matrices with non-orthogonal eigenvectors — and is
deferred to a future iteration of bead `71f`.

For the workbench, `linalg-eigh` completes the trio (solve, qr, svd,
eigh) that covers the vast majority of pure-linear-algebra needs.

## Why Jacobi (the recommended algorithm)

Three textbook options for symmetric eigh:

1. **Jacobi rotations** (Jacobi 1846; modern: Golub & Van Loan §8.4).
   Apply 2×2 Givens rotations to zero off-diagonal entries; iterate
   until off-diagonal Frobenius norm is below tolerance. `O(n³)` per
   sweep, `O(log n)` sweeps in the cyclic-by-rows variant. Guaranteed
   convergence; high relative accuracy on small eigenvalues; simple
   to implement.

2. **Tridiagonalisation + implicit-shift QR** (LAPACK DSYEVR/DSYEVD).
   Householder-tridiagonalise (`O(4n³/3)`), then implicit-shift QR
   sweeps on the tridiagonal (`O(n²)` per sweep, `O(n)` sweeps).
   Asymptotically faster (`O(n³)` total dominated by tridiag step)
   but considerably more code, with shift selection, deflation, and
   convergence-edge cases.

3. **Divide-and-conquer** (LAPACK DSYEVD; Cuppen 1981). Recursively
   splits the tridiagonal into halves, solves each, merges via
   secular equation. Fastest in practice for `n > ~50`, but the most
   complex to implement correctly.

The bench is correctness-first, so the algorithm choice is yours
(any of the three passes the tolerance regime). For the
implementation budget at `n ≤ 500`:

- **Jacobi wins on lines-of-code.** Half the implementation of
  tridiag+QR; a fraction of D&C.
- **Jacobi wins on accuracy on small eigenvalues** (Demmel-Veselić
  1992 made this precise for SVD; the same argument applies).
  Tridiag+QR can lose half the digits on the smallest eigenvalue
  when `A` has wide eigenvalue spread; Jacobi maintains relative
  accuracy.
- **Jacobi has no convergence-edge cases.** No shift selection.
  No deflation. The off-diagonal Frobenius norm monotonically
  decreases.

Tradeoff: Jacobi is `O(n³ log n)` vs `O(n³)` for tridiag+QR. At
`n=500`, Jacobi is maybe `5×` slower than LAPACK DSYEVR. But
`5 × 1s = 5s` vs `1s`, and ADR-0016 lifts the cap with warnings — a
planner reading "estimated wall-clock 5s" decides for itself.

If a future workload demands bigger eigh problems and Jacobi's
constant becomes painful, the substrate can be extended to dispatch
by size (mirroring the SVD Golub-Reinsch follow-up bead `y9u`).

## Why the agent-honest output

`linalg-solve`, `linalg-qr`, `linalg-svd` set the precedent. The
output is *not* just `(Q, λ)`, it is a record that lets a planner
decide whether to trust the answer:

```
{
  Q, eigenvalues,
  reconstruction_error,         // ||A·Q − Q·diag(λ)||_F / max(||A||_F, 1)
  orthogonality_error,          // ||QᵀQ − I||_F
  condition_number,             // |λ_max| / max(|λ_min|, EPS·|λ_max|)
  method,                       // "jacobi" or "tridiag-qr"
  warnings                      // soft strings
}
```

A planner reading this output decides:

- "Reconstruction below `1e-12`, orthogonality below `1e-13` — trust."
- "`condition_number = 1e15` — `A` is numerically singular; the
  smallest eigenvalue is below the noise floor; treat the
  corresponding eigenvector as a null direction."
- "`warnings` includes `'matrix size 800×800 above the 500-cell
  well-tested threshold; expected wall-clock ~13s'` — wait or
  escalate."

## Why the test set tiers

Mirrors `bench/linalg-{qr,svd}` with eigh-specific adaptations:

### A. Shape edges (8 cases)

Indexing failures surface here without numerical noise. `1×1`,
`2×2` identity/zero, `3×3` and `5×5` identity, `100×100`/`200×200`
identity. (No tall/fat — eigh requires square.)

### B. Random symmetric well-conditioned (7 cases)

`A = Q · diag(λ) · Qᵀ` with `Q` random orthogonal and `λ_i ∈ [0.5, 2]`.
Sized `5, 10, 20, 50, 100, 200`. Happy-path; failure here means a
core algorithmic bug.

### C. Hilbert (7 cases)

`H_ij = 1 / (i + j − 1)` is symmetric positive definite. For
`n ∈ {4, 6, 8, 10, 12, 20, 50}`, condition spans `~10⁴` to `~10¹⁸`.
The orthogonality of Q is independent of κ for both Jacobi and
tridiag+QR (Wilkinson 1965); residual is bounded too. The smallest
eigenvalues lose digits as κ grows — the bench's `tol_recon` is
wide enough to admit this honestly.

### D'. Random symmetric ill-conditioned (4 cases)

Replaces D-Vandermonde (which isn't symmetric). Constructed as
`Q · diag(λ) · Qᵀ` with `λ_i = 10^((i-1)/(n-1) · 14)`, giving
κ ≈ 10¹⁴. Stress-tests algorithmic accuracy on extreme spreads
without the special structure of Hilbert.

### E. Wilkinson / Pei (4 cases)

Wilkinson `W^+_n` is symmetric tridiagonal with deliberately
near-clustered eigenvalues. The `n=21` case famously has two
eigenvalues differing by `~10⁻¹³`, which any algorithm with a
naive convergence test will fuse incorrectly. Pei `αI + eeᵀ` has
one eigenvalue at `α + n` and `n − 1` eigenvalues at `α` — a
maximally-degenerate spectrum. Frank is *not* symmetric (skipped).

### F. Rank-deficient symmetric (5 cases)

Rank-1 outer `u·uᵀ` (one nonzero eigenvalue, n-1 zero); identity
with one diagonal zeroed; all-zeros `6×6` (all eigenvalues zero);
near-degenerate eigenvalues; repeated-eigenvalue `diag(1,1,1,2,2)`
(Q is non-unique — eigenvectors for repeated eigenvalues span a
2-d subspace).

### H. Eigenvalue-spectrum stress (4 cases)

- Clustered: `λ = (1, 1+1e-10, 1+2e-10, 1+3e-10, 1+4e-10)` — close
  but distinct eigenvalues, tests algorithmic resolution.
- Well-separated extremes: `λ = (10⁻⁸, 10⁸)` — eight orders of
  magnitude, tests relative-accuracy claim.
- All-same: `λ = (1, 1, 1, 1, 1)` — Q can be any orthogonal matrix.
- Alternating signs: `λ = (-2, -1, 0, 1, 2)` — indefinite matrix,
  tests sign handling and the condition-number computation.

### I. Industrial harwell-boeing (5 cases)

`bcsstk01..05`: real symmetric positive-definite structural-
engineering matrices, sizes 48-153, conditioning 4e3 to 7e6. SPD =
exactly the eigh use case. Calibrates against decades of LAPACK
test usage.

### J. Stress (1-2 cases)

Random symmetric `n=500` (Jacobi: ~10s pure TS); tests the post-
ADR-0016 uncapped regime. `n=1000` deferred (Jacobi extrapolates
to ~150s; would slow routine bench-runs).

### K. Boundary (3 cases)

Non-symmetric input → `tagged "linalg-eigh/non-symmetric-input"`;
`n=0` → `tagged "linalg-eigh/degenerate-shape"`; `1×1 [[0]]` → 
edge of the success path (eigenvalue is 0, Q is [[1]], all errors
exactly 0). The first two are tagged-boundary tests; the third is
a degenerate but valid success.

## Why these tolerances

Higham 2002 §20.6: for backward-stable symmetric eigh, the computed
factors `Q̃, λ̃` satisfy:

  `||A·Q̃ − Q̃·diag(λ̃)||_F ≤ c_1(n) · ε · ||A||_F`
  `||Q̃ᵀQ̃ − I||_F ≤ c_2(n) · ε`

with `c_1, c_2` low-degree polynomials in `n`. Concretely:

- `tol_recon = 100 · EPS · n · √n` — Higham bound + 100× safety.
- `tol_orth = 100 · EPS · n · √n` — same.
- `tol_struct = 100 · EPS · max(|λ_max|, 1)` — for the
  ascending-order check; allows LSB ties.

Empirically: SciPy LAPACK DSYEVD clears each tolerance by ≥1 order
of magnitude on the bench (the 100× safety factor is the
implementation budget for honest LSB disagreement, not slop).

## Why `non-symmetric-input` is a tag, not a `ToolError`

A planner often hands eigh a numerically-symmetric matrix that's
LSB-asymmetric due to floating-point computation upstream — `Aᵀ A`
of a noisy `A`, or `A + Aᵀ` with a small bug. The right move for
the planner is: read the asymmetry, decide whether it's noise (`<
1e-10 · ‖A‖`) or a real bug (`> 0.5 · ‖A‖`), then either symmetrise
or fix the upstream code.

Refusing with `ToolError` discards that diagnostic. The boundary tag
carries the `(i, j)` of maximum asymmetry plus the asymmetry value,
so the planner has the info to decide.

The honest-scope version of "I require symmetric input" is "I don't
silently symmetrise; I tell you exactly where you broke symmetry."

## Why no `linalg-eig` (non-symmetric)

The non-symmetric eigenvalue problem requires complex arithmetic
(eigenvalues can be complex conjugate pairs), defective-matrix
handling (Jordan blocks), Schur decomposition for backward stability,
and considerably more delicate convergence theory. It's a
substantially larger project than the symmetric case and lives under
bead `71f` for a later iteration.

For workflows that *think* they need general eig but actually have
symmetric `A` (the most common case), `linalg-eigh` is the right
tool. For workflows that have genuinely non-symmetric `A`, the
recommendation is currently: form `AᵀA`, take its eigh, and use the
spectral relationship `eig(A) ⊆ {±√λ : λ ∈ eig(AᵀA)}`. Imperfect but
honest.
