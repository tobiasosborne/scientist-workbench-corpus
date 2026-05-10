# Bench — `poly-factor-q` (Univariate polynomial factorization over ℚ)

## ⚠ How you will be graded

You will be graded on **EXACT IRREDUCIBLE FACTORIZATION**. No
tolerances — every coefficient is in ℚ, every factor must be
irreducible over ℚ, and the verifier checks **exact bit-equality**
of `prod_i factor_i^multiplicity_i` to the input polynomial via
exact-rational polynomial arithmetic. A candidate that is "almost
right" (off by content, missing a factor, returning a reducible
factor) is wrong.

This bench is the **floor**, not the ceiling. Passing it is
necessary but not sufficient. The tool must also conform to the
scientist-workbench seven-artefact contract (CLAUDE.md, README.md,
PRD-v0.2.md).

## Problem statement

Implement the exact univariate factorization over ℚ:

  given `f ∈ ℚ[x]` of positive degree, return the unique (up to
  ordering) factorization

      f = c · ∏_i p_i(x)^e_i

  where `c ∈ ℚ` is the rational content × leading-sign, each
  `p_i ∈ ℤ[x]` is **irreducible over ℚ**, **primitive**
  (`gcd(coefs) = 1`), and **positive-leading-coefficient**, and
  `e_i ≥ 1` is the multiplicity.

The constant prefactor `c` carries the sign and rational content;
all symbolic structure is in the `p_i`. This canonical form is
unique modulo factor-list permutation.

### Algorithm path (recommendation)

Standard pipeline (Cox-Little-O'Shea Ch. 4; Geddes-Czapor-Labahn
Ch. 8):

1. **Content/primitive split** — `f = c · pp(f)` where `c =
   content(f) ∈ ℚ` and `pp(f) ∈ ℤ[x]` is primitive.
2. **Square-free decomposition** — Yun 1976, splits `pp(f)` into
   `∏_i a_i(x)^i` with each `a_i` square-free and pairwise-coprime.
   `gcd`-only; trivial given `polyGcd` in `cas-core` (ADR-0013).
3. **Factor each `a_i` over `𝔽_p`** for a "lucky prime" `p` —
   Berlekamp 1967 or Cantor-Zassenhaus 1981 (we recommend
   Berlekamp for primes up to ~100, distinct-degree+equal-degree
   for larger primes).
4. **Hensel lift** the `𝔽_p` factorization to `ℤ/p^k` for
   `p^k > 2 · M(f)` where `M(f)` is the Mignotte coefficient
   bound — Zassenhaus 1969 (quadratic Hensel; doubling-precision
   per step).
5. **Recombine** `ℤ/p^k` factors back to ℤ — naive subset-sum
   (`O(2^r)` for `r` mod-`p` factors) or Hart-vanHoeij-Novocin 2011
   (LLL-based knapsack, polynomial-time). We require the v1
   implementation to handle Tier D (Swinnerton-Dyer max-`r` cases)
   in polynomial time, which means **vanHoeij-style lifting** is
   not optional for v1 admission.

Local sources: `docs/ground-truth/factor/berlekamp-1967.pdf`,
`docs/ground-truth/factor/vanhoeij-2002-knapsack.pdf`,
`docs/ground-truth/factor/hart-vanhoeij-novocin-2011.pdf`,
`docs/ground-truth/factor/cox-little-oshea-ideals-varieties-algorithms-4th.pdf`
(Ch. 4). The Mignotte 1974 paper is not staged locally
(AMS Cloudflare-walled even via Wayback); the bound is reproduced
in CLO §4.5 and Geddes Ch. 8.

The substrate is `packages/cas-core`'s `Q[x]` polynomial
arithmetic plus a new `packages/poly-factor` package layered on
top.

### Why exact factorization, not approximate root-finding

Two distinct problems:

| Problem | Tool | Output |
|---|---|---|
| Factor `f ∈ ℚ[x]` into ℚ-irreducibles | `poly-factor-q` (this bench) | `{c, [(p_i, e_i)]}` with each `p_i ∈ ℤ[x]` irreducible over ℚ |
| Find numerical roots of `f ∈ ℝ[x]` | `linalg-solve` companion via companion matrix | `[float64]` |
| Find symbolic roots of `f ∈ ℚ[x]` of degree ≤ 4 | `tools/poly-roots` (P3) | `[expression]` (Cardano/Ferrari radicals) |
| Isolate real roots of `f ∈ ℚ[x]` of any degree | `real-root-isolate` (P3) | `[(a, b)] : Q × Q` |

Factorization is the prerequisite for all of these — knowing
`f = (x²+1)(x-2)` reduces root-finding from "degree 3" to
"degree 1 and degree 2 separately." It is also load-bearing for
the `tools/solve` dispatcher's univariate path (`fij`).

## I/O contract (JSON)

### Bench wire format

