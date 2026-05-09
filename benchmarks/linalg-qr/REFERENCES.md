# References

## Primary references for the QR algorithm

1. **Householder, A. S.** (1958). "Unitary Triangularization of a
   Nonsymmetric Matrix." *Journal of the ACM*, 5(4), 339–342.
   DOI: [10.1145/320941.320947](https://doi.org/10.1145/320941.320947)
   — The original Householder reflection. Three pages, perfectly
   self-contained.

2. **Golub, G. H., & Van Loan, C. F.** (2013). *Matrix Computations*,
   4th ed., Johns Hopkins University Press. ISBN 9781421407944.
   — §5.1 (Householder reflections), §5.2 (Householder QR), §5.3
   (block Householder). The canonical algorithmic exposition.

3. **Higham, N. J.** (2002). *Accuracy and Stability of Numerical
   Algorithms*, 2nd ed., SIAM. ISBN 9780898715217.
   DOI: [10.1137/1.9780898718027](https://doi.org/10.1137/1.9780898718027)
   — Chapter 19 (QR factorisation), Theorem 19.4 (the backward
   stability bound `‖Q̃R̃ − A‖_F ≤ c(m,n)·ε·‖A‖_F`), §19.7
   (loss of orthogonality in MGS vs Householder).

## LAPACK references

4. **Anderson, E. et al.** (1999). *LAPACK Users' Guide*, 3rd ed.,
   SIAM. ISBN 9780898714470. Online:
   <https://netlib.org/lapack/lug/>
   — DGEQRF (Householder QR with implicit Q-as-reflectors storage),
   DORGQR (form Q explicitly from reflectors). The bench's reference
   implementation uses these via SciPy.

5. **Demmel, J. W., Hida, Y., Riedy, E. J., & Li, X. S.** (2009).
   "Extra-precise iterative refinement for overdetermined least
   squares problems." *ACM TOMS*, 35(4), 28:1–28:32.
   — Modern stability analysis of QR-based least squares; the
   verifier's tolerance choices borrow this paper's empirical
   constants for `c(m,n)`.

## Test-matrix sources

6. **Hilbert, D.** (1894). "Ein Beitrag zur Theorie des Legendre'schen
   Polynoms." *Acta Mathematica*, 18, 155–159.
   — The original definition of the Hilbert matrix.

7. **Todd, J.** (1954). "The Condition of the Finite Segments of the
   Hilbert Matrix." *NBS Applied Mathematics Series*, 39, 109–116.
   — The asymptotic `κ(H_n) ≈ (1 + √2)^{4n} / √(πn)` formula used
   in the bench's verifier-protocol prose.

8. **Pan, V. Y.** (2016). "How Bad Are Vandermonde Matrices?" *SIAM
   Journal on Matrix Analysis*, 37(2), 676–694.
   DOI: [10.1137/15M1030170](https://doi.org/10.1137/15M1030170)
   — Modern conditioning analysis of Vandermonde matrices on
   real nodes; the bench uses uniform `[0, 1]` nodes which are
   the most ill-conditioned standard family.

9. **Wilkinson, J. H.** (1965). *The Algebraic Eigenvalue Problem*,
   Oxford University Press. ISBN 9780198534181.
   — Wilkinson `W^+_n` definition; the eigenvalue-clustering
   stress matrix that bears his name.

10. **Frank, W. L.** (1958). "Computing eigenvalues of complex
    matrices by determinant evaluation and by methods of Danilewski
    and Wielandt." *Journal of SIAM*, 6, 378–392.
    — The Frank matrix; the canonical lower-Hessenberg test case.

## sci-wb internal references

- `docs/adr/0014-first-numerical-tier.md` — `n ≤ 200` cap rationale,
  agent-honest output discipline.
- `docs/adr/0015-determinism-tier.md` — `numerical: true` annotation,
  platform fingerprint convention.
- `docs/adr/0003-tool-output-error-patterns.md` — three output
  categories (happy path, record-with-flag, tagged boundary).
- `tools/linalg-solve/tool.ts` — agent-honest output precedent;
  the QR tool's output record mirrors this shape.
- `packages/linalg-core/src/{lu,solve,hager}.ts` — substrate for
  Float64Array dense linear algebra; QR will extend with `qr.ts`.
- Worklog 031 — first numerical tier design; cross-validation
  against SciPy LAPACK as the ground-truth oracle.

## Bench protocol references

- `../../tstournament/ts-bench-infra/problems/02-ntt/` — the bench
  layout this directory mirrors. Per-check verifier with named
  invariants, language-neutral JSON wire format, reproducible
  generator.
- `../../tstournament/WORKLOG.md` §"Staging recipe" and §"Agent
  brief template" — the protocol prose this PROMPT.md is modelled on.
