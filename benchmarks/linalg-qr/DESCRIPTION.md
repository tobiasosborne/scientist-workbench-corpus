# `linalg-qr` — design notes and rationale

This document is the longer-form companion to `PROMPT.md`. It exists
to make the design *defensible*, not just specified.

## Why Householder, and why it's a hard constraint

The QR factorisation `A = QR` of a real `m × n` matrix exists for
every `A`. The non-trivial part is *which* algorithm computes it.

Three textbook options, in order of historical appearance and
numerical behaviour:

1. **Classical Gram-Schmidt** (CGS). One pass over columns, project
   each column off the previously-orthogonalised columns. Cheap
   (`2mn²` flops), but `‖QᵀQ − I‖_F` scales as `O(κ(A)² · ε)`.
   Catastrophic on ill-conditioned `A`.
2. **Modified Gram-Schmidt** (MGS). Same cost, but reordered so that
   the projection of column `j` is updated immediately after each
   prior column is orthogonalised. `‖QᵀQ − I‖_F = O(κ(A) · ε)`.
   Better, but still loses orthogonality on `H_8` (`κ ≈ 10^{10}`)
   and worse.
3. **Householder reflections.** Each column is annihilated below the
   diagonal by a single rank-one orthogonal reflection
   `H = I − 2 v vᵀ / vᵀv`. `‖QᵀQ − I‖_F = O(ε)` independent of
   `κ(A)` — the orthogonality of `Q` is the orthogonality of a
   product of exact-precision reflectors, modulo the LSB roundoff of
   the reflector construction itself. Backward-stable: the
   computed factors satisfy `Q̃R̃ = A + E` with `‖E‖_F = O(ε · ‖A‖_F)`.

The bench's `Q_orthonormal` check (tolerance `100 · ε · m · √k`)
*passes* on Householder for every test case including `H_50`
(`κ > 10^{18}`), and *fails* on MGS at `H_8`. That's the design.

The same constraint applies in spirit even though the bench can't
literally enforce "you used Householder" — what it can enforce is
"`‖QᵀQ − I‖` is below a tolerance that only Householder achieves on
ill-conditioned inputs". Givens rotations would also pass; CGS and
MGS would not. (Givens are 2× more expensive in flops than
Householder for dense matrices and add no numerical benefit, hence
they're not the recommendation. Householder is the canonical choice
and the LAPACK default.)

## Why the agent-honest output

`linalg-solve` set the precedent (ADR-0014, worklog 031): the output
of a numerical-tier tool is *not* just the answer. It is a record
that lets a planner decide whether to trust the answer:

```
{
  Q, R, mode,
  diagonal_R,                 // rank-revealing structure
  reconstruction_error,       // ||QR − A||_F / max(||A||_F, 1)
  orthogonality_error,        // ||QᵀQ − I||_F
  method,                     // "householder"
  warnings                    // soft strings, possibly empty
}
```

A planner reading this output decides:

- "Reconstruction below `1e-12` and orthogonality below `1e-13` —
  trust." (Typical for well-conditioned `A`.)
- "Reconstruction below `1e-10` and orthogonality below `1e-13` —
  trust the factorisation but the input is mildly ill-conditioned."
- "Smallest `|diag(R)|` is `1e-15 · ‖A‖_F` — `A` is numerically
  rank-deficient; treat the smallest singular direction as null."
- "`warnings` includes `'reconstruction error 3.4e-9 above the soft
  floor'` — recompute with extended precision before using `Q` for
  least-squares."

The fields exist so that a planner can make those decisions without
having to *reimplement* the diagnostics in the orchestrator. The
two principles (`bd memories two-principles`) say: a TS expert
would type `qr(A)` and want all of this back. So that's what we ship.

The verifier's `self_reported_residual` and
`self_reported_orthogonality` checks enforce that the candidate's
self-reports are *honest* — not just "structurally present" but
"agree with an independent recomputation to `1e-6` relative". A tool
that lies about its own quality is inadmissible regardless of the
correctness of `Q`, `R` themselves. This is the agent-honest contract
made testable.

## Why these specific test tiers

