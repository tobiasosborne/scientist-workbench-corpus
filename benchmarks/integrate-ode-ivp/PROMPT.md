# Bench — `integrate-ode-ivp` (Non-stiff IVP via Dormand-Prince 5(4))

## ⚠ How you will be graded

You will be graded on **CORRECTNESS** and **NUMERICAL HONESTY**.

Produce the most elegant, most efficient, most numerically faithful
TypeScript implementation you can. This bench is the *floor*, not the
ceiling — passing it is necessary but not sufficient. The tool must
also conform to the scientist-workbench seven-artefact contract (see
`CLAUDE.md`, `README.md`, `PRD-v0.2.md`).

The verifier checks **invariants**, not byte-equality: trajectory
agreement is to method-tier tolerance, self-report honesty is to
factor-of-10, conservation drift is bounded by `100·rtol·tf`. Every
check has a tolerance derived from non-stiff IVP convergence theory
(Hairer-Nørsett-Wanner 1993 §II.10) with a 100× empirical safety
factor calibrated against SciPy.

## Problem statement

Implement adaptive integration of the initial value problem

  `dy/dt = f(t, y),  y(t0) = y0,  t ∈ [t0, tf]`

for non-stiff systems via **Dormand-Prince 5(4)** (DOPRI5), the
canonical embedded explicit Runge-Kutta pair: 5th-order solution
advances, 4th-order embedded estimate drives adaptive step-size
control via Gustafsson's PI law (1991). FSAL property exploited: 7th
stage reused as 1st stage of the next accepted step.

`f` is required to use the **closed expression vocabulary** shared
across `cas-diff`, `integrate-1d`, `optimize-lbfgs-projected`:

```
+, -, *, /, ^, neg,
exp, sin, cos, tan, log, sqrt, abs,
asin, acos, atan, sinh, cosh, tanh, asinh, acosh, atanh, log2, log10
```

Constants: `pi`, `e`. Numeric leaves: `integer`, `rational`, `float64`.
**Reuse `evalNumericExpr` from `@workbench/quadrature` directly** —
do not write a second evaluator.

Algorithm coefficients (Dormand-Prince 1980; verbatim copy in any
non-stiff IVP textbook). PI controller constants: `α = 0.7, β = 0.4`
for order `p = 5` (Gustafsson 1991). Default tolerances: `rtol = 1e-3,
atol = 1e-6`, matching `scipy.integrate.solve_ivp(method='RK45')`.

## I/O contract

### Bench wire format (raw JSON)

The bench's wire format is **raw JSON**. The adapter
`bench/integrate-ode-ivp/run-candidate.ts` bridges to the tool's
canonical `Value` protocol — it parses each `f_str` element via
`expr-parse` to construct the `f: list<expression>` field.

### Input (one JSON object on stdin to `run-candidate.ts`)

```jsonc
{
  "f_str":      ["<expression-string>", ...],   // n_components, in the closed vocabulary
  "vars":       ["<state-var-name>", ...],       // n_components state variable names (e.g. ["y0", "y1"])
  "t_var":      "<independent-var-name>",        // typically "t"
  "y0":         [<float>, ...],                   // n_components initial state
  "t_span":     {"t0": <float>, "tf": <float>},  // integration interval; t0 may exceed tf for reverse integration
  "options": {                                    // optional; all fields optional within
    "rtol":     <float>,                          // default 1e-3
    "atol":     <float>,                          // default 1e-6
    "max_step": <float>,                          // optional cap on step size
    "t_eval":   [<float>, ...]                    // optional output time grid; default [t0, tf]
  }
}
```

### Output (one JSON object on stdout from `run-candidate.ts`)

```jsonc
{
  "trajectory":        [[<float>, ...], ...],   // (n_eval × n_components) state at each t_eval
  "t_values":          [<float>, ...],           // (n_eval,) the time grid (== t_eval if provided, else internal-step grid)
  "error_estimate":    <float>,                   // sup-norm of last accepted local error / atol
  "n_evals":           <int>,                    // total f evaluations across accepted + rejected steps
  "n_steps_accepted":  <int>,
  "n_steps_rejected":  <int>,
  "converged":         <bool>,                   // true iff status === "success"
  "status":            "success" | "max_step_exceeded" | "tspan_exhausted" | "stiffness-detected",
  "method":            "dormand-prince-45",
  "warnings":          [<string>, ...]
}
```

### Tagged-boundary outputs

When the input is structurally well-formed but the algorithm refuses
to silently produce a wrong answer, emit a tagged boundary:

```jsonc
{"kind": "tagged", "tag": "integrate-ode-ivp/degenerate-tspan",
 "payload": {"t0": <float>, "tf": <float>}}

{"kind": "tagged", "tag": "integrate-ode-ivp/non-finite-during-eval",
 "payload": {"at_t": <float>, "at_y": [<float>, ...], "kind": "NaN" | "Infinity" | "-Infinity"}}
```

## Invariants checked

The verifier runs **8 independent checks** per success-path case:

1. **`shape`** — output object has all required fields; trajectory
   shape `(len(t_eval), n_components)`; `t_values` length equals
   trajectory rows.
