# References — `real-root-isolate` bench

## Primary algorithm sources

### Vincent-Akritas-Strzebonski (VAS) continued fractions

The headline algorithm. Three load-bearing references:

- **Vincent 1836**, *Sur la résolution des équations numériques*,
  J. Math. Pures Appl. 1: 341-372.
  The original theorem: a polynomial with at most one positive root
  is decidable via Möbius transformations + Descartes' rule of signs.
  Modern restatement: any squarefree polynomial's positive roots can
  be enumerated by a finite recursion of `x → x + b` and `x → 1/(x +
  1)` transformations, terminating in leaves where DRS gives a
  count of 0 or 1. (Pre-modern; not in the workbench's
  `docs/ground-truth/` collection — every modern reference reproduces
  the statement.)

- **Akritas, Strzebonski, Vigklas 2008**, *Improving the performance
  of the continued fractions method using new bounds of positive
  roots*, Nonlinear Analysis: Modelling and Control 13(3): 265-279.
  Open-access; introduces the LMQ (Local-Max Quadratic) positive-root
  bound used in the recursion. The contribution is the constant-factor
  improvement over Hong's 1998 bound, halving the average bisection
  depth in practice.

- **Tsigaridas & Emiris 2008**, *Univariate polynomial real root
  isolation: continued fractions revisited*, ESA 2008 (LNCS 5193:
  817-828).
  The complexity analysis: VAS with LMQ is `O(n · log B)` amortised
  rational-arithmetic operations, where `B` is the bit-length of the
  largest coefficient. Beats Sturm's `O(n^3 · log B)` and bisection's
  `O(n^2 · log B)`.

### SymPy's `dup_isolate_real_roots`

The bench's reference implementation
(`reference/real_root_isolate_reference.py`) wraps SymPy's
`Poly.intervals()`, which calls `dup_isolate_real_roots` from
`sympy/polys/rootisolation.py` (BSD-licensed). That file is a
faithful Python port of VAS-LMQ, with comments cross-referencing
Akritas-Strzebonski-Vigklas 2008. The future TS port at
`packages/real-roots` (bead `rra`) will mirror its structure — types,
not idioms (per ADR-0009 / TS-native frontend DSL axiom).

### Squarefree decomposition prefix — Yun 1976

The squarefree precondition is delivered by `packages/poly-factor::squareFree`
(worklog 052). The relevant paper:

- **Yun 1976**, *On square-free decomposition algorithms*, SYMSAC '76.
  The `O(n · M(n))` algorithm via successive GCD-with-derivative.
  Local copy: `docs/ground-truth/factor/yun-1976.pdf` (referenced by
  `bench/poly-factor-q`'s REFERENCES).

## Cross-validation oracles

Per ADR-0019 §3, goldens are admitted iff ≥ 2 of 3 oracles agree on
the **count of real roots** (interval *endpoints* differ across
implementations and are not compared byte-wise).

- **Wolfram `RootIntervals[poly][[1]]`** — primary cross-witness.
  Activated `wolframscript` 1.13.0 under TIB-Hannover-VPN.
  Documentation:
  <https://reference.wolfram.com/language/ref/RootIntervals.html>.
  Bridge: `bench/_corpus/oracle/wolfram.py`.
- **SymPy `Poly.intervals()`** — bench reference. Local install
  (1.14.0). Documentation:
  <https://docs.sympy.org/latest/modules/polys/reference.html#sympy.polys.polytools.Poly.intervals>.
- **SageMath `QQ['x'](p).roots(RR)` count** *when available* —
  preferred third witness for clustered cases (Tier C). Not required
  for v0.1; the workbench's primary install lacks Sage.

For tier-F refusals, the workbench's bounded-scope refusal
(non-squarefree, multivariate, non-polynomial) is admitted even when
Wolfram solves the input (Wolfram's `RootIntervals` squarefrees
internally). The oracle log records this as
`wolfram-ok-workbench-bounded-scope`.

## Theoretical context

- **Sturm 1829** — the first algorithm for counting real roots of a
  polynomial in an interval. The verifier *uses* SymPy's Sturm-sequence
  `count_roots` as ground truth (it's the trusted authority because
  it's a textbook decision procedure with a clean correctness proof);
  the tool itself uses VAS for production isolation, which is
  asymptotically faster.

- **Descartes' rule of signs (1637)** — the sufficient condition
  underlying Vincent's theorem. The number of positive real roots of
  `f(x)` equals the number of sign-changes in the coefficient
  sequence, *modulo 2*. Vincent's theorem extends DRS to a
  *recursive* decision procedure via Möbius transformations.

- **Lagrange / Cauchy bounds** — classical upper bounds on the
  magnitude of real roots; the rational-root candidates are bounded
  by these prior to LMQ refinement.

- **Mignotte 1981** — *Some useful bounds*. Constructed the family
  `M_{n,a}(x) = x^n − 2(ax − 1)^2` with two real roots clustered
  within `~1/a^{(n+2)/2}` of each other; the canonical stress test
  for real-root isolators (used here in Tier C).

- **ADR-0017** — solution-set shape; `tools/solve`'s univariate-poly
  lane composes this tool's intervals with `tools/poly-roots`'s
  closed-form roots and `tools/poly-factor`'s factor list.

- **ADR-0018** — `Root[poly, k]` algebraic-number form; will sort by
  this tool's *real* roots first, then complex by `(Im, Re)` —
  forthcoming in `packages/alg-num`.

- **ADR-0019** — bench discipline; this bench is the §1-§7 instance
  for `tools/real-root-isolate`.

## Reference implementations consulted (not included in repo)

- **SymPy `Poly.intervals()` / `dup_isolate_real_roots`** (BSD;
  `sympy/polys/rootisolation.py`) — the algorithmic reference; the TS
  port will mirror its structure idiomatically.
- **Mathematica `RootIntervals[]`** (closed; observed via
  `wolframscript` `InputForm` outputs). The cross-witness used for
  the count check.
- **MPFI / MPFR's interval extension** — high-precision interval
  arithmetic; relevant only for the *refinement* step (`xkz` bead),
  not the *isolation* step this bench tests.

## Sources NOT used (and why)

- **Newton-Hansen interval iteration** (Moore 1966; Hansen 1992) —
  not used for *isolation*. Reserved for `xkz` (lazy interval
  refinement, narrowing an existing isolating interval to arbitrary
  precision). The isolation step is rational-bisection-only.
- **Pinkert 1976** *complex polynomial root isolation* — out of v0.1
  scope; complex algebraics live in `Root[poly, k]` form (ADR-0018)
  emitted by `packages/alg-num` rather than as complex isolating
  rectangles.
- **Numerical eigenvalue methods** (companion matrix → `linalg-solve`)
  — approximate, not exact-rational; covered by `tools/linalg-solve`
  for the float64 use case.
- **Lambert-W / non-algebraic roots** — out of scope; the input is
  ℚ[x], so all roots are algebraic.
