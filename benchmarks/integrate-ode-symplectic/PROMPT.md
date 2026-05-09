# Bench — `integrate-ode-symplectic` (Hamiltonian flow via Velocity Verlet / Yoshida-4)

## ⚠ How you will be graded

You will be graded on **STRUCTURE PRESERVATION** above all.

The headline check is `energy_drift_secular: false` on the long-time
Kepler tier — a correct symplectic integrator never lets energy drift
grow linearly. Pointwise trajectory accuracy is secondary; symplectic
methods exhibit superb conservation but only `O(h^p)` pointwise error.

## Problem statement

Implement integration of separable Hamiltonian systems

  `H(q, p) = T(p) + V(q)`,  `dq/dt = ∂H/∂p`,  `dp/dt = −∂H/∂q`

via **Velocity Verlet** (2nd-order; default) or **Yoshida-4**
(4th-order via Suzuki-Yoshida composition of three Verlet steps).
Reference: Hairer-Lubich-Wanner *Geometric Numerical Integration*
§I.3.1 (Verlet) and §VI.3 (Yoshida).

Auto-derive `∂H/∂q` and `∂H/∂p` via `cas-diff` (in-process via
`@workbench/compose`). Detect non-separable input by checking that
each component of `∂H/∂q` is independent of `p_vars` (and vice versa);
if not → `tagged "integrate-ode-symplectic/non-separable-hamiltonian"`.

## I/O contract

### Bench wire format (raw JSON)

### Input

```jsonc
{
  "H_str":   "<Hamiltonian-expression-string>",
  "q_vars":  ["<q-name>", ...],
  "p_vars":  ["<p-name>", ...],
  "t_var":   "t",
  "q0":      [<float>, ...],
  "p0":      [<float>, ...],
  "t_span":  {"t0": <float>, "tf": <float>},
  "n_steps": <int>,                 // fixed step count (symplectic methods don't adapt)
  "options": {
    "scheme": "verlet" | "yoshida-4",  // default "verlet"
    "atol":   <float>                  // for energy-drift normalisation
  }
}
```

### Output

```jsonc
{
  "q_trajectory":         [[<float>, ...], ...],   // (n_steps+1 × |q|)
  "p_trajectory":         [[<float>, ...], ...],   // (n_steps+1 × |p|)
  "t_values":             [<float>, ...],
  "energy":               [<float>, ...],          // H at each step
  "energy_drift_max":     <float>,                  // max |H_i − H_0| / max(|H_0|, atol)
  "energy_drift_secular": <bool>,                   // linear-growth test
  "n_evals":              <int>,
  "n_steps":              <int>,
  "converged":            <bool>,
  "status":               "success" | "non-finite-during-eval",
  "method":               "velocity-verlet" | "yoshida-4",
  "warnings":             [<string>, ...]
}
```

### Tagged-boundary outputs

```jsonc
{"kind": "tagged", "tag": "integrate-ode-symplectic/degenerate-tspan",            "payload": {"t0", "tf", "n_steps"}}
{"kind": "tagged", "tag": "integrate-ode-symplectic/non-separable-hamiltonian",   "payload": {"reason"}}
{"kind": "tagged", "tag": "integrate-ode-symplectic/non-finite-during-eval",      "payload": {"at_t", "at_q", "at_p", "kind"}}
```

## Invariants checked

The verifier runs **8 independent checks** per success-path case:

1. **`shape`** — `q_trajectory`/`p_trajectory` shape `(n_steps+1, |q|)`/
   `(n_steps+1, |p|)`; required fields present.
2. **`finite_entries`** — all numeric entries finite.
3. **`monotone_t_values`** — uniformly spaced `[t0, tf]`.
4. **`status_consistency`** — `converged === (status === "success")`.
5. **`trajectory_accuracy`** — for cases with analytic / hand-Verlet
   reference, `||q_cand − q_ref||_∞ ≤ 1000 · h^p · ||q_ref||`. Loose
   pointwise bound (symplectic methods are good at conservation, not
   pointwise accuracy).
