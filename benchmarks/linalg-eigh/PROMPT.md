# Bench — `linalg-eigh` (Symmetric eigendecomposition)

## ⚠ How you will be graded

You will be graded on **CORRECTNESS** and **NUMERICAL HONESTY**.

Produce the most elegant, most efficient, most numerically faithful
TypeScript implementation you can. This bench is the *floor*, not the
ceiling — passing it is necessary but not sufficient. The tool must
also conform to the scientist-workbench seven-artefact contract (see
`CLAUDE.md`, `README.md`, `PRD-v0.2.md`).

The verifier checks invariants, not byte-equality: eigendecompositions
are unique only up to (a) sign convention on each eigenvector, and
(b) for repeated eigenvalues, arbitrary rotation in the corresponding
eigenspace. Every check has a tolerance derived from backward-stability
theory (Wilkinson 1965; Higham 2002 §20.6).

## Problem statement

Implement the symmetric eigendecomposition of a real `n × n`
*symmetric* matrix `A`:

  `A = Q · diag(λ) · Qᵀ`

with:
- `Q ∈ ℝ^{n×n}` orthogonal (`QᵀQ = I_n`),
- `λ ∈ ℝ^n` real eigenvalues sorted **ascending** (the
  `numpy.linalg.eigh` / LAPACK convention),
- columns of `Q` are eigenvectors corresponding to `λ_i`.

`A` is required to be symmetric on input. Per ADR-0003, non-symmetric
input is a *boundary condition* (not a programmer error worth a
ToolError) — the tool returns
`tagged "linalg-eigh/non-symmetric-input"` carrying the maximum
asymmetry coordinate so the planner can decide what to do (often:
`A := (A + Aᵀ)/2` then retry).

Algorithm choice is up to you; both pass the bench's tolerance regime:

- **Jacobi rotations** (Jacobi 1846; modern reference Golub & Van
  Loan §8.4). Apply 2×2 rotations to zero the largest off-diagonal,
  iterate until off-diagonal Frobenius norm is below tolerance.
  `O(n³)` per sweep, `O(log n)` sweeps. Guaranteed convergence,
  high relative accuracy on small eigenvalues, simple to implement.
- **Tridiagonalisation + implicit-shift QR** (LAPACK DSYTRD +
  DSTEQR, the default DSYEVD path). Householder-tridiagonalise,
  then implicit-shift QR sweeps on the tridiagonal. Asymptotically
  faster but considerably more code.

For `n ≤ 500`, Jacobi is the recommendation: half the lines, no
convergence-edge cases. ADR-0016 lifts the hard cap; `assessNumericalScale("eigh", n, n)` 
emits warnings beyond `n=500` and OOM is the only physical refusal.

The implementation operates on flat `Float64Array` storage (the
`@workbench/linalg-core` precedent set by `linalg-solve`, ADR-0014).
No FFI, no WASM, pure TypeScript, single platform per ADR-0015
(`numerical: true`).

## I/O contract (JSON)

### Bench wire format

Raw JSON. Adapter `bench/linalg-eigh/run-candidate.ts` bridges to the
tool's canonical `Value` protocol.

### Input (one JSON object on stdin)

```jsonc
{
  "A":      [[<float>, ...], ...]    // n × n, square, symmetric within tol
}
```

### Output (one JSON object on stdout)

```jsonc
{
  "Q":                       [[<float>, ...], ...],   // n × n, orthogonal
  "eigenvalues":             [<float>, ...],          // length n, ascending
  "reconstruction_error":    <float>,                  // ||A·Q − Q·diag(λ)||_F / max(||A||_F, 1)
  "orthogonality_error":     <float>,                  // ||QᵀQ − I||_F
  "condition_number":        <float>,                  // |λ_max| / max(|λ_min|, EPS · |λ_max|)
  "method":                  "jacobi" | "tridiag-qr",
  "warnings":                [<string>, ...]
}
```

`reconstruction_error` and `orthogonality_error` are the candidate's
*honest self-report*. The verifier recomputes and checks agreement
to `1e-6` relative.

`condition_number` is `|λ_max| / |λ_min|` (the spectral condition for
symmetric A) when `λ_min > 0`, else clamped to `1/EPS ≈ 4.5e15` to
keep finite. This differs from a singular-value condition: for
indefinite `A`, the smallest *magnitude* eigenvalue determines
conditioning of the inverse.

## Invariants checked

The verifier runs **7 independent checks** per case:

1. `shape` — `Q` is `n × n`, `eigenvalues` length `n`, output object
   has all required fields with correct types.
2. `finite_entries` — every entry of `Q`, `eigenvalues`, and the
   scalar diagnostic fields is finite.
3. `eigenvalues_ascending` — `λ[i] ≤ λ[i+1] + tol_struct` for all
   `i < n − 1`. Tolerance allows LSB ties.
4. `Q_orthonormal` — `||QᵀQ − I_n||_F ≤ tol_orth` with
   `tol_orth = 100 · EPS · n · √n`.
