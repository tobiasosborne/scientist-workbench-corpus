# lp-netlib — Linear Programming on the NETLIB collection

Phase 0 corpus bench for the convex-cone solver tier (epic
`scientist-workbench-eg9j`, ADR-0030 §F). Sibling: `lp-small/` covers
the 10–100 var resource-limited regime; this suite covers the canonical
LP test set across all sizes.

## What this suite grades

The candidate solver receives a linear program in **canonical SCS
form** (the form ADR-0030 §C specifies for `tools/cone-solve` and
`tools/lp-solve`):

```
minimise    cᵀx
subject to  A x = b
            x ∈ K = NonNegCone[0..n-1]    (this suite — pure LP)
```

and returns the optimal `x`, dual `y`, slack `s`, objective, and a
status from the five-class termination taxonomy
(`optimal | infeasible | unbounded | iter-cap | numerical-breakdown`),
plus a `tagged "<tool>/<class>"` boundary envelope for refusals.

For LP, `K` is always a single `NonNegCone` over every variable index.
The interesting variation is in `A`, `b`, `c`, and problem
pathology — not in the cone structure. (Mixed cones land in
`benchmarks/lp-small` and the future `benchmarks/mixed-conic` only.)

## Source

The NETLIB LP collection — 114 problems, public domain since 1985,
the canonical LP test set for four decades. Original source:
`netlib.org/lp/`. Distributed as MPS-format files; this suite ingests
them once via Gurobi's MPS reader and stores them in canonical
workbench wire form under `golden/inputs.json`.

Per-problem provenance (original NETLIB filename, SHA-256 of the
original MPS bytes, conversion notes) lives in the per-case `meta`
field alongside `input`. See `golden/verifier_protocol.md` for the
input record schema and `REFERENCES.md` for the upstream pointers.

## Wire format

Each case carries:

```json
{
  "id": "afiro",
  "input": {
    "minimize": { "c": [0.4, 0.0, …] },
    "subjectTo": {
      "Ax_eq_b": {
        "A": [[1.0, 0.0, …], …],
        "b": [-0.4, 0.0, …]
      },
      "cones": [
        { "head": "NonNegCone", "indices": [0, 1, …, 31] }
      ]
    },
    "precision": 1e-8
  },
  "meta": {
    "source":    "netlib.org/lp/data/afiro",
    "sha256":    "<64 hex of original MPS bytes>",
    "n_vars":    32,
    "n_eq":      27,
    "var_map":   { "orig_var_name": wire_index, … },
    "slack_intro": [{ "row": "R09", "sign": "<=", "slack_index": 28 }, …],
    "free_split":  [{ "orig_var": "X37", "plus_index": 12, "minus_index": 13 }, …]
  }
}
```

The `meta` field is bench-only metadata — the candidate solver never
sees it. Its role is provenance and post-hoc analysis (e.g.
distinguishing slack-induced degeneracy from problem-induced degeneracy
when an oracle disagreement surfaces).

The expected record carries the dual-witness consensus:

```json
{
  "id": "afiro",
  "expected": {
    "status":      "optimal",
    "objective":   -464.7531428573,
    "consensus": {
      "objective_gurobi": -464.7531428573012,
      "objective_mosek":  -464.7531428573008,
      "agreement":        true,
      "agreement_tol":    1e-8
    }
  }
}
```

Primal/dual values (`x`, `y`, `s`) are **not** pinned in
`expected.json` — for LP they are generically non-unique
(multiple-optimum problems, degenerate vertices). What is pinned is
the *objective value*, which is unique whenever the problem has a
finite optimum.

The verifier reconstructs feasibility and gap residuals from the
candidate's own `(x, y, s)`. Cross-validation against the oracle
happens through the objective and the status alone, with KKT
residuals checked locally.

## NETLIB → canonical SCS reduction

NETLIB MPS files express problems in **general form**:

