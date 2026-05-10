# Bench `poly-roots-radical` — algorithm + invariants

This document expands `PROMPT.md` with per-degree algorithm specs,
the verifier-invariant set, and the boundary cases that distinguish
honest refusal from wrong answer.

## Algorithm — factor + per-degree dispatch

`tools/poly-roots` (worklog 053) composes `tools/poly-factor` with
closed-form formulas. Given `f ∈ ℚ[x]`:

1. Call `tools/poly-factor` to get `(c, [(pᵢ, eᵢ)])` where each `pᵢ`
   is monic + irreducible over ℚ.
2. Per factor `(pᵢ, eᵢ)`:
   - `deg(pᵢ) = 1` → **linear root** `−b/a`. Rational, exact.
   - `deg(pᵢ) = 2` → **quadratic formula** `(−b ± √(b² − 4ac)) / (2a)`.
     Real if `Δ_q ≥ 0`; complex via `√(negative)` if `Δ_q < 0`.
   - `deg(pᵢ) = 3` → **Cardano 1545**. Faithful complex form per
     ADR-1yu — see "Casus irreducibilis" below.
   - `deg(pᵢ) = 4` → **Ferrari 1540**. Biquadratic fast path when
     `b = d = 0`.
   - `deg(pᵢ) ≥ 5` (all real roots) → **`Root[poly, k]` per real root**
     (ADR-0018; bead `yoc`). One `Root[]` value per real root,
     ascending by real value; method = `factor-then-radicals-or-root`.
   - `deg(pᵢ) ≥ 5` (mixed-real-complex) → **refusal**
     `tagged "poly-roots/complex-roots-not-yet-named"` —
     alg-num v0.1 names real algebraic numbers only.
3. Each root inherits the factor's multiplicity `eᵢ` from step 1.

### Casus irreducibilis (ADR-1yu)

A cubic with three distinct real roots and discriminant `Δ_c < 0`
sits in *casus irreducibilis* — the Cardano formulas produce roots
involving `√(negative)` and `^(1/3)` of complex numbers. The classical
workaround is to detect `Δ_c < 0` and switch to a trigonometric
formula `2√(−p/3) · cos(θ + 2πk/3)`.

**The workbench rejects this switch.** `tools/poly-roots` emits the
faithful complex-radical form even when it numerically evaluates to
NaN (because the closed vocabulary doesn't include `cos`, `acos`, or
real/imaginary projection). The result expressions are
*syntactically valid* in the closed numerical vocabulary; downstream
`ToReal` simplification can recover real values when called for. Per
ADR-1yu this is the price of keeping the symbolic-radical contract
honest — no silent dispatch to a different formula shape.

The verifier handles this by adding a numerical-evaluation fallback:
when `sympy.simplify(f.subs(x, root))` is non-zero, evaluate the
residue numerically and accept if `|residue| < 1e-9`. SymPy's
`simplify` is *conservative* on cube-roots-of-complex expressions
(it preserves the form `((a + b·i)^(1/3))` rather than simplifying
to a real); the numerical check is the principled tiebreaker.

### Multiplicity discipline

A double root `(x − 1)² = 0` produces ONE entry
`{root: "1", multiplicity: 2}` in the candidate list. This is the
poly-roots convention — distinct roots + multiplicity field. (The
sister convention in `tools/solve`'s univariate-poly lane is flat
repetition: two entries each with multiplicity 1; that's load-bearing
for ADR-0017's `Solution { bindings, branches }` shape, where each
solution is a distinct binding-set.)

## Verifier — the 4 checks

Every happy-path case runs all four checks (refusal cases run two).
Per `verifier_protocol.md`:

### `shape` — structural

- `kind == "ok"`.
- `roots` is a list; each entry is a record with string `root` and
  positive integer `multiplicity`.
- Each `root` parses via `sympy.sympify(s, locals={var: x})`.

### `each_root_satisfies` — exact (with numerical fallback)

For each `(rᵢ, eᵢ)`:

1. Compute `residue = sympy.simplify(f.subs(x, rᵢ))`.
2. If `residue == 0`: pass.
3. Else compute `residue₂ = sympy.radsimp(residue)`. If `0`: pass.
4. Else evaluate numerically via `complex(residue₂.evalf())`. If
   `|residue₂| < 1e-9`: pass (casus-irreducibilis fallback per
   ADR-1yu).
5. Else fail.

The three layers of simplification handle:
- Rational roots (immediately simplify to 0).
- Quadratic / quartic radicals (`radsimp` flattens `√` chains).
- Casus-irreducibilis cubics (`evalf` numerical confirmation).

### `count_with_multiplicity` — exact

`sum(multiplicityᵢ) == p.total_degree()`. Catches "dropped a root"
and "wrong multiplicity" mutations.

### `distinct_roots_match` — exact (bipartite)

Compute `sympy_roots = Poly(p, x).all_roots(multiple=False)` — a list
of `(root_expr, multiplicity)` pairs SymPy considers canonical.
Bipartite-match each candidate `(rᵢ, eᵢ)` to a unique SymPy entry
under:
- `multiplicity` exact equality.
- `simplify(rᵢ − sr) == 0` OR numerical fallback `|...| < 1e-9`.

The match is greedy (works on distinct multiplicities; SymPy
deduplicates roots by the same convention as the candidate).

## Mutation-prove harness

