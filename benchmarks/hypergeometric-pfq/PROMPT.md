# Bench — `hypergeometric-pfq` (arbitrary-precision pFq)

## How you will be graded

You will be graded on **CORRECTNESS** and **NUMERICAL HONESTY**.

Produce the most elegant, most efficient, most numerically faithful
TypeScript implementation you can. This bench is the *floor*, not the
ceiling — passing is necessary but not sufficient. The tool must also
conform to the scientist-workbench seven-artefact contract (see
`CLAUDE.md`, `README.md`, `PRD-v0.2.md`).

The verifier checks invariants, not byte-equality: arbitrary-precision
direct-series evaluation is *not* unique in working precision (the
cancellation-bump retry may converge at a higher `working_precision`
than necessary), and the relative error of two faithful evaluators
need not match below the requested target precision. Every check has a
tolerance derived from the problem's analytic regime — tier 0 closed-
form anchors get `1e-(precision-2)`; the slow-convergence near-unit-
circle band relaxes to `1e-(precision-8)` to absorb the cancellation
floor (see `golden/verifier_protocol.md`).

## Problem statement

Implement arbitrary-precision evaluation of the generalised
hypergeometric series

```
                                  z^k     ∏ⱼ (aⱼ)_k
  pFq(a₁,…,aₚ; b₁,…,b_q; z) = Σ  ──── · ─────────────
                              k≥0  k!     ∏ⱼ (bⱼ)_k
```

where `(x)_k = x(x+1)⋯(x+k−1)` is the rising-factorial Pochhammer
symbol, `(x)_0 = 1`. Convergence regime depends on the relation
between `p` and `q`:

  * `p ≤ q` — series converges for all finite `z`
  * `p == q + 1` — converges in the open unit disc `|z| < 1`
  * `p > q + 1` — *asymptotic only* (formally divergent, summed via
    Borel resummation; deferred to a future shard).

The `--precision=<int>` flag specifies the target relative precision
in **decimal digits**. Default 50; cap 100 000 (per ADR-0020). The
tool must:

  * Use `@workbench/bigfloat` arithmetic for all internal computation —
    no `Number`-typed shortcuts (the bit-determinism contract is
    BigInt-substrate, ADR-0020).
  * Detect cancellation in the direct power series and retry at higher
    working precision until the achieved precision meets the request,
    or refuse with a structured `tagged "hypergeometric-pfq/non-
    convergent"` envelope.
  * Detect parameter poles (a `b_j` is a non-positive integer such
    that `(b_j)_k` vanishes during summation) and refuse with a
    structured `tagged "hypergeometric-pfq/parameter-pole"` envelope.
  * Refuse `p > q + 1` and `p == q + 1` with `|z| ≥ 0.99` via
    `tagged "hypergeometric-pfq/non-convergent"` (deferred to
    asymptotic / analytic-continuation paths).

The implementation that passes this bench is the in-tree
`tools/hypergeometric-pfq/`, substrate `@workbench/hypergeometric`.

## I/O contract (JSON)

### Bench wire format

The bench passes raw JSON with **string** representations of every
number (decimal or rational `"p/q"`). The adapter
`bench/hypergeometric-pfq/run-candidate.ts` translates to the canonical
Value protocol the tool itself speaks. Rational strings like
`"1/3"` are expanded to decimal at sufficient working precision before
being passed to `bigfloat::cfromStrings`; this matches the Wolfram /
mpmath input convention and keeps the corpus exactly representable.

### Input (one JSON object on stdin)

```jsonc
{
  "a":         [{"re": "<dec-or-rational>", "im": "<dec-or-rational>"}, ...],
  "b":         [{"re": "<dec-or-rational>", "im": "<dec-or-rational>"}, ...],
  "z":         {"re": "<dec-or-rational>", "im": "<dec-or-rational>"},
  "precision": <int>     // decimal digits requested
}
```

### Output — success path

```jsonc
{
  "value": {"re": "<dec>", "im": "<dec>"},   // candidate's pFq value
  "achieved_precision":  <int>,               // dps actually achieved
  "method":              "closed-form-0F0" | "closed-form-1F0" | "direct-series",
  "n_terms":             <int>,                // direct-series terms summed
  "working_precision":   <int>,                // last-attempt working bits
  "warnings":            [<string>, ...]
}
```

### Output — refusal path (structural boundary)

```jsonc
{"kind": "tagged",
 "tag":  "hypergeometric-pfq/non-convergent",
 "payload": {"reason": "<string>"}}
```

