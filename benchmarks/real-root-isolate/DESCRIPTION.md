# Bench `real-root-isolate` — algorithm + invariants

This document expands `PROMPT.md` with the algorithmic context, the
verifier-invariant set, and the boundary cases that distinguish honest
refusal from wrong answer.

## Algorithm — Vincent-Akritas-Strzebonski (VAS) with LMQ

The `tools/real-root-isolate` implementation tracked under bead
`scientist-workbench-rra` will port SymPy's `dup_isolate_real_roots`
(BSD, `sympy/polys/rootisolation.py`) to TypeScript over the workbench's
`packages/cas-core::polyGcd` and rational arithmetic. The high-level
shape:

1. **Reduce to positive roots.** A polynomial `f(x)` has the same real
   roots as `f(−x)` reflected across zero, so:
   - `f_pos(x) = f(x)`           — finds roots in `(0, +∞)`.
   - `f_neg(x) = f(−x)`          — finds roots in `(0, +∞)` of `f_neg`,
     each `r > 0` corresponding to root `−r < 0` of `f`.
   - Rational roots at exactly `0`: detected by `f(0) == 0` and emitted
     as the singleton `(0, 0)`.
2. **VAS positive-root recursion.** For a polynomial whose positive
   roots we want isolated, apply Vincent's theorem (1836):

   *Vincent's theorem.* If a polynomial `g(x)` has at most one positive
   root, the recursion stops; this is decidable via Descartes' rule
   of signs (DRS) — `g` has at most one positive root iff the number
   of sign-changes in `g`'s coefficient sequence is 0 or 1.

   Otherwise apply one of two Möbius transformations to subdivide:
   - `g(x) → g(x + b)` where `b ≥ 0` is a positive-root *lower bound*.
   - `g(x) → x^d · g(1/x + 1)` (deg = `d`) where the recursion explores
     roots in `(0, 1)`.

   Each transformation maps a continued-fraction expansion step over
   the rationals, and the successive Möbius compositions produce
   rational endpoints `(a, b)` for each isolating interval at the
   recursion's leaves.

3. **LMQ bound.** The bound `b` chosen at each step is the Local-Max
   Quadratic positive-root bound (Akritas-Strzebonski-Vigklas 2008):
   ```
   LMQ(f) = max over k of (max over j>k with sign(c_j) ≠ sign(c_k) of
            (-c_k / (2 · 2^t_k · c_j))^(1/(j-k)))
   ```
   where `t_k` counts the "uses" of `c_k` as a denominator in earlier
   pairings. LMQ is the headline efficiency improvement: the bound is
   tight to within a factor 2 of the true positive-root upper bound,
   and thus controls the recursion depth tightly. Naive bisection
   (Cauchy bound + bisect) costs `O(n^2 · log B)` rational operations
   where `B` is the largest coefficient bit-length; VAS-LMQ achieves
   `O(n · log B)` amortised in practice (Tsigaridas-Emiris 2008).

4. **Rational-root extraction.** Before VAS, the rational root theorem
   identifies all rational candidates `p/q` with `p | c_0` and
   `q | c_n` (constant and leading coefficients). Each is tested via
   `f.eval(p/q) == 0`; passing roots are emitted as singletons. After
   factoring out rational roots via synthetic division, VAS handles the
   remaining (rational-root-free) factor.

### The squarefree precondition

VAS depends on the bijection between sign-change events of Sturm-like
sequences and real roots of `f`. A double root has *no* sign change
(the local quadratic-touching geometry); a triple root has one sign
change but represents three roots counted with multiplicity. The
algorithm cannot distinguish "1 simple root" from "1 triple root"
without a multiplicity-aware extension — and that's exactly what
`packages/poly-factor::squareFree` (Yun 1976, worklog 052) computes
upstream. The composition

```
factor → squareFree → real-root-isolate per squarefree factor → re-attach mult
```

is the canonical pipeline. Refusing the non-squarefree boundary keeps
this tool's contract crisp: input squarefree ⇒ output exact.