The seven tiers are chosen so that *each* tier discriminates a
different failure mode:

### A. Shape edges (10 cases)

These are the cases that an inexperienced implementation gets wrong
through indexing arithmetic, not through numerics. `1 × 1`, `2 × 1`,
`1 × 2`, identities, all-zero `2 × 2`, `5 × 3` (the tall-thin
canonical), `3 × 5` (the short-fat canonical), `100 × 100` (mid-size),
`200 × 200` (boundary). A QR implementation that handles all
seven of these has gotten the indexing right.

### B. Random well-conditioned (8 cases)

`A = U Σ Vᵀ` with `U`, `V` random orthogonal and `Σ_ii ∈ [0.5, 2]`.
These are the "happy path" cases. Failure here means a bug in the
core algorithm itself, not numerics or shape arithmetic. Sized
`5, 10, 20, 50, 100, 200` to also smoke-test the `n ≤ 200` cap.

### C. Hilbert (7 cases)

`H_ij = 1 / (i + j − 1)`. Famously ill-conditioned: `κ(H_n) ≈
(1 + √2)^{4n} / √(πn)` (Todd 1954). For `n ∈ {4, 6, 8, 10, 12, 20, 50}`,
`κ` ranges from `~10^4` to `~10^{18}`. This is the tier where MGS
fails `Q_orthonormal` and Householder passes. The factorisation
residual remains tight (Householder is backward-stable independent
of `κ`) but the *forward* error in `R` is unavoidably `O(κ · ε)` —
which is why `singular_values_match` has a tolerance band of
`1e-8 · κ`.

### D. Vandermonde (4 cases)

`V_ij = x_i^{j-1}` for nodes `x_i = (i − 1) / (n − 1)`. Also
exponentially ill-conditioned (`κ` grows like `(1 + √2)^n`), and
*structurally* different from Hilbert — the columns are not
diagonally-dominant, the rows are. A QR routine that hard-codes
assumptions about column scaling will fail Vandermonde even after
passing Hilbert.

### E. Wilkinson / Pei / Frank (5 cases)

These are not as ill-conditioned as Hilbert/Vandermonde but they
exercise specific structural properties:

- **Wilkinson `W^+_n`**: tridiagonal with `n + 1 − 2|i − ⌈n/2⌉|` on
  the diagonal and `1` on the off-diagonals. Famous for clustered
  eigenvalues; QR step in the symmetric eigensolver iteration uses
  this as a stress test. For us it's a tridiagonal-input check.
- **Pei matrix `αI + eeᵀ`** (`α = 1`): rank-one update of identity.
  Rank-revealing structure of `R` should show `n − 1` singular
  values clustered at `1` and one singular value at `n + 1`.
- **Frank matrix `f_ij = n + 1 − max(i, j)`**: lower-Hessenberg with
  one famously ill-conditioned eigenvalue near zero. QR factorisation
  is well-conditioned but the singular-value check stress-tests
  agreement at the smallest σ.

### F. Rank-deficient (5 cases)

QR doesn't promise to *detect* rank-deficiency (that's pivoted-QR or
SVD's job) but it must *survive* it: the factorisation should produce
finite `Q`, `R` with `‖QR − A‖` small, and `diag(R)` should reveal
the rank gap (small entries). The five cases:

- Rank-1 outer product `u vᵀ` (`u`, `v` random `5×1` vectors).
- `5 × 5` identity with one column zeroed (rank 4).
- `8 × 4` matrix with two near-equal rows (numerical rank 7
  → 3 effective).
- `6 × 6` zero matrix (rank 0; degenerate but still QR-able with
  `Q = I, R = 0`).
- `H_8` with a zero column appended (`8 × 9`, full row rank but
  with a zero pivot at the end).

The verifier accepts these — `R_upper_triangular` allows zero
diagonal entries; `Q_orthonormal` is enforced regardless. The
bench is testing *survival under rank-deficiency*, not detection.

### G. Tall-and-skinny / short-and-fat (6 cases)

