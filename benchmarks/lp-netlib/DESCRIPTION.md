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

The NETLIB LP collection — public domain since 1985, the canonical LP
test set for four decades. Original source: `netlib.org/lp/`. The
collection contains 114 problems; this suite ships the **21-problem
small subset** (cases with canonical-form `n_canonical × m_canonical
≤ 100,000` entries) and downloads all 114 to `data/lp-netlib/raw/`
for local re-ingestion when the wire format extends to sparse in v0.2
(ADR-0030 §"Open questions #5").

### Why the 100k entries gate

The canonical wire format encodes A as dense `list<list<float64>>` JSON
— each number takes ~16 ASCII chars including separators, so a 200K-entry
matrix is ~3 MB JSON, and the largest NETLIB problems (pds-20: 9.8B
entries; ken-18: 80B entries) are physically impossible to encode this
way.  The 100k gate keeps `inputs.json` under 20 MB committable to git
while preserving every classical-tractable NETLIB problem.  Cases
above the gate are *skipped entirely* — they are not stored sparsely
because the wire format does not (yet) support sparse.

### The 21 problems shipped

Famous tractable NETLIB classics: `afiro`, `adlittle`, `sc50a`, `sc50b`,
`sc105`, `sc205`, `scsd1`, `share1b`, `share2b`, `blend`, `beaconfd`,
`israel`, `lotfi`, `recipe`, `brandy`, `bore3d`, `boeing2`, `forplan`,
`kb2`, `scagr7`, `stocfor1`.  Sizes range from `afiro` (n_canonical=51,
m_canonical=27) to `forplan` (n_canonical=514, m_canonical=183).

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

## Case taxonomy (21 problems in v0.1; 114 NETLIB ultimately)

The 21 v0.1 problems are the small-subset slice of NETLIB
(canonical-form `n*m ≤ 100,000`). Per-problem dimensions recorded
in `meta`:

- **Tiny (n_can ≤ 100):** afiro (51×27), sc50a (78×50), sc50b (78×50),
  kb2 (77×52) — 4 problems.
- **Small (100 < n_can ≤ 300):** adlittle (138×56), sc105 (163×105),
  share2b (162×96), share1b (253×117), stocfor1 (165×117), scagr7
  (185×129), beaconfd (295×173), blend (114×74), recipe (299×186) —
  9 problems.
- **Medium (300 < n_can ≤ 800):** sc205 (317×205), israel (316×174),
  lotfi (366×153), bore3d (346×245), brandy (303×220), boeing2 (378×239),
  forplan (514×183), scsd1 (760×77) — 8 problems.

The grading gate from the epic (`eg9j`) was originally
`cone-solve ≥ 98/114` / `lp-solve ≥ 110/114` against the full
collection.  For the v0.1 small-subset suite, **the gate is reframed**:
`cone-solve` and `lp-solve` must hit 21/21 on `lp-netlib` v0.1.  The
full battery becomes a v0.2 gate once the sparse wire format lands
and the remaining 88 NETLIB problems join this suite.

## Boundary tags

The boundary-tag refusal envelope (`tagged "cone-solve/precision-
unreachable"` etc.) is exercised by `lp-small`'s pathological cases
(infeasible, unbounded, malformed). NETLIB problems are all
well-posed; this suite does not directly test boundary tags except
incidentally where a candidate's algorithm hits its numerical ceiling.
