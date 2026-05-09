# `integrate-ode-ivp` — design notes

Companion to `PROMPT.md`. Defends the design.

## Why non-stiff IVP first

The non-stiff initial value problem `dy/dt = f(t, y), y(t0) = y0` is
the workhorse of computational science: orbital mechanics, classical
field theory, ecology, kinetics in non-stiff regimes, and most of the
"differential equations of physics" a scientist hears in a 50-minute
talk are smooth and non-stiff over the integration window of interest.
Path-finding the tournament-protocol on it lets the stiff and
symplectic slices land against an already-proven scaffold.

Closed scope:
- One state-space dimension or many (the algorithm is dimension-
  generic; the bench tier A starts at `n=1` and tier C runs Lorenz
  at `n=3`, Kepler at `n=4`).
- Smooth `f` in the closed vocabulary shared with `cas-diff`,
  `integrate-1d`, `optimize-lbfgs-projected` (so symbolic Jacobians
  for the stiff slice come for free).
- Adaptive step-size control that *recognises* but does not *handle*
  stiffness (warning + `success: false`, suggesting
  `integrate-ode-stiff`).

Out of scope (for this slice):
- Stiff systems beyond mild stiffness (path: `integrate-ode-stiff`).
- Hamiltonian / structure-preserving integration (path:
  `integrate-ode-symplectic`).
- DAEs, delay differential equations, stochastic ODEs.
- Event detection, root-finding during integration.
- Discontinuity handling beyond honest tagging.

## Why DOPRI5 (the recommended algorithm)

Three textbook options for explicit non-stiff IVP:

1. **Dormand-Prince 5(4) (DOPRI5)** (Dormand & Prince 1980; modern
   reference: Hairer-Nørsett-Wanner 1993 §II.5). 7-stage explicit
   Runge-Kutta with embedded 5th- and 4th-order estimates; the 5th-
   order solution advances, the difference drives step control. FSAL
   property: 7th stage of step `n` is reused as 1st stage of step
   `n+1`, so accepted steps cost 6 new function evaluations. The
   `scipy.integrate.solve_ivp(method='RK45')` algorithm, the
   MATLAB `ode45` algorithm, the Hairer `dopri5` algorithm.

2. **Cash-Karp 5(4)** (Cash & Karp 1990). Older 6-stage embedded
   pair; comparable order, slightly looser error estimate, no FSAL.
   DOPRI5's FSAL gives it ~17% fewer evaluations per accepted step
   asymptotically.

3. **Verner 6(5) / 8(7)** (Verner 1978). Higher-order embedded pairs;
   8(7) is the right choice when *very* tight tolerances (`rtol <
   1e-10`) are routine. Steeper coefficient table, more stages per
   step. Defer to a follow-up bead if the workbench surfaces routine
   tight-tolerance demands.

For the path-finder, **DOPRI5 wins on three independent counts**:

- **Lines of code.** 7 stages, one coefficient table; substantially
  simpler than Verner 8(7)'s 13-stage table and shift-of-order
  bookkeeping.
- **Industry standard.** SciPy's default, MATLAB's default, Octave's
  default, Julia DifferentialEquations.jl's default for non-stiff
  problems. The TS-expert reaches for this name without hesitation.
- **PI controller is well-tuned for order 5.** Gustafsson's
  `α = 0.7, β = 0.4` constants are calibrated for 5th-order methods;
  using them with order-8 Verner requires either retuning or
  accepting a less-tight controller.

The substrate is `@workbench/ode-core` (new package, mirroring
`linalg-core` / `quadrature` precedent). Pure TypeScript on
`Float64Array`, no FFI, single platform per ADR-0015. ADR-0016 lifts
hard caps; `assessNumericalScale("ode-rkf45", n_components,
n_steps_estimate)` emits warnings into the multi-second regime, true
allocation-OOM is the only physical refusal.

## Why the agent-honest output

