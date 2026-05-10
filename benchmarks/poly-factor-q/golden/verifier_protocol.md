# Verifier protocol — `poly-factor-q`

`verify.py` consumes `{input, candidate}` JSON on stdin and emits
`{pass, reason, checks}` on stdout. Tolerance regime: **none**.
Every check is exact-rational equality (or boolean equality for
the irreducibility delegate). A candidate that is "correct within
`1e-12`" is wrong.

## Invocation

```sh
cat <case>.json | python3 verify.py
```

Stdin shape:

```jsonc
{
  "input": {
    "f":   "x^4 - 5*x^2 + 4",
    "var": "x"
  },
  "candidate": {
    "kind":     "ok" | "tagged",
    "content":  "1",
    "factors":  [{"factor": "x - 1", "multiplicity": 1}, ...],
    "method":   "<str>",
    "warnings": [<str>, ...],

    // tagged case only:
    "tag":      "poly-factor-q/non-polynomial",
    "payload":  {"detail": "<str>"}
  },
  "id": "<case-id>"
}
```

The `kind` field discriminates happy-path vs refusal-class
candidates. A happy-path candidate carries `content`, `factors`,
`method`, `warnings`; a tagged candidate carries `tag`, `payload`.

Stdout:

```jsonc
{
  "pass":   true,
  "reason": "all invariants hold",
  "checks": {
    "shape":                       {"pass": ..., "detail": "..."},
    "product_equals_input":        {"pass": ..., "detail": "..."},
    "each_factor_irreducible":     {"pass": ..., "detail": "..."},
    "factors_primitive":           {"pass": ..., "detail": "..."},
    "factors_positive_leading":    {"pass": ..., "detail": "..."},
    "refusal_class_matches":       {"pass": ..., "detail": "..."}
  }
}
```

A check that doesn't apply to the candidate's `kind` is reported
as `{"pass": true, "detail": "n/a for kind=<kind>"}`.

## The 5 happy-path checks — exact specifications

### 1. `shape`

Structure-only static check.

PASS iff:

- `input.f` is a string, `input.var` is a string.
- `candidate.kind ∈ {"ok", "tagged"}`.
- For `kind = "ok"`:
  - `candidate.content` is a string parseable as `Fraction`
    (canonical rational form preferred but not enforced —
    `"3/2"`, `"1.5"`, and `"-(-3)/2"` all PASS this check
    iff they round-trip through `Fraction` to the same value).
  - `candidate.factors` is a list (possibly empty for content-only
    inputs; but the empty-list case requires `f` itself to equal
    `content`, checked under `product_equals_input`).
  - Every entry of `factors` is a record `{factor: str,
    multiplicity: int}` with `multiplicity ≥ 1`.
  - Every `factor` string parses as a polynomial in `var` over ℤ
    via `sympy.Poly(factor_str, sympy.Symbol(var), domain="ZZ")`.
    A factor whose parse fails (e.g., contains `1/2`,
    non-polynomial terms, or a different variable) FAILS shape.
- For `kind = "tagged"`:
  - `candidate.tag` is a string of form `"poly-factor-q/<class>"`.
  - `candidate.payload` is a record (may be empty).

### 2. `product_equals_input` (only when `kind = "ok"`)

Compute

```
reconstructed = Fraction(content) * prod_i Poly(factor_i, var, domain="QQ") ** multiplicity_i
```

PASS iff `reconstructed.as_expr() - sympy.sympify(f, locals={var:
sympy.Symbol(var)})` simplifies to `0` exactly (via
`sympy.expand` over `domain="QQ"`).

Implementation note: we compare the polynomial-coefficient vectors,
not the string forms — `(x - 1)*(x + 1)` and `x^2 - 1` reconstruct
to the same coefficient vector and PASS, regardless of whether the
candidate emitted the factored or expanded form (both are legal
factor entries).

### 3. `each_factor_irreducible` (only when `kind = "ok"`)

For every `factor_i` in `candidate.factors`:

```
poly = sympy.Poly(factor_i, sympy.Symbol(var), domain="QQ")
PASS_i = poly.is_irreducible
```

PASS iff `PASS_i` is `True` for every `i`.

`is_irreducible` is delegated to SymPy as the ground-truth oracle
for univariate-over-ℚ irreducibility (per ADR-0019 §6: SymPy's
`Poly.is_irreducible` uses Wang's algorithm + Berlekamp,
production-tested). Constant factors (e.g., `factor = "1"` or
`factor = "-3"`) FAIL — content belongs in the `content` field.

