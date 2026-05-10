# `groebner-basis` — multivariate Gröbner-basis computation over ℚ

## What this bench tests

Given a finite set of polynomials `F = {f_1, …, f_m} ⊂ ℚ[x_1, …, x_n]`
(carried as expression strings) and a monomial order, the bench grades
whether a candidate implementation correctly computes a **Gröbner basis**
of the ideal `⟨F⟩` under that order.

The verifier checks four mathematical invariants — *not* byte-equality
with a reference output. The discipline is "any correct GB passes" per
ADR-0019 §1, not "the same GB SymPy emits passes."

## The four invariants

For each case the verifier runs:

1. **`shape`** — structural validation. Output is `{kind: "ok",
   basis: [<polynomials>], order, vars, n_pairs, warnings}` or
   `{kind: "tagged", tag: "groebner-basis/<class>", payload}`. Each
   basis element parses as a polynomial in the declared variables over
   ℚ. Tagged refusals carry one of the four declared classes.

2. **`ideal_containment_input_to_candidate`** — every input
   polynomial reduces to 0 modulo the candidate basis under the
   requested order. Failure ⇒ candidate is *missing* polynomials.
   Computed via SymPy `polys.rings.PolyElement.div` (the senior-grade
   multivariate division — CLO Ch.2 §3 Theorem 3 p.64).

3. **`ideal_containment_candidate_to_input`** — every candidate
   polynomial reduces to 0 modulo a reference Gröbner basis of the
   input (computed by SymPy as the trusted oracle). Failure ⇒
   candidate has *spurious* polynomials not in `⟨input⟩`. Together
   with (2), proves `⟨input⟩ = ⟨candidate⟩`.

4. **`s_pair_reduces_to_zero`** — for every pair `(g_i, g_j)` of
   distinct candidate basis elements,

   ```
       S(g_i, g_j) = (lcm/LM(g_i)) / LC(g_i) · g_i
                   − (lcm/LM(g_j)) / LM(g_j) · g_j
   ```

   reduces to 0 modulo the candidate. By **Buchberger's theorem**
   (CLO Ch.2 §6 Theorem 6 p.85), this *is* the Gröbner basis property:
   a basis G is a GB iff every S-pair reduces to 0 mod G. All pairs
   checked when |basis| ≤ 20; deterministic seeded sample of 50 pairs
   above threshold (case-id-keyed RNG via SHA-256 prefix).

Plus the refusal-class check `tag_matches` for the tagged lane.

Tolerance regime: **none**. Exact rational arithmetic via SymPy's QQ
domain. Default determinism tier (ADR-0015 symbolic): bit-identical
cross-platform forever.

## Why these four invariants are necessary AND sufficient

A polynomial set G is a Gröbner basis of `⟨F⟩` iff:

- `G ⊆ ⟨F⟩` (each g ∈ G is a polynomial combination of `f_i`) — this is
  invariant (3).
- `⟨G⟩ ⊇ ⟨F⟩` (each f ∈ F is a polynomial combination of `g_i`) — this
  is invariant (2).
- G is a GB of `⟨G⟩` (S-pair property) — this is invariant (4) by
  Buchberger's theorem.

The first two together give `⟨F⟩ = ⟨G⟩`; the third gives the GB
property. Plus structural shape (1) and the refusal envelope
(`tag_matches`), the certificate is complete.

This is a stronger discipline than byte-equality: a candidate that
emits a non-reduced GB, or a basis with non-monic leading coefficients,
or a permuted basis is admissible — all that matters is that the
mathematical certificate holds.

## Tier structure (80 cases)

```
  hand-curated         (15)
  classical-hard       ( 5)
  stratified-random    (40)   tier-A (15) + tier-B (15) + tier-C (10)
  monomial-degenerate  (10)
  refusal-envelope     (10)
                       ───
                        80
```

### `hand-curated` (15)

Cox-Little-O'Shea Ch.2 §6-§8 worked examples plus Buchberger 1979 §3
illustrative cases. Each carries a `ref` field citing the source page
(e.g. `CLO 4th ed. Ch.2 §6 Example 1 p.84 — the (x²+y, xy+1) classic`).
Includes the canonical bilinear example, the twisted cubic, the
quadric-surface system, two-circle intersection, three-quadric system,
and idempotency (input is already a reduced GB).

