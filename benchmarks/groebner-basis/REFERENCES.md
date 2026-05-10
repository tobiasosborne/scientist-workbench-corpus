# References — `groebner-basis` bench

## Primary algorithms

### Buchberger's algorithm

**Buchberger 1965** — Bruno Buchberger's PhD thesis (Innsbruck), the
origin of the algorithm. The construction reduces the problem of
ideal-membership testing to the systematic elimination of S-polynomial
remainders.

**Buchberger 1979** — "A criterion for detecting unnecessary reductions
in the construction of Gröbner bases", *EUROSAM '79*, LNCS 72, 3-21.

- Local: `docs/ground-truth/groebner/buchberger-1979-two-criteria.pdf`
- The two pair-pruning criteria the bench's reference implementation
  exercises:
  - **Criterion 1** (Coprime, §1): if `lcm(LM(f), LM(g)) = LM(f) ·
    LM(g)`, the S-polynomial reduces to zero by the product
    criterion. Drop the pair without computing it.
  - **Criterion 2** (Chain, §3): if there is `h` with `LM(h)`
    dividing `lcm(LM(f), LM(g))` and both `(f, h)` and `(g, h)` are
    already processed, drop the pair via t-representation.

**Gebauer-Möller 1988** — "On an installation of Buchberger's
algorithm", *J. Symbolic Computation* 6(2-3), 275-286.

- Not staged locally. The Gebauer-Möller installation is the standard
  bookkeeping refinement of Buchberger 1979's chain criterion. CLO
  Ch.2 §10 reproduces the cleaner pseudocode.

### Sugar pair selection

**Giovini-Mora-Niesi-Robbiano-Traverso 1991** — "One sugar cube,
please, or selection strategies in the Buchberger algorithm", *ISSAC
'91*, 49-54.

- Local: `docs/ground-truth/groebner/giovini-mora-niesi-robbiano-traverso-1991-sugar-cube.pdf`
- Establishes that **virtual degree** (sugar) controls coefficient
  swell at the strategy level. Sugar of polynomial f at input is
  `deg(f)` (total degree). Sugar of S-polynomial S(f, g) is
  `max(sug(f) − deg(LM(f)) + deg(lcm), sug(g) − deg(LM(g)) +
  deg(lcm))`. Pairs sorted ascending by sugar; arbitrary tiebreak
  (e.g., lex on (i, j) with i<j).
- The "sloppy sugar" variant allows sugar to be updated when a
  polynomial is reduced. Strictly better than non-sloppy on systems
  with high-degree intermediate reductions.
- Phase 3 implements sloppy sugar per RESEARCH-NOTE-x8d.md §2-B.

### Multivariate division

**Cox-Little-O'Shea Ch.2 §3 Theorem 3 p.64-66** — the canonical
pedagogical statement of the multivariate division algorithm. Given
`f` and divisor list `G = [g_1, …, g_s]`, the algorithm produces
quotients `q_i` and remainder `r` such that `f = Σ q_i g_i + r`,
where every term of `r` is NOT divisible by any `LM(g_i)`. The
verifier's `polyDivRem` function (in both verify.py and verify.ts) is
a direct port of this algorithm.

### Inter-reduction

**Cox-Little-O'Shea Ch.2 §7 Theorem 5 p.93** — the uniqueness theorem
for reduced Gröbner bases. After Buchberger's main loop, two
reductions: (1) drop any polynomial whose leading monomial is
divisible by another polynomial's LM; (2) reduce each surviving
polynomial's non-leading terms against the rest. The result is the
unique reduced GB.

## Modern textbook treatment

**Cox-Little-O'Shea (2015)** — *Ideals, Varieties, and Algorithms*
4th ed., Springer.

- Local:
  `docs/ground-truth/groebner/cox-little-oshea-ideals-varieties-algorithms-4th.pdf`
  (when staged; partial coverage in the workbench's `groebner/`
  ground-truth directory).
- Chapter 2 covers the entire pipeline: monomial orderings (§2),
  multivariate division (§3), Hilbert's basis theorem and Gröbner
  bases (§5), Buchberger's algorithm and S-polynomials (§6),
  reduced GB (§7).

## Bench-specific oracles

Per ADR-0019 (`docs/adr/0019-solve-bench-discipline.md`) and the
Phase 2a plan, every committed golden case is admitted iff Wolfram
and SymPy agree on the ideal generated.

### Wolfram

**`GroebnerBasis[F, vars, MonomialOrder -> ...]`** — primary oracle.
Documentation: <https://reference.wolfram.com/language/ref/GroebnerBasis.html>

- Invocation in `golden/generate.py:wolfram_groebner`:
  ```
  basis = GroebnerBasis[{f_1, ..., f_m}, {vars}, MonomialOrder -> Lexicographic];
  WriteString["stdout", ToString[InputForm[basis]]]
  ```
- Wolframscript emits a stray `Null` line after the answer; the
  parser in generate.py picks the first `{ … }` line and ignores
  the rest. Wolframscript can also segfault during cleanup AFTER
  printing well-formed output; we tolerate non-zero exit codes
  when stdout is well-formed.