`linalg-solve`, `linalg-qr`, `linalg-svd`, `linalg-eigh`,
`integrate-1d`, `optimize-lbfgs-projected` set the precedent. The
output is *not* just `(t_values, y(t_values))`; it is a record that
lets a planner decide whether to trust the answer:

```
{
  trajectory:        list<list<float64>>,   // (n_eval × n_components)
  t_values:          list<float64>,          // (n_eval,)
  error_estimate:    float64,                // sup-norm of last accepted local error / atol
  n_evals:           integer,                // total f-evaluations
  n_steps_accepted:  integer,
  n_steps_rejected:  integer,
  converged:         boolean,                // true iff status === 'success'
  status:            string,                 // 'success' | 'max_step_exceeded' | 'tspan_exhausted' | 'stiffness-detected'
  method:            string,                 // 'dormand-prince-45'
  warnings:          list<string>
}
```

A planner reading this output decides:

- "`converged: true`, `error_estimate: 1e-9`, `n_steps_rejected: 2`
  out of 47 accepted — stable, trust."
- "`converged: false`, `status: 'max_step_exceeded'` after `n_evals:
  10000` — budget exhausted; either retry with a tighter
  `max_step` budget or relax `rtol`."
- "`status: 'stiffness-detected'` warning string includes 'consider
  integrate-ode-stiff' — switch tools."
- "`warnings` includes `'matrix size 200×200 above the 100-component
  well-tested threshold'` — accept or escalate."

`converged: false` is **happy path** with diagnostic fields, not a
boundary tag. Per ADR-0003 §Category 2: "the algorithm ran to
completion on a valid input → record with explicit flag, not tagged."
Same precedent as `optimize-lbfgs-projected` budget exhaustion.

## Why the test set tiers

Mirrors `bench/linalg-eigh` and `bench/integrate-1d` (ODE-specific
adaptations noted). Goal: exercise every code path; cross-check
self-report against truth; punish naive implementations on cases
known to break them.

### A. Shape edges (4 cases)

Indexing failures and degenerate-dimension bugs surface here without
numerical noise. Scalar `n=1`, 2D coupled linear, 3D linear, scalar
zero RHS (constant solution).

### B. Smooth analytic oracles (6 cases)

Problems with closed-form solutions. The verifier compares against
the analytic answer at `t_eval` points to ~1e-12 absolute, not
against the SciPy reference. Cleanest possible oracle.

- **B_exp_decay**: `y' = -y, y(0) = 1` → `y(t) = exp(-t)` over `[0, 5]`.
- **B_exp_growth**: `y' = y, y(0) = 1` → `y(t) = exp(t)` over `[0, 5]`.
- **B_logistic**: `y' = y(1-y), y(0) = 0.5` → `y(t) = 1/(1+exp(-t))`
  over `[0, 10]`. Tests product nonlinearity and saturation.
- **B_harmonic_2d**: `y0' = y1, y1' = -y0`, IC `(1, 0)` → analytic
  `(cos t, -sin t)` over `[0, 4π]`. Long enough to exercise step
  selection across a periodic landscape.
- **B_riccati**: `y' = y² - t², y(0) = 1` over `[0, 1]`. No closed
  form, but smooth and short-horizon — reference computed at tight
  scipy tolerance to ~1e-13.
- **B_van_der_pol_mu1**: 2D, `μ = 1` (mildly non-linear,
  non-stiff regime). Reference at tight tolerance.

### C. Hairer-Nørsett-Wanner Vol I canonical (5 cases)

The non-stiff catalogue that defines the field.

- **C_lotka_volterra**: 2D Lotka-Volterra predator-prey at canonical
  parameters `(α=1.0, β=0.1, γ=1.5, δ=0.075)`, IC `(10, 5)`, over
  `[0, 15]`. No closed form; periodic with conserved quantity
  `H(x,y) = δx − γln(x) + βy − αln(y)`. Verifier checks `|H(t) −
  H(0)| / |H(0)| ≤ 100·rtol·tf`.
