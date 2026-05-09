# Bench — `integrate-ode-stiff` (Stiff IVP via Radau-IIA(5))

## ⚠ How you will be graded

You will be graded on **CORRECTNESS** and **NUMERICAL HONESTY**.

Produce the most elegant, most efficient, most numerically faithful
TypeScript implementation you can. This bench is the *floor*, not the
ceiling — passing it is necessary but not sufficient. The tool must
also conform to the scientist-workbench seven-artefact contract (see
`CLAUDE.md`, `README.md`, `PRD-v0.2.md`).

The verifier checks **invariants**, not byte-equality: trajectory
agreement is to method-tier tolerance, conservation drift (where
applicable) is bounded, bookkeeping invariants are structural. Every
check has a tolerance derived from Hairer-Wanner Vol II §IV.10
backward stability.

## Problem statement

Implement adaptive integration of the *stiff* initial value problem

  `dy/dt = f(t, y),  y(t0) = y0,  t ∈ [t0, tf]`

via **Radau-IIA(5)** — 3-stage 5th-order implicit Runge-Kutta with
collocation at the Radau-IIA quadrature points
`c = ((4 − √6)/10, (4 + √6)/10, 1)`. A-stable, L-stable, stiffly
accurate. The `scipy.integrate.solve_ivp(method='Radau')` algorithm.

Per-step Newton iteration on the `(3n × 3n)` implicit system. Use the
**simplified-Newton + complex-eigenvalue transformation** (Hairer-
Wanner 1999): `(I − h·A ⊗ J)` factorises into one real `n × n` linear
solve + one `(2n × 2n)` real linear solve via the eigenvalues of `A`.
Compose `linalg-solve` (in-process via `@workbench/compose`).

Jacobian source:
1. **Analytic via `cas-diff`** when `f`'s components are in the closed
   vocabulary (the default — works for every bench case).
2. **Finite-difference fallback** when symbolic differentiation refuses
   (out-of-vocabulary `f`). Emit a warning string.

## I/O contract

### Bench wire format (raw JSON)

Mirrors `integrate-ode-ivp` plus stiff-specific options.

### Input (one JSON object on stdin to `run-candidate.ts`)

```jsonc
{
  "f_str":      ["<expression-string>", ...],
  "vars":       ["<state-var-name>", ...],
  "t_var":      "<independent-var-name>",
  "y0":         [<float>, ...],
  "t_span":     {"t0": <float>, "tf": <float>},
  "options": {
    "rtol":     <float>,                  // default 1e-3
    "atol":     <float>,                  // default 1e-6
    "max_step": <float>,                  // optional cap
    "t_eval":   [<float>, ...],           // optional output grid
    "method":   "radau" | "bdf",          // default "radau"; "bdf" → method-not-implemented
    "jacobian": [["<expr>", ...], ...]    // optional analytic J; FD fallback if absent
  }
}
```

### Output (one JSON object on stdout)

```jsonc
{
  "trajectory":           [[<float>, ...], ...],
  "t_values":             [<float>, ...],
  "error_estimate":       <float>,
  "n_evals":              <int>,
  "n_steps_accepted":     <int>,
  "n_steps_rejected":     <int>,
  "n_jacobian_evals":     <int>,
  "n_lu_decompositions":  <int>,
  "converged":            <bool>,
  "status":               "success" | "max_step_exceeded" | "tspan_exhausted" | "newton-divergence",
  "method":               "radau-iia-5",
  "warnings":             [<string>, ...]
}
```

### Tagged-boundary outputs

```jsonc
{"kind": "tagged", "tag": "integrate-ode-stiff/degenerate-tspan",       "payload": {"t0", "tf"}}
{"kind": "tagged", "tag": "integrate-ode-stiff/non-finite-during-eval", "payload": {"at_t", "at_y", "kind"}}
{"kind": "tagged", "tag": "integrate-ode-stiff/jacobian-singular",      "payload": {"at_t", "at_y", "condition_number"}}
{"kind": "tagged", "tag": "integrate-ode-stiff/method-not-implemented", "payload": {"method": "bdf"}}
```

## Invariants checked

The verifier runs **9 independent checks** per success-path case:

