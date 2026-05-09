# `integrate-ode-stiff` verifier protocol

Reads `{input, candidate, id?}` on stdin; emits
`{pass, reason, checks: {<name>: {pass, detail}}}`. Reads
`expected.json` adjacent for reference trajectories.

Mirrors `bench/integrate-ode-ivp/golden/verifier_protocol.md` with
two stiff-specific check additions: `stiffness_handled` and
`jacobian_consumed`.

## Constants

- `EPS` = `2.220446049250313e-16`
- `SAFETY` = `100`
- `RTOL_DEFAULT` = `1e-3`, `ATOL_DEFAULT` = `1e-6`

## The 9 success-path checks

1. **`shape`** — `trajectory` is `(m, n)`; `t_values` length `m`;
   required fields present including `n_jacobian_evals` and
   `n_lu_decompositions`; `status` ∈ `{"success", "max_step_exceeded",
   "tspan_exhausted", "newton-divergence"}`.
2. **`finite_entries`** — every numeric entry finite.
3. **`monotone_t_values`** — ascending iff `t0 ≤ tf`; matches
   `options.t_eval` when provided.
4. **`status_consistency`** — `converged === (status === "success")`;
   counters non-negative; `n_jacobian_evals ≤ n_steps_accepted +
   n_steps_rejected + 1` (one Jacobian per accepted/rejected step
   plus one initial); `n_lu_decompositions ≤ 2 · (n_steps_accepted +
   n_steps_rejected) + 2` (real + complex factorisation per step).
5. **`trajectory_accuracy`** — `||cand − ref||_∞ ≤ 100·rtol·||ref|| +
   100·atol`. **No horizon scaling** — Radau is stiffly bounded
   (Hairer-Wanner Vol II §IV.10).
6. **`self_reported_error_estimate`** — non-negative; `≤ max(1, atol·1e6)`
   when `status === "success"`. Same structural floor as path-finder.
7. **`stiffness_handled`** — `n_evals ≤ 100 · n_steps_accepted`.
   A correct implicit method spends `O(s · k)` evaluations per
   accepted step where `s = 3` Radau stages and `k` is Newton
   iterations (`~3-7`). A pseudo-implicit candidate that secretly
   small-steps explicitly would explode to `n_evals >> 100 · n_acc`
   on stiff problems.
8. **`conservation`** *(when `expected.conservation` present)* —
   identical to path-finder.
9. **`jacobian_consumed`** *(when `input.options.jacobian` provided)* —
   `n_jacobian_evals ≥ 1`. The candidate must actually consume the
   user-supplied analytic Jacobian.

## Tagged-boundary checks

- `degenerate-tspan` — `t0 == tf`. Payload `{t0, tf}`.
- `non-finite-during-eval` — payload-shape check (`{at_t, at_y, kind}`).
- `jacobian-singular` — payload-shape check (`{at_t, at_y,
  condition_number}`).
- `method-not-implemented` — payload-shape check (`{method}`); accepted
  when `input.options.method ∈ {"bdf"}`.

## ToolError-expected checks

`{"kind": "tool_error", "name": str, "message": str}` accepted when
`expected.kind == "tool_error"`.

## What's NOT checked

- Exact match against SciPy's `n_evals` / `n_jacobian_evals`. The PI
  controller's exact reuse pattern is implementation-dependent; only
  structural bookkeeping bounds are enforced.
- Newton iteration count per step. Implementation-specific.
