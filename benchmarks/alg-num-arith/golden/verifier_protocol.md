# `bench/alg-num-arith` — verifier protocol

Per ADR-0019 §1: each invariant check has an exact specification a
candidate's output must satisfy. The verifier `verify.py` runs four
checks for arithmetic-happy cases, two for eq, and two for refusal,
and is mutation-proven against `test_mutations.py` (≥ 5 perturbations
guaranteed to RED-fail; this bench ships 8).

## Lane: arithmetic-happy (`expected.kind == "ok"`)

### `shape` — structural

- `cand.kind == "expression"` AND `cand.head == "Root"`.
- `cand.args` is `[Polynomial[c_0, …, c_n], k]`.
- The Polynomial sub-expression has `kind == "expression"` and
  `head == "Polynomial"`; its `args` is a non-empty list of
  `integer`-typed coefficient values.
- The `k` arg is an `integer`-typed value with `0 ≤ k_value < deg`
  where `deg = len(Polynomial.args) − 1`.

Failure mode: malformed wire encoding (wrong head, wrong arity,
non-integer coefficient/k, k out of range).

### `canonical_form` — minpoly canonicalisation invariants per ADR-0018

Let `p` be the candidate's Polynomial decoded as `sympy.Poly` over ℚ.

- **Irreducible over ℚ.** `sympy.Poly(p, x, domain="QQ").is_irreducible`.
- **Primitive.** `gcd(|c_0|, |c_1|, …, |c_n|) == 1`.
- **Positive leading.** `c_n > 0` (high-to-low coefficient list, the
  *leading* coefficient).

Failure mode: a tool that emits `2x² − 4` (non-primitive), `−x² + 2`
(negative leading), or `(x²−2)(x²−3)` (reducible) trips this check.

### `vanishes_at_op_value` — substitution check

Compute the *oracle reference value* `nv` = `op(a, b)` via SymPy:

  - `op == "add"` → `nv = sympy_real(a) + sympy_real(b)`.
  - `op == "sub"` → `nv = sympy_real(a) − sympy_real(b)`.
  - `op == "mul"` → `nv = sympy_real(a) × sympy_real(b)`.
  - `op == "div"` → `nv = sympy_real(a) / sympy_real(b)`.
  - `op == "neg"` → `nv = −sympy_real(a)`.
  - `op == "inv"` → `nv = 1 / sympy_real(a)`.

where `sympy_real(r)` is the SymPy real-algebraic-number value
extracted from the wire `Root[poly, k]`.

Then assert `p(nv) ≡ 0` symbolically (`sympy.simplify(p.eval(nv)) == 0`)
or, fallback, numerically (`|p.eval(nv).evalf(50)| < 1e-12 ·
max(1, |nv|^deg)`).

Failure mode: a tool that returns `Root[x² − 7, 1]` for `√2 + √3`
(numerical value ≈ 3.146; `(3.146)² − 7 ≈ 2.9 ≠ 0`) trips this check.

### `index_matches_real_position` — k disambiguation

The k of a `Root[poly, k]` is the position of the named real root in
`poly`'s ascending real-root list. After `vanishes_at_op_value`
confirms the candidate's polynomial vanishes at `nv`, this check
asserts `nv ≈ poly.real_roots()[k]` numerically (tolerance
`1e-10 · max(1, |nv|)`).

Failure mode: a tool that emits the right minpoly but the wrong k —
e.g., for `√2 + √3` (≈ 3.146, the *largest* real root of x⁴−10x²+1),
returns k=0 (the *smallest* real root, ≈ −3.146). The two are
algebraic conjugates that share a minpoly; only the k disambiguates.

## Lane: eq (`expected.kind == "boolean"`)

### `shape`

- `cand.kind == "boolean"`.
- `cand.value ∈ {true, false}`.

### `matches_oracle`

`cand.value == sympy_qqbar_eq(a, b)`. The SymPy oracle (per
`reference/alg_num_arith_reference.py`) decides equality by
simplifying the difference of the two algebraic numbers; falls back
to high-precision numerical comparison when symbolic simplification
is conservative.

Failure mode: a tool that flips the eq verdict, returns the wrong
boolean for a same-minpoly-different-k pair, or claims equality
across distinct minpolys.

## Lane: refusal (`expected.kind == "tagged"`)

### `shape`

- `cand.kind == "tagged"`.
- `cand.tag` starts with `alg-num-arith/`.
- `cand.payload.kind == "record"`.
- `cand.payload.fields.detail.kind == "string"` AND non-empty.

### `refusal_class_matches`

`cand.tag == expected.tag` exactly.

Failure mode: emitting `alg-num-arith/div-by-zero` instead of
`alg-num-arith/inv-of-zero`, or `alg-num-arith/<typo>`.

## Cross-lane: `lied_about_scope`

A pre-check before either lane runs: if `expected.kind` indicates one
lane (e.g. `"ok"` ⇒ arithmetic) but the candidate landed in another
(e.g. `kind == "tagged"`), the verifier short-circuits to a
shape-failure rather than entering the wrong-lane verifier.

This catches "the tool refused when it should have computed" and
"the tool fabricated an answer when it should have refused" with one
mutation entry.

## Verifier sensitivity proof — mutation harness

`test_mutations.py` carries 8 mutations:

1. **`wrong_minpoly_coef`** — flip a coefficient of the result minpoly
   ⇒ `vanishes_at_op_value`.
2. **`wrong_k_index`** — emit the right minpoly but the wrong k for
   a same-minpoly conjugate ⇒ `index_matches_real_position`.
3. **`reducible_minpoly`** — emit a reducible polynomial that still
   vanishes at the right value ⇒ `canonical_form`.
4. **`wrong_eq_value`** — flip the boolean for an eq case ⇒
   `matches_oracle`.
5. **`lied_about_scope`** — fabricate a Root[] answer for inv(0) ⇒
   `shape` (cross-lane).
6. **`wrong_refusal_tag`** — refuse with the wrong tag class ⇒
   `refusal_class_matches`.
7. **`negative_leading_coef`** — negate every coefficient ⇒
   `canonical_form`.
8. **`non_primitive`** — multiply every coefficient by 2 ⇒
   `canonical_form`.

GREEN baseline: 6/6 pass. RED mutations: 8/8 caught. Both 4-check
arithmetic and 2-check eq + refusal lanes are sensitive.
