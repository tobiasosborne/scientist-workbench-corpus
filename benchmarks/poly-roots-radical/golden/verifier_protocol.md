# Verifier protocol — `bench/poly-roots-radical`

This document specifies the exact tolerances and check semantics for
`bench/poly-roots-radical/golden/verify.py`. Every check is one of
two flavours: **structural** (boolean / type-shape) or **exact-with-
numerical-fallback** (rational/symbolic equality, with `evalf`-based
fallback for casus-irreducibilis radicals per ADR-1yu).

Per ADR-0019 §1.

## Top-level dispatch

The verifier reads `{input, candidate, expected, id, tier}` JSON
on stdin, writes `{pass, reason, checks}` on stdout, exits 0 on
PASS / 1 on FAIL.

`expected.kind` selects the lane:

```
expected.kind == "ok"     → 4 happy-path checks
expected.kind == "tagged" → 2 refusal checks
```

A candidate that claims `kind="ok"` for a case where `expected.kind
= "tagged"` (or vice versa) fails immediately with `shape:
lied-about-scope`.

## Tolerance regime

| Check | Tolerance | Tool |
|---|---|---|
| `shape` | NONE — type/structure | Python isinstance |
| `each_root_satisfies` | exact symbolic with `1e-9` numerical fallback | sympy.simplify + sympy.radsimp + complex.evalf |
| `count_with_multiplicity` | NONE — integer equality | Python int |
| `distinct_roots_match` | exact symbolic with `1e-9` numerical fallback | sympy bipartite-match |
| `refusal_class_matches` | NONE — exact tag string | Python `==` |

The `1e-9` numerical fallback is calibrated to catch typical
casus-irreducibilis float64 round-off. SymPy's `simplify` is
*conservative* on cube-roots-of-complex expressions — it preserves
`((a + b·i)^(1/3))` rather than reducing to the real value, so the
literal `f.subs(x, root) == 0` test is too strict. The numerical
fallback admits the case when `complex(residue.evalf())` is `0` to
float64 precision.

## Lane: happy-path (4 checks)

### `shape` — structural

- `cand.kind == "ok"`.
- `cand.roots` is a list.
- Each entry is a record with:
  - `root` — string parseable as a SymPy expression in `{var}`.
  - `multiplicity` — positive integer.
- Optional `content`, `method`, `warnings` fields not strictly
  required by the verifier (they're informational).

### `each_root_satisfies` — exact + 1e-9 fallback

For each root entry `(rᵢ, eᵢ)`:

1. Parse `rᵢ` via `sympy.sympify(s, locals={var: x})`.
2. Compute `residue = sympy.simplify(p.subs(x, rᵢ))`.
3. If `residue == 0`: pass.
4. Else compute `residue₂ = sympy.radsimp(residue)`. If `0`: pass.
5. Else evaluate numerically: `n = complex(residue₂.evalf())`. If
   `|n| < 1e-9`: pass (casus-irreducibilis fallback).
6. Else fail.

The verifier reports which root failed and the residue's symbolic
form, to help the agent see what's going wrong.

### `count_with_multiplicity` — exact

```python
sum(entry.multiplicity for entry in cand.roots) == p.total_degree()
```

A bug that drops a root, doubles a multiplicity, or returns
deduplicated roots without the multiplicity field fails this check.

### `distinct_roots_match` — exact + 1e-9 fallback

Compute `sympy_roots = sp.Poly(p, x, domain="QQ").all_roots(multiple=False)`
— a list of `(root_expr, multiplicity)` pairs.

Bipartite matching:

```
available := indices(sympy_roots)
for (rᵢ, eᵢ) in cand.roots:
    matched := None
    for idx in available:
        sr, sm := sympy_roots[idx]
        if sm != eᵢ: continue
        if simplify(rᵢ - sr) == 0: matched := idx; break
        if numerical_close(rᵢ, sr, 1e-9): matched := idx; break
    if matched is None: FAIL
    available.remove(matched)
if available: FAIL  # leftover SymPy roots ⇒ candidate missed some
```

This is the *headline* match — it ensures the candidate's distinct-
root list is *the same multiset* as SymPy's, modulo representation.

## Lane: refusal (2 checks)

### `shape` — structural

- `cand.kind == "tagged"`.
- `cand.tag` is a string starting with `"poly-roots/"`.
- `cand.payload` is a record.

### `refusal_class_matches` — exact

- `cand.tag == expected.tag` (when expected is pinned).
- `cand.payload.detail` is a non-empty string (loose payload predicate
  per ADR-0019 §5).

If `expected.tag` is not pinned, any tag in the `poly-roots/*`
namespace passes.

## Aggregate result

```jsonc
{
  "pass":   true,
  "reason": "all invariants hold",
  "checks": {
    "shape":                  {"pass": true,  "detail": "..."},
    "each_root_satisfies":    {"pass": true,  "detail": "..."},
    "count_with_multiplicity":{"pass": true,  "detail": "..."},
    "distinct_roots_match":   {"pass": true,  "detail": "..."},
    "refusal_class_matches":  {"pass": true,  "detail": "n/a for kind=ok"}
  }
}
```

A check that is "n/a for the lane" reports
`{"pass": true, "detail": "n/a for kind=<kind>"}` so the per-tier
matrix has uniform shape across cases.

## Reproducibility

The reference implementation (`reference/poly_roots_reference.py`)
is deterministic — given the same `(f, var)`, it produces the same
output bytes every run. The verifier's bipartite matching is greedy
and order-independent (multiplicities are exact integers; the
simplify-equality is well-defined).