```
minimise    cᵀ x
subject to  L_eq ≤ A_eq x ≤ U_eq      (mixed equality / ≤ / ≥ / range rows)
            lo ≤ x ≤ hi               (mixed free / bounded variables)
```

The golden generator reduces each problem to canonical SCS form via
this **explicit** transcription:

1. **Inequality rows.** `aᵀx ≤ b` becomes `aᵀx + s = b, s ≥ 0`
   (slack column appended to the variable vector). `aᵀx ≥ b` becomes
   `aᵀx − s = b, s ≥ 0`. Range rows `L ≤ aᵀx ≤ U` introduce two
   slacks.
2. **Variable bounds.** `lo ≤ x ≤ hi` with `lo > -∞` shifts:
   `x' = x − lo`, `0 ≤ x' ≤ hi − lo`, and a finite upper bound
   introduces a slack: `x' + t = hi − lo, t ≥ 0`.
3. **Free variables** (`-∞ ≤ x ≤ +∞`) split into `x = x⁺ − x⁻` with
   both `x⁺, x⁻ ≥ 0`.

The `var_map`, `slack_intro`, and `free_split` fields in `meta` record
the transcription so a downstream consumer can invert the candidate's
`x` back to original NETLIB variable names.

This is the textbook reduction (Vanderbei, *Linear Programming* 4th
ed., §2.5–2.7). It is done explicitly in code we own
(`golden/wire.py`) — not via SciPy's internal `_presolve`, which is
non-public API and version-locked. Auditability beats convenience.

## Oracle pattern

Dual-witness for v0.1: Gurobi + Mosek. COPT lands additively as a
third adapter when installed; the consensus check generalises from
2-of-2 to 2-of-3 without schema change.

Per case, the generator:

1. Loads the original MPS via `gurobipy.read()`.
2. Extracts general-form components (`c, A, b_eq, A_ub, b_ub, bounds`)
   from the Gurobi model.
3. Applies the reduction above, emits canonical-form `(c, A, b)` plus
   the cone declaration plus the `meta` record.
4. Runs **both** Gurobi and Mosek on the *canonical* problem.
5. Records both objectives; flags agreement (`|obj_g − obj_m| ≤ 1e-8 ·
   max(1, |obj_g|)`); writes consensus into `expected.json`.

The oracles run *again* on every grade invocation — not just at
generation time. This is the load-bearing point of the triple-witness
design: re-running surfaces version drift, platform variance, and
multiple-optimum oracle disagreement that wouldn't show at a single
generation snapshot. The cost is per-grade-run latency, acceptable
for a Phase 0 infrastructure piece. See `adapters/gurobi/lp-netlib.toml`
and `adapters/mosek/lp-netlib.toml`.

## Case taxonomy (114 problems)

The NETLIB collection is the canonical set, taken whole. Per-problem
dimensions are recorded in `meta` and surfaced via the build-time
DuckDB view. Indicative slice:

- **Tiny (n ≤ 50):** afiro, sc50a, sc50b — 3 problems.
- **Small (50 < n ≤ 500):** sc105, share2b, share1b, beaconfd, blend,
  recipe, bore3d, adlittle, … — ~20 problems.
- **Medium (500 < n ≤ 5000):** brandy, e226, finnis, israel, sctap1,
  scsd1, scsd6, … — ~50 problems.
- **Large (n > 5000):** fit2p, 80bau3b, dfl001 (if included), … —
  ~40 problems.

The grading gate from the epic (`eg9j`) is binary on the suite as a
whole: `cone-solve ≥ 98/114`, `lp-solve ≥ 110/114`. Size buckets are
informational and let the grading dashboard show where a candidate's
failures concentrate.

## Boundary tags

The boundary-tag refusal envelope (`tagged "cone-solve/precision-
unreachable"` etc.) is exercised by `lp-small`'s pathological cases
(infeasible, unbounded, malformed). NETLIB problems are all
well-posed; this suite does not directly test boundary tags except
incidentally where a candidate's algorithm hits its numerical ceiling.
