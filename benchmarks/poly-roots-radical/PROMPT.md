# Bench — `poly-roots-radical` (radical roots of univariate ℚ[x] for deg ≤ 4)

## ⚠ How you will be graded

You will be graded on **exact symbolic root correctness**. Every root
must substitute back into the input polynomial and reduce to `0`
under `sympy.simplify`. Every claimed root must pair with a SymPy
`Poly.all_roots()` entry, and the multiplicity counts must sum to the
input polynomial's total degree. No tolerances on the symbolic
checks; a small numerical fallback is allowed only for casus-
irreducibilis-style complex-radical roots that `simplify` is
conservative on (per ADR-1yu).

This bench is the floor for `tools/poly-roots`. Passing it is
necessary but not sufficient — the seven-artefact contract still
applies.

## Problem statement

Implement the symbolic radical root-finder for univariate polynomials
over ℚ of degree ≤ 4:

  given `f ∈ ℚ[x]` of degree `1 ≤ d ≤ 4` (or reducible into factors
  each of degree ≤ 4), return `[(rᵢ, eᵢ)]` where each `rᵢ` is an
  expression Value in the closed numerical vocabulary
  `+ − * / ^ neg sqrt`, and `eᵢ ≥ 1` is the multiplicity. The roots
  are exact symbolic — `(1 + √5)/2`, not `1.618...` — composable with
  `cas-diff`, `integrate-1d`, and the rest of the symbolic stack.

### Algorithm path

`tools/poly-roots` (worklog 053):

1. **Factor** via `tools/poly-factor` into ℚ-irreducibles
   (`f = c · ∏ pᵢ^{eᵢ}`).
2. **Per factor**, dispatch by degree:
   - deg 1 → linear root (`−b/a`)
   - deg 2 → discriminant formula `(−b ± √(b²−4ac))/(2a)`
   - deg 3 → Cardano 1545; **faithful complex form** in casus
     irreducibilis (Δ_c < 0, three real roots) per ADR-1yu — no
     trigonometric switch
   - deg 4 → Ferrari 1540 + biquadratic fast path
   - deg ≥ 5 (all real roots) → `Root[poly, k]` per real root (ADR-0018);
     workbench tool emits `deg` `Root[]` values in canonical sort
     order with `method = "factor-then-radicals-or-root"`.
   - deg ≥ 5 (one or more complex roots) →
     `tagged "poly-roots/complex-roots-not-yet-named"` —
     alg-num v0.1 names real algebraic numbers only.

### Why factor first

`tools/poly-factor` already handles content extraction, square-free
decomposition (Yun), and full Berlekamp-Zassenhaus. Factoring first
makes every irreducible piece monic + irreducible over ℚ — no
"is this reducible?" branching in the radicals layer. Repeated roots
are accounted for via the multiplicity field.

## I/O contract

### Input

```jsonc
{
  "f":   "x^4 - 5*x^2 + 4",  // polynomial in v over ℚ
  "var": "x"
}
```

### Output

```jsonc
{
  "kind":     "ok",
  "content":  "1",                  // leading rational coef (informational)
  "roots":    [
    {"root": "1",  "multiplicity": 1},
    {"root": "-1", "multiplicity": 1},
    {"root": "2",  "multiplicity": 1},
    {"root": "-2", "multiplicity": 1}
  ],
  "method":   "factor-then-radicals",
  "warnings": []
}

// or
{
  "kind": "tagged",
  "tag":  "poly-roots/<class>",
  "payload": {"detail": "..."}
}
```

Refusal classes (per `tools/poly-roots/tool.ts`):

- `poly-roots/complex-roots-not-yet-named` — irreducible factor of
  degree ≥ 5 has one or more *complex* roots; alg-num v0.1 names
  real algebraic numbers only.
- `poly-roots/non-polynomial`  — `f` is not a polynomial in `var` over ℚ.
- `poly-roots/multivariate`    — `f` mentions a non-`var` symbol.

