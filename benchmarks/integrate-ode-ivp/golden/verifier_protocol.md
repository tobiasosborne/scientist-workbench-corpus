# `integrate-ode-ivp` verifier protocol

The verifier reads `{input, candidate, id?}` on stdin and emits
`{pass, reason, checks: {<name>: {pass, detail}}}`. Every check is
computed independently; a case is overall `pass: true` iff every
check passes. The verifier reads `expected.json` (adjacent on disk)
for the reference trajectories on success-path cases.

## Constants

- `EPS` = `2.220446049250313e-16` (IEEE-754 double machine epsilon).
- `SAFETY` = `100` (multiplicative slack on Hairer-Nørsett-Wanner
  1993 §II.10 global-error bounds).
- `SELF_REPORT_REL_TOL` = `10.0` — error estimates are factor-of-10
  honest, not factor-of-1.000001 honest. Quadrature / step-control
  estimators are fundamentally rougher than linalg residuals.
- `RTOL_DEFAULT` = `1e-3`, `ATOL_DEFAULT` = `1e-6` — match
  scipy's `solve_ivp` defaults.

## The 8 success-path checks

### 1. `shape`

Pure structural. Given input with `n = len(y0)` and `t_eval` of
length `m` (default `m = 2`, `t_eval = [t0, tf]`):
- `trajectory` is `m × n` (nested list).
- `t_values` is a list of length `m`.
- Required output fields present with correct types: `trajectory`,
  `t_values`, `error_estimate`, `n_evals`, `n_steps_accepted`,
  `n_steps_rejected`, `converged`, `status`, `method`, `warnings`.
- `method` is a string; `warnings` is `list[str]`; `status` is one
  of the four documented values.

### 2. `finite_entries`

Every entry of `trajectory`, `error_estimate`, and the integer
counters is finite. Trajectory entries that are NaN/Inf are a
correctness failure regardless of `converged: false` — the tool must
emit a tagged boundary (`non-finite-during-eval`) instead of a
poisoned trajectory.

### 3. `monotone_t_values`

`t_values` ascending iff `t0 ≤ tf`, descending iff `t0 > tf`. When
the input provides `options.t_eval`, `t_values` must equal it
element-wise.

### 4. `status_consistency`

- `converged === True` iff `status === "success"`.
- `n_evals ≥ 6 · n_steps_accepted` — DOPRI5 is FSAL with 7 stages,
  so the asymptotic floor is 6 new evaluations per accepted step
  (the 7th is reused). Rejected steps add stages without
  incrementing `n_steps_accepted`.

### 5. `trajectory_accuracy`

For each `t_eval[i]`:
  `||trajectory[i] - y_ref(t_eval[i])||_∞ ≤ tol_traj`
  where `tol_traj = max(SAFETY · rtol · max(|y_ref|, 1), SAFETY · atol)`.

`y_ref` comes from `expected.json` (precomputed by `generate.py` via
analytic formula or `scipy.integrate.solve_ivp(method='DOP853',
rtol=1e-13, atol=1e-14)`).

For **chaotic problems** flagged with `chaotic_until_t = t_L` in
`expected.json` (Lorenz, planar restricted 3-body), trajectory
accuracy is checked only up to one Lyapunov time `t_L`; beyond
`t_L`, attractor-invariant statistics (mean, variance per component)
are checked instead with relative tolerance `1e-1` (chaos preserves
attractor statistics, not trajectories).

### 6. `self_reported_error_estimate`

The candidate's `error_estimate` is its own claim about the
solution's accuracy. The verifier recomputes the **actual**
sup-norm error normalised by `atol + rtol · ||y_ref||`:

  `actual = max_i (||trajectory[i] - y_ref(t_eval[i])||_∞ /
                   (atol + rtol · ||y_ref(t_eval[i])||_∞))`

