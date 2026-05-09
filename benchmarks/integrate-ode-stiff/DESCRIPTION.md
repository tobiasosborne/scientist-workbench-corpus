# `integrate-ode-stiff` — design notes

Companion to `PROMPT.md`. Defends the design.

## Why stiff IVP next

Stiffness is universal in chemistry kinetics, plasma physics,
electrical engineering, and any system with widely-separated time
scales. An explicit method (DOPRI5 in `integrate-ode-ivp`) handed a
stiff problem must take infinitesimally small steps for stability —
Robertson at `t=10¹¹` s requires `~10¹⁵` DOPRI5 steps and is
intractable. Radau-IIA(5) takes a few thousand and is fast.

Closed scope:
- Same closed `f` vocabulary as `cas-diff` / `integrate-1d` /
  `integrate-ode-ivp`. Symbolic Jacobian via `cas-diff` when feasible;
  finite-difference fallback otherwise.
- Per-step Newton iteration via `linalg-solve` (already in the
  workbench) for the implicit linear solve.
- Stiffness ratios up to ~`10¹²` (Robertson, VDPOL μ=10⁶, E5).
- The `scipy.integrate.solve_ivp(method='Radau')` algorithm.

Out of scope:
- Variable-order BDF (`scipy.integrate.solve_ivp(method='BDF')`).
  Single-method discipline keeps the path-finder simple. `options.method`
  exists for forward-compat but `'bdf'` raises `tagged
  "integrate-ode-stiff/method-not-implemented"`.
- DAEs, mass-matrix systems.
- Sensitivity analysis, parameter estimation.

## Why Radau-IIA(5)

Three textbook options for stiff IVP:

1. **Radau-IIA(5)** (Hairer-Wanner Vol II §IV.8). 3-stage, 5th-order
   implicit RK; collocation at the Radau quadrature points
   `c = ((4 − √6)/10, (4 + √6)/10, 1)`. A-stable, L-stable, stiffly
   accurate. Single method, no order-selection logic.