Raw JSON. Adapter `bench/poly-factor-q/run-candidate.ts` bridges
to the tool's canonical `Value` protocol.

### Input (one JSON object on stdin)

```jsonc
{
  "f":    "x^4 - 5*x^2 + 4",   // polynomial, ASCII expression syntax
  "var":  "x"                  // free variable
}
```

Coefficients in `f` are integers or rationals (e.g., `2/3`); the
operators `+ - * / ^` are accepted with conventional precedence.
The expression must parse to a univariate polynomial in `var`
over ℚ; non-polynomial input is malformed.

### Output (one JSON object on stdout)

```jsonc
{
  "content":   "1",            // rational, canonical form (the c above)
  "factors":   [
    {"factor": "x - 1", "multiplicity": 1},
    {"factor": "x + 1", "multiplicity": 1},
    {"factor": "x - 2", "multiplicity": 1},
    {"factor": "x + 2", "multiplicity": 1}
  ],
  "method":   "berlekamp-vanhoeij",
  "warnings": []
}
```

Constraints on the output:

- **`content`** — a canonical-form rational string (no leading
  `+`, no whitespace, denominator stripped if `1`, sign on
  numerator, lowest terms).
- **`factors[i].factor`** — an ASCII expression in `var`, parses
  to a polynomial in `ℤ[x]` that is **primitive**
  (`gcd(coefs) = 1`) and has **positive leading coefficient**.
  Each factor must be **irreducible over ℚ**.
- **`factors[i].multiplicity`** — a positive integer.
- The factor list is **deduplicated** — the same `(factor,
  multiplicity)` pair never appears twice. Different
  multiplicities of the same factor are illegal (combine).
- The factor list is **sorted** in canonical order (by degree
  ascending, then lexicographic on coefficient sequence). The
  verifier accepts any order, but the recommended canonical
  emission matches the test set's `expected.json`.
- **`method`** is informational; `"berlekamp-vanhoeij"` is the
  recommendation for v1.

### Refusal class

A single boundary tag:

- `tagged "poly-factor-q/non-polynomial"` — `f` does not parse
  as a polynomial in `var` over ℚ (e.g., contains `sin(x)`,
  `1/x`, `sqrt(x)`, or symbols other than `var`). Refusing here
  is honest scope.

`ToolError` (process exit 1) is reserved for *malformed* input:
`var` not a symbol, `f` not a string, the expression fails to
parse.

## Invariants the verifier checks

The verifier runs **5 independent checks** per case
(ADR-0019 §1; issue `3s2`):

1. **`shape`** — `content` parses as a rational; `factors` is a
   list of `{factor, multiplicity}` records; each `factor` parses
   as a polynomial in `var` over ℤ; each `multiplicity` is a
   positive integer; required fields present per kind.
2. **`product_equals_input`** — `content × ∏_i factor_i^multiplicity_i`
   equals `f` **exactly** as polynomials in ℚ[x]. Computed by
   exact-rational expansion via SymPy; coefficient-vectors must
   match bit-equally.
3. **`each_factor_irreducible`** — for every `factor_i`,
   `sympy.Poly(factor_i, var, domain="QQ").is_irreducible` is
   `True`. Delegated to SymPy as the ground-truth oracle for
   irreducibility (ADR-0019 §6).
4. **`factors_primitive`** — each `factor_i` has integer
   coefficients with `gcd(coefs) = 1`. (`gcd` via the absolute
   values; the all-zero case is excluded by being a polynomial of
   positive degree.)
5. **`factors_positive_leading`** — the leading coefficient of
   every `factor_i` (in canonical degree-descending form) is
   strictly positive.

These five are **necessary AND sufficient** for a valid exact
factorization in canonical form. Two outputs that pass all five
checks describe the same factorization modulo factor-list ordering.

## Test set tiers

