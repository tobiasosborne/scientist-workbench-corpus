# `integrate-ode-symplectic` — design notes

Companion to `PROMPT.md`. Defends the design.

## Why symplectic integration

For Hamiltonian systems `dq/dt = ∂H/∂p, dp/dt = −∂H/∂q`, **symplectic
integrators preserve the symplectic 2-form** `ω = dp ∧ dq`. The
practical consequence (Hairer-Lubich-Wanner §VI.6 backward error
analysis): energy drift is **bounded `O(h^p)` regardless of integration
horizon**, where `p` is the integrator's order. Compare to a non-
symplectic method (RKF45 in `integrate-ode-ivp`) whose energy error
grows linearly: `O(t · h^p)`.

The discriminator: Kepler 2-body over `10⁴` orbits.
- **Verlet** (`p = 2`): bounded drift `~10⁻⁵`.
- **Yoshida-4** (`p = 4`): bounded drift `~10⁻¹²`.
- **DOPRI5(4)** (non-symplectic): drift `~10⁻²` and growing.

For long-time orbital mechanics, MD simulations of NVE ensembles, beam
dynamics, plasma PIC — symplectic is **mandatory**.

## Why Velocity Verlet (the path-finder)

Three textbook options for separable Hamiltonian systems:

1. **Velocity Verlet** (Verlet 1967; HLW §I.3.1). 2nd-order, explicit,
   one force evaluation per step. The MD workhorse. Trivial to
   implement: `v_{n+½} = v_n + (h/2) · F(q_n)`,
   `q_{n+1} = q_n + h · v_{n+½}`,
   `v_{n+1} = v_{n+½} + (h/2) · F(q_{n+1})`.

2. **Leapfrog** (kick-drift-kick, equivalent to Verlet up to half-step
   shift). Same algorithm, different bookkeeping convention.

3. **Yoshida-4** (Yoshida 1990). 4th-order via composition of three
   Verlet steps with Suzuki-Yoshida coefficients. Three force
   evaluations per step. Order-of-magnitude better drift constant.

For the path-finder, **Velocity Verlet wins on simplicity** (~50 lines
in pure TS), with **Yoshida-4 as next tier** for 4th-order
applications. Both ship in `integrate-ode-symplectic` v0.1; the
`options.scheme` flag selects.

Constraint: Velocity Verlet (and Yoshida-4 by composition) requires
**separable Hamiltonian** `H(q, p) = T(p) + V(q)` (kinetic depends only
on `p`, potential only on `q`). Non-separable Hs need implicit
symplectic methods (Gauss-Legendre, Radau-IIA on the augmented system)
which are a substantially larger build. Non-separable input →
`tagged "integrate-ode-symplectic/non-separable-hamiltonian"`.

## Why the agent-honest output

The headline diagnostic is **energy drift**:

```
{
  q_trajectory:      list<list<float64>>,   # (n_steps+1 × len(q_vars))
  p_trajectory:      list<list<float64>>,
  t_values:          list<float64>,
  energy:            list<float64>,         # H(q_i, p_i) at each step
  energy_drift_max:  float64,               # max |H_i − H_0| / max(|H_0|, atol)
  energy_drift_secular: boolean,            # does drift grow ≥ linearly with t? (=> non-symplectic)
  n_evals:           integer,
  n_steps:           integer,
  converged:         boolean,
  status:            string,
  method:            string,                 # 'velocity-verlet' | 'yoshida-4'
  warnings:          list<string>
}
```

A planner reading this output decides:

- "`energy_drift_max < 1e-5`, `energy_drift_secular: false` — bounded
  drift, integration is symplectic-quality, trust over long horizon."
- "`energy_drift_secular: true` — drift growing linearly, switch
  scheme or revisit the H you handed me."
- "`energy_drift_max ≈ 1` — catastrophic drift, system probably
  non-separable; check the boundary tag I should have emitted."

`energy_drift_secular` is the load-bearing structural diagnostic: **a
correct symplectic integrator never sets it true on a separable H**.

## Why the test set tiers

### A. Shape edges (3 cases)
Trivial Hamiltonian: 1-DOF harmonic `H = (q² + p²)/2`. Bookkeeping
cases at `n_steps ∈ {1, 10, 100}`.

### B. Canonical Hamiltonian systems (5 cases)
Reference: hand-coded Verlet at small `h` plus analytic conserved
quantities.
- B_harmonic_1d: 1-DOF harmonic, exact periodic. Energy bounded ~1e-12.
- B_pendulum_small: small-angle pendulum, near-harmonic, energy bounded.
- B_pendulum_large: large-angle pendulum, full nonlinearity, near
  separatrix.
- B_kepler_e0_1period: Kepler 2-body circular, 1 period, exact orbit.
- B_double_pendulum: 4-DOF chaotic, energy conserved.

### C. Long-time conservation (3 cases — the discriminator)
The cases where Verlet/Yoshida shine.
- C_kepler_e05_100periods: Kepler at e=0.5 over 100 periods.
  `energy_drift_max ≤ 1e-3` for Verlet at h=T/100.
- C_kepler_e05_10000periods: 10⁴ periods. Bounded, NOT secular.
- C_henon_heiles_long: Hénon-Heiles at chaotic energy `E=1/8`,
  bounded.

### D. Comparison vs non-symplectic (1 case)
Same Kepler problem solved by `integrate-ode-ivp` (DOPRI5) at
comparable `h` would show secular drift. The bench encodes this as a
*structural* check on `energy_drift_secular: false` for the symplectic
candidate.

### E. Boundary cases (4 cases)
- E_degenerate_tspan: `t0 == tf` or `n_steps == 0`.
- E_non_separable: `H = (q + p)² / 2` (mixes q and p).
- E_dim_mismatch: `len(q_vars) != len(q0)`.
- E_unknown_head: `H` contains an unsupported function.

### F. Order check (2 cases)
Empirical convergence: same problem at `h, h/2, h/4` — energy drift
must scale as `h^p` for the integrator's order `p`.

Total: ~18 cases × 8-10 checks ≈ 160 invariant assertions.

## Why these tolerances

For a symplectic integrator of order `p` on a smooth separable
Hamiltonian, backward error analysis (HLW §VI.6 Theorem 6.1) gives:

  `|H(q_i, p_i) − H(q_0, p_0)| ≤ C · h^p`

for a problem-dependent constant `C` of `O(1)` for stable Hs over
exponentially-long times (`t ≤ exp(c/h)`). Practical: at `h = T/100`
for an oscillation of period `T`, Verlet gives drift `~10⁻⁴`, Yoshida-4
gives `~10⁻⁸`.

The verifier uses:

- **`tol_drift = 100 · h^p · drift_constant`** where `drift_constant`
  is per-case (default 1.0; per-problem overrides allowed for cases
  with unusual Hamiltonian curvature).
- **`tol_secular`**: drift series must NOT exhibit linear growth past
  ~5× the bounded oscillation magnitude. Test: fit `drift(t) ≈ A·t +
  B` and check `|A| · tf < 5 · tol_drift`. A correct symplectic
  integrator has `|A|·tf << B` (oscillatory, not secular).

The 100× safety factor is calibrated for canonical separable problems.
