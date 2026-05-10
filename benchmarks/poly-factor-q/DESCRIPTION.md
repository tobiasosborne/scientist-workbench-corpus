# `poly-factor-q` — context

Univariate polynomial factorization over ℚ is the second algorithmic
tier of the solve epic, after `linsolve-q`. It is the prerequisite
for every higher tier:

- **`tools/poly-roots`** (radicals up to deg-4, `Root[]` for ≥5)
  factors first, then dispatches each factor to its own root-finder.
- **`tools/solve`** univariate path (`fij`) factors, then unions
  the per-factor solution sets.
- **`real-root-isolate`** is faster on factored input
  (Sturm-sequence per factor independently).
- **Algebraic-number arithmetic** (`packages/alg-num`) factors a
  putative minpoly to verify it.

## What this bench earns

Three things, each load-bearing for the rest of the epic:

### 1. The substrate package `packages/poly-factor`

Five exported functions, in dependency order:

| function | issue | what it does |
|---|---|---|
| `squareFree(f)` | `153` | Yun 1976 — `f → [(a_i, i)]` square-free decomp |
| `factorOverFp(f, p)` | new bead under `d0o` | Berlekamp 1967 — `𝔽_p[x]` irreducibles |
| `henselLift(f, g0, h0, p, k)` | `0fy` | Zassenhaus 1969 — `𝔽_p → ℤ/p^k` quadratic |
| `mignotteBound(f)` | inline / `153` | upper bound on |coefs| of any factor of `f` |
| `factorIntZ(f)` | new bead under `d0o` | the full pipeline; uses all four above |

Each is independently testable. The bench is the *integration test*
for the whole pipeline; per-function `bun test` property tests are
the *unit tests*.

### 2. The bench itself — a punishing test surface

Eight tiers (A–H) with deliberate stress on the ways factorization
implementations break:

- **Tier A** (shape edges) — catches "doesn't handle deg-1" and
  "doesn't handle constant".
- **Tier B** (random primitive) — broad smoke.
- **Tier C** (cyclotomic) — catches "factored an irreducible".
  `Φ_30 = x^8 + x^7 − x^5 − x^4 − x^3 + x + 1` is the canonical
  "looks reducible but isn't" example.
- **Tier D** (Swinnerton-Dyer) — catches "exponential
  recombination". The polynomial `S_3` (minimal poly of
  `√2 + √3 + √5`) factors into `2^3 = 8` modular factors, all of
  which must be tried in subsets — naive Zassenhaus is `O(2^8)`
  recombinations. `S_4` is `O(2^16) ≈ 65k` recombinations,
  borderline; `S_5` is `O(2^32)`, untenable. We cap at `n=4` for
  v1 admission, but **demand polynomial-time recombination** —
  vanHoeij-style is required.
- **Tier E** (multiplicities) — catches "merged powers of the
  same factor", "off-by-one multiplicity".
- **Tier F** (large coefficients) — catches "Hensel lifted to
  insufficient `p^k`" and "re-introduced floating point". Mignotte
  bound times two is the floor; tier-F deliberately constructs
  inputs that touch the bound.
- **Tier G** (content) — catches "lost the rational prefactor",
  "absorbed sign into wrong factor".
- **Tier H** (refusal) — catches "silently produced garbage on
  out-of-vocabulary input." Honest scope (Rule 8).

### 3. The bench-discipline template for the rest of the epic

Subsequent solve-tier benches (`poly-roots-radical`,
`real-root-isolate`, `alg-num-arith`, `groebner-basis`,
`groebner-zerodim-extract`, `solve`, `solve-transcendental`)
mirror this structure. By writing it out fully here, the
machinery — generator, verifier, mutation-prove harness, agreement
layer — is exercised at scale before the higher tiers commit.

## Why factorization is hard (a short caveat)

Naive factorization (try every divisor) is super-exponential in
the degree. The algorithmic chain `mod-p → Hensel → recombine` is
folklore; what makes it *practical* is:

- **Mignotte bound (1974)** — every factor of `f ∈ ℤ[x]` has
  coefficients bounded by `2^{deg(f)} · |f|_∞ · √(deg(f)+1)`,
  give or take. This bounds the lift target precision: pick `p`
  and `k` so that `p^k > 2 · M(f)`, and any modular-factor
  recombination that "looks like" a true factor (test by trial
  division) IS one.
- **Berlekamp (1967)** — factoring over `𝔽_p` reduces to linear
  algebra in `𝔽_p^{n}`, polynomial-time.
- **Hensel (1918, Zassenhaus 1969 quadratic variant)** — modular
  factors over `𝔽_p` lift uniquely to `ℤ/p^k` for any `k`,
  given coprimality of the initial pair.
- **vanHoeij (2002), Hart-vanHoeij-Novocin (2011)** — recombination
  via lattice reduction; rather than try all `2^r` subsets of mod-`p`
  factors, build an LLL-reducible knapsack lattice whose short
  vectors *are* the true integer factors. Polynomial in `r`.

Without (4), Tier D is exponential. With (4), it's polynomial. The
bench structure forces the implementation to use it.

## What ships when this bench is green

- `packages/poly-factor` — substrate package, five functions
- `tools/poly-factor` — seven-artefact tool consuming
  `record{f: expression, var: symbol}` and emitting
  `record{content, factors: list, method, warnings}`
- ~60 golden cases triple-witnessed by Wolfram + SymPy +
  (when available) Sage
- A `golden/test_mutations.py` proving the verifier catches
  ≥5 characteristic regressions

## Related benches and dependencies

- **Upstream**: `linsolve-q` (already shipped); `cas-core::polyGcd`
  (ADR-0013, already shipped); `cas-core::content/primitive` —
  small extension to existing rational-polynomial machinery.
- **Downstream** (blocked on this): `tools/poly-roots` (`58q`,
  `yoc`); `tools/solve` univariate path (`fij`); `bench/poly-roots-radical`.
- **Sibling phase** (parallel): `packages/groebner` (`8y8`, `fcf`,
  `9du`, ...) — Phase 4 (multivariate). Independent, can develop
  in parallel.

See `docs/worklog/050-integrate-ode-symplectic.md` and the
linsolve-q P0..P1 worklog (next worklog shard, when written) for
the full bench-first methodology.
