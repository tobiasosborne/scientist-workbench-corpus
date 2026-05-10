# Verifier protocol — `bench/solve`

This document specifies the exact tolerances and check semantics
for `bench/solve/golden/verify.py`. Every check is one of three
flavours: **structural** (boolean / type-shape), **exact**
(rational-arithmetic equality), or **numerical** (with a stated
tolerance). The transcendental lane is the only place numerical
tolerances appear.

Per ADR-0019 §1.

## Top-level dispatch

The verifier reads `{input, candidate, expected, id, tier}` JSON
on stdin, writes `{pass, reason, checks}` JSON on stdout, exits 0
on PASS / 1 on FAIL.

The `expected` envelope carries a `lane` field (`linear`,
`univariate-poly`, `transcendental`, `refusal`) chosen at golden-
generation time by classifying the input via the SymPy reference.
The verifier dispatches by `lane`, NOT by `candidate.kind` — this is
how "lied-about-scope" bugs are caught (a candidate that returns
`kind=ok` for an input whose oracle agreement says it should refuse
fails the lane's `shape` check at the candidate-kind mismatch).

```
expected.lane == "linear"          → linear-lane verifier (5 checks)
expected.lane == "univariate-poly" → univariate-poly-lane verifier (4 checks)
expected.lane == "transcendental"  → transcendental-lane verifier (3 checks)
expected.lane == "refusal"         → refusal-lane verifier (2 checks)
```

A candidate that claims `kind=tagged` when `expected.lane ∈
{linear, univariate-poly, transcendental}` fails immediately with
`shape: lied-about-scope`; symmetrically, `kind=ok` when
`expected.lane=refusal` fails with the same.

## Tolerance regime

| Lane | Tolerance | Tool |
|---|---|---|
| Linear | NONE — exact in ℚ | `fractions.Fraction` |
| Univariate-poly | NONE — `sympy.simplify(f.subs(x, root)) == 0` | SymPy |
| Transcendental — cube | `1e-12 · max(1, |lhs|, |rhs|)` | `sympy.lambdify(modules='numpy')` |
| Transcendental — completeness grid | `1e-6` (root-position) | NumPy sign-change scan |
| Refusal | NONE — exact tag string match | Python `==` |

The `1e-12` cube tolerance is the default workbench-numerical-tier
tolerance (ADR-0014 §"Tolerances"). The `1e-6` grid tolerance is
calibrated as `5σ` of the worst float64-roundoff for sin/cos/exp
on the `[-50, 50]` window — wide enough that branch-position
roundoff doesn't false-fail, narrow enough that "off by π / k" or
"sign-flip" mutations trigger.

## Lane: linear (5 checks)

### `shape` — structural

- `cand.kind == "ok"`.
- `cand.solutions` is a list of length 0 (inconsistent), 1 (unique
  or under-determined). Length > 1 fails (linear systems have
  cardinality 0 or 1 over ℚ; multiple solutions is a univariate-poly-
  lane error).
- `cand.completeness ∈ {"complete", "finite-rep-of-infinite"}`.
- Per binding: `var` is a string, `value` is a parseable
  expression-string in the original `vars ∪ branches`. Branches is
  a list of strings (each a `t_i`-style symbol).
- `cand.warnings` is a list of strings.

### `exact_satisfaction` — exact

For each `Solution` in `solutions`:

1. Substitute the binding values into every input equation.
2. Free branch symbols `t_i` remain symbolic.
3. The resulting expression must `sympy.simplify` to exactly `0`
   (not a numerical near-zero — exact symbolic zero).

For `len(solutions) == 0` (inconsistent): pass iff the input system
is rank-deficient inconsistent (verified independently via
`sympy.Matrix(A).rank() < sympy.Matrix(A.row_join(b)).rank()`).

### `free_var_basis` — exact (under-determined only)

Required only when `cand.solutions[0].branches` is non-empty.

For each branch `t_i`, draw 10 random rationals from the fixed test
set `{-3, -1, 0, 1, 2, Fraction(5,3), Fraction(-7,4), 11, Fraction(-19,4), Fraction(6,7)}`.
For each tuple of choices (one rational per branch), instantiate
each binding's value (now a concrete rational) and verify
`A · x ≡ b` exactly.

PASS iff all `10^{|branches|}`-bounded `min(50, …)` tuples satisfy.

### `rank_consistent` — structural

`len(branches) == n_vars - rank(A)` per Rouché-Capelli.
`rank(A)` computed independently via SymPy.

### `completeness_correct` — structural

`completeness == "complete"` iff
`len(branches) == 0 AND len(solutions) ∈ {0, 1}`.

`completeness == "finite-rep-of-infinite"` iff
`len(branches) > 0`.

## Lane: univariate-poly (4 checks)

### `shape` — structural

- `cand.kind == "ok"`.
- `cand.completeness == "complete"`.
- Each `Solution` has exactly one binding, no branches.
- The binding's `var` matches the single variable in `cand.vars`.
- The binding's `value` parses as a SymPy expression (allowed heads:
  `+ - * / ^ Sqrt` plus the radical roots' `Pow`-with-rational-
  exponent forms; no transcendental heads).

### `each_root_satisfies` — exact

For each `Solution`, substitute its `value` for the variable in the
input polynomial. PASS iff `sympy.simplify(p.subs(x, value))` is
exactly `0`.

