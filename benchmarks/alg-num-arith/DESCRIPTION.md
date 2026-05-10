# Bench — `alg-num-arith` design notes

## What this bench tests

The wire surface of `tools/alg-num-arith` (worklog 065): given two
`Root[poly, k]` values (or one, for unary ops) and an operation
(`add`, `sub`, `mul`, `div`, `neg`, `inv`, `eq`), produce a canonical
`Root[]` (arithmetic) or `boolean` (eq) — and refuse honestly with
`alg-num-arith/{inv-of-zero, div-by-zero}` for the documented
boundary conditions.

The substrate is `@workbench/alg-num` v0.1 (worklog 062): real
algebraic numbers only, Sylvester-Bareiss resultants for
arithmetic, canonical-form-by-interval-disambiguation for
constructor selection. Complex algebraic naming is a future shard;
this bench's cases all stay within the real-only scope.

## Why a bench is needed

`packages/alg-num/test/arithmetic.test.ts` covers the in-memory
substrate with ~75 unit tests (worklog 062). What this bench adds:

1. **Wire-layer cross-validation.** The 7-artefact tool wraps the
   substrate; the bench exercises the wire-decode + wire-encode +
   schema pathways under real inputs. A bug in `valueToRoot` /
   `rootToValue` that didn't surface in the package tests (which
   pass `Root` instances directly) lands here.

2. **Independent oracle.** SymPy `qqbar`-class evaluation is the
   reference. The verifier evaluates the candidate's claimed
   minpoly + k against a SymPy-computed numerical value of `op(a,b)`
   — the polynomial must vanish, the k must place the candidate at
   the right position in the real-root order. Two distinct
   implementations with no shared code agreeing within tolerance is
   the ADR-0019 cross-validation discipline.

3. **Mutation-prove guarantee.** The verifier is shown sensitive to
   8 characteristic perturbations (4 arithmetic mutations, 2 refusal
   mutations, 1 eq mutation, 1 cross-lane lied-about-scope). The
   verifier passes the GREEN baseline; the RED mutations are all
   caught. ADR-0019 §4 floor of ≥ 5 met.

## Algorithm path

`tools/alg-num-arith` (worklog 065) wraps the substrate from
worklog 062:

1. **Wire-decode** `a` (and optional `b`) via
   `@workbench/alg-num.valueToRoot`. Non-canonical input is silently
   canonicalised per ADR-0018.
2. **Dispatch** on the `op` flag to the substrate function:
   `algNumNeg`, `algNumInv`, `algNumAdd`, `algNumSub`, `algNumMul`,
   `algNumDiv`, or (for op = eq) `rootCanonicalEq`.
3. **Wire-encode** the result via `rootToValue`, or return a boolean
   for eq, or a `tagged "alg-num-arith/<class>"` for the documented
   refusals (inv-of-zero, div-by-zero).

## Tier-by-tier rationale

- **A. elementary (10 cases).** The basic field operations on
  small-degree algebraic numbers — `√2`, `√3`, `√5`, `√6`, and the
  rational degenerate cases (`√2 · √2 = 2`, `√2 / √2 = 1`).
  Catches the most common bugs: wrong sign, wrong factor, off-by-one
  k.

- **B. nested (5 cases).** Deg-4 minpolys arising from nested
  square roots: `√(2 + √3)`, `√(2 − √3)`. These are roots of
  `x⁴ − 4x² + 1`, a *palindromic* polynomial whose conjugate
  structure exercises the sign-aware product-interval logic in
  `algNumMul` and the disambiguation pass in `makeRoot`. The
  classical denesting identity `√(2+√3) + √(2−√3) = √6` is the
  headline B-tier test.
  *(B-nested-05-inv-nested deferred — triggers a separate
  henselLiftPair palindromic-minpoly bug in poly-factor; tracked
  as a beads issue.)*

- **C. high-degree (5 cases).** Lehmer's L(x) = `x⁵ + x⁴ − 4x³ −
  3x² + 3x + 1` (the minimal polynomial of 2cos(2π/11), totally
  real, irreducible). Arithmetic between Lehmer roots produces
  resultants of degree up to 25 — the bench's stress-test for the
  Sylvester-Bareiss path. Catches O(n³) blow-up regressions and
  primitive-element-compression-class bugs.

- **D. conjugate-distinguishing (5 cases).** Same minpoly,
  different k. Sums and products of algebraic conjugates have
  fixed shapes (trace = sum of all roots; norm = product of all
  roots); the bench checks the substrate doesn't accidentally
  collapse `α + α'` (= trace = rational) to `α + α` (= 2α).

- **E. equality stress (5 cases).** Reflexivity, cross-minpoly
  distinguish, same-minpoly index distinguish, rational degenerate.
  `rootCanonicalEq` is structurally simple after canonicalisation —
  byte-equality of minpolys + index match — but the test catches
  any drift in the canonical-form invariants.

- **F. refusals (2 cases).** `inv(0)` and `div(_, 0)` honestly
  refuse with the documented tag classes.

## Sources cited

- **ADR-0018** — `Root[poly, k]` value-protocol primitive.
- **ADR-0019** — bench discipline (4-check verifier, mutation
  prove, triple-witness oracle).
- **Cohen 1993, *A Course in Computational Algebraic Number Theory*
  (GTM 138)** §3.6.2 — sum/product resultants for algebraic
  arithmetic.
- **Bareiss 1968** *Sylvester's identity and multistep integer-
  preserving Gaussian elimination*, Math Comp 22 — exact
  resultant computation.
- **SymPy `Poly.minimal_polynomial` + `Poly.real_roots()`** —
  the reference oracle.
- **Worklog 060–062** — alg-num substrate (Root construction,
  refinement, indexed construction, resultant arithmetic).
- **Worklog 065** — `tools/alg-num-arith` ship.

## Sources NOT used (and why)

- **Wolframscript `Root[]`** — Wolfram's algebraic-number naming
  uses 1-indexed `k`. A wolfram-side oracle would need an index
  adapter at the wire boundary. Deferred until a third witness is
  needed for cross-validating the SymPy oracle on edge cases. The
  v0.1 bench is single-oracle.
- **PARI/GP `nfroots`** — same story as Wolfram; differing `k`
  conventions. Future shard.
- **bench/poly-roots-radical's verifier** — covers radical-root
  output for deg ≤ 4 polynomials (`+ − * / ^ neg sqrt` vocabulary);
  this bench covers `Root[]`-headed output. Different surfaces;
  shared discipline.
