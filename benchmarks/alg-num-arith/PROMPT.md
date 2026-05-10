# Bench — `alg-num-arith` (field arithmetic over `Root[poly, k]`)

## ⚠ How you will be graded

You will be graded on **exact algebraic-number correctness**.
Every arithmetic output `Root[poly_out, k_out]` must satisfy:
- `poly_out` is irreducible, primitive, positive-leading, integer-
  coefficient (canonical form per ADR-0018);
- `poly_out(numerical_value)` evaluates to ≈ 0 within a tight
  tolerance, where `numerical_value` is computed by an independent
  oracle (SymPy `qqbar`-class evaluation of `op(a, b)` to
  high-precision floating-point);
- `k_out` matches the position of the named real root in
  `poly_out`'s ascending real-root order (verified by interval
  containment of the candidate's interval against
  `sympy.Poly(poly_out, x).intervals()[k_out]`).

For `op = "eq"`, the candidate boolean must match the SymPy oracle's
verdict exactly.

For boundary tags (`alg-num-arith/{inv-of-zero, div-by-zero}`), the
exact tag class must match.

Passing this bench is the floor for `tools/alg-num-arith`. The
seven-artefact contract still applies on top.

## Problem statement

Implement the field arithmetic on named algebraic numbers
(`Root[poly, k]`, ADR-0018):

  given two `Root[]` values `a` and `b` (or one, for unary ops) and
  an operation `op ∈ {add, sub, mul, div, neg, inv, eq}`, return
  the corresponding canonical `Root[]` result (for arithmetic) or
  `boolean` (for `eq`).

### Algorithm path

`tools/alg-num-arith` (worklog 065) wraps the substrate from
worklog 062:

- **Add / Sub.** Minpoly divides
  `squarefree(Res_y(f_a(y), f_b(x − y)))` (Cohen GTM 138 §3.6.2).
  Sylvester-Bareiss resultant in ℚ[x]. Hint interval
  `(a.lo + b.lo, a.hi + b.hi)`; refine inputs and retry if multiple
  factors of the resultant share a root in the hint.
- **Mul / Div.** Minpoly divides
  `squarefree(Res_y(y^{deg f_a} · f_a(x/y), f_b(y)))`. Sign-aware
  product hint interval (convex hull of the four corner products).
- **Neg.** `f_a(−x)` (alternating-sign coefficient substitution),
  interval mirror.
- **Inv (a ≠ 0).** `x^{deg f_a} · f_a(1/x)` (coefficient reversal),
  interval inversion. If `a = 0`, refuse with
  `alg-num-arith/inv-of-zero`.
- **Eq.** Both inputs canonicalise to `(minpoly, k, interval)`;
  result is `(a.minpoly == b.minpoly) AND (a.k == b.k)` per
  ADR-0018 §"Equality semantics."

### Why a wire envelope

The substrate ships in `@workbench/alg-num` (worklog 062). The tool
is the seven-artefact bench-able surface — schema, examples,
invariants, `--test` hook, goldens. It exists so an agent composing
tools reaches for `wb.algNumArith({a, b}, {op: "add"})` exactly
the way it reaches for `wb.modPow({base, exp, mod})`.

## I/O contract

### Input

```jsonc
{
  "a":   <Root[poly, k] expression>,
  "b":   <Root[poly, k] expression>   // omitted for unary ops
}
```

with the `op` carried separately as a tool flag.

### Output

```jsonc
{
  "kind": "expression",
  "head": "Root",
  "args": [
    {"kind": "expression", "head": "Polynomial", "args": [<integer c_0>, ..., <integer c_n>]},
    {"kind": "integer", "value": "<k>"}
  ]
}

// or, for op = "eq":
{"kind": "boolean", "value": true | false}

// or, for refusal:
{"kind": "tagged", "tag": "alg-num-arith/<class>", "payload": {"kind": "record", "fields": {"detail": <string>}}}
```

Refusal classes (per `tools/alg-num-arith/tool.ts`):

- `alg-num-arith/inv-of-zero` — `op = inv` with `a` representing zero.
- `alg-num-arith/div-by-zero` — `op = div` with `b` representing zero.