5. `eigendecomp_residual` — `||A·Q − Q·diag(λ)||_F ≤ tol_recon ·
   ||A||_F` with `tol_recon = 100 · EPS · n · √n`.
6. `self_reported_residual` — agrees with verifier recomputation
   to `1e-6` relative.
7. `self_reported_orthogonality` — agrees with verifier
   recomputation to `1e-6` relative.

These 7 are necessary AND sufficient for a valid symmetric
eigendecomposition up to sign-convention freedom on individual
columns of `Q` and rotation freedom in eigenspaces of repeated
eigenvalues.

The 100× safety factor is empirical: SciPy's `scipy.linalg.eigh`
clears each tolerance by ≥1 order of magnitude on the bench.

## Test set tiers

`golden/inputs.json` contains **~50 cases** spanning the same seven
tiers as `bench/linalg-{qr,svd}` (eigh-specific adaptations noted):

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 8 | `1×1`, `2×2` identity/zero, `3×3` identity, `5×5` identity, `100×100` identity, `200×200` identity |
| B. random symmetric well-conditioned | 7 | `n ∈ {5, 10, 20, 50, 100, 200}` constructed as `Q · diag(λ) · Qᵀ` with random orthogonal `Q` and `λ_i ∈ [0.5, 2]` |
| C. Hilbert (already symmetric) | 7 | `n ∈ {4, 6, 8, 10, 12, 20, 50}` — all SPD; eigenvalues are positive |
| D. *(skipped)* — Vandermonde is not symmetric | — | replaced with: random symmetric ill-conditioned via wide eigenvalue spread |
| D'. random symmetric ill-conditioned | 4 | `n ∈ {5, 10, 20, 50}` constructed with `λ_i = 10^((i-1)/(n-1) · 14)` (κ ≈ 10¹⁴) |
| E. Wilkinson / Pei (symmetric structural) | 4 | `W^+_5, W^+_11, W^+_21` (tridiagonal symmetric); Pei `n=10` (rank-one update of identity) |
| F. rank-deficient (symmetric) | 5 | rank-1 outer `u·uᵀ`, identity-with-zero-diagonal, all-zeros `6×6`, near-degenerate eigenvalues, repeated-eigenvalue `diag(1,1,1,2,2)` |
| G. *(skipped)* — eigh requires square | — | not applicable |
| H. eigenvalue-spectrum stress | 4 | clustered eigenvalues (`λ = (1, 1+1e-10, 1+2e-10, …)`), well-separated extremes (`λ = (10⁻⁸, 10⁸)`), all-same `λ = (1,1,…,1)`, alternating signs |
| I. industrial (NIST harwell-boeing) | 5 | `bcsstk01..05` real SPD structural-engineering matrices |
| J. stress (post ADR-0016) | 1-2 | random symmetric at `n=500` (Jacobi at `n=500` is ~5-10s) |
| K. boundary | 3 | non-symmetric input (tagged), `1×1` zero, `n=0` (degenerate-shape tagged) |

Total: **~50 cases × 7 checks = ~350 invariant assertions**.

Why no D-Vandermonde tier: Vandermonde matrices aren't symmetric, so
they're not valid input for eigh. Replaced with synthetic
ill-conditioned symmetric matrices that exercise the same algorithmic
stress (eigenvalues spanning many orders of magnitude).

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/linalg-eigh bun bench/linalg-eigh/run-candidate.ts
```

### Files

- `golden/inputs.json` — every test case.
- `golden/expected.json` — reference outputs from SciPy LAPACK
  DSYEVD (provided for sanity-checking; not consulted by verifier).
- `golden/verify.py` — invariant verifier (numpy + scipy).
- `golden/verifier_protocol.md` — exact tolerances per check.
- `golden/generate.py` — reproducible golden generation.
- `reference/eigh_reference.py` — Python+SciPy reference.
- `run-candidate.ts` — wire-format adapter.

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun. No FFI.
- Seven-artefact contract.
- `numerical: true` annotation.
- **No hard cap (ADR-0016)**: scale warnings instead.
- Boundary categories (ADR-0003):
  - `tagged "linalg-eigh/non-symmetric-input"` for asymmetric `A`
    (within tolerance `100·EPS·max(|A|)`). Payload: max-asymmetry
    coordinates.
  - `tagged "linalg-eigh/non-finite-input"` for `NaN`/`±Inf`.
  - `tagged "linalg-eigh/degenerate-shape"` for `n=0`.
  - `ToolError` for non-square `A`, ragged `A`, true OOM.
- Substrate: extend `@workbench/linalg-core` with `eigh()`.

## What you must do

1. Read `CLAUDE.md`, `tools/linalg-svd/tool.ts`,
   `packages/linalg-core/src/svd.ts`. The eigh substrate and tool
   are direct successors.
2. Implement bench candidate (`tools/linalg-eigh/`) to seven-artefact
   contract.
3. Run bench until 100% across 7 checks.
4. Run `bun run check`.
5. Report per-check totals.
