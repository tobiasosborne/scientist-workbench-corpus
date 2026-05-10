# References — `solve` bench

## Primary algorithm sources

The `solve` bench is the *headline* of the solve-suite-v1 epic
(`scientist-workbench-98a`). It composes substrates verified by
upstream benches; the substrate citations are inherited from those
benches and condensed here.

### Linear lane → Bareiss 1968

**Bareiss 1968** — "Sylvester's Identity and Multistep Integer-
Preserving Gaussian Elimination", *Mathematics of Computation*
22(103), 565-578.

- Local: `docs/ground-truth/linear/bareiss-1968-mathcomp.pdf`
- Argonne tech-report: `docs/ground-truth/linear/bareiss-1968-argonne-tech-report.pdf`
- AMS open archive: <https://www.ams.org/journals/mcom/1968-22-103/S0025-5718-1968-0226829-0/>

The linear lane in `tools/solve` calls `bareissSolve` from
`@workbench/cas-core` directly (`packages/cas-core/src/linsolve.ts`).
Verified end-to-end by `bench/linsolve-q` — see that bench's
`REFERENCES.md` for the algorithm-spec deep dive.

### Univariate-poly lane → Berlekamp + vanHoeij + Cardano/Ferrari

The factorisation prefix:

- **Berlekamp 1967** — "Factoring polynomials over finite fields",
  *Bell System Tech. J.* 46, 1853-1859.
  Local: `docs/ground-truth/factor/berlekamp-1967.pdf`.
- **vanHoeij 2002** — "Factoring polynomials and the knapsack
  problem", *J. Number Theory* 95(2), 167-189.
  Local: `docs/ground-truth/factor/vanhoeij-2002-knapsack.pdf`.
- **Hart-vanHoeij-Novocin 2011** — "Practical polynomial
  factoring in polynomial time", ISSAC '11.
  Local: `docs/ground-truth/factor/hart-vanhoeij-novocin-2011.pdf`.
- **Cox-Little-O'Shea 4th ed., Ch. 4** — pedagogical synthesis.
  Local: `docs/ground-truth/factor/cox-little-oshea-ideals-varieties-algorithms-4th.pdf`.

Bench: `bench/poly-factor-q` (verified factorization invariants).

The radicals suffix (`packages/poly-roots`, worklog 053):

- **Cardano** — *Ars Magna* (1545); cubic root formula. Modern
  refs: any algebra textbook covering complex numbers — we use
  Wikipedia's "Cubic equation #Cardano's formula" derivation
  ports. No local PDF needed; the formula is canonical.
- **Ferrari / Lagrange resolvent** — quartic via reduction to
  resolvent cubic. Same scope.
- **Abel-Ruffini** — degree ≥ 5 irreducible has no general radical
  solution; this is *why* the workbench routes deg-≥5 through
  `Root[poly, k]` (ADR-0018) rather than radicals. Real roots of an
  irreducible deg-≥5 factor become `Root[]` solutions; mixed-real-
  complex factors refuse with `solve/complex-roots-not-yet-named`
  (alg-num v0.1 names real algebraic numbers only).

Bench: `bench/poly-factor-q` (squarefree + factor). The radicals path
is goldened in `tools/poly-roots/goldens.spec.ts`; a dedicated
`bench/poly-roots-radical` is the open bead `iyj`.

### Transcendental lane → SymPy `solveset` shape

The 9-head invert table (`packages/solve/src/transcendental.ts`,
worklog 055) follows SymPy's `solveset` behaviour for
single-`head(arg) = c` patterns. Branch parameter naming `t_0, t_1,
…` matches the linear lane's free-variable naming; ADR-0017's
`k_0, k_1, …` namespace normalisation is deferred (worklog 055
§"Why branches are emitted with `t_0, t_1`").

- **SymPy `solveset` documentation** —
  <https://docs.sympy.org/latest/modules/solvers/solveset.html>.
  Used as the *invert-table cross-witness*; the workbench's invert
  table reproduces SymPy's left-inverse choices (principal-branch
  + period parameterisation).
- Local notes on inverse-function-multibranch handling:
  `docs/ground-truth/solve-disp/fateman-1991-solving-symbolic-equations.pdf`.

## Refusal-class motivation: Fateman 1991

**Fateman 1991** — "Notes on Computer Systems for Solving Symbolic
Equations", March 1991, revisited 2005.
Local: `docs/ground-truth/solve-disp/fateman-1991-solving-symbolic-equations.pdf`.

Two specific cases are admitted as Tier `v1-bank.refused.fateman`:

- **`cos(x) + cos(3x) + cos(5x) = 0`** (Fateman §1; the British
  A-level exam problem). Mathematica v2 returned `{}` with the
  `Solve::ifun` warning. PRESS produced 5 of the 10 roots in
  `[0, π]`. The honest answer is the infinite set
  `{n·π/6 | n ≢ 0 (mod 6)}`. v0.1 refuses with
  `solve/foreign-vocabulary` (the equation has multiple `cos`
  heads in n-ary `+`, outside the v0.1 invert pattern). Bead `b55`
  upgrades the refusal to `solve/transcendental-multibranch`.
