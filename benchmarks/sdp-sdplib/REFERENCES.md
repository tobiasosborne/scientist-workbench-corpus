# sdp-sdplib — references

## Primary

- **Borchers, B. (1999).** "SDPLIB 1.2, A Library of Semidefinite
  Programming Test Problems." *Optimization Methods and Software* 11.
  — The canonical SDP test set; reference optima and problem
  taxonomy are from Table 2 of this paper.

- **Yamashita, M., Fujisawa, K., Fukuda, M., Nakata, K., Nakata, M.
  (2003).** "SDPA-sparse format reference." — The `.dat-s` file
  format spec.

## Algorithm

- **Tütüncü, R. H., Toh, K. C., & Todd, M. J. (2003).** "Solving
  semidefinite-quadratic-linear programs using SDPT3." *Mathematical
  Programming Series B* 95(2). — Source of the "1e-6 SDP precision
  floor" that informs this suite's `1e-5` oracle agreement tolerance.

- **Todd, M. J., Toh, K. C., & Tütüncü, R. H. (1998).** "On the
  Nesterov-Todd direction in semidefinite programming." *SIAM J Opt*
  8(3). — The NT direction, which `tools/sdp-solve` defaults to via
  `@workbench/solver-ipm::solveSdpNt`.

- **Wright, S. J. (1997).** *Primal-Dual Interior-Point Methods*.
  SIAM. — Source of the KKT residual conventions (`r_p`, `r_d`,
  `r_c`) used by the verifier.

## Wire format

- **ADR-0030** (workbench repo, `docs/adr/0030-convex-cone-solver-tier.md`)
  — Defines the canonical cone-solver wire (PSDCone with
  strict-Mosek-format √2 off-diagonal scaling).

- **ADR-0032** (workbench repo, `docs/adr/0032-solver-ipm-port.md`)
  — Defines `@workbench/solver-ipm`, the substrate that
  `tools/sdp-solve` wraps.

## Sources

- SDPLIB GitHub mirror: https://github.com/vsdp/SDPLIB
- COPT (Cardinal Optimizer) v8 documentation:
  `/home/tobias/copt80/docs/copt-userguide_en/`
- Mosek 11.x SDP API (Task surface): mosek.com/documentation