### `classical-hard` (5)

The standard Gröbner-basis benchmark families:

- `cyclic-3` (lex + degrevlex) — 3-var; trivial.
- `cyclic-4` (lex) — 4-var; non-trivial reduction chain.
- `cyclic-5` (degrevlex only) — 5-var; lex variant >15s on this device,
  skipped per the 10s budget gate.
- `Katsura-3` (lex) — 4-var combustion-equilibrium model.

`Katsura-4` (5-var) was attempted in lex (~16s on this device, exceeds
the budget) and is therefore skipped per the Phase 2a plan.

Each carries a `wall_clock_baseline_seconds` field measured at
generate-time on this device. The verifier records actual wall-clock
per case as a metric (not an invariant) — ADR-0036 measurement-not-
invariant pin.

### `stratified-random` (40)

Three sub-tiers with deliberately-adversarial generation. The Phase 2a
plan calls out that naive uniform-random sampling produces 95%
generic-easy systems that defeat the bench's purpose; we therefore
bias toward known-hard structures:

| sub-tier | n_vars | total_deg ≤ | m_polys ∈ | count |
| --- | --- | --- | --- | --- |
| tier-A | 2 | 3 | {2, 3} | 15 |
| tier-B | 3 | 4 | {2, 3, 4} | 15 |
| tier-C | 4–5 | 3 | {3, 4} | 10 |

The adversarial mix (per `golden/generate.py`):

| sub-class | fraction | what it stresses |
| --- | --- | --- |
| random-uniform | 30% | broad smoke; coefficients ∈ ℚ with |num|≤9, denom ∈ {1,2,3,5,7} |
| near-coprime LMs | 30% | Buchberger Criterion 1 (coprime-LM pruning) |
| multiplicity-induced | 20% | repeated roots; lex GB does NOT have shape form |
| coefficient-swell-prone | 10% | small-coprime leads → reduction blow-up |
| large-but-structured | 10% | m=4, n_vars=4 with clean LM pattern |

Strategy is reproducible: same seed (`SEED=20260510`) + same constants
⟹ same case set.

### `monomial-degenerate` (10)

Boundary regression cases that exercise pre-conditions and edge
behaviour: single-generator ideals (already a GB), monomial ideals (LMs
only — already a GB), pure-power ideals (`x³, y², z⁴`), the trivial
ideal `{1}` (inconsistent system), already-reduced bases (idempotency),
and a non-zero-mixed-with-zero polynomial. Both `lex` and `degrevlex`
represented.

### `refusal-envelope` (10)

Boundary cases the verifier checks via the `tag_matches` invariant. The
four declared refusal classes:

| class | trigger |
| --- | --- |
| `groebner-basis/non-polynomial` | input contains `sin`, `cos`, `log`, `exp`, `sqrt`, `Abs`, … |
| `groebner-basis/parametric` | input mentions a symbol outside the declared `vars` |
| `groebner-basis/empty-input` | `polys` list is empty |
| `groebner-basis/empty-vars` | `vars` list is empty |

The Phase 3 tool implements detection for each; the SymPy reference
mirrors the same detection mechanically (so the bench has a
known-correct refusal oracle).

## Wire format — the contract Phase 3 must match

### Input (one JSON object)

```jsonc
{
  "polys": ["x**2 + y", "x*y + 1"],
  "vars":  ["x", "y"],
  "order": "lex" | "degrevlex"
}
```

The polynomial expressions use the closed `+ − * / ^` (and `**`)
vocabulary over integer / rational leaves and the symbols in `vars`.
Same vocabulary as `tools/poly-factor` and `tools/solve` already accept.

### Output — happy path

```jsonc
{
  "kind":     "ok",
  "basis":    ["y**3 + 1", "x - y**2"],
  "order":    "lex",
  "vars":     ["x", "y"],
  "n_pairs":  17,
  "warnings": []
}
```

The verifier accepts any permutation of the basis list. Reduced /
monic form is accepted but not required.