### SymPy

**`sympy.groebner(F, *vars, order=...)`** — secondary oracle. Built on
Buchberger + sugar + Gebauer-Möller in `sympy.polys.groebnertools`.
Returns a `GroebnerBasis` iterable over `Poly` objects. The bench's
SymPy reference (`reference/groebner_reference.py`) wraps this with the
canonical refusal-class detection (sin/cos/log heads → non-polynomial,
foreign symbols → parametric, empty inputs → empty-input/empty-vars).

### Agreement layer

The dual-witness agreement check, per `golden/generate.py:oracles_agree`,
proceeds:

1. Re-run SymPy's `groebner()` on each oracle's output to canonicalise
   (reduced form, monic leading coefficients).
2. Represent each basis element as a sorted tuple of `(exponent_vector,
   coefficient_string)` pairs.
3. Compare the two basis sets as `frozenset`s.

This is robust to basis-list permutation and to non-canonical output
forms from either oracle. Agreement is byte-equality after
canonicalisation; disagreement halts case admission with a flagged log.

## Theoretical context

- **Hilbert's basis theorem** — every ideal in `k[x_1, …, x_n]` is
  finitely generated. Justifies the existence of Gröbner bases
  (CLO Ch.2 §5).
- **Buchberger's theorem** — a basis G of the ideal `I` is a Gröbner
  basis iff every S-polynomial of pairs in G reduces to 0 mod G
  (CLO Ch.2 §6 Theorem 6 p.85). This is the THEORETICAL JUSTIFICATION
  for the bench's invariant 4 — if all S-pairs reduce to 0, the
  candidate IS a Gröbner basis of `⟨candidate⟩`. Combined with
  invariants 2 and 3 (ideal containment in both directions), the
  candidate is a GB of `⟨input⟩`.
- **Macaulay's basis theorem** — for any monomial ordering >, the
  set of monomials NOT in `LT_>(I)` is a vector-space basis of
  `k[x]/I`, and that quotient's dimension is ordering-independent
  (CLO Ch.2 §4 Proposition 4). This is the foundation for FGLM
  conversion (Phase 2b's `groebner-zerodim-extract` bench, separate
  dispatch).

## Reference implementations consulted (none included in repo)

- **SymPy** `sympy.polys.groebnertools.groebner` — Python-native
  Buchberger implementation. License: BSD. The bench's SymPy oracle.
- **Wolfram** `GroebnerBasis` — closed-source. The bench's Wolfram
  oracle.
- **Singular** `std()` — production C++ implementation; widely used
  in computational algebraic geometry. License: GPL. Not consulted
  for the v1 reference but is a future cross-implementation
  cross-check candidate (corpus query: any committed case can be
  graded against a Singular-backed adapter once written).
- **Macaulay2** `gb()` — production C++; specialises in commutative
  algebra workflows. License: GPL.
- **FGLM-aware engines (Magma, GAP)** — for the Phase 2b downstream
  bench, not relevant here.

For the v1 implementation Phase 3 reads CLO Ch.2 + Buchberger 1979 +
Giovini et al. 1991 directly and re-implements; cross-validation
against SymPy + Wolfram happens at the bench-oracle layer, not the
source-code layer.

## Test-set construction references

### Cyclic-n family

The cyclic-n system is the standard Buchberger-family benchmark; cyclic-3
is trivial, cyclic-4 is moderately hard, cyclic-5 lex is computationally
heavy (>15s in SymPy on commodity hardware), cyclic-6 lex is intractable
without F4-class linear-algebra reduction. The bench includes cyclic-3
and cyclic-4 (lex), and cyclic-5 (degrevlex only — lex skipped per the
device's 10s budget gate).

### Katsura family

Katsura-n is a chemistry-equilibrium model on `n+1` variables, due to
Shigetoshi Katsura. Standard form (Faugère ISSAC 1994):
- u_0 + 2(u_1 + u_2 + … + u_n) − 1 = 0
- u_0² + 2(u_1² + u_2² + … + u_n²) − u_0 = 0
- 2(u_0 u_1 + u_1 u_2 + …) − u_1 = 0
- …

The bench includes Katsura-3 (lex). Katsura-4 lex is ~16s on this
device (exceeds the budget) and is therefore skipped per the Phase 2a
plan.

### Adversarial random generation

The 40-case `stratified-random` tier is generated per the Phase 2a
plan: 30% random-uniform / 30% near-coprime LMs / 20%
multiplicity-induced / 10% coefficient-swell-prone / 10%
large-but-structured. Naive uniform sampling produces 95%
generic-easy systems that defeat the bench's purpose; the
adversarial mix biases toward known-hard structures.

The generation strategy is reproducible: same seed (`SEED=20260510`)
plus the hard-coded constants in `golden/generate.py:_gen_*` ⟹ same
case set on any device. Per CLAUDE.md Rule 9 (determinism),
regeneration is a pure function of the source.