- **`sin(6x)/sin(x) = 0`** (Fateman §8). Mathematica 2.0 found
  none, but reported `x = 0` in error (a removable singularity);
  Macsyma found 11 roots one of which was wrong (the same `x = 0`
  limit issue). v0.1 refuses with `solve/foreign-vocabulary` (the
  equation contains a rational-function denominator). The honest
  answer requires limit reasoning the workbench's solve doesn't have.

These are admitted as refusal-class goldens with the consensus
"Wolfram refuses / produces conditional expression AND SymPy refuses
/ produces ImageSet" → workbench refuses honestly.

Two supporting Fateman essays staged for context:

- `docs/ground-truth/solve-disp/fateman-advances-trends-cas-design.pdf`
- `docs/ground-truth/solve-disp/fateman-case-history-interactive-problem-solving.pdf`

## Cross-validation oracles

Per ADR-0019 §3, goldens are admitted iff ≥ 2 oracles agree.

- **Wolfram `Solve[eqs == 0, vars]` / `Reduce[]`** — primary oracle.
  Activated `wolframscript` 1.13.0 under TIB-Hannover VPN.
  Documentation: <https://reference.wolfram.com/language/ref/Solve.html>.
  Bridge: `bench/_corpus/oracle/wolfram.py`.
- **SymPy `solve(eqs, vars, dict=True)`** + `solveset` — secondary
  oracle. Local install (1.14.0).
  Documentation: <https://docs.sympy.org/latest/modules/solvers/solveset.html>.
  Bridge: `bench/_corpus/oracle/sympy_bridge.py:_solve`.
- **SageMath** *when available* — tertiary, preferred for algebraic-
  number territory. Not required for v0.1.

The agreement layer at `bench/_corpus/oracle/agreement.py:_agree_solve`
implements solution-set agreement via bipartite-matching of
"substitute-and-simplify-to-zero" residues. Branched-output
agreement (Mathematica `ConditionalExpression[…, C[1] ∈ Integers]`
vs SymPy `ImageSet`) is delegated to the cube verifier rather than
the agreement layer — the agreement layer routes branched cases to
the bench's `branched_substitution_cube` check.

## Reference implementations consulted

- **SymPy `sympy.solvers.solvers.solve`** — pure-Python; the
  bench's `reference/solve_reference.py` wraps this.
- **Wolfram `Solve[]` source** — closed; observed via
  `wolframscript` `InputForm` outputs through the oracle bridge.
- **`tools/solve/tool.ts`** — the workbench candidate this bench
  ultimately tests (via the future `run-candidate.ts` adapter).

For the v1 implementation we work from ADR-0017 + ADR-0019
specifications, with the substrates (`bareissSolve`,
`factorRatQ`, `tryTranscendentalInvert`) as load-bearing
prerequisites.

## Closed-form solutions for verification

For the small-degree univariate cases, the verifier additionally
checks roots via SymPy's `Poly.all_roots()` — closed-form for
deg ≤ 4, `RootOf` symbolic for deg ≥ 5 irreducible. The two paths
(workbench radicals vs SymPy roots) must produce sets of the same
cardinality and pairwise-equivalent expressions under `simplify`.

For the transcendental cases, the cube verifier evaluates the
*equation* numerically per integer tuple — there is no separate
closed form to compare against; the cube *is* the verification.

## Theoretical context

- **ADR-0003** — output / error patterns: `tagged "<tool>/<class>"`
  for boundary refusals, `ToolError` only for malformed input.
- **ADR-0017** — solution-set shape: `Solution { bindings, branches }`,
  `completeness ∈ {complete, finite-rep-of-infinite}`. The bench
  tests every clause of every field.
- **ADR-0019** — bench discipline: triple-witness, mutation-prove,
  invariant verification (this bench is the §1-§7 headline).

## Sources NOT used (and why)

- **Cylindrical algebraic decomposition (Collins 1975, Strzebonski
  2012)** — the gold standard for solving polynomial systems with
  inequalities. Out of scope for v1; bead `auz` (P3) tracks future
  CAD experiments.
- **Gröbner basis solving** (Buchberger 1965, Faugère F4 / F5) —
  the canonical algorithm for multivariate-zero-dim. The workbench
  *will* ship this (groebner stack: `8y8`, `fcf`, `9du`, `onh`,
  `h56`, `x8d`, `m0m`); until then, the bench's tier
  `rand.multivariate-zero-dim` admits refusal-class goldens.
- **Resultants / U-resultant for multivariate root-finding** —
  superseded by Gröbner + shape-lemma in modern CAS; not on the
  workbench roadmap.
- **`PRESS`** (Sterling-Bundy-Byrd-O'Keefe-Silver 1989) — the
  expert-system equation-solver Fateman 1991 cites. The workbench
  is decisively *not* heuristic-driven; its dispatch is closed-form
  per lane. PRESS is cited as a historical refusal-class oracle
  ("PRESS got 5 of 10 on Fateman's case"), not as an algorithmic
  reference.
