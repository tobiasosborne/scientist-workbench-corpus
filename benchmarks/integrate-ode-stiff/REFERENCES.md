# References

## Primary references for stiff IVP

1. **Hairer, E., & Wanner, G.** (1996). *Solving Ordinary Differential
   Equations II: Stiff and Differential-Algebraic Problems*, 2nd ed.,
   Springer Series in Computational Mathematics 14.
   ISBN 978-3-540-60452-5.
   — **The** reference for stiff ODE methods. §IV.5 (collocation
   methods); §IV.8 (Radau-IIA: 3-stage 5th-order, A-stable, L-stable);
   §IV.10 (implementation); §IV.15 (test problems including HIRES,
   OREGO, ROBER, E5). The bench's algorithmic structure follows this
   chapter.

2. **Hairer, E., & Wanner, G.** (1999). "Stiff differential equations
   solved by Radau methods." *Journal of Computational and Applied
   Mathematics*, 111(1–2), 93–111.
   DOI: [10.1016/S0377-0427(99)00134-X](https://doi.org/10.1016/S0377-0427(99)00134-X)
   — Companion paper. The simplified-Newton iteration with
   complex-eigenvalue transformation: the `s`-stage implicit system
   `(I − h·A ⊗ J) · ΔK = …` factorises as one real `n × n` solve plus
   one complex `2n × 2n` real solve, reducing per-step cost from
   `O((sn)³)` to `O(s · n³)`.

3. **Curtiss, C. F., & Hirschfelder, J. O.** (1952). "Integration of
   stiff equations." *Proceedings of the National Academy of Sciences*,
   38(3), 235–243.
   — The original stiffness paper, naming the phenomenon and
   motivating implicit methods.

## Test-problem corpora

4. **Mazzia, F., & Magherini, C.** (2008). *Test Set for Initial Value
   Problem Solvers, Release 2.4*, University of Bari.
   <https://archimede.uniba.it/~testset/>
   — IVPTESTSET. Stiff catalogue: HIRES, OREGO, ROBER, VDPOL (μ=10⁶),
   E5, MEDAKZO, AKZO, EMEP, BEAM, RING_MODULATOR. Reference solutions
   to ~1e-14 in machine-readable format. The bench draws from these
   under attribution.

5. **Hindmarsh, A. C.** (1983). "ODEPACK, A Systematized Collection
   of ODE Solvers." In: *Scientific Computing*, R. S. Stepleman et al.
   (Eds.), North-Holland, 55–64.
   — The LSODA / VODE reference. Bench cross-references but doesn't
   import LSODA directly.

6. **Robertson, H. H.** (1966). "The solution of a set of reaction
   rate equations." In: *Numerical Analysis: An Introduction*, J.
   Walsh (Ed.), Academic Press, 178–182.
   — The Robertson chemistry problem (3 species, rates spanning
   `10⁰ … 10⁷`). The canonical stiff test problem at long horizon
   (`t = 10¹¹` s).

7. **Schäfer, E.** (1975). "A new approach to explain the 'high
   irradiance responses' of photomorphogenesis on the basis of
   phytochrome." *Journal of Mathematical Biology*, 2, 41–56.
   — HIRES: 8-component photomorphogenesis problem. NHW Vol II
   §IV.10 test problem.

## sci-wb internal references

- `docs/adr/0003-tool-output-error-patterns.md` — three output categories.
- `docs/adr/0014-first-numerical-tier.md` — agent-honest output discipline.
- `docs/adr/0015-determinism-tier.md` — `numerical: true`.
- `docs/adr/0016-warning-based-numerical-scaling.md` — no hard cap.
- `tools/integrate-ode-ivp/tool.ts` — direct precedent (the path-finder
  for the ODE family).
- `tools/linalg-solve/tool.ts` — substrate composition: Radau-IIA's
  Newton iteration uses `linalg-solve` for per-step linear solves.
- `tools/cas-diff/tool.ts` — symbolic Jacobian path: when `f` is in the
  closed vocabulary, `cas-diff` produces the Jacobian directly;
  finite-difference fallback otherwise.
- `packages/ode-core/` — the substrate to extend with `radau.ts`.
- Worklog 048 — `integrate-ode-ivp` ships (the bench protocol this
  dossier inherits).

## Bench protocol references

- `../integrate-ode-ivp/` — immediate precedent for the bench layout,
  HNW-style tolerance discipline, and verifier shape.
- `../linalg-eigh/`, `../linalg-svd/`, `../linalg-qr/` — earlier
  numerical-tier benches.