### 4. `factors_primitive` (only when `kind = "ok"`)

Each factor's coefficient vector (in degree-descending integer
form) has gcd-of-absolute-values equal to 1.

PASS iff `gcd(|c_d|, |c_{d-1}|, ..., |c_0|) == 1` for every
`factor_i`, where `c_k` are the integer coefficients.

A factor like `"2*x + 4"` (coefs `[2, 4]`, gcd = 2) FAILS — the
content `2` should have been pulled into the `content` field as
`2*1 = 2`, leaving `"x + 2"` as the primitive factor.

### 5. `factors_positive_leading` (only when `kind = "ok"`)

The leading coefficient of every factor (in degree-descending
form) is strictly positive.

PASS iff `lc(factor_i) > 0` for every `i`.

A factor like `"-x + 1"` FAILS — the canonical form is `"x - 1"`
with the sign carried in `content`. This forces uniqueness of the
factor list up to ordering.

## The refusal-class check

### `refusal_class_matches` (only when `kind = "tagged"`)

PASS iff:

- `candidate.tag` matches the case's `expected.tag` *exactly*.
- `candidate.payload` satisfies the case's `expected.payload_predicate`
  (per ADR-0019 §5):
  - `"detail-non-empty"` → `payload.detail` is a non-empty string.

For the v1 single-refusal-class taxonomy
(`poly-factor-q/non-polynomial`), the predicate is always
`"detail-non-empty"`.

When `kind = "tagged"`, the four happy-path checks are reported as
`n/a for kind=tagged`.

When `kind = "ok"` but the case's `expected.kind = "tagged"`, the
candidate FAILS shape (it produced a happy-path output where a
boundary tag was required) and the four happy-path checks proceed
*regardless* — a candidate that says "ok" must back it up. This
catches "lied about scope" bugs.

## Aggregation

Case PASS = all applicable checks PASS.
Case FAIL = at least one applicable check FAIL.

The verifier exits 0 if PASS, 1 if FAIL. The full breakdown is
in stdout's `checks` object regardless of exit code.

## What is *not* checked

- **Internal algorithm.** Berlekamp vs Cantor-Zassenhaus, naive
  subset vs vanHoeij — all admissible, only the output and its
  exact-rational properties are checked. The bench's *Tier D*
  cases catch implementations whose recombination is exponential,
  but only via wall-clock timeout (60s per case via the bench
  harness), not via algorithm-name introspection.
- **Output formatting style.** `"x - 1"` vs `"-1 + x"` vs
  `"x + (-1)"` are all accepted by `sympy.Poly`; whichever the
  candidate emits is fine as long as the polynomial it parses to
  satisfies the invariants.
- **Factor-list order.** The verifier accepts any permutation.
  The bench's `expected.json` carries factors in the canonical
  order (degree-ascending, lex-tiebreak) for diff-friendly golden
  refresh, but the candidate is not required to match that order.
- **Performance / wall-clock.** Per-case 60-second timeout is the
  only gate (set by `bench/infra/run-bench.sh`). Tier D
  Swinnerton-Dyer-`n=4` is the load-bearing case for "the
  recombination algorithm is sub-exponential."
- **Method name.** `candidate.method` is informational; the
  verifier does not gate on its value, but the PROMPT recommends
  `"berlekamp-vanhoeij"` for the v1 implementation.
- **Warnings / advisories.** The `candidate.warnings` list is
  passed through to the bench output for debugging but does not
  participate in pass/fail determination.

## Tier-F Mignotte stress sanity assertion

For Tier-F cases, the bench's `inputs.json` carries a per-case
sidecar field `expected_max_coef_log2` — the log₂ of the largest
expected coefficient *in any factor of the input*, computed at
generate time by SymPy. The verifier confirms that every output
factor's largest absolute coefficient satisfies
`log₂(|c|) ≤ expected_max_coef_log2 + 1` (the `+1` is slack for
factor-permutation differences in coefficient distribution).

This is a sanity assertion, not an invariant — its purpose is to
catch implementations that produced *correct* factors at the cost
of intermediate coefficient blowup the bench is supposed to
detect (e.g., naive Z-arithmetic without `mod p` reduction).
Failure of this check emits a *warning*, not a fail.