- **C_brusselator**: 2D Brusselator at `A=1, B=3` (oscillating
  attractor), IC `(1.5, 3.0)`, over `[0, 20]`. Reference solution
  at tight scipy tolerance.
- **C_kepler_e05**: 4D Kepler 2-body at eccentricity `e = 0.5`,
  IC `q=(1-e, 0), p=(0, sqrt((1+e)/(1-e)))`, over one period
  `T = 2π`. Reference is the analytic ellipse parametrised by
  eccentric anomaly. Energy `H = ‖p‖²/2 - 1/‖q‖` and angular
  momentum `L = q×p` exactly conserved (verifier permits
  non-symplectic drift bounded by `100·rtol·tf` for path-finder).
- **C_lorenz**: 3D Lorenz at canonical `(σ=10, ρ=28, β=8/3)`, IC
  `(1, 1, 1)`, over `[0, 5]`. **Punishing** because chaos: pointwise
  agreement with reference is impossible past Lyapunov time
  `~1/λ ≈ 1`. Verifier checks pointwise to `t = 1` (one Lyapunov
  time), then attractor-invariant statistics (mean, variance) over
  `t ∈ [1, 5]`. Honest scope: tool *cannot* lie that pointwise
  Lorenz is accurate past `t=1` at default tol.
- **C_pleiades_3body**: 3-body restricted gravitational at NHW Vol I
  Appendix parameters; 12-component state. Reference at tight tol.

### D. Stress / conservation (4 cases)

Things that punish naive impls.

- **D_van_der_pol_mu10**: `μ = 10` — non-stiff but rapidly varying
  near the relaxation oscillation; forces frequent step rejection.
  Verifier checks `n_steps_rejected ≥ 5` (a non-rejecting
  implementation is silently failing the stiffness signal).
- **D_long_horizon**: `y' = -y` over `[0, 100]`. Tests step-count
  bookkeeping under a long-but-trivial integration. Verifier checks
  `n_evals` is in the expected range (not absurdly high, not
  sub-Nyquist).
- **D_high_freq**: `y' = sin(100t), y(0) = 0` over `[0, 1]`.
  Smooth integrand but ~16 oscillations; naive constant-step methods
  fail. Reference is `y(t) = (1 − cos(100t)) / 100`.
- **D_kepler_long**: Kepler `e = 0.6` over 5 orbital periods. Energy
  drift bound `|H(t) - H(0)|/|H(0)| ≤ 1e-3` at default `rtol = 1e-3`
  (a non-symplectic method's drift is `O(rtol · tf)`; Kepler's
  symplectic-class problem makes the drift visible at this horizon).

### E. Boundary cases (4 cases)

- **E_degenerate_tspan**: `t0 = tf = 0` →
  `tagged "integrate-ode-ivp/degenerate-tspan"` with payload
  `{t0, tf}`.
- **E_pole_in_rhs**: `y' = 1/(t - 1), y(0) = 0` over `[0, 2]`.
  Pole at `t = 1` →
  `tagged "integrate-ode-ivp/non-finite-during-eval"` with
  payload `{at_t, at_y}`.
- **E_unknown_head**: `y' = floor(y)` (`floor` is not in the closed
  vocabulary) → `ToolError` with `suggestion` listing admitted
  heads. Verifier accepts non-zero exit + correct tag-class.
- **E_dim_mismatch**: `len(f) = 2`, `len(y0) = 1` → `ToolError`
  with field-pointing `detail`. Verifier accepts non-zero exit +
  correct tag-class.

### F. Tolerance discipline (3 cases)

Same problem (`y' = -y, y(0) = 1` over `[0, 5]`), three rtol
regimes. Tests the tool's monotone tolerance contract.

- **F_tight**: `rtol = 1e-10` — must achieve actual error
  `≤ 100·rtol`.
- **F_default**: `rtol = 1e-6` — must achieve actual error
  `≤ 100·rtol`.
- **F_loose**: `rtol = 1e-3` — must achieve actual error
  `≤ 100·rtol`.

