# References

## Primary references for the SVD algorithm

1. **Golub, G. H., & Reinsch, C.** (1970). "Singular Value
   Decomposition and Least Squares Solutions." *Numerische
   Mathematik*, 14(5), 403–420.
   DOI: [10.1007/BF02163027](https://doi.org/10.1007/BF02163027)
   — The original Golub-Reinsch SVD: Householder bidiagonalization
   followed by implicit-shift QR sweeps on the bidiagonal.

2. **Demmel, J., & Kahan, W.** (1990). "Accurate Singular Values of
   Bidiagonal Matrices." *SIAM Journal on Scientific and Statistical
   Computing*, 11(5), 873–912.
   DOI: [10.1137/0911052](https://doi.org/10.1137/0911052)
   — The zero-shift implicit-QR variant that gives accurate small
   singular values where the original Golub-Reinsch loses precision.
   This is what LAPACK DGESVD actually implements (LAPACK calls it
   "DBDSQR").

3. **Demmel, J., & Veselić, K.** (1992). "Jacobi's Method is More
   Accurate than QR." *SIAM Journal on Matrix Analysis*, 13(4),
   1204–1245.
   DOI: [10.1137/0613074](https://doi.org/10.1137/0613074)
   — One-sided Jacobi for SVD; superior accuracy on the smallest
   singular values; simpler to implement than implicit-shift QR.
   The LAPACK DGEJSV path.

4. **Golub, G. H., & Van Loan, C. F.** (2013). *Matrix Computations*,
   4th ed., Johns Hopkins University Press. ISBN 9781421407944.
   — §8.6 (SVD algorithms), §8.6.1 (Householder bidiagonalization),
   §8.6.2 (Golub-Kahan SVD step), §8.6.4 (one-sided Jacobi).

5. **Higham, N. J.** (2002). *Accuracy and Stability of Numerical
   Algorithms*, 2nd ed., SIAM. ISBN 9780898715217.
   — §20 (the eigenvalue / singular value problem), §20.3 (SVD
   backward stability bounds, the `c(m,n)·ε·‖A‖_F` constants).

## LAPACK references

6. **Anderson, E. et al.** (1999). *LAPACK Users' Guide*, 3rd ed.,
   SIAM. ISBN 9780898714470. Online:
   <https://netlib.org/lapack/lug/>
   — DGESVD (Golub-Reinsch), DGESDD (divide-and-conquer; default
   in `scipy.linalg.svd`), DGEJSV (one-sided Jacobi). The bench's
   reference implementation uses DGESDD via SciPy.

7. **Drmač, Z., & Veselić, K.** (2008). "New Fast and Accurate
   Jacobi SVD Algorithm." *SIAM Journal on Matrix Analysis*,
   29(4), 1322–1342 (Part I) and 1343–1362 (Part II).
   — Modern engineering of one-sided Jacobi with QRP preconditioning
   (the LAPACK DGEJSV implementation); cited for the 'when does
   Jacobi beat QR' empirical analysis.

## Test-matrix sources

8. **Hilbert, D.** (1894). "Ein Beitrag zur Theorie des Legendre'schen
   Polynoms." *Acta Mathematica*, 18, 155–159.

9. **Todd, J.** (1954). "The Condition of the Finite Segments of the
   Hilbert Matrix." *NBS Applied Mathematics Series*, 39, 109–116.

10. **Pan, V. Y.** (2016). "How Bad Are Vandermonde Matrices?" *SIAM
    Journal on Matrix Analysis*, 37(2), 676–694.

11. **Wilkinson, J. H.** (1965). *The Algebraic Eigenvalue Problem*,
    Oxford University Press. ISBN 9780198534181.

## sci-wb internal references

- `docs/adr/0014-first-numerical-tier.md` — `n ≤ 200` cap rationale,
  agent-honest output discipline.
- `docs/adr/0015-determinism-tier.md` — `numerical: true`
  annotation, platform fingerprint convention.
- `docs/adr/0003-tool-output-error-patterns.md` — three output
  categories.
- `tools/linalg-qr/tool.ts` — direct precedent for SVD's tool
  shape (mode flag, agent-honest output, three boundary categories).
- `packages/linalg-core/src/qr.ts` — direct precedent for SVD's
  substrate algorithm structure (Householder reflectors,
  in-place storage, scaled-DNRM2 norm computation).
- Worklog 043 — `linalg-qr` ships; the precedent for both the
  bench scaffold pattern and the agent-honest output record.

## Bench protocol references

- `../linalg-qr/` — the immediate precedent. SVD's verifier and
  generator are derivative of QR's; only the invariant set
  differs (8 checks vs 7).
- `../../tstournament/ts-bench-infra/problems/02-ntt/` — the
  upstream protocol source.
