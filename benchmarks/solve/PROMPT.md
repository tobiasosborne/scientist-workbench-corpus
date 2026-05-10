# Bench — `solve` (the headline solve-suite-v1 bench)

## ⚠ How you will be graded

You will be graded on **mathematical-invariant correctness modulo
representation**. Two answers that satisfy the same invariant set are
admitted as equal — `{x → (1+√5)/2}` and `{x → Root[x²−x−1, 1]}` are
both valid representations of the golden ratio (ADR-0019 §1).

This bench is the **headline floor** for `tools/solve` (ADR-0017,
PRD-v0.2 §"Solve"). Passing it is necessary but not sufficient. The
tool must also conform to the seven-artefact contract.

## Problem statement

`Solve[eqs, vars]` for the workbench. Given a list of equations
`eqs` (each implicitly `eqᵢ = 0`) and a list of unknowns `vars`,
return a `Solution`-shaped record per ADR-0017 with `solutions`,
`completeness ∈ {complete, finite-rep-of-infinite}`, and `branches`.

Branch-honest by design: where Mathematica v1/v2 silently returned
principal-branch slices, the workbench emits the full branched
solution set with explicit integer-valued branch parameter symbols.

### Capability surface — four dispatch lanes (v0.1)

The classifier directs each input to one lane:

| Lane | Detection | Substrate |
|---|---|---|
| **Linear** over ℚ | total degree ≤ 1 in `vars`, no cross-products | `bareissSolve` (ADR-0019 / `linsolve-q`) |
| **Univariate polynomial** | one eqn in one var, deg ≥ 1 | `factorRatQ` + Cardano/Ferrari radicals (`poly-factor` / `poly-roots`) |
| **Transcendental univariate** | single `head(arg) = c` for `head ∈ {sin,cos,tan,sinh,cosh,tanh,exp,log,abs}`, `arg` linear in `x` | `tryTranscendentalInvert` (ADR-0017 branched output) |
| **Refusal** | multivariate-non-zero-dim, parametric, foreign-vocab, deg ≥ 5 irreducible, mixed-trig sums, etc. | `tagged "solve/<class>"` |

The multivariate-zero-dim lane (Gröbner + shape lemma) is *pending*
the groebner stack (beads `8y8`/`fcf`/`9du`/`onh`/`h56`/`x8d`/`m0m`).
Until it ships, multivariate inputs route to the refusal lane with
`tagged "solve/multivariate-non-zero-dim"`. The bench's tier-F.MV
cases test that boundary; when groebner ships, those cases regenerate
to happy-path expectations under the same `id` (one re-run of
`generate.py`).

### Refusal-class roster

Per ADR-0017 + `tools/solve`'s emitted classes:

- `solve/complex-roots-not-yet-named` — single eqn, single var,
  irreducible factor of degree ≥ 5 has one or more *complex* roots
  that alg-num v0.1 cannot yet name. (All-real deg-≥5 factors are
  emitted as `Root[poly, k]` solutions on the happy path per
  ADR-0018; this refusal fires only for the mixed-real-complex case
  until complex algebraic naming ships.)
- `solve/multivariate-non-zero-dim` — multivariate input not in the
  zero-dim lane (until groebner ships, *all* multivariate refuses here).
- `solve/parametric-non-trivial` — symbol present in eqs that isn't in
  `vars` (e.g., `a·x = 1` solving for `x`).
- `solve/foreign-vocabulary` — input not a polynomial *and* not a
  v0.1 transcendental pattern (e.g., `1/x = 0`, `sqrt(x) = 1`,
  `sin(x) + cos(x)`).
- `solve/transcendental-multibranch` — Fateman 1991 territory:
  mixed-trig sums beyond the half-angle scope (`cos(x)+cos(3x)+cos(5x)=0`).
  *v0.1 emits this only via the foreign-vocabulary path because the
  multibranch detector hasn't shipped yet — bead `b55`.* The bench's
  tier-F.fateman cases admit the v0.1 emission and remain a regression
  check when `b55` lands.
- `solve/constant-equation` — eqn reduces to `c = 0` for `c ≠ 0`.
- `solve/empty-input` / `solve/empty-vars`.

## I/O contract

### Wire format

The bench operates in canonical JSON wire format following the
existing `bench/linsolve-q/`, `bench/poly-factor-q/` precedents.
Each case carries `{ id, tier, input }` where `input` is a
text-form record (the workbench `Value`-protocol bridge happens in
the future `run-candidate.ts`; until then the verifier consumes
SymPy reference output directly).

#### Input (per case)

```jsonc
{
  "id":   "v1-linear-2x2-unique",
  "tier": "v1-bank",
  "input": {
    "eqs":  ["x + y - 3", "x - y - 1"],
    "vars": ["x", "y"]
  }
}
```

Equation strings are SymPy-parseable expressions. The implicit
right-hand side is `0` (the caller is responsible for moving
`lhs == rhs` into `lhs - rhs` form before invoking solve). This
matches `tools/solve`'s contract.

#### Output / candidate (per case)

The candidate envelope mirrors the workbench's tool output as JSON:

