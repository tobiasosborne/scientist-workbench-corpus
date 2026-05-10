# Bench `solve` — algorithm + invariants

This document expands the `PROMPT.md` brief with per-lane
algorithm specs, the verifier-invariant set, and the boundary cases
that distinguish "honest refusal" from "wrong answer."

## The four lanes

`tools/solve`'s body (after equation-to-`Poly` reduction) classifies
the input via `@workbench/solve::classifyInput` into one of:

- `linear` — total degree ≤ 1 in `vars` *and* no cross-variable
  products. Carries the `(A, b)` rational matrix extracted from the
  equations.
- `univariate-poly` — single equation, single variable, deg ≥ 1.
- `unsupported` — anything else; carries a refusal class string.

A pre-classification fast-path tries the transcendental matcher
(`tryTranscendentalInvert`) when there is exactly one equation in
exactly one variable. If the matcher recognises the equation as
`head(a·x + b) = c` with `head` in the v0.1 invert table, it short-
circuits the polynomial classifier and emits the branched-solution
result.

The dispatcher (`dispatchClassified`) executes each non-refusal lane:

- linear → `bareissSolve(A, b)` over ℚ; result mapped to
  ADR-0017 `Solution { bindings, branches }`.
- univariate-poly → `factorRatQ(p)` then dispatch each irreducible
  factor by degree to `linearRoot` / `quadraticRoots` / `cubicRoots`
  / `quarticRoots`. Multiplicities preserved as repetition.
- unsupported → `tagged "solve/<class>"`.

### Lane: linear (Bareiss one-step over ℚ)

