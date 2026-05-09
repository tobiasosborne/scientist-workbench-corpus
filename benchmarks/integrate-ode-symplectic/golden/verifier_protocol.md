# `integrate-ode-symplectic` verifier protocol

Reads `{input, candidate, id?}` on stdin; emits
`{pass, reason, checks: ...}`. Reads `expected.json` adjacent on disk.

The headline check is `energy_drift_not_secular` — a correct
symplectic integrator's energy drift is bounded `O(h^p)` regardless
of horizon (HLW §VI.6 backward error analysis).

## Constants

- `EPS` = `2.220446049250313e-16`
- `SAFETY` = `100`

## The 8 success-path checks

1. **`shape`** — `q_trajectory`/`p_trajectory` shape `(n_steps+1, |q|)`/
   `(n_steps+1, |p|)`; required fields present including
   `energy`, `energy_drift_max`, `energy_drift_secular`.
2. **`finite_entries`** — every numeric entry finite.
3. **`monotone_t_values`** — uniformly spaced `[t0, tf]`.
4. **`status_consistency`** — `converged === (status === "success")`;
   `n_steps == n_steps_actual`; counters non-negative.
5. **`trajectory_accuracy`** — `||q_cand − q_ref||_∞ ≤ tol_traj` with
   `tol_traj = 1000 · h^p · max(||q_ref||, 1)` for the integrator's
   order `p` (2 for Verlet, 4 for Yoshida-4). Loose pointwise bound —
   symplectic methods optimise conservation, not pointwise accuracy.
6. **`energy_drift_bounded`** — `energy_drift_max ≤ tol_drift` per
   case (default `100 · h^p · drift_constant`; per-case overrides).
7. **`energy_drift_not_secular`** *(when `expected.long_time` true)* —
   `energy_drift_secular: false`. The discriminator. A non-symplectic
   candidate fails here on long-time Kepler.
8. **`order_consistency`** *(tier F: when `expected.order_check` group
   present)* — `energy_drift_max(h)` / `energy_drift_max(h/2)` ratio
   approximates `2^p` within a factor of 4.

## Tagged-boundary checks

- `degenerate-tspan` — `t0 == tf` or `n_steps == 0`. Payload
  `{t0, tf, n_steps}`.
- `non-separable-hamiltonian` — payload-shape (`{reason}`).
  Verified by the verifier re-checking separability via sympy on the
  input H.
- `non-finite-during-eval` — payload-shape only.

## ToolError-expected checks

`{"kind": "tool_error", "name": str, "message": str}` accepted when
`expected.kind == "tool_error"`.

## What's NOT checked

- Exact match against the reference Verlet trajectory. Symplectic
  methods give `O(h^p)` pointwise error; trajectory comparison uses
  `1000 · h^p` tolerance, not `100 · rtol`.
- Cross-case tolerance monotonicity. Each tier-F case is graded
  individually.
