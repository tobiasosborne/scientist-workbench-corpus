# lp-netlib — agent prompt

You are grading a candidate linear-programming solver against the
NETLIB LP collection. The candidate consumes one canonical-form LP
on stdin (`{minimize: {c}, subjectTo: {Ax_eq_b: {A, b}, cones},
precision}`) and produces a solution record on stdout. See
`DESCRIPTION.md` for the full wire schema.

## What "wonderful" looks like

- All 114 cases pass all 10 checks against the candidate.
- Where Gurobi and Mosek disagree (multiple-optimum problems), the
  candidate's objective lies inside the consensus interval and the
  case is correctly flagged `oracle_disagreement` rather than failing.
- `achieved_precision` is honest: never smaller than the recomputed
  residual.

## What to do if a check fails

1. Read the case `id` and look up the corresponding `meta` block in
   `inputs.json`. It carries the original NETLIB filename, dimensions,
   and the `var_map` / `slack_intro` / `free_split` records from the
   general-form-to-canonical reduction.
2. Read the candidate's `warnings` field. A well-behaved candidate
   surfaces near-singularity or termination-at-iter-cap there.
3. Compare against Gurobi's primal/dual on the same case: run
   `bun src/cli.ts grade gurobi lp-netlib --case-id=<id>`.
4. If multiple oracles disagree on the case, the case has multiple
   optima or ill-conditioned dual — file the finding in
   `failing-invariants.sql` output rather than treating it as a
   candidate bug.
