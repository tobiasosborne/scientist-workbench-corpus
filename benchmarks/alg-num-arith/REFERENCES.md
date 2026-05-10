# Bench — `alg-num-arith` references

## Primary canonical sources

- **ADR-0018** — `Root[poly, k]`: a value-protocol primitive for
  arbitrary algebraic numbers. The wire encoding, canonical-form
  invariants (irreducible, primitive, integer-coefficient, positive-
  leading), index conventions (real-first ascending; complex
  lexicographic by `(Im, Re)`), and lazy-isolating-interval
  semantics. Local file `docs/adr/0018-root-of-polynomial.md`.

- **ADR-0019** — solve / alg-num bench discipline. The 4-check
  verifier requirement, ≥ 5-mutation-prove floor, triple-witness
  oracle protocol. Local file `docs/adr/0019-solve-bench-
  discipline.md`.

- **ADR-0003** — output / error patterns. The three-category
  exhaustive contract (record-happy, record-with-flag, tagged-
  boundary, ToolError-malformed) that the bench's verifier
  enforces.

## Algorithmic references

- **Cohen, Henri** (1993). *A Course in Computational Algebraic
  Number Theory*, Graduate Texts in Mathematics 138. Springer.
  - §3.6 — resultants and subresultants. The Sylvester-matrix
    construction and its evaluation by Bareiss elimination over
    ℚ[x] for sum/product of algebraic numbers.
  - §3.5.6 — monic transform for non-monic polynomial
    factorisation, used in `inv(α)` to handle the resulting
    non-monic minpoly.
  - §4.5 — primitive-element theorem and `rnfequation`-class
    extraction (relevant to bead `5i2`, ≥ 3-algebraics
    compression).

- **Bareiss, E.H.** (1968). *Sylvester's identity and multistep
  integer-preserving Gaussian elimination*. Mathematics of
  Computation 22.103, 565–578. The fraction-free elimination
  algorithm used for resultant computation in
  `packages/alg-num/src/resultant.ts`.

- **Brown, W.S. and Traub, J.F.** (1971). *On Euclid's algorithm
  and the theory of subresultants*. JACM 18(4), 505–514. Alternate
  resultant computation via the subresultant PRS — equivalent
  result, different complexity profile (`O(n²)` worst case vs
  Bareiss's `O(n³)`). The substrate uses Bareiss for simplicity;
  switching to PRS is a follow-on.

- **Strzebonski, Adam** (1997). "Computing in the field of complex
  algebraic numbers." Journal of Symbolic Computation 24.6,
  647–656. Mathematica-internal account of the equivalent
  primitive — including the lazy-isolating-interval discipline
  this bench's tool inherits.

## Open-source reference implementations

- **SymPy `qqbar`** — Python port of SageMath's `qqbar` ring; the
  bench's primary oracle. Source:
  `sympy/polys/numberfields/`. Used here for:
    - `sympy.minimal_polynomial(expr, x)` — canonical minpoly of
      an algebraic-number SymPy expression.
    - `sympy.Poly.real_roots(multiple=True)` — ascending
      enumeration of real roots, the reference for the
      `index_matches_real_position` check.
    - `sympy.simplify(a − b)` — algebraic-number equality decision
      (with high-precision `evalf` fallback for cases simplify is
      conservative on).

- **SageMath `qqbar` / `AA`** — `sage/rings/qqbar.py` (GPL-3).
  The closest open-source reference for lazy
  `(minpoly, interval)` algebraic-number arithmetic. The
  workbench's substrate borrows the lazy-interval-refinement
  discipline directly. Not used as an oracle in this bench (would
  require a Sage-aware test harness); the SymPy oracle is
  sufficient for v0.1.

- **PARI/GP `nfroots`, `rnfequation`** — production-grade
  algebraic-number-theory routines in C with GP wrapper. The
  reference for primitive-element compression (bead `5i2`); not
  yet wired as an oracle for this bench.

- **Wolfram `Root[]`** — Mathematica's algebraic-number naming
  primitive. Source: closed; but the index conventions are well-
  documented (`Root[poly_function, k]`, 1-indexed k). The `k`-index
  shifted by one at the wire boundary; future Wolfram-oracle
  integration would need this adapter.

## Mathematica-v1 / SymPy semantics references

- **Mathematica `RootReduce`, `Together`, `Simplify`** — the
  semantic surface area `tools/alg-num-arith` and `tools/solve`
  align with at the user-facing level. See Wolfram documentation
  for the semantic-equivalence relations the wire output must
  preserve.

- **Wolfram, Stephen** (1988). *Mathematica: A System for Doing
  Mathematics by Computer*. Section 3.3 (numerical evaluation of
  algebraic numbers) and Section 3.4 (`Root[]` semantics). The
  v1/v3 distinction in the `Root[]` semantics — `RootReduce`
  matters for the canonicalisation.

## Workbench-internal pointers

- `packages/alg-num/src/arithmetic.ts` — the in-memory functions
  the tool wraps. ~330 LOC.
- `packages/alg-num/src/resultant.ts` — Sylvester-Bareiss
  resultant. ~190 LOC.
- `packages/alg-num/src/root.ts` — `Root` type, `makeRoot`,
  canonical-form rules, equality.
- `packages/alg-num/src/refine.ts` — interval refinement.
- `packages/alg-num/src/by-index.ts` — `makeRootByIndex` (used by
  `valueToRoot` for wire-input canonicalisation).
- `packages/alg-num/src/encoding.ts` — `rootToValue` /
  `valueToRoot` wire bridge.
- `packages/poly-factor/src/factor.ts` — irreducibility check
  inside the canonicalisation; the source of the
  `henselLiftPair` palindromic-minpoly bug seen in
  `B-nested-05-inv-nested` (deferred; tracked separately).
- `tools/alg-num-arith/tool.ts` — the tool wrapping it all.
  Worklog 065.
