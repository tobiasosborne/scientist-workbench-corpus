# Bench — `meijer-g` (Meijer G-function)

## How you will be graded

You will be graded on **CORRECTNESS** and **NUMERICAL HONESTY**.

Produce the most elegant, most efficient, most numerically faithful
TypeScript implementation you can. This bench is the *floor*, not the
ceiling — passing is necessary but not sufficient. The tool must also
conform to the scientist-workbench seven-artefact contract (see
`CLAUDE.md`, `README.md`, `PRD-v0.2.md`).

The verifier checks invariants, not byte-equality: arbitrary-precision
direct-residue evaluation is *not* unique in working precision (the
cancellation-bump retry may converge at a higher `working_precision`
than necessary), and the relative error of two faithful evaluators
need not match below the requested target precision. Every check has a
tolerance derived from the problem's analytic regime — Tier-0 anchors
get `1e-(precision − 4)`; the parameter-coalescence tier relaxes to
`1e-(precision − 12)` to absorb Johansson's `hmag` perturbation cost
(see [`DESCRIPTION.md`](DESCRIPTION.md) §"Why these tolerances").

## Problem statement

Implement evaluation of the Meijer G-function

```
                m,n  ⎛ a₁,…,aₚ │   ⎞       1     ⌠   ∏ Γ(b_j − s) · ∏ Γ(1 − a_j + s)
              G    ⎜          │ z ⎟ =  ─────  ⎮  ─────────────────────────────────────  z^s ds
                p,q  ⎝ b₁,…,b_q │   ⎠     2πi   ⌡L  ∏ Γ(1 − b_j + s) · ∏ Γ(a_j − s)
```

with three contour choices `L_-`, `L_+`, `L_∞` whose admissibility
depends on `(m, n, p, q)` and on `|z|` (DLMF §16.17.2).

The implementation that passes this bench is the in-tree
`tools/meijer-g/`, substrate `@workbench/meijer-core`. It composes
four algorithmic lanes:

  1. **symbolic dispatch** (`meijergSymbolic`) — Adamchik–Marichev /
     Roach pattern-table reduction; emits closed-form expressions in
     the `cas-core` special-function vocabulary.
  2. **Slater residue summation** (`meijergSlater`) — Series-1 /
     Series-2 selection by `(p, q, m, n, |z|)`; Johansson `hmag`
     perturbation for parameter coalescence; cancellation-driven
     precision-bump retry.
  3. **Mellin–Barnes contour quadrature** (`meijergContour`) —
     vertical-contour `Re(s) = c` BigComplex G7K15 driver with
     Stirling-rate-derived truncation.
  4. **Braaksma asymptotic** (`meijergAsymptotic`) — principal-sector
     algebraic dominant asymptotic for `|z| → ∞`.

The dispatcher itself is **cost-ascending**: `symbolic` → `Slater` →
`contour` → `asymptotic` → refuse.  Each lane's pre-filter
(`canUseSlater` / `canUseContour` / `canUseAsymptotic`) decides
"applicable here?" before any numerical work runs.  The dispatch loop
is a flat switch over four lanes; no bespoke per-layer envelope
handling at the call site (ADR-0027 §1).

The `--precision=<int>` flag specifies the target relative precision
in **decimal digits**.  Default 50; cap 100 000 (per ADR-0020).

## I/O contract (JSON)

### Bench wire format

The bench passes raw JSON with **string** representations of every
number (decimal or rational `"p/q"`).  The adapter
`bench/meijer-g/run-candidate.ts` translates to the canonical Value
protocol the tool itself speaks; rational strings like `"1/3"` are
expanded to decimal at sufficient working precision before being
passed to `bigfloat::cfromStrings`.

### Input (one JSON object on stdin)

```jsonc
{
  "an":         [{"re": "<dec-or-rational>", "im": "<dec-or-rational>"}, ...],
  "ap":         [...],
  "bm":         [...],
  "bq":         [...],
  "z":          {"re": "<dec-or-rational>", "im": "<dec-or-rational>"},
  "precision":  <int>,
  "request_mode": "auto" | "symbolic-required" | "numerical-required"  // optional
}
```

### Output — symbolic match

```jsonc
{
  "kind": "symbolic",
  "rule": "bateman-5-6-8",
  "source": "Bateman §5.6 (8)",
  "note": "G^{1,0}_{0,1}(_; 0 | z) = e^{-z}",
  "method": "symbolic-dispatch",
  "expr": <opaque AST blob>
}
```

### Output — numerical success

```jsonc
{
  "kind": "numerical",
  "value": {"re": "<dec>", "im": "<dec>"},
  "achieved_precision": <int>,
  "method": "slater-series-1" | "slater-series-2" | "mellin-barnes" | "braaksma-algebraic",
  "working_precision": <int>,
  "warnings": [<string>, ...],
  "diagnostics": <record>
}
```

### Output — refusal (structural boundary)

```jsonc
{"kind": "tagged",
 "tag":  "meijer-g/<class>",
 "payload": {"reason": "<string>", "ruled_out_methods": [...]}}
```

Refusal classes:

  * `out-of-region` — every applicable layer refused.
  * `non-finite-input` — z or a parameter contains NaN/Inf.
  * `degenerate-shape` — m + n = 0.
  * `symbolic-required-no-match` — request_mode = symbolic-required
    and no rule matched.
  * `forced-method-refused` — `--force-method=<lane>` and that lane
    refused.
  * `input-error` — malformed precision / out-of-range flag.

### Output — tool-error path