6. **`energy_drift_bounded`** — `energy_drift_max ≤ tol_drift` per
   case (default `100 · h^p · drift_constant`).
7. **`energy_drift_not_secular`** *(long-time tier C cases)* —
   `energy_drift_secular: false`. The discriminator: a non-symplectic
   integrator submitted as candidate would fail here on Kepler over
   `10⁴` orbits.
8. **`order_consistency`** *(tier F)* — `energy_drift_max(h)`,
   `energy_drift_max(h/2)`, `energy_drift_max(h/4)` ratio approximates
   `2^p`.

For tagged boundaries, payload-shape check.

## Test set tiers

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 3 | 1-DOF harmonic at `n_steps ∈ {1, 10, 100}` |
| B. canonical | 5 | harmonic, pendulum (small/large), Kepler 1-period, double pendulum |
| C. long-time conservation | 3 | Kepler 100 periods, Kepler 10⁴ periods, Hénon-Heiles |
| D. non-separable detection | 1 | `H = (q+p)²/2` → tagged |
| E. boundary | 4 | degenerate, non-separable, dim mismatch, unknown head |
| F. order check | 2 | h, h/2, h/4 — drift scales `h^p` |

Total: ~18 cases.

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/integrate-ode-symplectic bun bench/integrate-ode-symplectic/run-candidate.ts
```

## Hard constraints

Same as `integrate-ode-ivp` and `integrate-ode-stiff` (read those PROMPTs).
Plus:

- **Substrate**: extend `packages/ode-core/` with `verlet.ts` (single
  Verlet step on Float64Array), `yoshida.ts` (Suzuki-Yoshida
  composition), `hamiltonian-flow.ts` (top-level driver: separability
  check via `cas-diff`, force-and-velocity update loop, energy
  tracking, secular-drift detection).
- **Compose**: `cas-diff` (in-process) for `∂H/∂q` and `∂H/∂p`. The
  candidate calls `wb.run("cas-diff", {f: H, var: q_i})` etc. and
  caches the resulting expression Values for fast evaluation in the
  inner loop.
- **Closed vocabulary** for `H` matches `cas-diff` /
  `integrate-1d` / `integrate-ode-ivp` / `integrate-ode-stiff`.
- **Boundary categories** (ADR-0003): three tags as listed.

## What you must do

1. Read `bench/integrate-ode-ivp/PROMPT.md` first — most discipline
   transfers verbatim.
2. Read `tools/integrate-ode-ivp/tool.ts`, `packages/ode-core/`, and
   `tools/cas-diff/tool.ts` end-to-end.
3. Implement `packages/ode-core/src/{verlet.ts, yoshida.ts,
   hamiltonian-flow.ts}` and `tools/integrate-ode-symplectic/`.
4. Run the bench until 100% across all 8 checks.
5. Run `bun run check`.
6. Add worklog `docs/worklog/050-integrate-ode-symplectic.md`.
7. Update README catalog row + `scripts/demo-scope.ts`.
8. `bd close scientist-workbench-4gr` after green.

## Things that will tempt you and which are wrong

- **Implementing 4th-order via embedded RK4 + symplectic projection.**
  Wrong — that's not symplectic. Use Suzuki-Yoshida composition.
- **Adaptive step control.** Wrong — symplectic methods are
  inherently fixed-step. Adaptive symplectic (with reversible step
  control) is a substantial extension; defer.
- **Computing `∂H/∂q` and `∂H/∂p` numerically every step.** Wrong —
  cache the symbolic-differentiated expressions once at setup; eval
  them numerically in the loop.
- **Returning `q_trajectory` and `p_trajectory` interleaved.** Wrong
  — the agent-honest separation is two arrays, since `q` and `p` have
  different physical meaning.
- **Treating non-separable H as a soft failure.** Wrong — Velocity
  Verlet's symplecticity guarantee depends on separability.
  Boundary-tag immediately, do NOT silently produce wrong-quality
  output.

The two principles are the highest-priority decision rule.
