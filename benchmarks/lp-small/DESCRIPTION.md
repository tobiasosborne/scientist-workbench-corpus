# lp-small — Small LPs for the resource-limited regime

Phase 0 corpus bench companion to `lp-netlib/`. Same canonical SCS
wire format (ADR-0030 §C). Same dual-witness oracles. Different
problem distribution: small (n ≤ 100), pathological, parametric.

## Why this suite exists separately from lp-netlib

The convex-cone solver tier (epic `scientist-workbench-eg9j`) is
forced by the D_W1 dogfood gap into a substrate decision: pure-TS,
phone-deployable, no FFI to OpenBLAS, no WASM-wrap of SCS. The
target regime is *resource-limited* problems — quantum-information
SDPs, small LP relaxations of combinatorial problems, parameter-
fitting via convex regression. Most of these fit in n ≤ 100.

NETLIB skews larger. Of its 114 problems, only ~25 have `n ≤ 100`.
Grading exclusively against NETLIB would optimise for problems the
target use case rarely sees and silently under-weight the
pathologies that small LPs surface uniquely.

`lp-small` is hand-curated to cover:

- The **size regime** that matters (n ∈ [3, 100]).
- The **algorithmic pathologies** that distinguish simplex from
  interior-point from ADMM (Klee-Minty cycling, Beale degeneracy,
  multiple optima).
- The **status taxonomy edges** (infeasible, unbounded,
  numerical-breakdown) that NETLIB does not exercise.
- The **boundary-tag refusal envelope** (`cone-solve/malformed-cone`,
  `cone-solve/degenerate-shape`, etc.) that NETLIB skips entirely.

The two suites are complementary: NETLIB is the canonical comprehensiveness
test; `lp-small` is the resource-regime stress test.

## Wire format

Identical to `lp-netlib`. Each case is a canonical SCS-form LP:

```json
{
  "id": "rand_dense_25x10",
  "input": {
    "minimize": { "c": [...] },
    "subjectTo": {
      "Ax_eq_b": { "A": [[...], ...], "b": [...] },
      "cones": [{ "head": "NonNegCone", "indices": [0, 1, ..., 24] }]
    },
    "precision": 1e-8
  },
  "meta": {
    "family":     "A_random_dense",
    "generator":  { "kind": "random_dense", "seed": 20260511, "n": 25, "m": 10 },
    "expected_status": "optimal"
  }
}
```

The `meta.family` field bins cases for per-family dashboards.
`meta.generator` captures everything needed to regenerate the case
bit-identically — `rng_seed`, `dimensions`, `parameters`. The
suite's `golden/generate.py` is reproducible from `meta.generator`
alone; the generated outputs are pinned by sha256.

## Case taxonomy (~40 problems)

Eight families. Counts are the v0.1 floor; the suite expands
additively as new pathologies surface in real candidate-debugging
sessions.

### Family A — Random dense, well-conditioned (16 problems)

Seeded `random_dense`, sizes (m, n) ∈ {10, 25, 50, 100}², with the
m ≤ n constraint (more variables than equality constraints → bounded
non-empty feasible polytope generically). Eigenvalue spread of `Aᵀ A`
bounded in [1, 10] by construction. The "easy baseline" family —
any reasonable solver passes all of these.

### Family B — Klee-Minty cubes (4 problems)

The Klee-Minty 1972 cube: a parametric LP family whose simplex
method visits all `2ⁿ` vertices under standard Dantzig pivot rule.
Interior-point methods solve it in O(√n) iterations. Surfaces the
algorithm-dispatch decision: pure simplex degrades exponentially; a
solver dispatching to IPM at size ≥ 5 should win cleanly.

    minimise  − Σ 10^(n−i) · x_i
    subject to  x_1 ≤ 1
                2·Σ_{j<i} 10^(i−j) · x_j + x_i ≤ 10^i  for i = 2..n
                x ≥ 0

Sizes n ∈ {3, 5, 8, 10}.

### Family C — Beale's 1955 cycling LP (1 problem)

Beale's textbook example exhibits Dantzig-rule cycling: simplex
revisits the same basis without changing the objective. A solver
using Bland's rule or lexicographic perturbation should not cycle.
Surfaces the anti-cycling discipline.

    minimise  − (3/4)·x₁ + 150·x₂ − (1/50)·x₃ + 6·x₄
    subject to  (1/4)·x₁ − 60·x₂ − (1/25)·x₃ + 9·x₄ ≤ 0
                (1/2)·x₁ − 90·x₂ − (1/50)·x₃ + 3·x₄ ≤ 0
                x₃ ≤ 1
                x ≥ 0

### Family D — Transportation & assignment (4 problems)

Bipartite-network LPs. Totally unimodular constraint matrix ⟹
integer optima out of the LP relaxation. Tests primal feasibility
in the totally-unimodular regime where vertex-basis solvers should
return exact-integer x components.

- 3×4 transportation (12 vars, 7 constraints)
- 5×8 transportation (40 vars, 13 constraints)
- 4-job assignment (16 vars, 8 constraints)
- 6-job assignment (36 vars, 12 constraints)

### Family E — Degenerate / multiple optima (3 problems)

Problems where the optimal face has dimension ≥ 1. Two valid
solvers return different vertices. The `oracle_agreement` check
must accept any candidate objective inside the consensus interval;
the verifier is correct iff a candidate returning *either* vertex
passes. Surfaces the multi-optimum tolerance pattern.

### Family F — Near-infeasible (2 problems)

Constraints that are *exactly* infeasible but parameterised by an
ε that approaches 0 from the feasible side. Tests the
infeasibility-certificate path: a Farkas certificate `y` with
`Aᵀ y ≤ 0`, `bᵀ y > 0` proves infeasibility. Solvers that "decide
infeasibility numerically" without producing a certificate fail
the certificate-shape sub-check.

### Family G — Unbounded (2 problems)

Problems with a primal-unbounded direction. The candidate must
return `status: "unbounded"` AND an unbounded-direction certificate
`d ≥ 0, A·d = 0, cᵀ d < 0`. Solvers that return a happy
`status: "optimal"` with `objective: −∞` fail.

### Family H — Boundary tags (3 problems)

Malformed inputs that exercise the tagged-refusal envelope:

- `H_empty_problem` — `c = []`, `A = []`, `b = []`. The empty LP.
  Trivially optimal with `objective = 0`. (Not a boundary tag —
  this is a structural edge inside the success path. Listed here
  because it's the smallest possible input.)
- `H_malformed_cone` — `cones` references an index out of range.
  Expected: `tagged "cone-solve/malformed-cone"`.
- `H_non_finite_input` — `A` contains a NaN entry. Expected:
  `tagged "cone-solve/non-finite-input"`.

## Oracle pattern

Identical to `lp-netlib`. Dual-witness Gurobi + Mosek; consensus
objective + agreement flag pinned in `expected.json`; live re-running
on every grade invocation. See `lp-netlib/DESCRIPTION.md` §"Oracle
pattern" for the full description.

## Reproducibility

The entire suite regenerates from the seed `20260511` (locked in
`golden/generate.py`). Re-running `python3 golden/generate.py` is
bit-identical given (Gurobi version, Mosek version, platform
fingerprint). If a Gurobi-version change shifts an objective beyond
1e-12, the consensus snapshot legitimately updates and the
sha256-pinned golden is regenerated with a `regenerated_at` bump —
this is the documented version-drift surfacing channel.