2. **BDF** (Curtiss-Hirschfelder 1952; Hindmarsh's LSODA / scipy 'BDF').
   Variable-order (1–5) backward differentiation. Faster on mildly
   stiff problems but order-selection adds substantial code.

3. **SDIRK** (Singly Diagonally Implicit RK). Lower per-step cost than
   Radau, but the L-stable variants have lower order (typically 4 vs
   Radau's 5).

For the path-finder, **Radau-IIA(5) wins on three independent counts**:

- **Single algorithm, no order switching.** Half the implementation of
  variable-order BDF.
- **Higher order.** 5 vs 4 (SDIRK) for the same backward-stability
  guarantee.
- **Industry standard.** The `scipy.integrate.solve_ivp(method='Radau')`
  algorithm; the textbook reference (Hairer-Wanner Vol II is the
  field's reference book and Radau-IIA is its centrepiece).

The simplified-Newton-iteration trick (Hairer-Wanner 1999) reduces the
`(3n × 3n)` real linear solve per Newton step to one `n × n` real solve
+ one `(2n × 2n)` real solve per iteration via a complex-eigenvalue
transformation of the Radau matrix `A`. The substrate composes
`linalg-solve` for these.

## Why the agent-honest output

Inherits the path-finder's record shape and adds two stiff-specific
fields:

```
{
  trajectory:           list<list<float64>>,
  t_values:             list<float64>,
  error_estimate:       float64,
  n_evals:              integer,
  n_steps_accepted:     integer,
  n_steps_rejected:     integer,
  n_jacobian_evals:     integer,           # NEW: how often we recomputed J
  n_lu_decompositions:  integer,           # NEW: how often we re-factorised
  converged:            boolean,
  status:               string,             # 'success' | 'max_step_exceeded' | 'tspan_exhausted' | 'newton-divergence'
  method:               string,             # 'radau-iia-5'
  warnings:             list<string>
}
```

A planner reading this output decides:

- "`converged: true`, `n_jacobian_evals: 8`, `n_steps_accepted: 142` —
  Jacobian reused well, system not too violent."
- "`n_jacobian_evals` ≈ `n_steps_accepted` — Newton struggled, system
  is rapidly varying or controller is too aggressive."
- "`n_lu_decompositions` >> `n_jacobian_evals` — step size changed
  often (each step-size change forces a refactor of `(I − h·A ⊗ J)`)."

## Why `jacobian-singular` is a tag, not a `ToolError`

A planner often hands the solver a problem where `J` becomes singular
at some encountered state — Robertson when species concentrations hit
zero, mass-action kinetics at the boundary of the simplex, semi-explicit
DAEs that aren't quite an ODE. The right move for the planner is: read
the `(at_t, at_y, condition_number)` payload, decide whether to
regularise (add `ε·I` to `J`) or refactor the model. Refusing with
`ToolError` discards that diagnostic.

## Why the test set tiers

Mirrors `bench/integrate-ode-ivp` (stiff-specific adaptations noted).

### A. Shape edges (3 cases)
Trivial cases that exercise the implicit-loop machinery without
numerical noise: scalar `y' = -y`, `y' = -1000 y` (mildly stiff), 2D
linear stiff system.

### B. Smooth analytic / mild-stiff oracles (3 cases)
Reference at `scipy.integrate.solve_ivp(method='Radau', rtol=1e-13)`.
- B_linear_stiff_2d: 2D linear with eigenvalues `(-1, -1000)` —
  classic 1000-fold stiffness ratio. Analytic available.
- B_van_der_pol_mu1: mild stiffness regime, `μ=1`.
- B_exp_decay_long: `y' = -y` over `[0, 1000]` — long horizon, mild.

### C. Stiffness sweep (4 cases)
Same problem family at growing stiffness. Verifies controller robustness.
- C_vdp_mu_100, C_vdp_mu_1e3, C_vdp_mu_1e4, C_vdp_mu_1e6.

### D. NHW Vol II canonical (5 cases)
The catalogue.
- D_robertson: 3-species chemistry, rates `(0.04, 1e4, 3e7)`,
  `tf = 10¹¹`. Famous stiff test.
- D_hires: 8-component photomorphogenesis (Schäfer 1975).
- D_oregonator: Belousov-Zhabotinsky reaction, 3 components.
- D_e5: NHW E5 problem, 4 components, rates `4e-2, …, 8.4e9`.
- D_pollu: NHW Pollu (air-pollution chemistry), 20 components.

### E. Boundary cases (4 cases)
- E_degenerate_tspan, E_pole_in_rhs (inherited from path-finder).
- E_jacobian_singular: synthetic `f` whose Jacobian goes singular at
  a known state.
- E_dim_mismatch (ToolError).

### F. Tolerance discipline (3 cases)
Same problem (Robertson) at `rtol ∈ {1e-3, 1e-6, 1e-9}`.

### I. Industrial (IVPTESTSET stress, 2 cases)
- I_ring_modulator: 15-component electrical circuit (canonical IVPTESTSET
  stress problem).
- I_medakzo: 400-component method-of-lines PDE discretisation.

Total: ~24 cases × 8-10 checks ≈ 200 invariant assertions.

## Why these tolerances

Hairer-Wanner Vol II §IV.10 establishes that for backward-stable Radau
with adaptive step control achieving local error `≤ tol_local`, the
global error on a stiffly-bounded problem is `O(tol_local)` —
*independent of horizon*, unlike the explicit case. The verifier uses:

- **`tol_traj = 100 · rtol`** (no horizon scaling, unlike the explicit
  case — implicit methods don't accumulate the way explicit ones do).
- **`tol_self_report`** via the same structural check as path-finder
  (non-negative finite; semantic agent-honesty deferred).
- **Bookkeeping invariants**: `n_jacobian_evals ≤ n_steps_accepted +
  n_steps_rejected`, `n_lu_decompositions ≤ 2·n_steps_accepted` (real
  + complex factorisation per step in the worst case).

The 100× safety factor is calibrated so SciPy Radau clears each bound
by ≥1 order of magnitude.
