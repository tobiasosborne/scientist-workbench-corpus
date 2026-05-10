# Verifier protocol — `bench/real-root-isolate`

This document specifies the exact tolerances and check semantics for
`bench/real-root-isolate/golden/verify.py`. Every check is one of two
flavours: **structural** (boolean / type-shape) or **exact**
(rational / Sturm-sequence count, no floating-point at any step).

Per ADR-0019 §1.

## Top-level dispatch

The verifier reads `{input, candidate, expected, id, tier}` JSON on
stdin, writes `{pass, reason, checks}` on stdout, exits 0 on PASS / 1
on FAIL.

`expected.kind` selects the lane:

```
expected.kind == "ok"     → 4 happy-path checks
expected.kind == "tagged" → 2 refusal checks
```

A candidate that claims `kind="ok"` for a case where `expected.kind ==
"tagged"` (or vice versa) fails immediately with `shape:
lied-about-scope`.

## Tolerance regime

| Check | Tolerance | Tool |
|---|---|---|
| `shape` | NONE — type / structure | Python `isinstance` |
| `each_interval_contains_one_root` | NONE — exact rational + Sturm count | `sympy.Poly.count_roots` + `sympy.Poly.eval` |
| `intervals_disjoint_and_ordered` | NONE — exact rational comparison | `sympy.Rational` `<` / `==` |
| `count_matches_total_real_roots` | NONE — integer equality | `Poly.count_roots()` over ℝ |
| `refusal_class_matches` | NONE — exact tag string | Python `==` |

No floating-point appears anywhere. The bench is **default determinism
tier** (ADR-0015) — bit-identical cross-platform forever.

## Interval shape conventions

The candidate emits a list of intervals matching the SymPy
`Poly.intervals()` shape (also the standard Akritas-Strzebonski VAS
output): each entry is a record `{lo, hi}` with rational-string
endpoints. The semantic is two-shape:

- **Strict open `(lo, hi)`** when `lo < hi` — bracketing one **irrational**
  real root. Neither endpoint coincides with a root of `f`.
- **Singleton `{lo}`** when `lo == hi` — naming one **rational** root
  exactly. `f(lo) = 0` necessarily.

The two-shape split matters because rational roots have an exact
representation (the singleton) while irrational roots only admit
rational-endpoint approximations. Forcing every interval to be strictly
open would require the reference to widen `(r, r)` to `(r − ε, r + ε)`
for some `ε` chosen so no other root lies inside — strictly more work
than emitting `(r, r)` and tagging it as a singleton.

## Lane: happy-path (4 checks)

### `shape` — structural

- `cand.kind == "ok"`.
- `cand.intervals` is a list.
- Each entry is a record with:
  - `lo` — string parseable as `sympy.Rational`.
  - `hi` — string parseable as `sympy.Rational`.
- Optional `method`, `warnings` fields not strictly required (they're
  informational).

### `each_interval_contains_one_root` — exact

For each interval `(lo, hi)`:

- If `lo > hi`: FAIL.
- If `lo == hi`: pass iff `f(lo) == 0` (singleton at a rational root).
- If `lo < hi`: compute the *open* root count
  ```
  n_open = count_roots(lo, hi)              # closed [lo, hi]
         − [f(lo) == 0 ? 1 : 0]              # exclude lo if it's a root
         − [f(hi) == 0 ? 1 : 0]              # exclude hi if it's a root
  ```
  Pass iff `n_open == 1`.

The closed `count_roots(lo, hi)` is SymPy's Sturm-sequence-based
implementation; subtracting the boundary contributions yields the
strict-open count, which is what the open-interval shape claims.

### `intervals_disjoint_and_ordered` — exact

For each adjacent pair `(intervals[i], intervals[i+1])`:

- `intervals[i].hi <= intervals[i+1].lo`.

Equality is permitted because under SymPy's open + singleton convention,
adjacent intervals can share a non-root endpoint as a separator (the
singleton `(0, 0)` and the open `(0, 1)` co-exist with shared boundary
0; SymPy's intervals output for `4x³ − 3x` is exactly
`[(-1, 0), (0, 0), (0, 1)]`). The `each_interval_contains_one_root` and
`count_matches_total_real_roots` checks together rule out double-
counting at shared boundaries.

### `count_matches_total_real_roots` — exact

```python
len(cand.intervals) == p.count_roots()      # over ℝ
```

The Sturm-sequence ground truth for the total real-root count is
`Poly.count_roots()` with no bounds (i.e., `[−∞, +∞]`). A bug that
drops an interval, doubles an interval, or fails to emit a singleton
for a rational root fails this check.

## Lane: refusal (2 checks)

### `shape` — structural

- `cand.kind == "tagged"`.
- `cand.tag` is a string starting with `"real-root-isolate/"`.
- `cand.payload` is a record.

### `refusal_class_matches` — exact

- `cand.tag == expected.tag` (when expected is pinned).
- `cand.payload.detail` is a non-empty string (loose payload predicate
  per ADR-0019 §5).

If `expected.tag` is not pinned, any tag in the
`real-root-isolate/*` namespace passes.

## Aggregate result

```jsonc
{
  "pass":   true,
  "reason": "all invariants hold",
  "checks": {
    "shape":                            {"pass": true, "detail": "..."},
    "each_interval_contains_one_root":  {"pass": true, "detail": "..."},
    "intervals_disjoint_and_ordered":   {"pass": true, "detail": "..."},
    "count_matches_total_real_roots":   {"pass": true, "detail": "..."},
    "refusal_class_matches":            {"pass": true, "detail": "n/a for kind=ok"}
  }
}
```

A check that is "n/a for the lane" reports
`{"pass": true, "detail": "n/a for kind=<kind>"}` so the per-tier
matrix has uniform shape across cases.

## Reproducibility

The reference implementation
(`reference/real_root_isolate_reference.py`) is deterministic — given
the same `(f, var)`, it produces the same output bytes every run
(SymPy `Poly.intervals()` is deterministic). The verifier's checks are
purely rational + Sturm-count operations; no floating-point dependency.