`bareissSolve` is the same routine `tools/linsolve-q` ships. The
forward elimination uses Bareiss's integer-preserving recurrence
(Sylvester's identity, Bareiss 1968 §I); back-substitution is
standard. Free variables are reported as `t_0, t_1, …` symbols
parameterising the null space. Inconsistency is detected by a row
`[0 ⋯ 0 | β]` with `β ≠ 0`.

The translation to the `solve` output shape:

- unique → `solutions = [one Solution]`, `completeness = "complete"`,
  `branches = []`.
- under-determined → `solutions = [one Solution with branches t_0…t_{f−1}]`,
  `completeness = "finite-rep-of-infinite"`.
- inconsistent → `solutions = []`, `completeness = "complete"`.

### Lane: univariate polynomial (factor + radicals)

`factorRatQ` produces the canonical factorisation
`f = c · ∏_i p_i^{e_i}` with each `p_i ∈ ℤ[x]` irreducible over ℚ
(`packages/poly-factor`, worklog 052). Each `p_i` is then mapped by
degree:

- deg 1 → `linearRoot` (the unique rational root).
- deg 2 → `quadraticRoots` (Cardano-Vieta-type closed form).
- deg 3 → `cubicRoots` (Cardano).
- deg 4 → `quarticRoots` (Ferrari).
- deg ≥ 5 irreducible (all real roots) → one `Root[poly, k]`
  solution per real root × multiplicity, in canonical sort order
  (ADR-0018; substrate `@workbench/alg-num`).
- deg ≥ 5 irreducible (one or more complex roots) → refusal
  `solve/complex-roots-not-yet-named`.

Multiplicity is preserved as repetition: `(x − 1)²` produces two
`Solution` entries each with binding `x = 1` (per ADR-0017's flat
shape; worklog 054 §"Why solutions are flat").

### Lane: transcendental univariate (invert table + linear-arg substitution)

Pattern detector recognises five literal forms (`tryTranscendentalInvert`):

- `head(arg)` (bare).
- `head(arg) − c` and `c − head(arg)`.
- `head(arg) + (−c)` (one term in n-ary `+`).
- After `decomposeAsHeadOfLinearEqualsConstant` (worklog 055 §"What
  changed / 37r"): `head(a·x + b) = c` → invert head, then solve the
  linear residue.

The 9-head invert table:

| `head` | Inverse (principal) | Branches | Completeness |
|---|---|---|---|
| `exp` | `log(c)` | none | `complete` |
| `log` | `exp(c)` | none | `complete` |
| `sin` | `arcsin(c) + 2π·t_0`, `π − arcsin(c) + 2π·t_1` | `t_0, t_1` | `finite-rep-of-infinite` |
| `cos` | `±arccos(c) + 2π·t_0` | `t_0` (with ±) | `finite-rep-of-infinite` |
| `tan` | `arctan(c) + π·t_0` | `t_0` | `finite-rep-of-infinite` |
| `sinh` | `arsinh(c)` | none | `complete` |
| `cosh` | `±arccosh(c)` | none | `complete` |
| `tanh` | `artanh(c)` | none | `complete` |
| `abs` | `c, −c` | none | `complete` |

Out-of-domain inputs (e.g., `cos(x) = 5`) emit the symbolic formula
`arccos(5) + 2π·t_0` rather than refusing — the answer is correct
over ℂ; the consumer's domain-aware simplifier filters as needed
(worklog 055 §"Why no transcendental-out-of-domain refusal").

## Verifier — the 4-lane dispatch

The verifier mirrors the dispatcher's classification. Each case
is checked by **the lane its input belongs to**, NOT by the
candidate's claimed kind. A candidate that says `kind=ok` for an
input whose oracle agreement says it should refuse fails `shape`
("lied about scope," mirroring poly-factor-q's `_check_shape_*`).

### `shape` (always)

Candidate envelope is well-formed: `kind ∈ {ok, tagged}`; if `ok`,
required fields `vars`, `solutions`, `completeness`, `warnings`
present; each binding parses as expression-string in `vars ∪ branches`.

### Linear lane

Five checks per `bench/linsolve-q` discipline. The novel piece vs
linsolve-q: under-determined cases here are encoded as one `Solution`
with branches (ADR-0017), not as an "underdetermined" envelope kind.
Substitution semantics: each `t_i` symbol in the binding values is
free; check by sampling 10 rational tuples per branch dimension.

### Univariate-polynomial lane

Four checks. The substitution-and-simplify check delegates to SymPy's
`simplify` for the symbolic-zero test (the workbench's `cas-simplify`
is identical for these inputs by ADR-0017's design, but we use SymPy
inside the verifier for cross-validation). The multiplicity-aware
count check is the headliner: a candidate that returns roots of
`(x − 1)² = 0` as a single `x = 1` instead of two repeated bindings
fails `count_with_multiplicity`.

### Transcendental lane (the cube)

Per ADR-0019 §2, the cube `[-3, 3]^n` for `n` branches:

- `n = 1`: 7 tuples.
- `n = 2`: 49 tuples.
- `n = 3`: 343 tuples (max for v0.1).

Per-tuple substitution: instantiate each branch parameter, plug into
the equation (with `pi` → `math.pi`, etc.), evaluate via
`sympy.lambdify(..., 'numpy')` for speed. Numerical pass tolerance
`|residual| < 1e-12 · max(1, |lhs|, |rhs|)`.

The completeness grid (1D, 2000 points on `[-50, 50]`) detects
*missing branches*: the equation `f(x) = 0` evaluated on the grid;
sign changes flagged as roots; each grid root must be within
`1e-6` of *some* candidate-instantiated tuple. The grid resolution
is calibrated so that any branch with period ≤ 100 (the v0.1 invert
heads have period 2π ≈ 6.28) is sampled densely enough to trigger
at least 15 sign changes within the window.

### Refusal lane

Two checks per ADR-0019 §5: tag exact-match plus a loose payload
predicate (`payload.detail` non-empty string). Refusal-class
admission requires the oracles also refused (or returned a
`ConditionalExpression` / `ImageSet` Wolfram/SymPy can't simplify).

## Mutation-prove harness

Per ADR-0019 §4, ≥ 5 perturbations of the reference, each
demonstrating RED on at least one tier. The perturbations:

1. **Lied-about-kind: `ok` claimed for a multivariate-non-zero-dim
   input.** Should refuse with `solve/multivariate-non-zero-dim`;
   instead returns spurious `solutions: []`. Fails `shape` on the
   refusal-lane mismatch.
2. **Sign flip in linear back-substitution.** Computes `x_i =
   (rhs + Σ_{j>i} A_{ij}·x_j) / A_{ii}` instead of `−`. Fails
   `exact_satisfaction` on tier rand.linear.
3. **Dropped multiplicity in univariate-poly.** Returns deduplicated
   roots: `(x − 1)²` produces one binding instead of two. Fails
   `count_with_multiplicity` on tier rand.univariate-poly.
4. **Missed branch in transcendental sin.** Emits only the first
   branch `arcsin(c) + 2πk_0`, drops `π − arcsin(c) + 2πk_1`. Fails
   `completeness_grid` on tier rand.transcendental-univariate
   (the grid root at `π − arcsin(c)` falls outside the 1e-6 ball
   of every emitted-tuple instantiation).
5. **Wrong period in transcendental tan.** Uses `2π·k_0` instead of
   `π·k_0`. Fails `branched_substitution_cube` on `k_0 = 1`: the
   substituted equation residual is `tan(arctan(c) + 2π) − c = 0`
   (which holds), but the *next* root the grid finds at `arctan(c) + π`
   isn't in the candidate's tuple-instantiation set. Caught by
   `completeness_grid`.
6. **Reported `unique` for inconsistent linear.** A row `[0 ⋯ 0 | β]`
   with `β ≠ 0` ignored; a spurious `Solution` returned. Fails
   `exact_satisfaction` on tier v1-bank.handled.

Each mutation must produce RED on at least one tier when run
through the verifier; `test_mutations.py` asserts directly via the
mutated reference.

## Tier-by-tier rationale

- **v1-bank.handled (15).** The "headline" — what Mathematica v1
  customers expect to work. If this tier fails, the bench has
  found a regression in the dispatcher / lane substrate.
- **v1-bank.refused (5).** The honest-refusal tier. Two Fateman
  1991 mixed-trig cases plus three other historical refusals that
  are still refusals at v0.1. Catches lied-about-scope bugs (a
  candidate that *claims* to handle `cos(x)+cos(3x)+cos(5x)=0`
  when it's actually returning a wrong principal-branch slice).
- **rand.linear (15).** The well-conditioned baseline. If this
  fails, the linear lane is fundamentally broken.
- **rand.univariate-poly (25).** Largest tier — the dispatcher's
  most-common workload. Stratified across degrees 2-10 to catch
  per-degree-formula bugs (cubic ↔ quartic Ferrari is famously
  finicky).
- **rand.multivariate-zero-dim (25).** Today: tests the *refusal
  boundary*. Tomorrow (when groebner ships): regenerates to
  happy-path expectations, becomes the largest happy-path tier.
- **rand.transcendental-univariate (15).** Catches branch-period
  errors and missed-branch bugs that only the cube + completeness-
  grid verifier can detect.

## Sources cited

- **ADR-0017** — solution-set shape; first user-visible exercise of
  every field.
- **ADR-0019** — bench discipline; this bench is the headline
  instantiation.
- **Bareiss 1968** — Sylvester's identity for the linear lane;
  same paper as `bench/linsolve-q`.
- **Fateman 1991** — `docs/ground-truth/solve-disp/fateman-1991-
  solving-symbolic-equations.pdf`. Source of two refusal-class
  cases (cos+cos+cos and sin(6x)/sin(x)).
- **Cox-Little-O'Shea Ch. 4** — univariate-polynomial-factorisation
  background; same source as `bench/poly-factor-q`.
- **`docs/worklog/054`, `055`** — the implementation shards this
  bench tests.

## Sources NOT used (and why)

- **Strzebonski 2012** (cylindrical algebraic decomposition) — out
  of scope for v1 (paywalled in `MISSING.md`; CAD is a v2+ topic).
- **Wolfram `Reduce[]` semantics** beyond `Solve[]` — `Reduce`
  handles inequalities and quantifier elimination; `solve` is
  scope-bounded to equations.
- **Half-angle / Weierstrass substitution** (`packages/solve` bead
  `3il`, P3) — would expand the transcendental lane to handle
  `(1 − cos x)/sin x = 1/2`-style cases. v0.1 refuses; v0.2 ships
  the substrate; this bench's Tier rand.trans regenerates with
  expanded happy-path expectations when that lands.