`(50, 3)`, `(100, 5)`, `(200, 10)` tall — least-squares regime, the
common case for regression. `(3, 50)`, `(5, 100)`, `(10, 200)` fat —
underdetermined regime, the common case for sparse recovery. The
asymmetry between `m ≥ n` and `m < n` in the QR shape conventions
is a known source of off-by-one errors; this tier flushes them out.

### H. Complete-mode (4 cases)

A representative case from each of A/B/C/G with `mode: "complete"`.
The `m − n` extra columns of `Q` span the orthogonal complement of
`A`'s column space. Verifier checks `Q` is `m × m` orthonormal and
`R` has bottom `m − n` rows zero.

## Why these specific tolerances

Higham 2002, Theorem 19.4: the computed Householder factors `Q̃`,
`R̃` satisfy

  `‖Q̃ R̃ − A‖_F ≤ c_1(m, n) · ε · ‖A‖_F`
  `‖Q̃ᵀ Q̃ − I‖_F ≤ c_2(m, n) · ε`

where `c_1, c_2` are low-degree polynomials in `m, n` (specifically,
`c_1 ≈ m·n · γ_n`, `c_2 ≈ m·γ_n` with `γ_n ≈ n·ε / (1 − n·ε)`).

The bench uses:
- `tol_recon = 100 · ε · max(m,n) · √(min(m,n))` — Higham's bound
  with a 100× safety factor for cumulative roundoff in the
  Frobenius-norm summation itself.
- `tol_orth = 100 · ε · m · √k` — same.
- `tol_struct = 100 · ε · max(m,n)` for the upper-triangular
  zero-below-diagonal check, scaled by `‖A‖_F`.

The 100× safety factor is empirically chosen: SciPy LAPACK DGEQRF
clears each bound by 1–2 orders of magnitude on the bench, so 100×
catches "the algorithm went catastrophically wrong" without rejecting
"the algorithm is fine but the LSB summation order differs from
LAPACK's".

An earlier draft of this bench included a `singular_values_match`
check (compare `|diag(R)|` sorted descending against `A`'s singular
values from `numpy.linalg.svd`). Dropped: `|diag(R)|` is *not*
equal to the singular values of `A` in general — the equality holds
only when `A` is itself upper triangular. The two are related only
by the determinant identity `∏|R_ii| = |det(A)|` for square `A`.
The factorisation residual + R upper-triangularity + Q
orthonormality already characterise QR uniquely up to column-sign
choice, so the dropped check would have been overconstraint, not
extra signal.

## Why the bench wire format is raw JSON, not canonical Value

scientist-workbench's value protocol (PRD §0.1, ADR-0004) requires
`float64` values to be hex-encoded big-endian IEEE-754 bits, not
JSON numbers. The tool itself speaks this protocol; the bench
*outside* the tool should be language-neutral so it could be
re-implemented in C++/Julia/Rust later for cross-validation
(mirroring tstournament's deliberate language-neutrality).

The adapter `bench/linalg-qr/run-candidate.ts` is a tiny harness
that bridges the two: it reads raw JSON on stdin, encodes to
canonical `Value`, calls the tool in-process via `@workbench/compose`
(`runMemoized` is *not* used — every bench case is freshly executed
to test the algorithm, not the cache), decodes the result back to
raw JSON, and writes to stdout. The adapter is provided in this
directory.

## Why no NIST Matrix Market for the path-finder

The bench could in principle pull a subset of the harwell-boeing
collection (small dense matrices like `bcsstk01`, `bcspwr04`,
`west0067`) for an "industrial regression" tier. The reasons it
doesn't, for this first run:

1. The path-finder's purpose is to shake down the protocol on
   sci-wb. Adding network-dependent corpora is a separate concern.
2. The 49 hand-crafted cases already span Householder's known
   failure modes (none of which exist) and known soft-edges
   (orthogonality on ill-conditioning, structural zeros under
   rank-deficiency, shape arithmetic). Adding 50 more well-
   conditioned random matrices would not increase signal.
3. If a follow-up bench version adds the harwell-boeing tier, the
   verifier doesn't change — only `golden/inputs.json` grows.

The path-finder's lesson is what informs whether tier-H is worth
adding to `linalg-svd` and `linalg-eigh`.