The candidate's `error_estimate` must be within `SELF_REPORT_REL_TOL
= 10×` of `actual`:

  `1/SELF_REPORT_REL_TOL ≤ candidate.error_estimate / max(actual, EPS)
                       ≤ SELF_REPORT_REL_TOL`

Failure here means the tool is *lying about its own reliability* —
the integrator step controller is in a regime where it doesn't
believe the error it reports. This is broken even if the trajectory
is correct: a planner reading `error_estimate` and trusting it would
make wrong decisions.

(Self-report tighter than truth is also a failure — the controller
is over-confident. The two-sided bound catches both.)

### 7. `conservation` *(tier C/D only — when `conservation` field
present in expected.json)*

For problems with a conserved quantity `H(y)`:

  `max_i |H(trajectory[i]) - H(trajectory[0])| /
          max(|H(trajectory[0])|, atol) ≤ SAFETY · rtol · |tf - t0|`

DOPRI5 is non-symplectic: drift `O(rtol · tf)` is *expected*. The
bound `100 · rtol · tf` is the empirical envelope SciPy clears by
≥1 order of magnitude.

The conserved-quantity definition `H(y)` is encoded in
`expected.json` as one of:
- `{"kind": "lotka_volterra_h", "alpha": ..., "beta": ..., "gamma": ..., "delta": ...}`
- `{"kind": "kepler_energy"}` — `H = ||p||²/2 - 1/||q||`, indices
  `q = y[0:2], p = y[2:4]`.
- `{"kind": "kepler_angular_momentum"}` — `L = q × p` (scalar in 2D).
- `{"kind": "harmonic_energy"}` — `H = (q² + p²)/2`.

For chaotic problems no `conservation` field is set; the check is skipped.

## Tagged-boundary checks

For tagged outputs, the verifier runs a single `boundary` check that
confirms:
- The tag matches the input's actual boundary category.
- The payload has the documented shape.

Recognised tags:
- `integrate-ode-ivp/degenerate-tspan` — accepted iff input
  `t_span.t0 == t_span.tf`. Payload: `{t0, tf}` matching input.
- `integrate-ode-ivp/non-finite-during-eval` — accepted iff
  evaluating the (parsed) RHS at *some* point in `[t0, tf]` produces
  a non-finite value. Verifier samples 100 random points to check.
  Payload: `{at_t, at_y, kind ∈ {"NaN", "Infinity", "-Infinity"}}`.

Mis-classified tags (e.g. `degenerate-tspan` on a non-degenerate
input) fail the `boundary` check.

## ToolError-expected checks

For cases marked `expected_class == "tool_error"` in `expected.json`,
the verifier accepts:
- `{"kind": "tool_error", "name": str, "message": str}` from
  `run-candidate.ts` (it wraps thrown ToolErrors into this marker
  shape on the bench's stdout).

The verifier does *not* check the exact error message — only that
the candidate produced a `tool_error` marker rather than a success
record or tagged boundary.

## Failure-reason format

When a check fails, `detail` includes the offending value, the
tolerance, and (for trajectory checks) the time index `i` and the
component magnitude that violated the bound. Standard verifier
diagnostic floor.

## What's NOT checked

- **Exact match against SciPy DOP853 trajectory.** SciPy DOP853 at
  rtol=1e-13 is the *reference*, but a correct DOPRI5 implementation
  at rtol=1e-3 will routinely differ from the reference at the
  rtol=1e-3 level. The trajectory_accuracy check has `tol_traj =
  100·rtol`, not `1e-12`.
- **Exact match against SciPy's `n_evals`/`n_steps_accepted`.** The
  PI controller's exact step-acceptance pattern depends on numerical
  details; only the FSAL lower bound `n_evals ≥ 6·n_steps_accepted`
  and reasonability bounds are checked.
- **Tolerance-monotonicity across tier F cases.** Each tier-F case
  checks its own `tol_traj = 100 · rtol_case`; if all three pass
  individually, monotonicity is observable but not enforced as a
  cross-case check (which would require state across verifier
  invocations).
- **Step rejection counts in tier D.** DOPRI5 with PI control on
  vdP `μ=10` will produce *some* rejections, but the exact count
  is implementation-dependent. The check is structural: `if μ ≥ 10,
  expect at least one rejection across the integration` is too
  loose to enforce. We rely on `trajectory_accuracy` to catch a
  controller that under-rejects and produces wrong answers.