## Verifier — the 4 checks

Every happy-path case runs all four; refusal cases run two. Per
`verifier_protocol.md`:

### `shape` — structural

- `kind == "ok"`.
- `intervals` is a list; each entry is a record with rational-string
  `lo` and `hi`.

### `each_interval_contains_one_root` — exact

Per interval `(lo, hi)`:

- If `lo > hi`: FAIL.
- If `lo == hi`: pass iff `f(lo) == 0` (singleton).
- If `lo < hi`: open root count
  ```
  n_open = count_roots(lo, hi) − [f(lo) = 0] − [f(hi) = 0]
  ```
  Pass iff `n_open == 1`.

This handles SymPy's two output shapes (open `(a, b)` and singleton
`(r, r)`) uniformly via the boundary-correction term.

### `intervals_disjoint_and_ordered` — exact

For each adjacent pair, `intervals[i].hi <= intervals[i+1].lo`.
Equality is permitted because adjacent intervals can share a non-
root separator (e.g., the singleton `(0, 0)` adjacent to the open
`(0, 1)` for `4x³ − 3x`). The other two invariants
(`each_interval_contains_one_root` + `count_matches_total_real_roots`)
together rule out double-counting at shared boundaries.

### `count_matches_total_real_roots` — exact

`len(intervals) == p.count_roots()` over `[−∞, +∞]`. Catches "dropped a
root", "doubled an interval", and "missing-rational-root-singleton"
mutations.

## Mutation-prove harness

Per ADR-0019 §4, ≥ 5 perturbations of the reference. This bench ships
**9** (each demonstrates RED on the labelled check):

1. `dropped_interval` — pop one entry from a 3-real-root cubic
   ⇒ count_matches_total_real_roots.
2. `added_spurious_interval` — append a fake interval at `(10, 20)`
   ⇒ count + each_interval (no root in (10, 20)).
3. `widened_to_two_roots` — replace a singleton with `(0, 3)` spanning
   3 roots ⇒ each_interval.
4. `overlapping_intervals` — insert a wide `(1, 3)` overlapping
   adjacent singletons ⇒ intervals_disjoint_and_ordered.
5. `singleton_not_root` — move a singleton from `1` (root) to `5`
   (non-root) ⇒ each_interval.
6. `lied_about_scope` — for non-squarefree input, fabricate `ok`
   instead of refusing ⇒ shape.
7. `wrong_refusal_tag` — refuse with `not-squarefree` instead of
   `multivariate` ⇒ refusal_class_matches.
8. `reversed_order` — emit intervals in descending order ⇒
   intervals_disjoint_and_ordered.
9. `endpoint_at_root` — replace a singleton at `0` with the
   non-singleton `(0, 1/2)`, leaving the open count off by one
   ⇒ each_interval (exercises the boundary-correction term).

GREEN baseline 7/7 + RED mutations 9/9 = verifier sensitive.

## Tier-by-tier rationale

- **A. trivial.** Linear and quadratic polynomials with rational and
  irrational roots, plus the "no real roots" quadratic. Fastest
  signal of total breakage.
- **B. Chebyshev / Legendre.** `T_n` and `P_n` of degrees 3-7 — every
  one has exactly `n` distinct real roots in `(−1, 1)`, all
  irrational. The classical orthogonal-polynomial sequences are dense
  with closely-spaced real roots; isolating them tests bisection cadence
  on a known-counts ground truth.
- **C. clustered.** Mignotte's `M_{n,a}(x) = x^n − 2(ax − 1)^2` family
  is the textbook stress test for real-root isolators: two real roots
  cluster within `~1/a^{(n+2)/2}` of each other near `x = 1/a`. At
  `a = 10` the rational separation between the two clustered roots is
  fine enough that VAS must bisect for several rounds before isolating
  them. The `(1/100, 2/100, 3/100)` cluster verifies the rational-root
  handling at small scale; the `(1, 1 + 10^-6)` case verifies VAS
  doesn't fail on rational-root pairs separated by tiny rationals.