```jsonc
{"kind": "tagged",
 "tag":  "hypergeometric-pfq/parameter-pole",
 "payload": {"which": "a"|"b", "which_idx": <int>}}
```

### Output — tool-error path

If the tool's input fails schema validation (a `ToolError` with
`exit 1` from the runner), the adapter writes:

```jsonc
{"kind": "tool_error", "name": "<class>", "message": "<string>"}
```

The verifier rejects every case whose candidate is a `tool_error` (no
case in this bench expects a malformed-input refusal — those are
`ToolError`s on legitimately broken inputs and are out of scope).

## Invariants checked

The verifier runs **5 + 1 invariant checks** per case (see
`golden/verifier_protocol.md` for derivations):

| # | Name                       | Path        | Tolerance           |
|---|----------------------------|-------------|---------------------|
| 1 | `no_tool_error`            | both        | strict              |
| 2 | `shape`                    | success     | strict              |
| 3 | `finite_value`             | success     | strict              |
| 4 | `method_admissible`        | success     | `∈ {0F0, 1F0, ds}` |
| 5 | `self_reported_precision`  | success     | `0 ≤ ap ≤ requested`|
| 6 | `value_accuracy`           | success     | per-case `tolerance_rel` |
| 7 | `boundary_envelope`        | refusal     | tag-strict, payload-substring |

Tolerances per case live in `golden/expected.json::cases[i].tolerance_rel`,
pinned by tier:

  * **Tier 0** (closed-form anchors)         — `1e-(p−2)` (here `p=50`).
  * **Tier A** (generic happy path)          — `1e-(p−2)`.
  * **Tier B** (large parameters)            — `1e-(p−4)` to `1e-(p−6)` —
                                                 cancellation in the direct
                                                 power series eats a few
                                                 dps when the parameters
                                                 are tens-of-units in
                                                 magnitude; documented per
                                                 case.
  * **Tier C** (near-unit-circle)            — `1e-(p−4)` to `1e-(p−10)` —
                                                 slow convergence + summand
                                                 cancellation; the tightest
                                                 admitted slow case relaxes
                                                 to `1e-40` at `p=50`.
  * **Tier D** (parameter coalescence)       — `1e-(p−4)` to `1e-(p−6)` —
                                                 the bumped-precision retry
                                                 must fire and surmount.
  * **Tier E** (refusal)                     — N/A; envelope check.

## Test set tiers

`golden/inputs.json` contains **53 cases** across six tiers:

| Tier | Cases | What it probes |
|------|-------|----------------|
| **0** — closed-form anchors    | 11 | identities reducing to `exp / log / sin / cos / sinc / (1−z)^{−a}` — the cross-check witness is the elementary side, not mpmath. |
| **A** — generic happy path     | 15 | `0F1, 1F1, 2F1, 3F2` at mid-range parameters and `|z| ∈ [0, 0.5]` — pure direct-series exercise. Includes one case at `precision=100` to stress the `--precision` flag. |
| **B** — large parameters       | 10 | `a, b ∈ [10, 100]` integer or half-integer; the cancellation-driven precision-bump retry should keep accuracy at requested precision. |
| **C** — near-unit-circle       |  8 | `|z| ∈ [0.85, 0.98]` with `p == q + 1`; slow-convergence regime; many summands; cancellation across alternating signs. |
| **D** — parameter coalescence  |  5 | integer-spaced numerator/denominator parameters; near-Pochhammer-cancellation; the bumped-precision retry is the discriminator. |
| **E** — refusal cases          |  4 | `p > q + 1` (asymptotic-only); `\|z\| ≥ 0.99` with `p == q + 1`; `1F0` at `z = 1`; the famous `2F1(1,1;2;−1) = log 2` boundary case (the tool conservatively refuses; an analytic-continuation path is a deferred v0.2 capability). |

**Total**: 53 cases × ~5 checks per success-case = ~250 invariant
assertions plus 4 structural-refusal envelope checks.