### Output — boundary refusal

```jsonc
{
  "kind":    "tagged",
  "tag":     "groebner-basis/<class>",
  "payload": {"detail": "<reason>"}
}
```

`<class>` ∈ {empty-input, empty-vars, parametric, non-polynomial}.

### `ToolError` — malformed input

`ToolError` (process exit 1) is reserved for *malformed* input:
`vars` not a list of symbols, `order` not a string, an expression
fails to parse. These are tool-error contracts (ADR-0003), not
boundary refusals.

## Oracle protocol — dual-witness Wolfram + SymPy

Per ADR-0019 §3 and the Phase 2a plan, every golden case is admitted
iff Wolfram's `GroebnerBasis[]` and SymPy's `groebner()` agree on the
ideal generated. Disagreement is a finding — logged with both outputs
and halts case admission.

**Wolfram invocation** (verified working on this device):

```sh
wolframscript -code 'basis = GroebnerBasis[{f_1, ..., f_m}, {vars}, MonomialOrder -> Lexicographic]; WriteString["stdout", ToString[InputForm[basis]]]'
```

Wolframscript is observed to occasionally segfault during cleanup AFTER
printing well-formed output. We capture stdout-only, parse the result,
and tolerate non-zero exit codes when stdout is well-formed Mathematica
syntax. (See `golden/generate.py:wolfram_groebner` for the protocol.)

**SymPy invocation:**

```python
G = sp.groebner([Poly(p, *vars, domain='QQ') for p in polys], *vars, order='lex')
```

**Agreement** is computed by reducing both bases to canonical form
(monic leading coefficient, sorted by leading monomial) and
byte-comparing the canonical sets. The canonicaliser in `generate.py`
uses SymPy's reduced-GB pass on each oracle's output.

For the `refusal-envelope` tier the oracles are not invoked — the
expected output is the boundary tag, validated against the bench
design.

## Lockstep TS verifier

`golden/verify.ts` is a senior-grade TypeScript port of `verify.py`'s
invariants 1, 2, and 4 using pure BigInt rational arithmetic over
multivariate polynomials (sparse-map representation, leading-term
search by linear scan under the supplied comparator, multivariate
polynomial division per CLO §3 Theorem 3, S-polynomial per §6
Definition 4). Invariant 3 is delegated to verify.py — implementing
it would require porting Buchberger to TS, which is the Phase 3 tool's
job.

`generate.py` runs `verify.ts` on every committed case during golden
refresh and asserts the pass/fail outcome matches `verify.py`. All 80
admitted cases passed lockstep agreement at generation. Future drift
(e.g., a SymPy version bump that changes monomial-order conventions)
will surface as a lockstep disagreement and halt re-generation.

## What is NOT covered

- **Coefficient fields beyond ℚ.** No `𝔽_p[x]`, no algebraic
  extensions, no `ℚ(α)`.
- **Monomial orders beyond lex / degrevlex.** No block ordering, no
  weighted ordering, no general elimination ordering.
- **Solution extraction.** That's the separate `groebner-zerodim-extract`
  bench (Phase 2b, separate dispatch — bead `dt39e`).
- **F4 / F5 algorithms.** The reference implementation is
  Buchberger-based (per the Phase 1 RESEARCH-NOTE-x8d.md §2-G:
  F4 is deferred until v0.1 Buchberger is bench-confirmed).
- **Wall-clock pass/fail.** Per ADR-0036, runtime is recorded as a
  metric; the per-case gate is the four invariants.

## Phase 3 dependency

The bench is the SPEC for Phase 3 (bead `scientist-workbench-x8d`). The
tool `tools/groebner-basis/tool.ts` does not yet exist. `run-candidate.ts`
declares the wire-format contract Phase 3 must match. Phase 3 ships with
a `bun src/cli.ts grade scientist-workbench groebner-basis` invocation
that exercises this bench against the new tool.

The `groebner-basis` substrate (Buchberger + sugar + Gebauer-Möller)
also feeds the `tools/solve` multivariate-polynomial dispatch lane
(ADR-0017 + ADR-0019). Phase 3 ships both substrate and dispatcher
hook in a single bead.