1. **`shape`** — required fields present; trajectory dims `(m, n)`.
2. **`finite_entries`** — every entry finite.
3. **`monotone_t_values`** — ascending iff `t0 ≤ tf`.
4. **`status_consistency`** — `converged === (status === "success")`;
   counters non-negative; `n_jacobian_evals ≤ n_steps_accepted +
   n_steps_rejected + 1`; `n_lu_decompositions ≤ 2 · (n_steps_accepted
   + n_steps_rejected) + 2` (real + complex factorisation).
5. **`trajectory_accuracy`** — `||cand − ref||_∞ ≤ 100·rtol·||ref|| +
   100·atol`. **No horizon scaling** (Radau is stiffly-bounded).
6. **`self_reported_error_estimate`** — non-negative; `≤ max(1, atol·1e6)`
   when `status === "success"`.
7. **`stiffness_handled`** — `n_evals < 100·n_steps_accepted` (a
   broken implicit method that secretly explicitly steps would blow
   this bound on stiff problems).
8. **`conservation`** *(where applicable)* — for problems with a
   conserved quantity, drift bounded by `100·rtol`.
9. **`jacobian_consumed`** *(when `options.jacobian` provided)* —
   `n_jacobian_evals ≥ 1` (the analytic J was actually used).

For tagged boundaries, payload-shape check.

## Test set tiers

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 3 | scalar mild stiff, scalar exp decay, 2D linear stiff |
| B. analytic / mild-stiff | 3 | linear stiff 2D analytic, vdP μ=1, exp decay long horizon |
| C. stiffness sweep | 4 | vdP μ ∈ {100, 1e3, 1e4, 1e6} |
| D. NHW Vol II canonical | 5 | Robertson, HIRES, Oregonator, E5, Pollu |
| E. boundary | 4 | degenerate-tspan, pole-in-rhs, jacobian-singular, dim-mismatch |
| F. tolerance discipline | 3 | Robertson at rtol ∈ {1e-3, 1e-6, 1e-9} |
| I. industrial stress | 2 | ring-modulator (15 comp), MEDAKZO (40 comp) |

Total: ~24 cases.

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/integrate-ode-stiff bun bench/integrate-ode-stiff/run-candidate.ts
```

## Hard constraints

Same as `integrate-ode-ivp` (read its `PROMPT.md`). Plus:

- **Substrate**: extend `packages/ode-core/` with `radau.ts` (Radau
  Butcher table + simplified-Newton step), `newton-iteration.ts` (the
  iteration loop with FD-Jacobian fallback), and `complex-solve.ts`
  (the `(2n × 2n)` real solve from a complex-coefficient system).
- **Compose**: `linalg-solve` for the linear solves. Do NOT inline
  LU; the workbench's solver is already there with iterative
  refinement and condition-number tracking.
- **Closed vocabulary**: same as path-finder. Symbolic Jacobian via
  `cas-diff` (in-process); FD fallback when out-of-vocab.
- **Boundary categories** (ADR-0003): four tags as listed above.

## What you must do

1. Read `bench/integrate-ode-ivp/PROMPT.md` first — most of the
   discipline transfers verbatim.
2. Read `tools/integrate-ode-ivp/tool.ts` and `packages/ode-core/`
   end-to-end. You're extending this substrate.
3. Implement `packages/ode-core/src/{radau.ts, newton-iteration.ts,
   complex-solve.ts}` and `tools/integrate-ode-stiff/`.
4. Run the bench until 100% across all 9 checks.
5. Run `bun run check`.
6. Add worklog `docs/worklog/049-integrate-ode-stiff.md`.
7. Update README catalog row + `scripts/demo-scope.ts`.
8. `bd close scientist-workbench-09g` after green.

## Things that will tempt you and which are wrong

- **Inlining LU decomposition** instead of using `linalg-solve`. Wrong
  — the workbench's solver has iterative refinement and condition
  reporting; reusing it gets `condition_number` for free in the
  jacobian-singular detection path.
- **Recomputing the Jacobian every step.** Wrong — Hairer-Wanner
  prescribes Jacobian reuse across multiple steps controlled by the
  Newton convergence rate. The PI controller uses Jacobian-staleness
  signals.
- **Using BDF.** Wrong — the path-finder is single-method (Radau).
  `options.method = "bdf"` boundary-tags `method-not-implemented`.
- **Skipping the complex-eigenvalue split.** Inlining a `(3n × 3n)`
  solve per Newton step is `27×` more work than the split. Don't.

The two principles are the highest-priority decision rule.
