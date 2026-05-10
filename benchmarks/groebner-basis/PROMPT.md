# Bench — `groebner-basis` (Multivariate Gröbner basis over ℚ)

## How you will be graded

You will be graded on four **mathematical invariants** — not byte-equality
to a reference output. Per ADR-0019 §1, the bench accepts ANY correct
Gröbner basis. The four invariants are:

1. **`shape`** — the candidate output is well-formed.
2. **`ideal_containment_input_to_candidate`** — every input polynomial
   reduces to 0 modulo the candidate basis (so `⟨input⟩ ⊆ ⟨candidate⟩`).
3. **`ideal_containment_candidate_to_input`** — every candidate
   polynomial reduces to 0 modulo a reference GB of the input (so
   `⟨candidate⟩ ⊆ ⟨input⟩`). Together with (2), `⟨input⟩ = ⟨candidate⟩`.
4. **`s_pair_reduces_to_zero`** — every S-pair of candidate basis
   elements reduces to 0 modulo the candidate (Buchberger's theorem).

Plus the `tag_matches` check for the refusal lane.

Tolerance regime: **none**. Exact rational arithmetic via SymPy QQ
domain. A candidate that is "almost a Gröbner basis" within float
tolerance is wrong.

This bench is the **floor**, not the ceiling. Passing it is necessary
but not sufficient. The Phase 3 tool must also conform to the
scientist-workbench seven-artefact contract (CLAUDE.md, README.md,
PRD-v0.2.md).

## Problem statement

Given:

- A finite set `F = {f_1, …, f_m}` of polynomials in `ℚ[x_1, …, x_n]`
  expressed as ASCII expression strings.
- A variable order `vars = [x_1, …, x_n]`.
- A monomial order `order ∈ {lex, degrevlex}`.

Compute a **Gröbner basis** `G` of the ideal `⟨F⟩` under `order`. The
basis must satisfy the four invariants above.

### Algorithm path (recommendation)

Standard Buchberger pipeline with the v0.1 substrate decisions from
`docs/ground-truth/groebner/RESEARCH-NOTE-x8d.md`:

1. **Sparse polynomial substrate** — use `Poly<Rat>` from
   `packages/cas-core/`. Generic ring arithmetic exists; new code
   needed for the order-aware leading-term scan and multivariate
   division.

2. **Pair selection** — sugar strategy (Giovini-Mora-Niesi-Robbiano-
   Traverso 1991), specifically "sloppy sugar." Sugar of input `f` is
   `deg(f)`; sugar of `S(f, g)` is `max(sug(f) − deg(LM(f)) +
   deg(lcm), sug(g) − deg(LM(g)) + deg(lcm))`.

3. **Pair pruning** — both Buchberger criteria in the Gebauer-Möller
   formulation:
   - *Criterion 1* (coprime LMs, Buchberger 1979 §1): if
     `lcm(LM(f), LM(g)) = LM(f) · LM(g)`, drop the pair.
   - *Criterion 2* (chain criterion, Buchberger 1979 §3): if there is
     `h ∈ basis` with `LM(h) | lcm(LM(f), LM(g))` and both `(f, h)`
     and `(g, h)` are already processed, drop the pair.

4. **Inter-reduction** — after the main loop, compute the unique
   reduced Gröbner basis (CLO Ch.2 §7 Theorem 5 p.93). Required for
   FGLM correctness in Phase 2b's `groebner-zerodim-extract` bench;
   not strictly required by this bench's invariants (any correct GB
   passes), but emit the reduced form regardless.

Substrate: new `packages/groebner/` package layered on
`@workbench/cas-core`'s `Poly<Rat>`. Phase 1 RESEARCH-NOTE-x8d.md §5
enumerates the additions needed (a `MonomialOrder` interface,
`drlOrder(vars)`, `lexOrder(vars)`, `polyMultiDivRem`,
`buchbergerDRL`).

## I/O contract (JSON)

### Bench wire format

Raw JSON. Adapter `benchmarks/groebner-basis/run-candidate.ts` bridges
to the tool's canonical `Value` protocol.

### Input (one JSON object on stdin)

```jsonc
{
  "polys": ["x**2 + y", "x*y + 1"],   // expression strings in vars over ℚ
  "vars":  ["x", "y"],                 // variable order
  "order": "lex" | "degrevlex"         // monomial order
}
```

