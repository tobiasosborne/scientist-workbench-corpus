# Verifier protocol — `groebner-basis`

`verify.py` consumes `{input, candidate, id, expected?}` JSON on stdin
and emits `{pass, reason, checks, wall_clock_seconds}` on stdout.

Tolerance regime: **none**. Every check is exact-rational equality
(BigInt over ℚ via SymPy's QQ domain). A candidate that is "almost a
Gröbner basis" within float tolerance is wrong.

## Invocation

```sh
cat <case>.json | python3 verify.py
```

`verifier.cmd = "python3"` in `manifest.toml` is the canonical
verifier. `verify.ts` is a TypeScript LOCKSTEP verifier that is not the
canonical — its purpose is to catch SymPy/TS divergence on committed
cases (run by `generate.py` during golden refresh).

## Stdin shape

```jsonc
{
  "input": {
    "polys": ["x**2 + y", "x*y + 1"],   // list of polynomial expression strings
    "vars":  ["x", "y"],                 // declared variable order
    "order": "lex" | "degrevlex"         // monomial order
  },
  "candidate": {
    "kind":     "ok" | "tagged",
    // "ok" lane:
    "basis":    ["y**3 + 1", "x - y**2"],
    "order":    "lex",
    "vars":     ["x", "y"],
    "n_pairs":  0,                       // metric, not invariant
    "warnings": [],

    // "tagged" lane:
    "tag":      "groebner-basis/non-polynomial",
    "payload":  {"detail": "<reason>"}
  },
  "id":       "<case-id>",                // optional — used for sample-RNG seed
  "expected": {                           // optional — when supplied, checked
    "kind":   "ok" | "tagged",
    "tag":    "groebner-basis/<class>"    // for tagged-lane cross-check
  }
}
```

## Stdout shape

```jsonc
{
  "pass":   true,
  "reason": "all invariants hold",
  "checks": {
    "shape":                                {"pass": ..., "detail": "..."},
    "ideal_containment_input_to_candidate": {"pass": ..., "detail": "..."},
    "ideal_containment_candidate_to_input": {"pass": ..., "detail": "..."},
    "s_pair_reduces_to_zero":               {"pass": ..., "detail": "..."},
    "tag_matches":                          {"pass": ..., "detail": "..."}
  },
  "wall_clock_seconds": 0.0123
}
```

A check that doesn't apply to the candidate's `kind` is reported as
`{"pass": true, "detail": "n/a for kind=<kind>"}`.

The `wall_clock_seconds` field is a **metric**, not an invariant. Per
ADR-0036 (measurement-not-invariant pin), the per-case PASS/FAIL gate
is the four invariants alone. Runtime drift is reported by
corpus-query at the aggregate level, not the per-case level.

## The four invariants — exact specifications

The four invariants are mathematical identities with no tolerance.
Together they form Buchberger's correctness certificate (CLO Ch.2 §6
Theorem 6 p.85).

### 1. `shape`

Structure-only static check.

PASS iff:

- `candidate.kind ∈ {"ok", "tagged"}`.
- For `kind = "ok"`:
  - `candidate.basis` is a list of strings, each parseable as a
    polynomial in the declared `vars` over ℚ.
  - `candidate.order ∈ {"lex", "degrevlex"}` and equals `input.order`.
  - `candidate.vars` equals `input.vars` (same list, same order).
  - No basis element mentions a symbol outside `input.vars`
    (foreign-symbol leak).
- For `kind = "tagged"`:
  - `candidate.tag` is a string in the `groebner-basis/` namespace.
  - `candidate.tag ∈ {"groebner-basis/empty-input",
    "groebner-basis/empty-vars", "groebner-basis/parametric",
    "groebner-basis/non-polynomial"}` — one of the four declared
    refusal classes.
  - `candidate.payload` is a record (possibly empty).

### 2. `ideal_containment_input_to_candidate` (only when `kind = "ok"`)

Every input polynomial reduces to 0 modulo the candidate basis under
the requested order.

```
for each f in input.polys:
    let r := remainder of f divided by candidate.basis under input.order
    require r == 0 exactly
```

Computed via `sympy.polys.rings.PolyElement.div` (a senior-grade
multivariate polynomial division — the same routine SymPy's `groebner`
uses internally). Failure means `<input> ⊄ <candidate>`: the candidate
is missing polynomials.

### 3. `ideal_containment_candidate_to_input` (only when `kind = "ok"`)

Every candidate polynomial reduces to 0 modulo a Gröbner basis of the
input.

```
let G_ref := sympy.groebner(input.polys, vars, order=input.order)
for each g in candidate.basis:
    let r := remainder of g divided by G_ref under input.order
    require r == 0 exactly
```

Failure means `<candidate> ⊄ <input>`: the candidate has spurious
elements not in the input ideal. SymPy's `groebner` is the trusted
oracle for the reference GB; the verifier doesn't need to verify
SymPy's correctness here, only use SymPy as the canonical oracle (per
the dual-witness Wolfram + SymPy oracle protocol enforced at
generate-time).