2. **`finite_entries`** — every entry of `trajectory`,
   `error_estimate`, `n_evals`, `n_steps_accepted`,
   `n_steps_rejected` is finite (or correct-typed integer).
3. **`monotone_t_values`** — `t_values` ascending iff `t0 ≤ tf`,
   descending iff `t0 > tf`; matches `t_eval` exactly when
   provided.
4. **`status_consistency`** — `converged` iff `status === "success"`;
   `n_evals ≥ 6 · n_steps_accepted` (FSAL lower bound).
5. **`trajectory_accuracy`** — `‖y_candidate(t_eval) -
   y_ref(t_eval)‖_∞ ≤ tol_traj` where `tol_traj = max(100·rtol,
   100·atol)`. For chaotic problems (Lorenz), checked only up to
   one Lyapunov time `t_L`; attractor statistics checked beyond.
6. **`self_reported_error_estimate`** — `error_estimate` within
   `10×` of verifier-recomputed actual sup-norm error normalised
   by `atol + rtol·‖y_ref‖`. Agent-honest discipline.
7. **`conservation`** *(tier C/D only)* — for problems with a
   conserved quantity `H(t, y)`: `max_t |H(y(t)) - H(y(0))| /
   max(|H(y(0))|, atol) ≤ 100·rtol·|tf - t0|`. Allows non-
   symplectic drift bounded by `O(rtol·tf)`.
8. **`tolerance_monotonicity`** *(tier F only)* — for the same
   problem at three rtol regimes, actual error must monotonically
   tighten with rtol. A solver whose `rtol = 1e-10` answer isn't
   tighter than its `rtol = 1e-3` answer is broken.

For **tagged boundaries** (`degenerate-tspan`, `non-finite-during-eval`),
a single `boundary` check confirms the input *is* the relevant
boundary case and the payload has the documented shape.

For **`ToolError` cases** (unknown head, dim mismatch), the bench
runner observes non-zero exit and the verifier accepts via the
`tool_error_expected` check class.

## Test set tiers

`golden/inputs.json` contains **~28 cases** spanning:

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 4 | scalar `n=1`, 2D coupled, 3D coupled, scalar zero RHS |
| B. smooth analytic oracles | 6 | exp decay/growth, logistic, harmonic 2D, Riccati, vdP μ=1 |
| C. NHW Vol I canonical | 5 | Lotka-Volterra (with conserved H), Brusselator, Kepler e=0.5 (with H+L), Lorenz (chaos), 3-body Pleiades |
| D. stress / conservation | 4 | vdP μ=10 (force step rejections), long-horizon, high-freq, Kepler e=0.6 over 5 orbits |
| E. boundary cases | 4 | degenerate-tspan, pole-in-rhs, unknown-head, dim-mismatch |
| F. tolerance discipline | 3 | same problem at rtol ∈ {1e-3, 1e-6, 1e-10}; monotonicity |
| G. reverse integration | 1 | t0 > tf; recover `y(t0)` from `y(tf)` |
| H. dense output accuracy | 2 | sub-step `t_eval` requires Hermite continuous extension |

Total: **~28 cases × 8 checks ≈ 200 invariant assertions**.

Reference solutions:
- **Analytic** where available (B tier exp/logistic/harmonic, C
  Kepler ellipse via eccentric anomaly).
- **`scipy.integrate.solve_ivp(method='DOP853', rtol=1e-13,
  atol=1e-14)`** otherwise — 8th-order Dormand-Prince at machine-
  precision tolerance, accurate to ~1e-12 absolute.