The expressions use the closed `+ − * / ^` (and `**`) vocabulary over
integer / rational leaves and the symbols in `vars`. The same
vocabulary as `tools/poly-factor` and `tools/solve` already accept.

### Output (one JSON object on stdout)

Happy path:

```jsonc
{
  "kind":     "ok",
  "basis":    ["y**3 + 1", "x - y**2"],
  "order":    "lex",
  "vars":     ["x", "y"],
  "n_pairs":  17,                     // metric: pair-pruning effectiveness
  "warnings": []
}
```

Constraints:

- **`basis`** — list of expression strings, each parsing as a
  polynomial in `vars` over ℚ. The verifier accepts any permutation;
  reduced / monic form is accepted but not required.
- **`order`** — must equal `input.order`.
- **`vars`** — must equal `input.vars` (same list, same order).
- **`n_pairs`** — total pair count examined (informational metric;
  the verifier does not gate on this).
- **`warnings`** — informational list of strings.

Boundary refusal:

```jsonc
{
  "kind":    "tagged",
  "tag":     "groebner-basis/<class>",
  "payload": {"detail": "<reason>"}
}
```

`<class>` is one of:

| class | trigger |
| --- | --- |
| `groebner-basis/non-polynomial` | input contains `sin`, `cos`, `log`, `exp`, `sqrt`, `Abs`, … |
| `groebner-basis/parametric` | input mentions a symbol outside `vars` |
| `groebner-basis/empty-input` | `polys` list is empty |
| `groebner-basis/empty-vars` | `vars` list is empty |

### `ToolError` — malformed input

`ToolError` (process exit 1) is reserved for *malformed* input
(ADR-0003): `polys` not a list, `vars` not a list of symbols, `order`
not a string, an expression fails to parse. These are NOT boundary
refusals — they are tool-error contracts.

## Test set tiers

`golden/inputs.json` contains **80 cases** spanning five tiers:

| Tier | Cases | What it probes |
| --- | --- | --- |
| `hand-curated` | 15 | CLO Ch.2 §6-§8 worked examples, Buchberger 1979 §3, the (x²+y, xy+1) classic, twisted cubic, two-circle intersection, idempotency, three-quadric system. Each case carries a `ref` field citing the source. |
| `classical-hard` | 5 | cyclic-3 (lex+degrevlex), cyclic-4 (lex), cyclic-5 (degrevlex only — lex >15s on this device), Katsura-3 (lex). Each carries a `wall_clock_baseline_seconds` field. |
| `stratified-random` | 40 | Three sub-tiers (n=2 / n=3 / n=4-5) with the adversarial-mix generation: 30% random-uniform, 30% near-coprime LMs (Crit 1 stress), 20% multiplicity-induced (lex GB lacks shape form), 10% coefficient-swell-prone, 10% large-but-structured. Reproducible with `SEED=20260510`. |
| `monomial-degenerate` | 10 | single-generator ideals, monomial ideals, pure-power ideals, trivial ideal {1}, already-reduced bases. Both `lex` and `degrevlex`. |
| `refusal-envelope` | 10 | non-polynomial / parametric / empty-input / empty-vars boundary cases. Verifier asserts kind=tagged with the correct class. |

