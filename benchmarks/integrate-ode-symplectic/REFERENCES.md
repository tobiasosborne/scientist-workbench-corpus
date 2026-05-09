# References

## Primary references for symplectic integration

1. **Hairer, E., Lubich, C., & Wanner, G.** (2006). *Geometric
   Numerical Integration: Structure-Preserving Algorithms for
   Ordinary Differential Equations*, 2nd ed., Springer Series in
   Computational Mathematics 31. ISBN 978-3-540-30663-4.
   — **The** reference for symplectic integration. §I.3.1 (Verlet);
   §VI.3 (composition methods, Yoshida); §VI.6 (backward error
   analysis: why energy drift is bounded `O(h^p)` regardless of
   horizon for symplectic methods); §IV.1 (Kepler 2-body as the
   canonical test). Bench's structure follows this book.

2. **Verlet, L.** (1967). "Computer experiments on classical fluids.
   I. Thermodynamical properties of Lennard-Jones molecules."
   *Physical Review*, 159(1), 98–103.
   DOI: [10.1103/PhysRev.159.98](https://doi.org/10.1103/PhysRev.159.98)
   — The original Verlet method. Ubiquitous in molecular dynamics.

3. **Yoshida, H.** (1990). "Construction of higher order symplectic
   integrators." *Physics Letters A*, 150(5–7), 262–268.
   DOI: [10.1016/0375-9601(90)90092-3](https://doi.org/10.1016/0375-9601(90)90092-3)
   — Suzuki-Yoshida 4th-order composition: three Verlet steps with
   `w₁ = 1/(2 − 2^(1/3))`, `w₂ = -2^(1/3)/(2 − 2^(1/3))`,
   `w₃ = w₁`. The 4th-order coefficient table the bench uses.

4. **Suzuki, M.** (1991). "General theory of fractal path integrals
   with applications to many-body theories and statistical physics."
   *Journal of Mathematical Physics*, 32, 400–407.
   — Companion to Yoshida 1990 on composition methods.

## Test-problem corpora

5. **Sussman, G. J., & Wisdom, J.** (1992). "Chaotic evolution of
   the solar system." *Science*, 257(5066), 56–62.
   — Long-time integration of the outer solar system. The
   discriminator test: symplectic methods over `~10⁹` years preserve
   energy bounded; non-symplectic drift unbounded.

6. **Hénon, M., & Heiles, C.** (1964). "The applicability of the
   third integral of motion: Some numerical experiments." *The
   Astronomical Journal*, 69, 73–79.
   — The Hénon-Heiles potential, a canonical chaotic Hamiltonian
   system. Energy conservation is the hallmark.

## sci-wb internal references

- `docs/adr/0003-tool-output-error-patterns.md` — three output categories.
- `docs/adr/0014-first-numerical-tier.md` — agent-honest output discipline.
- `docs/adr/0015-determinism-tier.md` — `numerical: true`.
- `docs/adr/0016-warning-based-numerical-scaling.md` — no hard cap.
- `tools/integrate-ode-ivp/tool.ts` — direct precedent (the
  path-finder). Symplectic borrows the bench shape.
- `tools/cas-diff/tool.ts` — used in-process to differentiate `H(q,p)`
  symbolically. Without this, Velocity Verlet would need users to
  pre-compute partial derivatives.
- `packages/ode-core/` — the substrate to extend with `verlet.ts` /
  `yoshida.ts`.
- Worklog 048 — `integrate-ode-ivp` ships (the bench protocol this
  inherits).

## Bench protocol references

- `../integrate-ode-ivp/`, `../integrate-ode-stiff/` — immediate
  precedents.
