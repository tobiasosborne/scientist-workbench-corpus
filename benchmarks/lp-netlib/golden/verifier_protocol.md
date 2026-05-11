# lp-netlib verifier protocol

Prose-form definitions of the 10 checks declared in `manifest.toml`.
Implementation lives in `verify.ts`.

## Wire I/O

The verifier reads one JSON object on stdin per case:

```json
{
  "input":     { "minimize": {…}, "subjectTo": {…}, "precision": 1e-8 },
  "candidate": { "status": "optimal", "x": [...], "dual": [...], "slack": [...],
                 "objective": -464.75, "achieved_precision": 1.2e-9,
                 "iterations": 17, "method": "simplex",
                 "condition_estimate": 3.2e4, "warnings": [] },
  "expected":  { "status": "optimal", "objective": -464.7531428573,
                 "consensus": { "objective_gurobi": …, "objective_mosek": …,
                                "agreement": true, "agreement_tol": 1e-8 } },
  "id":        "afiro"
}
```

And writes one JSON object on stdout:

```json
{
  "pass":   true,
  "reason": "all checks pass",
  "checks": {
    "shape":                   { "pass": true, "detail": "..." },
    "finite_entries":          { "pass": true, "detail": "..." },
    "status_consistency":      { "pass": true, "detail": "..." },
    "primal_feasibility":      { "pass": true, "detail": "r_p = 3.2e-12" },
    "primal_nonneg":           { "pass": true, "detail": "min(x) = 0.0" },
    "dual_feasibility":        { "pass": true, "detail": "r_d = 5.1e-13" },
    "complementary_slackness": { "pass": true, "detail": "r_c = 1.8e-11" },
    "optimality_gap":          { "pass": true, "detail": "gap = 2.3e-13" },
    "oracle_agreement":        { "pass": true, "detail": "diff = 4.0e-13" },
    "self_reported_precision": { "pass": true, "detail": "claimed 1.2e-9 ≥ recomputed 1.8e-11" }
  }
}
```

Exit code 0 always (the verifier never errors out on a "bad" case;
it returns `pass: false` and surfaces the failure as data).

## Check definitions

### 1. `shape`

Candidate is either a success record with exactly these keys —
`{status, x, dual, slack, objective, achieved_precision, iterations,
method, condition_estimate, warnings}` — or a tagged envelope
`{kind: "tagged", tag, payload}` whose `tag` starts with
`"cone-solve/"` or `"lp-solve/"` and whose payload is a JSON object.
`x`, `dual`, `slack` are lists of numbers; `status` is a known
string; `objective`, `achieved_precision`, `condition_estimate` are
numbers; `iterations` is a non-negative integer; `method`,
`warnings` are string-typed.

Pass / fail. No tolerance.

### 2. `finite_entries`

Every number in the success record is finite (no NaN, no ±Inf). A
tagged-refusal case skips this check (its payload may legitimately
carry `Infinity` for unbounded objective certificates).

### 3. `status_consistency`

`candidate.status` is in the known taxonomy
`{optimal, infeasible, unbounded, iter-cap, numerical-breakdown}` or
the candidate is a tagged envelope. When a tagged envelope is
returned, the case's `expected.status` must be one of `{infeasible,
unbounded, numerical-breakdown}` — i.e. tagged envelopes are
acceptable on a known-pathological case but not on a known-optimal
one.

When `candidate.status === "optimal"` we require
`expected.status === "optimal"` (and vice versa). When the candidate
returns `infeasible` or `unbounded`, the expected status must match
(no false positives on infeasibility certification).

### 4. `primal_feasibility`

For optimal-status cases. With `r_p = ‖A·x − b‖_∞`:

    r_p ≤ 1e-8 · max(1, ‖b‖_∞)

Detail field reports `r_p` and the tolerance bound.

### 5. `primal_nonneg`

For each `NonNegCone` in `input.subjectTo.cones`, for each `i` in its
`indices`:

    x[i] ≥ −1e-8

The `−ε` allowance absorbs round-off; truly negative entries
(violations beyond round-off) indicate a contract bug. Future
cone types (`SOCone`, `PSDCone`, …) get their own membership checks;
NETLIB-LP only exercises `NonNegCone`.

### 6. `dual_feasibility`

For optimal-status cases. With `r_d = ‖Aᵀ·y + s − c‖_∞`:

    r_d ≤ 1e-8 · max(1, ‖c‖_∞)
    s[i] ≥ −1e-8  for every i in any NonNegCone slice

Detail field reports `r_d` and the `min(s)` value.

### 7. `complementary_slackness`

For optimal-status cases. With `r_c = |xᵀ·s|`:

    r_c ≤ 1e-8 · max(1, |cᵀ·x|)

(For LP this is equivalent to `x[i]·s[i] = 0` for each i, since both
are non-negative. The aggregate inner-product form is more robust
to round-off and is what Wright 1997 §2.4 defines.)

### 8. `optimality_gap`

For optimal-status cases:

    | cᵀ·x − bᵀ·y | ≤ 1e-8 · max(1, |cᵀ·x|)

This is the duality gap derived from the candidate's *own* primal
and dual. It is the strongest single-number self-consistency check.

### 9. `oracle_agreement`

Only when `expected.consensus.agreement === true`:

    | candidate.objective − expected.objective | ≤ 1e-8 · max(1, |expected.objective|)

When `expected.consensus.agreement === false` (Gurobi and Mosek
disagreed at generation time), this check passes trivially with
detail `oracle_disagreement_at_generation; dropped from gating`. The
case still gets the other 9 checks.

### 10. `self_reported_precision`

For optimal-status cases:

    candidate.achieved_precision ≥ max(r_p, r_d, r_c)

The candidate is allowed to over-claim its own residual (be more
honest than tight) but never to under-claim. This catches the
silent-lie failure mode that CLAUDE.md Rule 8 forbids.

## When the candidate is a tagged refusal

Six of the 10 checks (`shape`, `status_consistency`,
`oracle_agreement`, and the three KKT-residual checks) are
re-purposed for refusal cases:

- `shape` becomes: tag string is in the known refusal set
  (`cone-solve/precision-unreachable`, `cone-solve/non-finite-input`,
  `cone-solve/degenerate-shape`, `cone-solve/malformed-cone`,
  `lp-solve/...` analogous); payload is a JSON object.
- `status_consistency` accepts the tagged refusal iff the expected
  status is in the pathological set.
- The three KKT-residual checks, `finite_entries`, `oracle_agreement`,
  `self_reported_precision` are skipped (pass-by-N/A with detail
  `tagged refusal — KKT inapplicable`).
- `primal_nonneg` is skipped.

A tagged refusal on a known-optimal case is a hard fail.

## Notation

`‖·‖_∞` is the max-absolute-value (infinity / Chebyshev) norm.
`ε` (machine epsilon) is 2.220446049250313e-16.
The `1e-8` relative tolerance throughout matches the ADR-0030
default `precision`; candidates that pass with a tighter `precision`
flag should still meet 1e-8 (the suite does not test
precision-tightening — that's the job of `tools/lp-solve`'s `--precision`
range testing).