Together, (2) and (3) prove `<input> = <candidate>` (ideal equality).

### 4. `s_pair_reduces_to_zero` (only when `kind = "ok"`)

For every pair `(g_i, g_j)` of distinct elements of the candidate
basis with `i < j`, the S-polynomial

```
S(g_i, g_j) := (lcm(LM(g_i), LM(g_j)) / LM(g_i)) / LC(g_i) · g_i
             − (lcm(LM(g_i), LM(g_j)) / LM(g_j)) / LC(g_j) · g_j
```

reduces to 0 modulo the candidate basis. By Buchberger's theorem (CLO
Ch.2 §6 Theorem 6 p.85), this IS the Gröbner basis property: a basis
G is a GB iff every S-pair reduces to 0 mod G.

Sample regime:

- If `|candidate.basis| ≤ 20`: check **all** `n(n-1)/2` pairs.
- If `|candidate.basis| > 20`: check a deterministic random sample of
  50 pairs (`hashlib.sha256(case_id)` seeded RNG, so the sample is
  reproducible across runs).

The check's `detail` field records which regime was used.

## The refusal-class check

### `tag_matches` (only when `kind = "tagged"`)

PASS iff:

- `candidate.tag` is in the `groebner-basis/` namespace and equals one
  of the four declared classes (see invariant 1 above).
- When `expected.tag` is supplied (golden-mode), `candidate.tag ==
  expected.tag` exactly. This catches the mutation
  `refusal_class_confusion` where a candidate emits the wrong refusal
  class (e.g., `empty-input` instead of `non-polynomial`).

When `expected.kind = "tagged"` but `candidate.kind = "ok"`, the
mismatch fires the `shape` check (lied-about-scope detection — see
`mutation_lied_about_scope` in `test_mutations.py`).

## TypeScript lockstep (`verify.ts`)

`verify.ts` is a senior-grade TS port of the four invariants using pure
BigInt rational arithmetic for multivariate polynomials. It is run by
`generate.py` as a sanity check at golden-refresh time:

- For every committed case, both `verify.py` and `verify.ts` are
  invoked on the SymPy-reference output. If their `pass/fail` outcomes
  differ, generation halts with a flagged log.

The TS verifier replicates invariants 1, 2, and 4 byte-for-byte.
**Invariant 3 is delegated to verify.py** — it requires computing a
reference Gröbner basis, which would require porting Buchberger to TS
(out of scope for the lockstep verifier; that's the job of the Phase 3
tool itself). The TS verifier records its (3) check as `pass: true,
detail: "delegated to verify.py"`. Lockstep agreement is therefore on
(1), (2), (4) — sufficient to catch SymPy/TS parser drift, monomial
order divergence, and S-pair arithmetic regressions.

## Aggregation

Case PASS = all applicable checks PASS.
Case FAIL = at least one applicable check FAIL.

The verifier exits 0 if PASS, 1 if FAIL. The full breakdown is in
stdout's `checks` object regardless of exit code.

## What is *not* checked

- **Reduced form.** A reduced GB is unique (up to ordering); but the
  verifier accepts ANY correct GB. Per ADR-0019 §1, this is the
  invariant-not-byte-equality discipline. A candidate that emits a
  non-reduced GB satisfying all four invariants is admissible.
- **Basis-element ordering.** The verifier accepts any permutation of
  the basis list. The bench's `expected.json` carries the
  SymPy-canonical ordering for diff-friendly golden refresh, but the
  candidate is not required to match.
- **Wall-clock performance.** Per ADR-0036, `wall_clock_seconds` is a
  metric. Drift `> 3×` baseline triggers a corpus-query warning, not
  a per-case failure.
- **Method name / `n_pairs`.** Informational fields. The verifier
  doesn't gate on them, but the corpus query reports `n_pairs` as a
  pair-pruning effectiveness metric for cross-implementation
  comparison.
- **Monic / canonical form.** Per the ADR-0019 §1 discipline, only
  ideal equality is verified. A candidate basis with non-monic
  leading coefficients is admissible if the four invariants hold —
  the resulting basis still generates the same ideal.

## Tier-specific notes

- **Refusal-envelope cases**: `verify.py` checks the candidate is
  tagged with the correct class. The four happy-path invariants are
  reported as `n/a for kind=tagged`.
- **Classical-hard cases** (`cyclic-3..5`, `Katsura-3`): the
  `expected.wall_clock_baseline_seconds` is recorded at generate-time
  on this device. Implementation-induced runtime drift surfaces as a
  corpus-query metric, not as an invariant failure.
- **Stratified-random cases**: the 40-case battery is generated with
  the adversarial-mix strategy described in `generate.py` — 30%
  random-uniform, 30% near-coprime LMs, 20% multiplicity-induced, 10%
  coefficient-swell-prone, 10% large-but-structured. The seeded
  generation is fully reproducible (`SEED = 20260510` + the
  hard-coded constants); the sub-class mix is documented per case in
  `inputs.json`'s `ref` field.
