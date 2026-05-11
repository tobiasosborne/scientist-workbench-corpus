# lp-small — references

## Problem families

- **Klee, V. & Minty, G. J.** (1972). *How good is the simplex
  algorithm?* In Inequalities III (O. Shisha, ed.), 159–175.
  Academic Press. The canonical pathological LP family for the
  simplex method; exponential vertex enumeration under Dantzig's
  pivot rule.

- **Beale, E. M. L.** (1955). *Cycling in the dual simplex algorithm.*
  Naval Research Logistics Quarterly 2(4), 269–275. The 4-variable
  cycling example used in Family C.

- **Bland, R. G.** (1977). *New finite pivoting rules for the simplex
  method.* Mathematics of Operations Research 2(2), 103–107. The
  anti-cycling rule that resolves Beale's example.

- **Dantzig, G. B., Orden, A. & Wolfe, P.** (1955). *The generalized
  simplex method for minimizing a linear form under linear
  inequality restraints.* Pacific Journal of Mathematics 5, 183–195.
  Original simplex method paper; degenerate-LP discussion.

- **Hoffman, A. J.** (1953). *Cycling in the simplex algorithm.*
  National Bureau of Standards Report 2974. (Historical reference;
  Hoffman's cycling example is folded into Family C alongside Beale's.)

## Algorithm / verifier tolerances

Same as `lp-netlib/REFERENCES.md`:

- Wright 1997 *Primal-Dual Interior-Point Methods* §2.4 for the
  KKT-residual tolerance scaling.
- Vanderbei 2014 *Linear Programming* 4th ed. §2.5–2.7 for the
  reduction to canonical SCS form.
- Higham 2002 *Accuracy and Stability of Numerical Algorithms* §20
  for backward error bounds.

## Reference oracles

- **Gurobi Optimizer**, version 13.0.1.
- **Mosek**, version 11.1.6.
- **COPT** (additive, lands when installed).

## Workbench-side

- **ADR-0030** — convex cone solver tier.
- **ADR-0028** — bench migration to corpus.
- **ADR-0015** — determinism tier (the `numerical: true` annotation).
- **Epic `scientist-workbench-eg9j`** + companion bead
  `scientist-workbench-oz67`.