```jsonc
// happy path
{
  "kind": "ok",
  "vars": ["x", "y"],
  "solutions": [
    {
      "bindings": [{"var": "x", "value": "2"}, {"var": "y", "value": "1"}],
      "branches": []
    }
  ],
  "completeness": "complete",
  "warnings": []
}

// transcendental branched
{
  "kind": "ok",
  "vars": ["x"],
  "solutions": [
    {
      "bindings": [{"var": "x", "value": "asin(1/2) + 2*pi*t_0"}],
      "branches": ["t_0"]
    },
    {
      "bindings": [{"var": "x", "value": "pi - asin(1/2) + 2*pi*t_1"}],
      "branches": ["t_1"]
    }
  ],
  "completeness": "finite-rep-of-infinite",
  "warnings": []
}

// refusal
{
  "kind": "tagged",
  "tag":  "solve/multivariate-non-zero-dim",
  "payload": {"detail": "..." }
}
```

Binding `value` strings parse as SymPy expressions in the original
variables ∪ branch symbols `{t_0, t_1, …}`. (The workbench's tool
emits `Value` records; the verifier accepts the wire-form
serialization the future `run-candidate.ts` will produce. For now,
the SymPy reference produces this shape directly.)

## Invariants the verifier checks

The verifier dispatches by **(input-classification × candidate.kind)**
to one of four lane verifiers. ADR-0019 §1 enumerates the per-lane
invariants; ADR-0019 §2 specifies the branch-honest cube semantics
for the transcendental lane.

### Lane: linear

Five checks (mirror `bench/linsolve-q` discipline):

1. **`shape`** — `solutions` is a list of length 0 (inconsistent),
   1 (unique or under-determined); `completeness ∈ {complete, finite-rep-of-infinite}`;
   each binding parses as a rational or affine-in-branches expression.
2. **`exact_satisfaction`** — substitute each solution's bindings
   into every equation; result is `0` exactly in ℚ.
3. **`free_var_basis`** *(under-determined only)* — for each branch
   `t_i`, instantiate with 10 random rationals from
   `{-3, -1, 0, 1, 2, 5/3, -7/4, 11, -19/4, 6/7}`; resulting concrete
   binding satisfies every equation exactly.
4. **`rank_consistent`** — `len(branches) = n_vars − rank(A)` per
   Rouché-Capelli.
5. **`completeness_correct`** — `completeness == "complete"` iff
   `len(branches) == 0` *and* `len(solutions) == 1`.

### Lane: univariate polynomial

Four checks:

1. **`shape`** — `solutions` is a flat list, each with one binding
   `{var: x, value: <expression>}`, no branches; completeness `"complete"`.
2. **`each_root_satisfies`** — substitute each solution's `value`
   into the input polynomial; `cas-simplify` (or SymPy `simplify`)
   reduces to `0` symbolically.
3. **`count_with_multiplicity`** — `len(solutions) ==
   sum_{factor} deg(factor) · multiplicity(factor)` for the ℚ-irreducible
   factor list of the input. (Multiplicity expressed as repetition,
   per ADR-0017 + worklog 054 §"Why solutions are flat.")
4. **`distinct_roots_match`** — the multiset of `simplify(value)` over
   solutions equals the multiset of roots SymPy reports via
   `Poly.all_roots()`.

### Lane: transcendental univariate

Per ADR-0019 §2 (the branched-substitution cube):

1. **`shape`** — `completeness == "finite-rep-of-infinite"`;
   `branches` non-empty; each binding's value parses with branch
   symbols ⊆ branches.
2. **`branched_substitution_cube`** — for each solution, for each
   integer tuple `(k_1, …, k_n) ∈ [-3, 3]^n` (49 tuples for n=2,
   343 for n=3), instantiate the value, substitute into the equation,
   numerically evaluate; `|residual| < 1e-12 · max(1, |lhs|, |rhs|)`.
   All tuples for all solutions must satisfy.
3. **`completeness_grid`** — sample the equation on a 1D grid of
   2000 points uniform on `[-50, 50]`; identify actual roots (sign
   changes of the equation function); every grid root must fall
   within `1e-6` of *some* candidate solution instantiated at *some*
   integer tuple. A grid root that no candidate-tuple matches is a
   missed branch ⇒ FAIL.

### Lane: refusal

Two checks (per ADR-0019 §5):

1. **`tag_matches`** — `cand.tag == expected.tag` exactly; tag is
   in the documented `solve/*` namespace.
2. **`payload_predicate`** — `payload.detail` is a non-empty string
   (loose predicate, mirroring `linalg-X/non-symmetric-input` style).

## Test set tiers

`golden/inputs.json` contains **100 cases** total:

