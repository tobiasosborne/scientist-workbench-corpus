# Bench `linsolve-q` — algorithm + invariants

This document expands the `PROMPT.md` brief with the algorithm
specification, the invariant set the verifier checks, and the
boundary cases that distinguish "correct refusal" from "wrong
answer."

## The algorithm — Bareiss one-step (1968)

### Setup

Augment `A` with the right-hand side: `M^(0) = [A | b]`, an
`m × (n+1)` rational matrix.

Define `M^(0)_{ij} = M[i, j]` (1-indexed throughout, following the
paper).

Sentinel: `M^(0)_{0, 0} = 1` (Bareiss's `a_00^(-1) = 1`).

### Forward elimination

For `k = 1, 2, …, min(m, n)`:

1. **Pivot selection.** Find the smallest `i ≥ k` with
   `M^(k-1)_{i, k} ≠ 0`. If none: `M^(k-1)_{k, k}` was already 0
   and the column is a free direction — the column index `k` is
   free, and we reduce the *augmented* part directly. (See
   "Free variables and rank deficiency" below for the bookkeeping.)
2. **Pivot swap.** Exchange row `i` with row `k` so the pivot is
   in row `k`. Track the swap; it does not affect the *solution*
   but does affect the determinant sign (irrelevant here).
3. **Row reduction.** For each `i > k`, for each `j ≥ k+1` (and
   the augmented column `n+1`):

   ```
   M^(k)_{i, j} = (M^(k-1)_{k, k} · M^(k-1)_{i, j}
                 − M^(k-1)_{i, k} · M^(k-1)_{k, j})
                 / M^(k-2)_{k-1, k-1}
   ```

   The division is **exact in ℤ** when all inputs are integer (per
   Sylvester's identity, §I of the paper). When inputs are
   rational, the division is exact in ℚ — no precision loss.

4. **Zero out below-pivot column.** Set `M^(k)_{i, k} = 0` for
   `i > k`.

After `n` steps (assuming full column rank), `M^(n)` is upper-
triangular with the pivots on the diagonal.

### Back substitution

Standard. For `i = n, n-1, …, 1`:

```
x[i] = (M^(n)_{i, n+1} − Σ_{j > i} M^(n)_{i, j} · x[j]) / M^(n)_{i, i}
```

The divisions in back-substitution may introduce denominators (this
is *not* the integer-preserving phase) — that's expected and
correct over ℚ.

### Free variables and rank deficiency

When in Step 1 above no non-zero pivot is found in column `k` at or
below row `k`, the column corresponds to a free variable. Let
`free_count` be the number of such columns; the rank is `n -
free_count` if the system is square-or-tall (or `min(m, n) -
free_count` more generally).

To produce the parametric form: introduce free symbols `t_0, t_1,
…, t_{free_count − 1}` for each free column. Back-substitute as
above, but with `x[free_columns] = symbolic free variable`. The
result has each bound `x[i]` as a *linear* combination of the
free variables with rational coefficients plus a rational constant.

### Inconsistency detection

A row `[0, 0, …, 0 | β]` with `β ≠ 0` is the witness of
inconsistency: the equation `0 = β` is unsatisfiable.

Equivalently (Rouché-Capelli theorem): rank(A) < rank([A | b])
iff inconsistent. The verifier uses the rank-comparison form; the
implementation can use either (the row-witness form is faster).

### Zero-column inputs

`m × 0` matrices: `n = 0`, no variables. The system is consistent
iff `b = 0` (every row of `b` is zero); else inconsistent. A
consistent `m × 0` system has the empty solution `x = []` —
return `kind = unique`, `x = []`, `rank = 0`, `free_vars = []`.

A `0 × n` matrix has no equations; every `x ∈ ℚ^n` is a solution.
Return `kind = under-determined`, `free_vars = [t_0, …, t_{n-1}]`,
`rank = 0`. (This boundary case is in Tier A.)

## Invariant verifier — the 6 checks

Each check returns `{pass: bool, detail: string}`. The case passes
iff all 6 pass.

### 1. `shape`

Structure-only: types and field presence. Doesn't check values.

- `A` is a list of lists of strings, all rows length `n`, with `m
  ≥ 0`, `n ≥ 0` (zero is allowed for boundary cases).
- `b` is a list of strings, length `m`.
- Every coefficient string parses as a rational (regex
  `-?(\d+|\d+/\d+)` or equivalent).
- `kind ∈ {unique, under-determined, inconsistent}`.
- Required fields per kind present.

### 2. `exact_satisfaction_unique` (only when `kind = unique`)

Compute `r = A · x − b` exactly in ℚ. PASS iff every entry of `r`
is `Rational(0)`. No tolerance.

### 3. `free_var_basis_underdetermined` (only when `kind = under-determined`)

For each free variable `t_i` listed in `free_vars`, generate 10
random rational substitutions from
`{-3, -1, 0, 1, 2, 5/3, -7/4, 11, -19/4, 6/7}`. For each
substitution, instantiate `x` (each entry now a concrete rational)
and verify `A · x = b` exactly. PASS iff all 10 substitutions of
all `free_count` variables (= `10 · free_count` instantiations)
satisfy.

The point: under-determined output that is *correct* must work for
*every* choice of free variables. Sampling 10 catches off-by-one
constant terms and missing free-variable terms; full verification
requires symbolic substitution which is the `rank_consistent`
check.

### 4. `rank_consistent` (always)

Compute `rank(A)` independently using `sympy.Matrix(A).rank()`.
PASS iff `candidate.rank == sympy_rank`.

### 5. `inconsistency_witness` (only when `kind = inconsistent`)

Compute `rank(A)` and `rank([A | b])` via SymPy. PASS iff
`rank([A | b]) > rank(A)`. (Necessary AND sufficient for
inconsistency by Rouché-Capelli.)

### 6. `free_var_count_correct` (only when `kind = under-determined`)

PASS iff `len(free_vars) == n - rank`.

The free-variable count is forced by the rank-nullity theorem;
mis-reporting it is a structural bug, not a parametrisation
choice.

## Mutation-prove harness

Per ADR-0019 §4, `golden/test_mutations.py` includes ≥ 5
characteristic perturbations of the reference, each demonstrating
RED on at least one tier:

1. **Off-by-one in pivot row index** — `i > k` becomes `i >= k`,
   contaminating the pivot row's reduction. Fails `exact_satisfaction`
   on Tier B small dense.
2. **Sign flip in cross-multiply** — `M_kk · M_ij + M_ik · M_kj`
   instead of `−`. Fails `exact_satisfaction` on Tier A 2×2.
3. **Forgot the divisor `M^(k-2)_{k-1, k-1}`** — naive `kk · ij −
   ik · kj`. Output is correct *in ℚ* but bit-blows on Tier H
   integer-preserving stress (verifier checks bit-length of the
   output strings — entries above 10⁵ digits fail).
4. **Dropped the augmented column from row reduction** — `b` not
   updated alongside `A`. Fails `exact_satisfaction` everywhere
   except trivial-`b = 0` cases.
5. **Reported `kind = unique` for under-determined** —
   under-determined system reduces to a row of zeros; if the
   implementation skips back-substitution for that row but still
   reports `unique`, the free-variable basis isn't reported.
   Fails `free_var_count_correct` on Tier E.
6. **Reported `kind = unique` for inconsistent** — a row `[0
   ⋯ 0 | β]` ignored; spurious `x` returned. Fails
   `exact_satisfaction` on Tier F.

Each mutation must produce RED on at least one tier when run
through the verifier; the harness asserts this directly.

## Tier-by-tier rationale

Why each tier exists and what it specifically catches:

- **A. shape edges.** Catches degenerate-input bugs (off-by-one on
  `n=1`, mishandling `m × 0`, mishandling `0 × n`).
- **B. random well-conditioned.** The "happy path." If this fails,
  the algorithm is fundamentally wrong.
- **C. classical structural.** Hilbert is famously ill-conditioned
  *over ℝ* — forces the algorithm to compute large rationals
  exactly. Pascal and Vandermonde are similarly stress-ful but
  with different sparsity/coefficient structures.
- **D. integer-coefficient stress.** Pure-integer inputs that
  reveal whether the implementation actually exploits the
  integer-preserving property or naively converts to ℚ.
- **E. structural rank-deficient.** Free-variable reporting is a
  whole code path on its own. Catches "implemented unique solving
  only."
- **F. inconsistent.** Catches "implemented Bareiss but didn't
  add the inconsistency check at the end."
- **G. industrial.** Larger, structured matrices where
  performance matters but exactness is non-negotiable.
- **H. integer-preserving stress.** The headline test: catches
  implementations that "got the right answer but with bit-blown
  intermediate values." Specifically built around inputs whose
  naive Gaussian elimination over ℚ would produce exponentially
  growing denominators; Bareiss's bookkeeping keeps them bounded
  by the determinant of submatrices (Hadamard bound). The
  verifier checks the *bit-length* of the largest intermediate
  value reported in the output's `warnings` field; a candidate
  that secretly does naive ℚ-Gaussian fails the bit-budget.

## Sources cited

- **Bareiss 1968** "Sylvester's Identity and Multistep Integer-
  Preserving Gaussian Elimination", *Math. Comp.* 22(103) 565-578.
  Local: `docs/ground-truth/linear/bareiss-1968-mathcomp.pdf`.
  Section II.A.2 (pp. 568-569) is the algorithm spec; §V (pp.
  573-574) gives the worked-example bit-growth comparison this
  bench's Tier H is patterned on.
- **Geddes, Czapor, Labahn** *Algorithms for Computer Algebra*
  §9.5. Modern textbook treatment.
- **Rouché-Capelli theorem** (any linear-algebra textbook) — used
  for the inconsistency witness.
- **Hadamard's inequality** — bounds intermediate Bareiss values
  by `|det(submatrix)| ≤ ∏ ‖row_i‖`. The basis of the bit-budget
  check in Tier H.

## Sources NOT used (and why)

- **Multi-modular reconstruction** (CRT) — the modern fast
  alternative for very large dense systems. Implementation
  effort not justified at v1 capability scope; future v2 bead.
- **Iterative refinement** — only useful for *floating-point*
  approximate solves. Exact arithmetic doesn't have refinement
  semantics.
- **LAPACK DGESV / SciPy `linalg.solve`** — float64; covered by
  the existing `linalg-solve` tool. Not the same problem.