- **Conservation invariants** computed analytically from problem
  definitions (Hamiltonians, conserved quantities).

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/integrate-ode-ivp bun bench/integrate-ode-ivp/run-candidate.ts
```

### Files

- `golden/inputs.json` — every test case.
- `golden/expected.json` — reference outputs (analytic where
  available; SciPy DOP853 otherwise; not consulted by verifier
  except for trajectory comparison).
- `golden/verify.py` — invariant verifier (numpy + scipy).
- `golden/verifier_protocol.md` — exact tolerances per check.
- `golden/generate.py` — reproducible golden generation.
- `reference/ivp_reference.py` — Python+SciPy reference.
- `run-candidate.ts` — wire-format adapter.

## Hard constraints (sci-wb-specific)

- **Pure TypeScript on Bun.** No FFI, no WASM, no `child_process`.
- **Seven-artefact contract.** Schema (declared via `S.*`),
  examples (≥10), invariants, property tests / `--test` hook,
  goldens directory (≥30 once "v1-complete"), README, source.
- **`numerical: true` annotation** (ADR-0015). Provenance carries
  the platform fingerprint when float64 leaves are present.
- **No hard cap (ADR-0016).** Use
  `assessNumericalScale("ode-rkf45", n_components,
  estimated_n_steps)` for warnings; OOM is the only physical
  refusal.
- **Closed vocabulary for `f`** matches `cas-diff` /
  `integrate-1d` / `optimize-lbfgs-projected`. **Reuse
  `evalNumericExpr` from `@workbench/quadrature`** — do not write
  a second evaluator. Unknown head → `ToolError` with
  `suggestion` listing admitted heads.
- **Boundary categories (ADR-0003):**
  - `tagged "integrate-ode-ivp/degenerate-tspan"` for `t0 == tf`.
  - `tagged "integrate-ode-ivp/non-finite-during-eval"` for f
    returning NaN/±Inf at any (t, y) during integration.
  - `ToolError` for `len(f) ≠ len(y0) ≠ len(vars)`, unknown
    expression head, malformed options, true OOM.
- **Determinism.** Same input bytes + same platform fingerprint
  → bit-identical `trajectory`, `error_estimate`, all integer
  counters. No `Date.now`, no `Math.random`.
- **In-process call site.** The bench invokes via
  `loadWorkbench()` + `wb.run("integrate-ode-ivp", ...)` — that is
  the canonical TS-expert call site (ADR-0012). The tool must be
  side-effect-free at module import time;
  `if (import.meta.main) void runTool(def);` at the bottom.

## Substrate

Implement on `@workbench/ode-core` (new package, mirroring
`linalg-core` / `quadrature` precedent):

- `packages/ode-core/src/dopri5.ts` — DOPRI5 coefficient table,
  single-step routine on `Float64Array`.
- `packages/ode-core/src/pi-controller.ts` — Gustafsson PI step-size
  control.
- `packages/ode-core/src/integrate.ts` — top-level adaptive driver
  with FSAL bookkeeping, dense output (Hermite continuous extension),
  status / convergence handling.
- `packages/ode-core/src/eval-rhs.ts` — thin wrapper around
  `evalNumericExpr` for vector RHS evaluation; no new evaluator.
- `packages/ode-core/src/scale.ts` — `assessNumericalScale("ode-rkf45",
  n_components, estimated_n_steps)` mirroring linalg-core's pattern.

The tool itself (`tools/integrate-ode-ivp/tool.ts`) is the thin
contract layer: schema declaration, `fn` body that delegates to
`integrate()` from `@workbench/ode-core`, output encoding.

## What you must do

1. **Read** `CLAUDE.md` (the laws and the rules), `PRD-v0.2.md`
   (canonical design), `tools/integrate-1d/tool.ts` (immediate
   precedent for an adaptive numerical tool), `tools/optimize-lbfgs-
   projected/tool.ts` (multi-component-state precedent),
   `packages/quadrature/src/eval-expr.ts` (the closed-vocabulary
   evaluator you will reuse), `bench/linalg-eigh/` (the bench
   protocol you will pass), `docs/adr/0014-first-numerical-tier.md`,
   `docs/adr/0015-determinism-tier.md`,
   `docs/adr/0016-warning-based-numerical-scaling.md`.

2. **Internalise the two principles** (`bd memories two-principles`):
   "what would a TypeScript expert expect/want" and "irresistible to
   agents (who are TS experts)" — these are the same principle. The
   tool's wire surface, output record fields, and call-site ergonomics
   are graded against these.

3. **Implement** `packages/ode-core/` and `tools/integrate-ode-ivp/`
   to the seven-artefact contract.

4. **Run the bench** until 100% across all 8 checks:
   ```sh
   bash bench/infra/run-bench.sh bench/integrate-ode-ivp \
       bun bench/integrate-ode-ivp/run-candidate.ts
   ```

5. **Run** `bun run check` until green (typecheck, workspace tests,
   per-tool `--test`, oracle on goldens).

6. **Add a worklog shard** at `docs/worklog/048-integrate-ode-ivp.md`
   following the established structure (Context → What changed → Why
   these choices → Frictions surfaced → Acceptance → Pointers).

7. **Update**: README catalog row, the main README's "File layout"
   to add `packages/ode-core/`, `scripts/demo-scope.sh` /
   `scripts/demo-scope.ts` with one IVP demo.

8. **Report** per-check totals.

## Things that will tempt you and which are wrong

- Writing a new expression evaluator in `ode-core/`. Reuse
  `evalNumericExpr`. The closed vocabulary is settled.
- Catching `non-finite-during-eval` and returning `success: false`
  with NaN in the trajectory. **Wrong.** Tag the boundary; do not
  lie.
- Returning `(t_values, trajectory)` as a flat tuple to "match scipy
  more closely." **Wrong.** The agent-honest record is the contract.
- Adding event detection ("it's just root-finding"). **Defer.**
  v0.2 bead. Keep the v0.1 wire format minimal.
- Implementing dense output by linearly interpolating between
  accepted-step endpoints. **Wrong.** DOPRI5 has a 4th-order
  Hermite continuous extension (HNW Vol I §II.6) using the
  k-vectors from the accepted step. The `H_dense_*` cases probe
  this.
- Hard-capping `n_components ≤ 100` because "the bench tier J
  doesn't go above that." **Wrong.** ADR-0016 is explicit: no hard
  caps, scale warnings instead.

The two principles are the highest-priority decision rule. When in
doubt, take a position. Don't escalate questions where the principles
produce a clear answer.
