# References — `linsolve-q` bench

## Primary algorithm

**Bareiss 1968** — "Sylvester's Identity and Multistep Integer-
Preserving Gaussian Elimination", *Mathematics of Computation*
22(103), 565-578.

- Local: `docs/ground-truth/linear/bareiss-1968-mathcomp.pdf`
- Argonne tech-report variant (longer; same algorithm with worked
  examples): `docs/ground-truth/linear/bareiss-1968-argonne-tech-report.pdf`
- AMS open archive: <https://www.ams.org/journals/mcom/1968-22-103/S0025-5718-1968-0226829-0/>

Load-bearing sections cited from the local PDF:

- **§I, pp. 565-567** — Sylvester's identity. The mathematical
  foundation: `a_ij^(k)` defined as the determinant of `A_11`
  bordered by row `i` and column `j` (Eq. 3, p. 565). Equations
  (4)-(6) derive the integer-preserving recurrence; the divisor
  `[a_ll^(l-1)]^{k-l-1}` is shown to divide the determinant
  exactly (the integer-preserving claim).
- **§II.A.2, pp. 568-569** — The one-step algorithm. Equation
  (8) on p. 569 is the recurrence we implement:

      a_ij^(k) = (a_kk^(k-1) · a_ij^(k-1) − a_ik^(k-1) · a_kj^(k-1))
                / a_{k-1,k-1}^(k-2)

  with sentinels `a_00^(-1) = 1`, `a_ij^(0) = a_ij`. Figure 1
  on p. 569 is the flow chart with overwriting-storage sequencing.
- **§II.A.3, pp. 569-571** — The two-step algorithm. Equation (7)
  on p. 568 is the recurrence; constant-factor speedup, more
  bookkeeping. **Deferred to v2** for `linsolve-q`; v1 ships the
  one-step variant only.
- **§V, pp. 573-574** — Worked-example bit-growth comparison
  (Bareiss vs naive Gaussian on a 4×4 integer system). The basis
  of the bench's Tier H bit-budget check.

## Modern textbook treatment

**Geddes, Czapor, Labahn (1992)** *Algorithms for Computer
Algebra*. Springer / Kluwer.

- Chapter 9 §9.5 covers fraction-free elimination methods,
  including Bareiss. Pedagogically clearer than the original
  paper for an implementation-first read.
- TIB-Hannover access: <https://link.springer.com/book/10.1007/b102438>
- **Not staged locally** — chapter-level access via institutional
  proxy is sufficient.

## Cross-validation oracles

Per ADR-0019 (`docs/adr/0019-solve-bench-discipline.md`), goldens
are admitted iff ≥ 2 oracles agree.

- **Wolfram `LinearSolve[A, b]`** — primary oracle. Activated
  Wolfram kernel under TIB Hannover VPN.
  Documentation: <https://reference.wolfram.com/language/ref/LinearSolve.html>
- **SymPy `Matrix(A).solve(b)`** + `linsolve` — secondary
  oracle. Local install (1.14.0+).
  Documentation: <https://docs.sympy.org/latest/modules/solvers/solveset.html#linsolve>
- **SageMath `A.solve_right(b)`** *(when SageMath available)* —
  tertiary oracle. Provides independent witness when Wolfram and
  SymPy disagree on representation of free-variable parametrisation.

The agreement layer at `bench/_corpus/oracle/agreement.py`
implements the per-kind comparison.

## Theoretical context

- **Sylvester's identity** — any linear-algebra textbook covering
  determinants. Sylvester (1851) "On a remarkable theorem in the
  theory of equal roots and discriminants of functions of a
  single variable", *Phil. Mag.* 4(1) 138-145. Used by Bareiss
  to prove the divisibility property that makes the algorithm
  integer-preserving.
- **Rouché-Capelli theorem** — any linear-algebra textbook.
  The verifier's `inconsistency_witness` check.
- **Hadamard's inequality** —
  `|det(M)| ≤ ∏_i ‖row_i(M)‖_2`. Bounds intermediate Bareiss
  values; basis of the Tier H bit-budget check.

## Reference implementations consulted (none included in repo)

- **FLINT** `fmpq_mat_solve_fraction_free` — production C
  implementation. License: LGPL (compatible with our AGPL).
  Source: <https://github.com/flintlib/flint/blob/main/src/fmpq_mat/solve_fraction_free.c>
- **SymPy** `sympy.matrices.matrices.MatrixBase._fast_inv_jordan` —
  Bareiss-equivalent inner loop in pure Python. License: BSD.
- **Sage** `sage.matrix.matrix_rational_dense.bareiss` — Cython
  implementation; the cleanest pedagogical reference. License:
  GPL.

For the v1 implementation we work from the paper directly —
re-implementation rather than transliteration. Cross-validation
against these implementations happens at the bench-oracle layer,
not the source-code layer.

## Closed-form solutions for verification

For the small-`n` Tier-A and Tier-B cases, the verifier *also*
checks against Cramer's rule when `n ≤ 6` and the system is
square + full-rank: `x_i = det(A_i) / det(A)` where `A_i` is `A`
with column `i` replaced by `b`. The two paths (Bareiss vs
Cramer) must produce bit-equal outputs — a third witness against
the paper's algorithm being correctly transcribed.