Total: 80 cases × (4 invariants for happy lane + 1 invariant for refusal
lane) ≈ 350 invariant assertions.

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bun src/cli.ts grade scientist-workbench groebner-basis
```

(Run from the corpus repo root with `WORKBENCH_ROOT` defaulting to
`../scientist-workbench`.)

For inner-loop iteration during tool development:

```sh
PATH=/home/tobias/.amp/bin:$PATH bun src/cli.ts grade scientist-workbench groebner-basis --max-cases=20
```

### Files

- `golden/inputs.json` — every test case (80 cases, sha256-pinned).
- `golden/expected.json` — reference outputs from SymPy + Wolfram
  (dual-witness oracle agreement enforced at generate-time;
  consulted by the verifier only for `expected.tag` cross-check).
- `golden/verify.py` — the canonical 4-invariant verifier (per
  `manifest.toml`'s `verifier.cmd = "python3"`).
- `golden/verify.ts` — TypeScript lockstep verifier (NOT canonical;
  invariants 1, 2, 4 only — invariant 3 is delegated to verify.py).
- `golden/verifier_protocol.md` — exact specification per check.
- `golden/generate.py` — reproducible golden generation (dual-
  witness oracle + lockstep TS check).
- `golden/test_mutations.py` — mutation-prove harness (≥6 RED
  perturbations of the SymPy reference).
- `golden/oracle_log.json` — per-case oracle agreement record.
- `reference/groebner_reference.py` — Python reference (SymPy
  `sp.groebner` wrapped to emit the bench wire format).
- `run-candidate.ts` — wire-format adapter to `tools/groebner-basis`
  (Phase 3 tool — does not yet exist).

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun. No FFI.
- Seven-artefact contract.
- Default determinism tier (symbolic, bit-identical cross-platform
  forever — no `numerical: true` annotation; ADR-0015).
- Boundary categories (ADR-0003):
  - `tagged "groebner-basis/non-polynomial"` for non-polynomial heads.
  - `tagged "groebner-basis/parametric"` for foreign symbols.
  - `tagged "groebner-basis/empty-input"` for empty polys list.
  - `tagged "groebner-basis/empty-vars"` for empty vars list.
  - `ToolError` for malformed input.
- Substrate: new `packages/groebner/` package layered on
  `@workbench/cas-core` (per RESEARCH-NOTE-x8d.md §5).
- Pair selection: sugar (Giovini et al. 1991), sloppy variant.
- Pair pruning: Buchberger Criterion 1 (coprime LMs) + Gebauer-Möller
  Criterion 2 (chain).
- Output: the unique reduced Gröbner basis (verifier accepts any
  correct GB, but the canonical reduced form is required for FGLM
  correctness in Phase 2b's downstream bench).
- The basis-list is canonicalised (sorted by leading monomial under
  the requested order, descending); the verifier accepts any order,
  but the bench's `expected.json` is deterministic for diff-friendly
  golden refresh.

## What you must do (Phase 3)

1. Read the Phase 1 ground-truth note:
   `docs/ground-truth/groebner/RESEARCH-NOTE-x8d.md` — especially
   §2 (the seven algorithmic decisions) and §5 (substrate
   dependencies).
2. Read the local Buchberger 1979 (`buchberger-1979-two-criteria.pdf`),
   Giovini et al. 1991 (`giovini-mora-niesi-robbiano-traverso-1991-
   sugar-cube.pdf`), and Cox-Little-O'Shea Ch.2 PDFs.
3. Implement `MonomialOrder`, `drlOrder(vars)`, `lexOrder(vars)`,
   `leadingTerm(p, order)`, `polyMultiDivRem(f, G, order)` in
   `packages/groebner/src/order.ts` and `packages/groebner/src/division.ts`.
4. Implement the Buchberger main loop with sugar pair selection and
   Buchberger Criteria 1+2 in `packages/groebner/src/buchberger.ts`.
5. Implement post-loop inter-reduction in
   `packages/groebner/src/reduce.ts`.
6. Implement `tools/groebner-basis/` to seven-artefact contract.
7. Run `bun src/cli.ts grade scientist-workbench groebner-basis` from
   the corpus repo until 80/80 cases pass.
8. Run `bun run check` in the workbench repo.
9. Report per-tier pass/fail totals and the `n_pairs` distribution
   (a metric for cross-implementation comparison).

## What this bench does NOT cover

- **Coefficient fields beyond ℚ** (no `𝔽_p[x]`, no algebraic extensions).
- **Monomial orders beyond lex / degrevlex** (no block, weighted, or
  general elimination orders).
- **Solution extraction** (multivariate `solve` dispatch). That's the
  separate `groebner-zerodim-extract` bench (Phase 2b, bead `dt39e`).
- **F4 / F5 algorithms.** The v0.1 substrate is Buchberger-based; F4
  is deferred until v0.1 ships and coefficient-swell is observed to
  be the bottleneck.
- **Performance / wall-clock pass/fail.** Per ADR-0036, runtime is a
  metric, not an invariant. Drift `> 3×` baseline triggers a
  corpus-query warning, not a per-case failure.
- **Pair-pruning effectiveness as an invariant.** `n_pairs` is
  informational. A candidate that doesn't apply Criterion 1 will pay
  unnecessary cost (slower wall-clock, higher `n_pairs`) but produces
  correct output — the verifier admits it.