- **D. large-degree.** Wilkinson `(x − 1)(x − 2) … (x − 50)` is the
  classical *floating-point* hardness test (numerical eigenvalue methods
  collapse on it); for *exact rational* arithmetic it's trivial — the
  50 roots are integers — but it stresses the rational-arithmetic
  depth of every operation. Chebyshev `T_11` (deg 11) and `T_13` (deg
  13) contribute densely-clustered irrational real roots in `(−1, 1)`;
  individual Chebyshev polynomials are squarefree (n distinct roots
  by construction), but products of *odd* Chebyshev or Legendre share
  the root at zero and thus collapse to non-squarefree — caught here
  by Tier F's broader refusal coverage. The half-integer-30 case
  `prod_{i=1..30}(2x − i)` has 30 rational-but-half-integer roots —
  verifies the singleton form for non-integer rationals.
- **E. rational-coefficient stress.** Large coefficients (×1000),
  mixed denominators (1/2, 1/3, 1/5, 1/7), and roots spanning multiple
  orders of magnitude (1 to 10000). Exercises the BigInt-rational
  arithmetic the workbench's substrate is built on.
- **F. refusals.** Three permanent-class refusals
  (`not-squarefree`, `multivariate`, `non-polynomial`) verify the
  per-class tag dispatch. Non-squarefree comes in two flavours
  (pure double `(x − 1)²` and mixed `(x − 1)²(x + 1)`) because the
  squarefreeness check can short-circuit on either.
- **G. structural edges.** No-real-roots cases (`x² + 1`, `x⁴ + 1`,
  Φ_12, Φ_13) exercise the empty-interval-list output. The poly with
  a rational root at exactly zero (`x³ − x`) verifies the singleton-
  at-zero case (which the boundary-correction term in the verifier
  must handle correctly).

## Sources cited

- **ADR-0019** — bench discipline.
- **Vincent 1836** — *Sur la résolution des équations numériques*.
  The original sufficient-condition theorem for "exactly one positive
  root" via Möbius transformations.
- **Akritas, Strzebonski, Vigklas 2008** — *Improving the performance
  of the continued fractions method using new bounds of positive
  roots*. Nonlinear Analysis: Modelling and Control 13(3), 265-279.
  The LMQ bound this tool uses.
- **Tsigaridas & Emiris 2008** — *Univariate polynomial real root
  isolation: continued fractions revisited*. ESA 2008. The complexity
  analysis showing VAS-LMQ is `O(n · log B)` amortised.
- **Yun 1976** — *On square-free decomposition algorithms*. SYMSAC.
  The squarefree-decomposition reference; bench/poly-factor-q's
  P2-3 ships the workbench's TS port.
- **Mignotte 1981** — *Some useful bounds*. The clustered-root
  family used in tier C.
- **`docs/worklog/052`** — squareFree implementation, the upstream
  composition partner.
- **`bench/poly-factor-q`** — substrate bench (factor list invariants
  this bench composes upstream).

## Sources NOT used (and why)

- **Newton iteration on isolating intervals** (Hansen 1992) — not used
  *for isolation*; reserved for `xkz` (lazy interval refinement) which
  *narrows* an existing isolating interval to arbitrary precision. The
  isolation step itself does not need Newton.
- **Sturm-sequence enumeration** (Sturm 1829) — historically the first
  real-root-counting algorithm; correct but `O(n^3)` rational ops per
  query, vs VAS-LMQ's `O(n · log B)`. The verifier *uses* SymPy's
  Sturm-sequence `count_roots` because it's the trusted ground truth,
  but the tool itself uses VAS for the production isolation.
- **Approximate roots via numerical eigenvalues** of the companion
  matrix — `tools/linalg-solve` covers the float64 case. Not exact-
  rational; not what this tool ships.
- **Complex root isolation** (Pinkert 1976; Wilf 1978) — out of v0.1
  scope; complex algebraics live in the `Root[poly, k]` form (ADR-0018)
  emitted by `packages/alg-num`, not as isolating rectangles.