### `count_with_multiplicity` — exact

`len(cand.solutions)` equals the sum over the input polynomial's
ℚ-irreducible factor list `[(f_i, e_i)]` of `deg(f_i) · e_i`.
Computed independently via `sp.Poly(p, x, domain='QQ').factor_list()`.

This catches "dropped multiplicity" bugs: `(x − 1)² = 0` must
produce two `Solution` entries each binding `x = 1`. Returning
deduplicated roots fails this check.

### `distinct_roots_match` — exact

The multiset `[simplify(value)]` over `cand.solutions` equals the
multiset of roots SymPy reports via `Poly(p, x).all_roots()`.
Comparison via bipartite-matching: each candidate root must pair
with exactly one SymPy root such that
`simplify(cand_value - sympy_value) == 0`.

For deg ≥ 5 irreducible factors: post-yoc the workbench emits
`Root[poly, k]` solutions when all roots are real and refuses with
`solve/complex-roots-not-yet-named` when any are complex
(alg-num v0.1 limit). The bench's existing G-tier deg-≥5 cases are
mixed-real-complex and route through the refusal lane; the all-real
Root[]-emit path is exercised by `tools/solve`'s per-tool goldens
(the bench's reference oracle would need a Root[] canonical
formatter to mirror the all-real path; tracked separately).

## Lane: transcendental (3 checks)

### `shape` — structural

- `cand.kind == "ok"`.
- `cand.completeness == "finite-rep-of-infinite"` (always for v0.1
  trans cases — the only `complete` trans cases are `exp/log/abs/sinh/
  cosh/tanh = c` with finite roots, but those still emit the v0.1
  branched shape; the bench's transcendental tier filters those out
  to keep the cube semantics applicable).
- Each `Solution` has at least one binding with the variable; may
  have one or more branch parameters listed in `branches`.
- Branch symbols all match the `t_<integer>` pattern.

### `branched_substitution_cube` — numerical (1e-12)

For each `Solution`:

1. Let `n = len(solution.branches)`. The cube is `[-3, 3]^n`,
   `7^n` tuples (1 ≤ n ≤ 3 for v0.1; assertion at golden-generation
   time).
2. For each integer tuple, substitute each branch parameter into
   the binding's value (yielding a concrete numerical expression).
3. Substitute that into the input equation (`lhs - rhs` form).
4. Numerically evaluate via `sympy.lambdify(..., modules='numpy')`.
5. PASS the tuple iff `|value| < 1e-12 · max(1, |lhs|, |rhs|)`
   where `lhs, rhs` are the equation sides evaluated separately.

PASS the case iff every tuple of every solution passes.

### `completeness_grid` — numerical (1e-6)

Detects *missing branches*: emit one branch when two are needed.

1. Sample the equation `f(x) = lhs - rhs` on a 1D grid of 2000
   points uniform on `[-50, 50]`.
2. Identify "grid roots" — points where `f` changes sign between
   adjacent samples (excluding sign changes at NaN/Inf locations).
3. For each grid root `x*`, check that there exists *some* candidate
   solution and *some* integer tuple in the cube `[-3, 3]^n`
   such that `|x* - solution.value(tuple)| < 1e-6`.
4. PASS iff every grid root is matched.

The grid window `[-50, 50]` covers ~16 periods of the v0.1
transcendental heads (`sin/cos` period 2π; covers k ∈ [-7, 7]
inside the cube, matching). The 2000-point density yields ~7
samples per period — enough to trigger sign changes for
non-pathological roots.

Edge case: out-of-domain inputs (e.g., `cos(x) = 5`) produce
candidate solutions with complex values (`arccos(5)` is complex).
The grid scan finds no real roots; the candidate's complex-valued
expression instantiated at any real tuple also has imaginary
component → grid check trivially passes (no real grid roots to
match). This is honest behaviour: the workbench answers the
complex-domain question; the grid catches missed real branches but
not "shouldn't have given a complex answer."

## Lane: refusal (2 checks)

### `tag_matches` — exact

- `cand.kind == "tagged"`.
- `cand.tag == expected.tag` exactly.
- `cand.tag` starts with `"solve/"`.

### `payload_predicate` — structural

- `cand.payload` is a record (object).
- `cand.payload.detail` is a non-empty string.

The loose payload predicate mirrors `linalg-X/non-symmetric-input`
admission (worklog 044 §"Boundary discipline"). Stricter
per-class predicates can be added in a follow-up if specific
fields become load-bearing for downstream tools.

## Aggregate result

```jsonc
{
  "pass":   true,
  "reason": "all invariants hold",
  "checks": {
    "shape":               {"pass": true, "detail": "..."},
    "exact_satisfaction":  {"pass": true, "detail": "..."},
    // ...
  }
}
```

A check that is "n/a for this lane" is reported as
`{"pass": true, "detail": "n/a for lane=<lane>"}` so the per-tier
matrix in the bench harness has uniform shape.

Failure causes the bench harness to print the `reason` field; the
per-check `detail` strings are the diagnostic for triage.

## Reproducibility

All random rational draws (free-variable basis sampling) use
`random.Random(seed=20260507)` deterministically per case
(`random.Random(seed=20260507 + hash(case_id) % 2^31)` for
case-specific stability across repeated runs). The numerical
evaluation in the cube uses NumPy float64 (which is
arch-deterministic for the elementary functions used).