`ToolError` for malformed input only (non-Root values; missing/extra
`b` field for the chosen op's arity).

## Invariants the verifier checks

Per ADR-0019 §1, four checks per happy-path arithmetic case + two
for refusals + one extra for eq:

### Happy-path arithmetic (`kind = "expression"`, head = `Root`)

1. **`shape`** — head=`Root`, args=`[Polynomial, integer]`,
   Polynomial.args is non-empty integer list, k is non-negative
   integer ≤ deg.
2. **`canonical_form`** — Polynomial is irreducible over ℚ
   (`sp.Poly(poly, x, domain="QQ").is_irreducible`), primitive
   (gcd of coefficients = 1), positive-leading.
3. **`vanishes_at_op_value`** — let `nv` be the numerical value of
   `op(a, b)` computed via SymPy `qqbar`-class evaluation; assert
   `|sp.Poly(poly_out, x).eval(nv)| < 1e-12 * max(1, |nv|^deg)`.
4. **`index_matches_real_position`** — assert `nv ∈ sp.Poly(poly_out,
   x, domain="QQ").intervals()[k_out]` (the k_out-th real-root
   interval bracketing). Disambiguates same-minpoly conjugates.

### Eq (`kind = "boolean"`)

1. **`shape`** — `kind == "boolean"`.
2. **`matches_oracle`** — candidate value matches
   `sympy_qqbar_eq(a, b)`.

### Refusal (`kind = "tagged"`)

1. **`shape`** — tag in `alg-num-arith/*` namespace; payload is a record.
2. **`refusal_class_matches`** — exact tag string match;
   `payload.detail` is non-empty.

These are **necessary AND sufficient** for a valid alg-num-arith
output modulo representation equivalence.

## Test set tiers

`golden/inputs.json` contains **30 cases**:

| Tier | Cases | What it probes |
|---|---|---|
| **A. elementary** | 10 | `√2 + √3`, `√2 · √3`, `√2 · √2 = 2` (rational degenerate), `1 + √2`, `√3 − √2`, `neg(√2)`, `inv(√2)`, `√6 / √2 = √3`, `√2 / √2 = 1`, `add(0, √2) = √2`. |
| **B. nested** | 5 | `sqrt(2 + sqrt(3))` — the inner-square-root algebraics — interacting with each other (`sqrt(2+sqrt(3)) + sqrt(2−sqrt(3))`, `sqrt(2+sqrt(3)) · sqrt(2−sqrt(3)) = 1`); deg-4 minpoly arithmetic. |
| **C. high-degree** | 5 | Random irreducible deg-5 to deg-7 algebraics (Lehmer's L(x) = 2cos(2π/11), Salem-1, total-real cyclotomic-real-subfield variants); arithmetic between them produces deg-25+ resultants. |
| **D. conjugate-distinguishing** | 5 | Same minpoly, different k: `α + α'` (sum of conjugates = trace), `α · α'` (product = norm). VAS-LMQ interval disambiguation must pick the correct factor of the resultant when conjugates collide. |
| **E. equality stress** | 5 | (1) Reflexivity (`α == α`); (2) cross-minpoly distinguish (`+√2 != +√3`); (3) same-minpoly index-distinguish (`+√2 != −√2`); (4) round-trip `add(α, neg(α)) == 0`; (5) round-trip `mul(α, inv(α)) == 1`. |

**30 cases × ≤4 happy-path checks + ≤2 refusal checks + ≤2 eq checks
≈ 100 invariant assertions.**

## Oracle protocol

Per ADR-0019 §3, golden cases would be admitted iff ≥ 2 of 3 oracles
agree. v0.1 of this bench ships a *single-oracle* protocol:

1. **SymPy `qqbar`-class evaluation** — `sp.AlgebraicNumber` /
   `sp.minpoly` / `sp.Poly.intervals()` provide minpoly and
   real-root-interval witnesses; the verifier evaluates the
   candidate's claim against these.

Wolfram and Sage as third witnesses are deferred until a
`Root[]`-canonical formatter is ported to those environments. The
bench's verify.py is the *necessary-and-sufficient* contract.

## Verifying your solution

```sh
# Generate goldens (deterministic; SymPy-only).
python3 bench/alg-num-arith/golden/generate.py

# Run the verifier on the workbench tool's outputs:
python3 bench/alg-num-arith/golden/verify.py

# Mutation-prove gate:
python3 bench/alg-num-arith/golden/test_mutations.py
```

### Files

- `golden/inputs.json` — 30 test cases.
- `golden/expected.json` — reference output kinds (ok / tagged) per
  case.
- `golden/verify.py` — 4-check (arithmetic) / 2-check (refusal) /
  2-check (eq) verifier.
- `golden/verifier_protocol.md` — exact specifications per check.
- `golden/generate.py` — reproducible golden generation.
- `golden/test_mutations.py` — mutation-prove harness.
- `reference/alg_num_arith_reference.py` — SymPy-backed reference.

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun (workbench tool side). No FFI.
- Default determinism tier (symbolic, bit-identical cross-platform
  forever — no `numerical: true`).
- Inputs and outputs are **wire-encoded `Root[poly, k]`
  expressions** per ADR-0018 — *not* SymPy `RootOf` objects, *not*
  algebraic-number floating-point approximations.
- Refusals are *honest* (only when the substrate truly cannot
  produce a Root; never for "this would take longer than 1 second").