If the tool's input fails schema validation (a `ToolError` with
`exit 1` from the runner), the adapter writes:

```jsonc
{"kind": "tool_error", "name": "<class>", "message": "<string>"}
```

## Invariants checked per case

| # | Name                       | Path        | Tolerance |
|---|----------------------------|-------------|-----------|
| 1 | `no_tool_error`            | both        | strict |
| 2 | `shape`                    | success     | strict (kind ∈ {symbolic, numerical}) |
| 3 | `finite_value`             | numerical   | re/im parse as finite mpf |
| 4 | `method_admissible`        | both        | numerical: ∈ {slater-{1,2}, mellin-barnes, braaksma-algebraic}; symbolic: == 'symbolic-dispatch' |
| 5 | `self_reported_precision`  | numerical   | `0 ≤ achieved_precision ≤ requested` |
| 6 | `value_accuracy`           | numerical   | per-case `tolerance_rel` |
| 7 | `symbolic_rule_present`    | symbolic    | non-empty `rule` field |
| 8 | `boundary_envelope`        | refusal     | tag-strict |

Tolerances per case live in `golden/expected.json::cases[i].tolerance_rel`.

## Test set tiers

`golden/inputs.json` contains **~95 cases** across nine tiers; see
[`DESCRIPTION.md`](DESCRIPTION.md) for the full breakdown.

Truth values come from one of three sources, declared per case via
`expected.truth_method`:

  * `elementary-rhs@200dps`         — Tier 0 anchors; closed-form RHS
                                       evaluated directly.
  * `consensus-wolfram-mpmath@110dps` — Wolfram + mpmath agree at 80+
                                       sig figs.
  * `mpmath-only@110dps`             — Wolfram unavailable; mpmath
                                       sole witness.

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/meijer-g bun bench/meijer-g/run-candidate.ts
```

The harness pipes each test case through your program and through
`golden/verify.py`, prints a per-check summary, and exits 0 only if
every case is `"pass": true`.

### Files

- `golden/inputs.json` — every test case (id, tier, an/ap/bm/bq/z, precision, request_mode).
- `golden/expected.json` — pinned truth values + tolerance contracts.
- `golden/verify.py` — invariant verifier (mpmath at 80 dps reference).
- `golden/oracle-disagreements.log` — Wolfram/mpmath disagreement
  cases that were quarantined out of the golden.
- `reference/generate-truth.py` — the seeded generator.  Re-running
  it produces deterministic inputs.json + expected.json given the
  same mpmath / wolframscript versions.
- `run-candidate.ts` — wire-format adapter (raw JSON ↔ canonical Value
  protocol; dispatches to the in-tree tool via `executeToolDef`).

## Hard constraints (sci-wb-specific)

- **Pure TypeScript on Bun.**  No FFI, no WASM, no `child_process`.
  The bench's adapter calls `executeToolDef(def, input, flags)`
  directly because the in-process surface (`@workbench/compose`'s
  `runWorkbench`) does not yet auto-merge ADR-0020's standard
  `--precision` flag for arbprec tools — that's a known gap, filed
  as follow-up beads `lc1` / `rn2`.
- **Seven-artefact contract.** Schema (declared via `S.*`), examples
  (≥3), invariants, `--test` hook, goldens directory, README, source.
- **`arbprec: true` annotation** (ADR-0020).  Provenance carries the
  tool name + version + input hash; the precision flag is part of
  the input identity.
- **Closed expression vocabulary.**  Parameters and `z` are real and
  complex `bigcomplex` values constructed via `cfromStrings`; no
  expressions, no foreign symbols.
- **Boundary categories (ADR-0003 + ADR-0027).** As listed above.
  `ToolError` is reserved for malformed input.
- **Determinism.** `arbprec: true` is *bit-identical cross-platform
  forever* given an explicit `--precision`.

## What you must do

1. **Read** `CLAUDE.md`, `docs/adr/0019-solve-bench-discipline.md`,
   `docs/adr/0027-meijerg-dispatcher.md`,
   `tools/meijer-g/tool.ts`, `packages/meijer-core/src/dispatcher.ts`,
   `bench/hypergeometric-pfq/` (the reference bench shape).
2. **Internalise the two principles** (`bd memories two-principles`):
   "what would a TypeScript expert expect/want" and "irresistible to
   agents".
3. **Run the bench** until 100% across all checks:
   ```sh
   PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
       bench/meijer-g bun bench/meijer-g/run-candidate.ts
   ```
4. **Run** `bun run check` until green.

## Things that will tempt you and which are wrong

- **Returning a `Number`-typed Float64 truth and claiming "good
  enough" arbitrary precision.** The tool is `arbprec: true`; the
  contract is that 50 dps means `1e-50` relative.  A float64 truth
  is capped at `2.22e-16` relative — wrong for every case beyond
  precision = 15.
- **Catching slow-convergence and silently returning a partial sum
  with a low `achieved_precision`.**  The tool's contract is that
  `achieved_precision` ≤ requested; it must *refuse* (tag the
  boundary) when the cancellation budget is exhausted.  The
  verifier's `value_accuracy` check is the ground truth; lying
  about achieved precision will fail the bench even if the value
  happens to be close.
- **Routing the quarantine band to Slater anyway** to "pass" Tier G's
  refusal cases.  The dispatcher's pre-filter (`canUseSlater`)
  refuses the band by design (per ORACLE-STRATEGY.md).  Forcing
  through is a regression; the bench's `boundary_envelope` only
  accepts refusals on Tier G.

The two principles are the highest-priority decision rule.