Per ADR-0019 §4, ≥ 5 perturbations of the reference. This bench
ships **8** (each demonstrates RED on the labelled check):

1. `dropped_root` — pop one entry from the biquadratic factorisation
   ⇒ count_with_multiplicity.
2. `wrong_multiplicity` — claim multiplicity 1 for `(x−1)²` ⇒ count.
3. `wrong_root_value` — replace one biquadratic root with `5` ⇒
   each_root_satisfies.
4. `added_spurious_root` — append a fake root ⇒ count + each_root +
   distinct_match.
5. `lied_about_scope` — for deg-5 input, fabricate an `ok` envelope
   instead of refusing ⇒ shape.
6. `wrong_refusal_tag` — refuse with `non-polynomial` instead of
   `complex-roots-not-yet-named` ⇒ refusal_class_matches.
7. `casus_irreducibilis_wrong` — swap one root of `x³ − 3x + 1`
   for `1` (NOT a root) ⇒ each_root_satisfies (exercises the
   numerical fallback layer).
8. `zero_multiplicity` — multiplicity = 0 ⇒ shape.

GREEN baseline 5/5 + RED mutations 8/8 = verifier sensitive.

## Tier-by-tier rationale

- **A. linear (deg 1).** The dispatcher's simplest path; catches
  off-by-one in the rational division.
- **B. quadratic.** Discriminant sign + square-root extraction. Two
  irreducible cases (`x² + 1`, `x² + x + 1`) verify the imaginary
  unit / cube-root-of-unity emission. Double-root case verifies the
  multiplicity field.
- **C. cubic.** Cardano's three Δ_c regimes:
  - Δ_c > 0 → one real, two complex.
  - Δ_c = 0 → repeated root + simple root.
  - Δ_c < 0 → three real (casus irreducibilis).

  Two casus cases confirm the faithful-complex-form behaviour
  numerically passes the verifier's `radsimp` + `evalf` chain.
- **D. quartic Ferrari.** Biquadratic fast path tested separately
  from the general resolvent-cubic path. `(x² − 1)²` and
  `(x − 1)⁴` exercise the multiplicity-with-quartic-degree
  combinations.
- **E. reducible.** The headline test of *factor first, then radicals*:
  inputs that produce mixed-degree factor lists. A bug that handled
  each factor type but mis-composed the multiplicity (e.g., emitted
  factor's roots without the outer multiplicity weight) would fail
  Tier E but pass Tiers A-D.
- **F. numeric stress.** Large/small/mixed-denominator coefficients
  exercise BigInt-rational arithmetic and the `valueToRatFn` content
  extraction. Near-zero discriminant cases verify that
  `tools/poly-factor` doesn't hit a "division-by-zero" precision bug.
- **G. refusals.** Three deg ≥ 5 cases (Eisenstein quintic with
  1 real + 4 complex roots, irreducible sextic with 0 real roots,
  Φ_7 cyclotomic with 0 real roots) verify the
  `complex-roots-not-yet-named` tag fires consistently. After bead
  `yoc` (alg-num v0.1) shipped, an all-real deg-≥5 polynomial emits
  `Root[poly, k]` values directly; this bench's G-tier cases are
  intentionally mixed-real-complex to exercise the remaining
  refusal. Two non-polynomial cases (`sin(x)`, `1/x + x`) and one
  multivariate (`x · y`) verify the per-class tag dispatch.

## Sources cited

- **ADR-0019** — bench discipline.
- **ADR-1yu** — casus irreducibilis: faithful complex form.
- **Cardano 1545** *Ars Magna* — cubic formula. Reproduced in any
  modern algebra textbook.
- **Ferrari 1540** — quartic via resolvent cubic. Same.
- **Galois 1832** — Abel-Ruffini theorem (no general radical formula
  for deg ≥ 5). Why deg ≥ 5 routes through `Root[poly, k]` (ADR-0018)
  rather than radicals.
- **ADR-0018** — `Root[poly, k]`: the value-protocol primitive
  naming algebraic numbers by `(minpoly, k)` for deg-≥5 (substrate
  shipped via beads `xyt`, `xkz`, `6cd`, `rti`; consumer wired via
  `yoc`).
- **`docs/worklog/053`** — the implementation shard of `tools/poly-roots`.
- **`bench/poly-factor-q`** — substrate bench (factor list invariants
  this bench composes).

## Sources NOT used (and why)

- **Trigonometric Cardano formula** — gives real values for casus
  irreducibilis without complex radicals, but introduces `cos`, `acos`,
  `arctan` heads outside the closed vocabulary. Per ADR-1yu we accept
  the symbolic-but-numerically-NaN faithful complex form.
- **`Root[poly, k]`** for *complex* algebraic numbers — Mathematica's
  full algebraic-number representation. Real `Root[]` shipped via
  bead `yoc` (alg-num v0.1, this bench's deg-≥5 path). Complex
  `Root[]` requires planar complex-root isolation
  (Pinkert / Pan-Sevriuk) and is a future shard. Until then, the
  G-tier mixed-real-complex inputs refuse honestly.
- **Numerical eigenvalue methods** (companion matrix → `linalg-solve`)
  — that's a different problem (approximate roots over ℝ).
- **Sturm sequences for real-root counting** — bench `q8q`
  (`bench/real-root-isolate`) is the dedicated home.
