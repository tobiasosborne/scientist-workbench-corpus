# lp-netlib — references

## Problem collection

- **NETLIB LP collection.** Source: `netlib.org/lp/`. Public domain
  since 1985. Original distribution README at `netlib.org/lp/data/readme`.
  Catalogue of the 114 problems with original sources at
  `netlib.org/lp/data/index`. The collection was assembled by
  David Gay (Bell Labs) and reflects roughly 30 years of LP test
  problems contributed by industrial and academic users since the
  1960s.

- **Maros, I. & Mészáros, C.** (1999). *A repository of convex
  quadratic programming problems.* Optimization Methods and Software,
  11(1–4), 671–681. (The QP sister collection used by
  `benchmarks/qp-maros-meszaros/`; cited here because the small-LP
  subset they include is folded into `lp-small`.)

## Algorithm / verifier tolerances

- **Wright, S. J.** (1997). *Primal-Dual Interior-Point Methods.*
  SIAM. §2.4 defines the KKT residuals (`primal_residual`,
  `dual_residual`, `complementarity`) and their tolerance scaling
  by `max(1, ‖b‖_∞)` / `max(1, ‖c‖_∞)` — the basis for the
  verifier's 1e-8 relative tolerance.

- **Vanderbei, R. J.** (2014). *Linear Programming: Foundations and
  Extensions,* 4th ed. Springer. §2.5–2.7 is the canonical
  textbook reduction from NETLIB-general-form LP to standard form
  (slack introduction, bound shift, free-variable split) used in
  `golden/wire.py`.

- **Higham, N. J.** (2002). *Accuracy and Stability of Numerical
  Algorithms,* 2nd ed. SIAM. §20 — backward error analysis for
  linear systems; the basis for the `100 × ε_machine` floor in
  residual checks where the problem matrix conditioning matters.

## Reference oracles

- **Gurobi Optimizer**, version 13.0.1. Commercial LP / MIP solver.
  Used as the primary oracle. Adapter: `adapters/gurobi/lp-netlib.toml`.

- **Mosek**, version 11.1.6. Commercial LP / SOCP / SDP solver.
  Used as the secondary oracle. Adapter:
  `adapters/mosek/lp-netlib.toml`.

- **COPT** (Cardinal Optimizer). Tertiary oracle, additive; lands
  when installed.

## Workbench-side

- **ADR-0030** — convex cone solver tier. The canonical reference
  for the wire format, status taxonomy, and bench-gating numerics.
  `scientist-workbench/docs/adr/0030-convex-cone-solver-tier.md`.

- **ADR-0028** — bench migration to corpus. Why bench/* lives here,
  not in scientist-workbench. `scientist-workbench/docs/adr/0028-bench-migration-to-corpus.md`.

- **ADR-0015** — determinism tier. The `numerical: true` annotation
  and platform-fingerprint contract that this suite's candidates
  conform to.

- **Epic `scientist-workbench-eg9j`** — the parent issue for this
  bench and the three candidate tools it gates.