The headline tier is **C**: convergence in `[0.95, 0.99)` is the
slowest the tool admits — the iteration cap is sized analytically to
`target · ln 2 / ln(1/|z|)`, so `|z| = 0.98` at 50 dps requires
`~5700` summands; this tier verifies the tool's iteration-cap
selection is honest (not silently trimmed) and that the cancellation
detector flags but doesn't quarantine the genuine slow-convergers.

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/hypergeometric-pfq bun bench/hypergeometric-pfq/run-candidate.ts
```

The harness pipes each test case through your program and through
`golden/verify.py`, prints a per-check summary, and exits 0 only if
every case is `"pass": true`.

### Files

- `golden/inputs.json` — every test case (id, tier, a/b/z, precision).
- `golden/expected.json` — pinned truth values + tolerance contracts;
  consulted by the verifier for the value-accuracy check.
- `golden/verify.py` — invariant verifier (mpmath at 80 dps for the
  comparison reference).
- `golden/verifier_protocol.md` — what each check pins, with the
  derivation of every tolerance.
- `reference/generate-mpmath-truth.py` — the seeded generator.
  Re-running it produces byte-identical inputs.json + expected.json
  given the same mpmath / wolframscript versions; consensus failures
  abort the build, never silently propagate.
- `run-candidate.ts` — wire-format adapter (raw JSON ↔ canonical Value
  protocol; dispatches to the in-tree tool via `executeToolDef`).

## Hard constraints (sci-wb-specific)

- **Pure TypeScript on Bun.** No FFI, no WASM, no `child_process`.
  The bench's adapter calls `executeToolDef(def, input, flags)`
  directly because the in-process surface (`@workbench/compose`'s
  `runWorkbench`) does not yet auto-merge ADR-0020's standard
  `--precision` flag for arbprec tools — that's a known gap, filed
  as a follow-up bead.
- **Seven-artefact contract.** Schema (declared via `S.*`), examples
  (≥10), invariants, property tests / `--test` hook, goldens
  directory, README, source.
- **`arbprec: true` annotation** (ADR-0020). Provenance carries the
  tool name + version + input hash; the precision flag is part of the
  input identity, so different precisions cache to different output
  hashes.
- **Closed expression vocabulary.** The corpus parameters are real and
  complex `bigcomplex` values constructed via `cfromStrings`; no
  expressions, no foreign symbols.
- **Boundary categories (ADR-0003):**
  - `tagged "hypergeometric-pfq/non-convergent"` — `p > q + 1`,
    `|z| ≥ 0.99` with `p == q + 1`, `1F0(a;;1)` singular,
    cancellation-driven precision-bump exhausted.
  - `tagged "hypergeometric-pfq/parameter-pole"` — exact non-positive
    integer `b_j` produces a vanishing Pochhammer.
  - `ToolError` — only for malformed input (negative precision,
    non-record at top level, etc.). Never used for refusal.
- **Determinism.** `arbprec: true` is *bit-identical cross-platform
  forever* given an explicit `--precision`. The bench cases run at
  fixed precision; same-input invocation produces byte-identical
  output.

## What you must do

1. **Read** `CLAUDE.md`, `docs/adr/0019-solve-bench-discipline.md`,
   `docs/adr/0020-arbprec-determinism-tier.md`,
   `tools/hypergeometric-pfq/tool.ts`,
   `packages/hypergeometric/src/pfq.ts`,
   `bench/linalg-qr/` (the reference bench shape).
2. **Internalise the two principles** (`bd memories two-principles`):
   "what would a TypeScript expert expect/want" and "irresistible to
   agents".
3. **Run the bench** until 100% across all checks:
   ```sh
   PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
       bench/hypergeometric-pfq bun bench/hypergeometric-pfq/run-candidate.ts
   ```
4. **Run** `bun run check` until green.
5. **Add a worklog shard** at `docs/worklog/079-bench-hypergeometric-pfq.md`
   following the standard structure.
6. **Update**: workbench `README.md` catalog row.
7. **Report** per-check totals.

## Things that will tempt you and which are wrong

- **Returning a `Number`-typed Float64 truth and claiming "good
  enough" arbitrary precision.** The tool is `arbprec: true`; the
  contract is that 50 dps means `1e-50` relative. A float64 truth is
  capped at `2.22e-16` relative — wrong for every case beyond
  precision = 15.
- **Catching slow-convergence and silently returning the partial sum
  with a low `achieved_precision`.** The tool's contract is that
  `achieved_precision` ≤ requested; it must *refuse* (tag the
  boundary) when the cancellation budget is exhausted. The verifier's
  `value_accuracy` check is the ground truth; lying about achieved
  precision will fail the bench even if the value happens to be
  close.
- **Quarantining a Tier-C `|z| = 0.97` case as "non-convergent" to
  speed up the bench.** The tool's iteration cap is sized
  analytically to admit it. Refusing inside the admitted region is
  a regression; the bench's `boundary_envelope` only accepts
  refusals on Tier E.

The two principles are the highest-priority decision rule.