If `F_tight`'s actual error is *not* monotonically tighter than
`F_default`'s, the controller is broken.

### G. Reverse integration (1 case)

`y' = -y` over `[5, 0]` (`tf < t0`); IC `y(5) = exp(-5)`.
Must integrate backward and recover `y(0) ≈ 1` to method tolerance.
SciPy `solve_ivp` accepts; we match.

### H. Dense output accuracy (2 cases)

User-requested `t_eval` points strictly between integrator step
endpoints. Naive implementations stop-at-tend and lose digits at
intermediate points. DOPRI5 has a 4th-order Hermite continuous
extension (Hairer-Nørsett-Wanner Vol I §II.6) that the candidate
must use.

- **H_dense_smooth**: `y' = sin(t)` over `[0, 10]` at 100 evenly-
  spaced `t_eval`. Verifier checks `‖y_candidate(t_eval) -
  y_ref(t_eval)‖_∞ ≤ 100·rtol`.
- **H_dense_kepler**: Kepler `e = 0.5` over 1 period at 50 dense
  `t_eval`. Tests Hermite extension on a vector problem.

## Why these tolerances

Hairer-Nørsett-Wanner 1993 §II.10 establishes that for a stable
adaptive Runge-Kutta with PI control achieving local error
`≤ tol_local` per step, the global error after `N` steps is bounded
by `C(p) · N · tol_local` where `C(p)` is a problem-dependent
constant of `O(1)` for stable ODEs over the integration interval.
For DOPRI5 with `tol_local = atol + rtol · ‖y‖`, the global error
on a smooth problem is empirically `~10·rtol·tf`.

The verifier uses:

- **`tol_traj = 100 · rtol`** for trajectory accuracy (Higham-
  style 100× safety on the asymptotic rate).
- **`tol_self_report = 1e-1`** relative — the candidate's reported
  `error_estimate` must be within `10×` of the verifier's
  recomputed actual error. (Numerical error estimates are
  fundamentally rougher than linalg residuals — quadrature error
  estimators are factor-of-2-honest, not factor-of-1.000001-honest.)
- **`tol_conservation = 100 · rtol · tf`** for conserved quantities
  in tier-C/D (energy, angular momentum) — DOPRI5 is non-symplectic;
  drift `O(rtol · tf)` is *expected*, not a bug. The symplectic
  slice's verifier will tighten this to `O(h²)` per step.

Empirically: SciPy `solve_ivp(method='RK45', rtol=1e-13, atol=1e-14)`
clears each tolerance by ≥3 orders of magnitude on the bench (the
100× safety factor is the implementation budget for honest
controller variance, not slop).

## Why `non-finite-during-eval` is a tag, not a `ToolError`

A planner often hands the solver an RHS with a removable
singularity it didn't know about (a Riccati equation that hits a
movable pole, a chemical-kinetics ODE with a zero-crossing in a
denominator). The right move for the planner is: read the
`(at_t, at_y)` payload, decide whether the singularity is at the
boundary (truncate `tspan`) or interior (refactor RHS), then retry.

Refusing with `ToolError` discards that diagnostic. The boundary
tag carries the failing time and state, so the planner has the info
to decide. Same precedent as `integrate-1d`'s
`integrate-1d/non-finite-during-eval`.

## Why no event detection

Event detection (root-finding `g(t, y(t)) = 0` during integration)
is a separate algorithmic concern (Hermite-bisection on the
continuous extension, dense-output of the event component). It
doubles the substrate's surface area and changes the output shape
(`events: list<{t, y}>` adds a discriminated union in the schema).
It is the right v0.2 extension once the path-finder ships; for v0.1
the tool refuses event-style use cases by silently integrating past
roots and trusting the agent to post-process if needed.

The substrate `@workbench/ode-core` is structured to admit event
detection in v0.2 without changing the v0.1 wire format (it would
add an optional `events` field to the input record and an optional
`events: list<{t, y_at_event, event_index}>` to the output record).
