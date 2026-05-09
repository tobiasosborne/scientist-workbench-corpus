# References

## Primary references for non-stiff IVP

1. **Dormand, J. R., & Prince, P. J.** (1980). "A family of embedded
   Runge-Kutta formulae." *Journal of Computational and Applied
   Mathematics*, 6(1), 19–26.
   DOI: [10.1016/0771-050X(80)90013-3](https://doi.org/10.1016/0771-050X(80)90013-3)
   — The DOPRI5(4) coefficient table. The 5th-order solution
   advances; the 4th-order embedded estimate drives step control.
   FSAL property (First Same As Last): the 7th stage of step `n`
   equals the 1st stage of step `n+1`, so accepted steps cost only
   6 new f-evaluations.

2. **Hairer, E., Nørsett, S. P., & Wanner, G.** (1993). *Solving
   Ordinary Differential Equations I: Nonstiff Problems*, 2nd ed.,
   Springer Series in Computational Mathematics 8. ISBN 978-3-540-56670-0.
   — §II.4 (embedded Runge-Kutta methods); §II.5 (DOPRI5 coefficient
   table and convergence theory); §II.10 (PI step-size control);
   Appendix (canonical test problems: Lotka-Volterra, Brusselator,
   Kepler 2-body, exponential decay, harmonic oscillator). The
   single most-cited textbook for non-stiff IVP.

3. **Gustafsson, K.** (1991). "Control theoretic techniques for
   stepsize selection in explicit Runge-Kutta methods." *ACM
   Transactions on Mathematical Software*, 17(4), 533–554.
   DOI: [10.1145/210232.210242](https://doi.org/10.1145/210232.210242)
   — The PI step-size controller. Replaces I-only control's
   oscillatory step sequence with a damped PI law: `h_{n+1} =
   h_n · (tol/err_n)^{α/p} · (err_{n-1}/err_n)^{β/p}` with
   `α = 0.7, β = 0.4` for order `p=5`. Used in scipy
   `solve_ivp(method='RK45')`, MATLAB `ode45`, Hairer's `dopri5`.

4. **Hairer, E., & Wanner, G.** (1999). "Stiff differential
   equations solved by Radau methods." *Journal of Computational
   and Applied Mathematics*, 111(1–2), 93–111.
   DOI: [10.1016/S0377-0427(99)00134-X](https://doi.org/10.1016/S0377-0427(99)00134-X)
   — Companion to (2). Cited here only for the stiff-detection
   heuristic the path-finder uses to *recognise* (not handle)
   stiffness, emitting a warning that suggests `integrate-ode-stiff`.

## Test-problem corpora

5. **Hairer, E., Nørsett, S. P., & Wanner, G.** (1993). *Solving
   ODEs I*, Appendix.
   — Non-stiff test catalogue: Brusselator, Lotka-Volterra, Kepler
   2-body (analytic ellipse), pleiades 7-body, restricted three
   body, harmonic oscillator. Reference solutions accurate to
   ~1e-12 quoted in the appendix tables.

6. **Enright, W. H., Hull, T. E., & Lindberg, B.** (1975). "Comparing
   numerical methods for stiff systems of ODEs." *BIT Numerical
   Mathematics*, 15, 10–48.
   DOI: [10.1007/BF01932994](https://doi.org/10.1007/BF01932994)
   — DETEST classes A–E. Class A (single equations), B (small systems),
   C (moderate systems), D (oscillatory), E (small systems with
   known analytic solutions). The non-stiff tier of the bench
   draws from class A and B.

7. **Mazzia, F., & Magherini, C.** (2008). *Test Set for Initial
   Value Problem Solvers, Release 2.4*, University of Bari.
   <https://archimede.uniba.it/~testset/>
   — IVPTESTSET. Reference solutions to ~1e-14 in machine-readable
   format. Permissively licensed for research use. Drawn upon for
   the industrial tier (with attribution); locally precomputed via
   the bench's reference implementation rather than imported as
   data, since the reference is at our chosen tolerance regime.

## Conservation invariants for verifier (path-finder relevance)

8. **Hairer, E., Lubich, C., & Wanner, G.** (2006). *Geometric
   Numerical Integration*, 2nd ed., Springer. ISBN 978-3-540-30663-4.
   — §I.2 (Kepler 2-body Hamiltonian, energy and angular momentum
   formulae used by the verifier's conservation checks for the
   non-symplectic-but-energy-aware tier-D cases).

## sci-wb internal references

- `docs/adr/0003-tool-output-error-patterns.md` — three output categories.
- `docs/adr/0014-first-numerical-tier.md` — agent-honest output discipline.
- `docs/adr/0015-determinism-tier.md` — `numerical: true`.
- `docs/adr/0016-warning-based-numerical-scaling.md` — no hard cap; scale warnings.
- `tools/integrate-1d/tool.ts` — adaptive-quadrature precedent;
  shared closed expression vocabulary; agent-honest output record;
  tagged-boundary taxonomy (`non-finite-during-eval`,
  `degenerate-interval`).
- `tools/optimize-lbfgs-projected/tool.ts` — multi-component-state
  precedent; `vars: list<symbol>`/`x0: list<float64>` parallel-list
  shape; `options` record pattern; `success: false` happy-path with
  `status` + `warnings`.
- `tools/cas-diff/tool.ts` — closed expression vocabulary
  (re-exports the same head set used here).
- `packages/quadrature/src/eval-expr.ts` — `evalNumericExpr`,
  `ADMITTED_HEADS`, `ADMITTED_CONSTANTS`, `UnknownVocabularyError`
  (reusable by the ODE-core RHS evaluator).
- Worklog 039 — `integrate-1d` ships.
- Worklog 040 — `optimize-lbfgs-projected` ships (multi-component
  numerical-tier precedent).
- Worklog 041 — `cas-diff` ships (shared vocabulary; symbolic
  Jacobian path for stiff slice).
- Worklog 047 — `linalg-eigh` ships (tournament-protocol bench
  template this dossier mirrors).

## Bench protocol references

- `../linalg-eigh/`, `../linalg-svd/`, `../linalg-qr/` — immediate
  precedents for the bench layout, Higham-style tolerance discipline,
  and self-report cross-check pattern.
- `../../tstournament/ts-bench-infra/problems/` — upstream bench
  protocol (`generate.py`, `verify.py`, `verifier_protocol.md`,
  `reference/`, raw-JSON wire format, language-neutral verifier).
