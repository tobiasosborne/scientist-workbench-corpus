# References

## Primary references for symmetric eigh

1. **Jacobi, C. G. J.** (1846). "Über ein leichtes Verfahren die in
   der Theorie der Säcularstörungen vorkommenden Gleichungen
   numerisch aufzulösen." *Crelle's Journal*, 30, 51–94.
   — The original Jacobi rotation method. 180 years later still the
   reference for symmetric eigh by 2×2 zero-out.

2. **Wilkinson, J. H.** (1965). *The Algebraic Eigenvalue Problem*,
   Oxford University Press. ISBN 9780198534181.
   — §5 (Jacobi method, convergence proofs); §8 (tridiagonal QR);
   the canonical numerical-analysis treatment.

3. **Golub, G. H., & Van Loan, C. F.** (2013). *Matrix Computations*,
   4th ed., Johns Hopkins University Press. ISBN 9781421407944.
   — §8.4 (Jacobi for symmetric eigh; cyclic-by-rows variant);
   §8.3 (tridiagonalisation + implicit-shift QR); §8.5 (divide-
   and-conquer).

4. **Higham, N. J.** (2002). *Accuracy and Stability of Numerical
   Algorithms*, 2nd ed., SIAM. ISBN 9780898715217.
   — §20.6 (symmetric eigenvalue problem backward stability);
   §20.7 (Jacobi accuracy on small eigenvalues).

5. **Demmel, J., & Veselić, K.** (1992). "Jacobi's Method is More
   Accurate than QR." *SIAM Journal on Matrix Analysis*, 13(4),
   1204–1245.
   DOI: [10.1137/0613074](https://doi.org/10.1137/0613074)
   — Foundational result on Jacobi's relative-accuracy advantage
   for the smallest eigenvalues. The argument transfers from SVD
   (the paper's title) to the symmetric eigh problem they treat
   in parallel.

## LAPACK references

6. **Anderson, E. et al.** (1999). *LAPACK Users' Guide*, 3rd ed.,
   SIAM. ISBN 9780898714470. Online:
   <https://netlib.org/lapack/lug/>
   — DSYEV (basic), DSYEVR (relatively-robust representations,
   the LAPACK speed leader for n > ~50), DSYEVD (divide-and-
   conquer), DSYEVJ (one-sided Jacobi). The bench's reference
   uses DSYEVD via SciPy.

7. **Cuppen, J. J. M.** (1981). "A divide and conquer method for
   the symmetric tridiagonal eigenproblem." *Numerische
   Mathematik*, 36, 177–195.
   — The DSYEVD algorithm (after tridiagonalisation). Mentioned
   for completeness; not used in the recommended pure-TS path.

8. **Dhillon, I. S., & Parlett, B. N.** (2004). "Multiple
   Representations to Compute Orthogonal Eigenvectors of Symmetric
   Tridiagonal Matrices." *Linear Algebra and its Applications*,
   387, 1–28.
   — Algorithmic basis for DSYEVR. Reference for a future tridiag-QR
   path; not needed for v0.1.

## Test-matrix sources

9. **Hilbert, D.** (1894); **Todd, J.** (1954) — Hilbert matrices
   and their conditioning. Cited in `bench/linalg-{qr,svd}/REFERENCES.md`.

10. **Wilkinson, J. H.** (1965) — Wilkinson `W^+_n` matrices, see (2)
    above.

11. **Pei, M. L.** (1962). "A test matrix for inversion procedures."
    *Communications of the ACM*, 5(8), 508.
    — The Pei matrix `αI + eeᵀ`.

## sci-wb internal references

- `docs/adr/0014-first-numerical-tier.md` — agent-honest output discipline.
- `docs/adr/0015-determinism-tier.md` — `numerical: true`.
- `docs/adr/0016-warning-based-numerical-scaling.md` — no hard cap.
- `docs/adr/0003-tool-output-error-patterns.md` — three output categories.
- `tools/linalg-svd/tool.ts` — direct precedent (mode flag, agent-honest
  output, three boundary categories with `non-finite-input` and
  `degenerate-shape`; eigh adds a fourth, `non-symmetric-input`).
- `packages/linalg-core/src/svd.ts` — direct substrate precedent
  (one-sided Jacobi pattern; eigh's Jacobi for symmetric eigh follows
  the same column-pair-rotation discipline).
- Worklog 044 — `linalg-svd` ships.
- Worklog 045 — ADR-0016 cap lift + NIST industrial benchmarks
  (the bench standards eigh inherits).

## Bench protocol references

- `../linalg-{qr,svd}/` — immediate precedents.
- `../../tstournament/ts-bench-infra/problems/02-ntt/` — upstream
  protocol source.
