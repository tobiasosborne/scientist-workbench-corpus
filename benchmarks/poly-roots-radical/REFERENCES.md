# References — `poly-roots-radical` bench

## Primary algorithm sources

### Linear / quadratic / cubic / quartic — closed-form radicals

The dispatch per degree is canonical algebra:

- **Linear**: `−b/a`. Trivial.
- **Quadratic**: `(−b ± √(b² − 4ac)) / (2a)`. Brahmagupta ~628 CE; modern
  treatment in any algebra textbook.
- **Cubic — Cardano 1545**: del Ferro / Tartaglia / Cardano *Ars Magna*.
  Modern reference: Geddes-Czapor-Labahn §8.3 (closed-form polynomial
  factorisation), Cox-Little-O'Shea §1.3.
- **Quartic — Ferrari 1540** via resolvent cubic. Same modern refs.

These formulas are not re-derived in source — `tools/poly-roots`'s
`@workbench/cas-core` substrate (`linearRoot, quadraticRoots,
cubicRoots, quarticRoots`) ports faithful symbolic versions in TS.
Bench: this one (verifies invariants, not formulas).

### Casus irreducibilis — ADR-1yu

A cubic with three distinct real roots and `Δ_c < 0` is the
*casus irreducibilis*. Cardano's formula naturally produces complex
intermediate values; the trigonometric formula
`2√(−p/3) · cos((θ + 2πk)/3)` gives real values but introduces
`cos`/`acos`. The workbench's closed numerical vocabulary
(`+ − * / ^ neg sqrt`) excludes the trigonometric heads, so
**ADR-1yu mandates the faithful complex-radical form**. The result
expressions are *syntactically valid* but numerically NaN under
naive eval; downstream `ToReal` simplification (a future bead)
recovers the real values.

The verifier handles this via a numerical-evaluation fallback —
`sympy.evalf` produces the correct real value when SymPy's
`simplify` is conservative.

### Factorisation prefix — Berlekamp-Zassenhaus + vanHoeij

Same citations as `bench/poly-factor-q`:

- **Berlekamp 1967** — `docs/ground-truth/factor/berlekamp-1967.pdf`
- **vanHoeij 2002** — `docs/ground-truth/factor/vanhoeij-2002-knapsack.pdf`
- **Hart-vanHoeij-Novocin 2011** —
  `docs/ground-truth/factor/hart-vanhoeij-novocin-2011.pdf`
- **Cox-Little-O'Shea 4th ed., Ch. 4** —
  `docs/ground-truth/factor/cox-little-oshea-ideals-varieties-algorithms-4th.pdf`

The poly-factor pipeline produces the irreducible-factor list this
bench's tier E composes per-degree closed forms over.

## Cross-validation oracles

Per ADR-0019 §3, goldens are admitted iff ≥ 2 of 3 oracles agree.

- **Wolfram `Solve[f == 0, var]`** — primary cross-witness.
  Activated `wolframscript` 1.13.0 under TIB-Hannover-VPN.
  Documentation: <https://reference.wolfram.com/language/ref/Solve.html>.
  Bridge: `bench/_corpus/oracle/wolfram.py`.
- **SymPy `Poly.all_roots(multiple=False)`** — bench reference.
  Local install (1.14.0).
  Documentation: <https://docs.sympy.org/latest/modules/polys/agca/extensions.html>.
- **SageMath `QQbar`** *when available* — preferred third witness for
  casus-irreducibilis cubic roots (`qqbar` is the algebraic-numbers
  ring; equality-on-equal returns `True` independent of representation).
  Not required for v0.1.

For tier-G refusals, the workbench's bounded-scope refusal at v0.1
(deg ≥ 5) is admitted even when Wolfram solves with `Root[]` —
that's the honest "we stop at deg 4 in radicals; `Root[]` is bead
`yoc`" boundary. The oracle log records this as
`wolfram-solved-workbench-bounded-scope`.

## Theoretical context

- **Abel-Ruffini theorem (1799 / 1824)** — no general algebraic
  formula for the roots of a degree-5 (or higher) polynomial in
  terms of radicals. Galois 1832 generalised to characterise
  *which* polynomials are radically solvable (those with solvable
  Galois group). The workbench's deg-5 refusal at the radicals
  layer is an honest boundary at this theorem.
- **ADR-0017** — solution-set shape; `tools/solve`'s univariate-poly
  lane dispatches to this tool's closed-form formulas, then
  flattens the `(root, multiplicity)` list into ADR-0017's
  `Solution { bindings, branches }` repetition shape.
- **ADR-0019** — bench discipline; this bench is the §1-§7 instance
  for `tools/poly-roots`.
- **ADR-1yu** — casus irreducibilis: faithful complex form.

## Reference implementations consulted (none included in repo)

- **SymPy `Poly.all_roots`** — pure Python; the bench's reference
  wraps it.
- **Wolfram `Solve[f == 0, x]`** — closed; observed via
  `wolframscript` `InputForm` outputs.
- **`tools/poly-roots/tool.ts`** — the workbench candidate this
  bench tests (via the future `run-candidate.ts` adapter).

## Closed-form solutions for verification

Every tier-A / tier-B case is verified against Cramer-style closed
forms in addition to the numerical substitution check. Tier-C and
Tier-D cases are verified by the substitution + bipartite-match
chain; the casus-irreducibilis cubics rely on the
`evalf(complex)` fallback because `simplify` is conservative on
cube-roots-of-complex.

## Sources NOT used (and why)

- **Trigonometric Cardano** — would give real values for casus
  irreducibilis but breaks the closed numerical vocabulary. Per
  ADR-1yu we accept faithful complex form; downstream `ToReal`
  simplification is a future bead.
- **Bairstow's method** (numerical quadratic-factor extraction) —
  approximate, not relevant to the symbolic-radical contract.
- **Ostrowski / Newton iteration on isolating intervals** — that's
  bead `rra` (`packages/real-roots: VAS with LMQ bound`) for the
  real-root-isolate tool, not radicals.
- **`Root[poly, k]`** Mathematica object — algebraic-number
  representation; lifts the deg ≥ 5 cap; bead `yoc` after the
  alg-num substrate ships.
- **PSLQ / LLL for radical reconstruction** — would let us guess
  closed forms from numerical roots; not the workbench's design
  (we work from exact polynomial input, not numerical guesses).