`golden/inputs.json` contains **~60 cases** spanning seven tiers
(per ADR-0019 §7):

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 6 | `5` (constant; refuses or content-only), `x` (deg-1, irreducible), `x − 3`, `2*x + 4` (content extraction), `x^2 + 1` (irreducible deg-2 over ℚ), `x^2 − 4` (deg-2 splits) |
| B. random low-degree primitive | 12 | `n ∈ {2, 3, 4, 5, 7, 10}` random small-coefficient primitive ℤ[x]; coefficients in `[-9, 9]`; mixed reducible / irreducible |
| C. cyclotomic Φ_n | 8 | `Φ_n` for `n ∈ {3, 5, 7, 8, 12, 15, 24, 30}`. All irreducible over ℚ — single-factor outputs. Catches "factored too aggressively" bugs |
| D. Swinnerton-Dyer max-`r` | 6 | minimal poly of `√p_1 + … + √p_n` for `n ∈ {2, 3, 4}`. Each is irreducible over ℚ but factors into `2^n` pieces mod every prime — naive Zassenhaus recombination is `O(2^{2^n})`. **Polynomial-time recombination is required.** |
| E. square-laden (multiplicities) | 8 | `(x − 1)^k` for `k ∈ {2, 3, 5, 7}`; `(x²+1)^3 · (x−2)^2`; `(x−1)^2 · (x+1)^2 · (x²+x+1)`; deg-15 mixed-multiplicity; nested squares-of-squares |
| F. large coefficients (Mignotte stress) | 6 | `f = ∏ (x − a_i)` with `a_i ∈ [-50, 50]` random integers (deg 8, 12, 15) — pre-multiplication, post-expansion coefficients near `2^{deg · log₂ max(a_i)}`; plus deliberate Mignotte-bound-touching cases |
| G. content / scaling | 6 | `7 · (x − 1)`, `(2/3)·(x²+x+1)`, `−5·(x³−1)`, `0` (degenerate; refuses), large-content `1024·(x²+1)`, and a scaled square-laden `−18·(x−1)^3·(x²+1)` |
| H. refusal class | 4 | non-polynomial input (`sin(x)`, `1/x`, `sqrt(x)`, mixed-vars `x*y`) → `tagged "poly-factor-q/non-polynomial"` |

Total: **~56 cases × 5 checks = ~280 invariant assertions**
(refusal-class cases skip the polynomial-shape checks; counted at
1 check each).

## Verifying your solution

```sh
bash bench/infra/run-bench.sh \
    bench/poly-factor-q bun bench/poly-factor-q/run-candidate.ts
```

### Files

- `golden/inputs.json` — every test case.
- `golden/expected.json` — reference outputs from SymPy +
  Wolfram (triple-witnessed per ADR-0019; provided for sanity-
  checking, not consulted by the verifier).
- `golden/verify.py` — the 5-check invariant verifier.
- `golden/verifier_protocol.md` — exact specifications per check.
- `golden/generate.py` — reproducible golden generation.
- `golden/test_mutations.py` — mutation-prove harness (≥5 RED
  perturbations of the reference per ADR-0019 §4).
- `reference/poly_factor_q_reference.py` — Python reference
  (SymPy `Poly.factor_list` over ℚ).
- `run-candidate.ts` — wire-format adapter to `tools/poly-factor`.

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun. No FFI.
- Seven-artefact contract.
- Default determinism tier (symbolic, bit-identical cross-platform
  forever — no `numerical: true` annotation; ADR-0015).
- Boundary categories (ADR-0003):
  - `tagged "poly-factor-q/non-polynomial"` for non-polynomial
    input. Payload: `{ detail: "<offending term>" }`.
  - `ToolError` for malformed input: `f` not a string, `var` not
    a single-symbol expression, parse failures, multivariate
    polynomial.
- Substrate: new `packages/poly-factor` package layered on
  `@workbench/cas-core`'s `Q[x]` polynomial arithmetic. Square-free
  decomposition (Yun) and Hensel lifting (Zassenhaus quadratic) are
  prerequisite substrate beads (`153`, `0fy`).
- The factor-list is canonicalised (sorted by degree ascending,
  lex-coefficients on tie); the verifier accepts any order, but
  the bench's `expected.json` is deterministic for diff-friendly
  golden refresh.

## What you must do

1. Read `docs/adr/0017-solution-set-shape.md`,
   `docs/adr/0019-solve-bench-discipline.md`, the local Berlekamp,
   van Hoeij, Hart-vanHoeij-Novocin, and Cox-Little-O'Shea PDFs
   under `docs/ground-truth/factor/`.
2. Implement `squareFree(f)` in `packages/poly-factor/src/
   squarefree.ts` (issue `153`).
3. Implement `henselLift(f, g0, h0, p, k)` in `packages/poly-factor/
   src/hensel.ts` (issue `0fy`).
4. Implement `factorOverFp(f, p)` (Berlekamp; new bead) and
   `recombine(modular_factors, mignotte_bound)` (vanHoeij; new bead).
5. Implement `tools/poly-factor/` to seven-artefact contract
   (issue `d0o`).
6. Run bench until 100% across 5 checks across all 8 tiers.
7. Run `bun run check`.
8. Report per-tier-per-check totals.

## What this bench does NOT cover

- **Factorization over fields beyond ℚ** (no `𝔽_p[x]`, no
  `ℚ(α)[x]` algebraic extensions). The `𝔽_p[x]` factorization is
  *internal* to the algorithm; we do not expose it as a tool.
  Algebraic-extension factoring is a P3 bead after `packages/
  alg-num` ships.
- **Multivariate factorization.** Strictly univariate. Multivariate
  factor and Groebner-basis routes are separate phases (P4).
- **Numerical or approximate factorization.** All arithmetic is
  exact rational; numerical-root-finding is `linalg-solve` /
  `tools/poly-roots`.
- **Specialised structures** (e.g., factor over cyclotomic,
  Galois-group-aware) — out of scope for v1.