| Tier | Cases | Description |
|---|---|---|
| **v1-bank.handled** | 15 | Mathematica v1/v2 returned a correct answer. Linear (4) + univariate-poly (6) + transcendental-simple (5). Spans easy → moderate. |
| **v1-bank.refused** | 5 | v1/v2 returned `{}` or wrong; the workbench v0.1 *also* refuses honestly. Two from Fateman 1991 (mixed-trig sums); plus mv-non-zero-dim, deg-5 irreducible, constant-eq. |
| **rand.linear** | 15 | Stratified random `m × n` for `(m, n) ∈ {(1,1), (2,2), (3,3), (4,4), (3,2 over), (2,3 under)}`. Coefficients in `[-9, 9]`. |
| **rand.univariate-poly** | 25 | Stratified random degree `d ∈ {2,…,10}`. Coefficients in `[-9, 9]`. Mix of fully-reducible / partially-reducible / irreducible / multiplicity ≥ 2. |
| **rand.multivariate-zero-dim** | 25 | *v0.1: refusal-class goldens.* Random multivariate systems Wolfram/SymPy can solve but the workbench currently refuses with `solve/multivariate-non-zero-dim`. Will regenerate to happy-path when groebner ships. |
| **rand.transcendental-univariate** | 15 | Random `head(a·x + b) = c` for `head` in the v0.1 invert table; constants in `[-5, 5]`, coefficients integer non-zero. |

Total: **100 cases** spanning the four dispatch lanes + refusal
classes. Per-lane check counts: linear×5, univariate-poly×4,
transcendental×3, refusal×2.

## Triple-witness oracle protocol

Per ADR-0019 §3, every golden case is admitted iff ≥ 2 of 3 oracles
agree on the expected output modulo verifier-invariant equivalence.
Oracles in priority order:

1. **`wolframscript`** via `Solve[]` / `Reduce[]`
   (`bench/_corpus/oracle/wolfram.py`).
2. **SymPy** via `solve` / `solveset` / `linsolve`
   (`bench/_corpus/oracle/sympy_bridge.py`, kind=`solve`).
3. **SageMath** when available — preferred third witness for
   algebraic-number territory; not required.

Disagreement protocol: if all three disagree, the case is dropped
(logged in `oracle_log.json`). If two agree, the agreed answer is
the golden; the third's output is logged with the disagreement
classification. If two disagree and the third is unavailable, the
case is dropped.

For refusal-class cases (Tier v1-bank.refused.fateman, Tier
rand.multivariate-zero-dim): the *workbench's* expected refusal is
admitted iff the oracles also fail to produce a clean discrete
solution set on the same input (Wolfram returns `ConditionalExpression`
or `$Failed`, SymPy raises `NotImplementedError` or returns an
`ImageSet`). The agreement layer's `kind='refusal'` semantics
(`bench/_corpus/oracle/agreement.py:_agree_refusal`) is the consensus
condition.

## Verifying your solution

```sh
# Generate goldens (slow; full triple-witness via wolframscript).
python3 bench/solve/golden/generate.py

# Fast iteration with SymPy-only witnessing:
WB_LIVE_ORACLE=0 python3 bench/solve/golden/generate.py

# Run mutation-prove gate:
pytest bench/solve/golden/test_mutations.py -v

# Future: run the workbench candidate end-to-end via run-candidate.ts
# (deferred to a follow-up shard, mirroring poly-factor-q's pattern).
```

### Files

- `golden/inputs.json` — 100 test cases, seeded reproducible.
- `golden/expected.json` — reference outputs (SymPy reference).
- `golden/oracle_log.json` — triple-witness consensus log per case.
- `golden/verify.py` — 4-lane dispatch verifier.
- `golden/verifier_protocol.md` — exact specifications per check.
- `golden/generate.py` — reproducible golden generation.
- `golden/test_mutations.py` — ≥5 RED perturbations of the
  reference (ADR-0019 §4).
- `reference/solve_reference.py` — Python reference (SymPy-backed
  per-lane solver matching `tools/solve`'s output shape).

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun. No FFI. (Workbench-side; the bench's Python
  reference + verifier is the standard cross-language oracle.)
- Default determinism tier (symbolic, bit-identical cross-platform
  forever — ADR-0015).
- Boundary categories per ADR-0003: `tagged "solve/<class>"` for
  refusals; `ToolError` for malformed input only.
- Solution-set shape per ADR-0017.
- Bench discipline per ADR-0019 (this document is its first solve-
  level instantiation).

## What this bench does NOT cover

- **Inequalities.** `Solve` for `x^2 < 1` is `Reduce[]`-territory;
  the workbench has no inequality-solver in v0.1.
- **Solving over fields beyond ℚ.** No `𝔽_p` solving, no
  `ℚ(α)` algebraic-extension solving (P3 after `packages/alg-num`).
- **Symbolic parameters in coefficients beyond refusal.** `a·x = 1`
  refuses with `solve/parametric-non-trivial`; "solve treating `a` as
  parameter, return parametric `x = 1/a` with the implicit `a ≠ 0`
  side condition" is post-v1.
- **Differential / functional equations.** `D[y, x] == y` is the
  remit of a future `dsolve` tool, not this bench.
- **Equation systems with infinite continuous components**
  (e.g., `x = y` in `(x, y)` expecting "the line `x = y`"). The
  workbench v0.1 returns the parametric form via the linear lane;
  beyond linear, "infinite continuous" maps to refusal.