Each entry's `root` is an expression-string in the closed vocabulary
(`+ − * / ^ neg sqrt`); the verifier parses it via SymPy. The
candidate emits ONE entry per *distinct* root with a multiplicity
field (vs `tools/solve`'s flat-with-repetition shape).

## Invariants the verifier checks

Per ADR-0019 §1, four checks per happy-path case + two for refusals:

### Happy-path (`kind = "ok"`)

1. **`shape`** — kind="ok", roots is a list of `{root, multiplicity}`,
   each root parses via SymPy in `{var}`, each multiplicity is a
   positive integer.
2. **`each_root_satisfies`** — `sympy.simplify(f.subs(x, root)) == 0`
   for every root. Numerical fallback `|f(root)| < 1e-9` accepted for
   casus-irreducibilis radicals SymPy is conservative on (ADR-1yu).
3. **`count_with_multiplicity`** —
   `Σ multiplicityᵢ = total_degree(p)`.
4. **`distinct_roots_match`** — bipartite-match of `(root,
   multiplicity)` pairs against `sp.Poly(p, x, domain="QQ")
   .all_roots(multiple=False)`. Each candidate pair maps to exactly
   one SymPy pair under `simplify`-equality on root and exact
   multiplicity equality.

### Refusal (`kind = "tagged"`)

1. **`shape`** — tag in `poly-roots/*` namespace; payload is a record.
2. **`refusal_class_matches`** — exact tag string match;
   `payload.detail` is non-empty.

These five checks are **necessary AND sufficient** for a valid
radical-root output modulo representation equivalence.

## Test set tiers

`golden/inputs.json` contains **50 cases**:

| Tier | Cases | What it probes |
|---|---|---|
| **A. linear (deg 1)** | 6 | `x`, `x − 3`, `2x + 4` (content), `3x + 1` (rational), `x/2 − 1/3` (rational coefs), `−x + 5` (negative leading) |
| **B. quadratic** | 8 | rational roots `(2, 3)`, irrational `±√2`, golden ratio min poly, complex roots `±i, ω`, double root, double zero, shifted complex |
| **C. cubic** | 8 | rational 3-roots, cyclotomic `x³−1`, depressed real, **two casus irreducibilis** (`x³−3x+1`, `x³−7x+6`), 1-real-2-complex, triple root, content cubic |
| **D. quartic (Ferrari)** | 8 | biquadratic real & no-real, depressed Ferrari, cyclotomic `x⁴−1`, quadruple root, two double roots, perfect-square-quadratic, Ferrari resolvent stress |
| **E. reducible** | 6 | linear×quadratic, two irreducible quads, quad×casus-cubic, three linears, mixed-multiplicity, difference-of-squares quartic |
| **F. numeric stress** | 8 | large coefs, tiny rational coefs, near-zero discriminant, large-cubic, biquadratic spread, content-12, mixed denoms |
| **G. refusals** | 6 | deg-5 Eisenstein (1 real, 4 complex), deg-6 irreducible (0 real), Φ_7 cyclotomic (0 real), sin(x), `1/x + x`, multivariate `x·y`. *(All deg-≥5 G-cases have complex roots; alg-num v0.1 refuses these. An all-real deg-≥5 polynomial would emit `Root[]` values per ADR-0018 / bead `yoc`.)* |

**50 cases × ≤4 happy-path checks + ≤2 refusal checks ≈ 200 invariant
assertions.**

## Triple-witness oracle protocol

Per ADR-0019 §3, golden cases are admitted iff ≥ 2 of 3 oracles agree.
The oracles in priority order:

1. **`wolframscript`** via `Solve[f == 0, var]`
   (`bench/_corpus/oracle/wolfram.py`).
2. **SymPy** via `Poly.all_roots(multiple=False)`
   (the bench's reference implementation).
3. SageMath when available — preferred third witness for casus-
   irreducibilis cubic roots (Sage's `qqbar` ring resolves these
   without the complex-radical workaround).

For tier-G refusals, the workbench's bounded-scope refusal at v0.2
(deg ≥ 5 with complex roots) is admitted even when Wolfram solves
with `Root[]` — that's the honest "real algebraic numbers only in
alg-num v0.1; complex Root[] is a future shard" boundary. The oracle
log records this as `wolfram-solved-workbench-bounded-scope`.

After bead `yoc` shipped, an all-real deg-≥5 input emits `Root[]`
values directly; this bench's G-tier cases are intentionally
mixed-real-complex to exercise the remaining refusal.

## Verifying your solution

```sh
# Generate goldens (slow; full triple-witness via wolframscript).
python3 bench/poly-roots-radical/golden/generate.py

# Fast iteration with SymPy-only:
WB_LIVE_ORACLE=0 python3 bench/poly-roots-radical/golden/generate.py

# Mutation-prove gate:
python3 bench/poly-roots-radical/golden/test_mutations.py
```

### Files

- `golden/inputs.json` — 50 test cases.
- `golden/expected.json` — reference outputs.
- `golden/oracle_log.json` — triple-witness consensus per case.
- `golden/verify.py` — 4-check (happy) / 2-check (refusal) verifier.
- `golden/verifier_protocol.md` — exact specifications per check.
- `golden/generate.py` — reproducible golden generation.
- `golden/test_mutations.py` — mutation-prove harness (≥5 RED).
- `reference/poly_roots_reference.py` — SymPy-backed reference.

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun. No FFI.
- Default determinism tier (symbolic, bit-identical cross-platform
  forever — no `numerical: true`).
- Output shape per `tools/poly-roots/tool.ts` (ADR-1yu's
  faithful-complex-form discipline).
- Closed-vocabulary roots only: `+ − * / ^ neg sqrt`. No
  trigonometric heads, no `Root[]` (deferred to bead `yoc`).
- Boundary categories per ADR-0003: tagged refusals listed above;
  `ToolError` for malformed input only (`f` not a string, `var` not
  a symbol, parse failures, zero polynomial).

## What this bench does NOT cover

- **Roots in non-radical form** (`Root[poly, k]`) — bead `yoc`.
- **Algebraic-number arithmetic** between roots — `packages/alg-num`
  stack (`xyt → xkz → 6cd → rti → 5i2`).
- **Real-root isolation** of high-degree polynomials — bead `q8q`.
- **Multivariate roots** — out of scope (Gröbner stack pending).
- **Numerical / approximate roots** — `tools/linalg-solve` covers the
  float64 case via the companion-matrix path.
