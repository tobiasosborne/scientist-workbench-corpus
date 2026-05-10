# `meijer-g` — design notes and rationale

This document is the longer-form companion to `PROMPT.md`.  It explains
*why* the bench has the shape it does — what each tier discriminates,
why each tolerance, why the corpus stops where it does.

## Why MeijerG is the headline mega-test

The Meijer G-function is the apex of the special-function pantheon:
every `pFq`, every Bessel / Whittaker / parabolic-cylinder / Coulomb,
every Legendre / Chebyshev / Laguerre / Hermite / Gegenbauer, every
error / Fresnel / exponential-integral, every polylogarithm / Lerch,
plus all elementary `sin / cos / exp / log / arcsin / arctan / pow`
cases is a `MeijerG` value at specific parameters and argument.

The `tools/meijer-g` dispatcher (Layer 7 of the
[`tstournament` problem-13 campaign](../../../tstournament/ts-bench-infra/problems/13-meijer-g/PLAN.md))
composes four algorithmic layers — symbolic dispatch + Slater
residue-summation + Mellin–Barnes contour quadrature + Braaksma
asymptotic — into a cost-ascending dispatch ladder with honest
refusal.  This bench is the validation surface for that contract.

A regression in any one lane is a regression in everything downstream.
The bench is the discriminator that the inner-loop unit tests
(`tools/meijer-g/tool.test.ts`, 35 cases; `packages/meijer-core/`,
~162 tests) cannot be on their own — the unit tests pin per-lane
correctness; the bench is *graded* test discrimination across the
nine documented operating modes.

## The four failure modes (per the campaign DESCRIPTION.md)

The campaign brief names four distinct failure modes that the bench
must discriminate:

1. **Naive Slater residue summation** when two `b`-parameters differ
   by an integer — the simple-pole formula gives `0/0`; correct
   handling needs Johansson `hmag` perturbation or polygamma derivative
   residues.  **Tier E** probes this.
2. **Stokes-phenomenon misses** at `|z| ≈ 1` in the balanced `p == q ∧
   m + n == p` case, where Slater's two natural series both diverge
   term-wise on the boundary.  **Tier G** asserts the dispatcher
   correctly refuses (quarantine band per
   [ORACLE-STRATEGY.md](../../../tstournament/ts-bench-infra/problems/13-meijer-g/ORACLE-STRATEGY.md)).
3. **Wrong branch convention** on the negative real axis (`z = −r ±
   iε`).  **Tier F** probes this.
4. **Symbolic-dispatch myopia** — failing to recognise that the input
   has a closed-form reduction and falling back to a slow numerical
   eval.  **Tier 0 + Tier A + Tier B** probe this; Tier H's speed-gate
   discriminates implementations that always Slater-fall-through.

## Why nine tiers

Per [VERIFIER-PROTOCOL.md §"Tier-by-tier tolerance table"](../../../tstournament/ts-bench-infra/problems/13-meijer-g/VERIFIER-PROTOCOL.md):

| Tier | Cases | Probes |
|------|-------|--------|
| **0** Closed-form anchors  | ~36 | Identities reducing to elementary functions, with the truth value computed *directly from the elementary RHS* at 200 dps — bug-immune to either oracle's MeijerG codepath. |
| **A** Elementary symbolic  | ~13 | `request_mode = symbolic-required` cases that the dispatcher's symbolic lane *must* match.  Currently uses integer-only parameters because rational-real BigComplex inputs don't yet flow through `bigcomplexToSymbolicValue` (filed as a follow-up). |
| **B** Special-fn numerical | ~8 | Bessel-K / erfc / Γ-product reductions.  Most route through the *numerical* lane in v0.1 because the symbolic-table doesn't yet include the multi-slot Bessel / Whittaker patterns (those are `hv0.6.*` follow-ups). |
| **C** Generic Slater       | ~16 | Middle of parameter space, `\|z\|` away from coalescence and the unit circle. The dispatcher's bread-and-butter numerical lane. |
| **D** Anti-Stokes          | ~8  | `\|z\| ∈ [0.95, 1.05]` (just outside the quarantine band where applicable) and moderate-`\|z\|` where multiple lanes apply. Probes lane selection at the boundary. |
| **E** Coalescence          | ~9  | Integer-spaced poles in `bm` or `aN`. Probes Johansson's `hmag` perturbation retry, the empirical precision estimator (worklog 084 / bead `7usr`), and the structured refusal for ≥3-pole clusters (worklog 084 / bead `fwsz`). |
| **F** Branch-cut           | ~7  | `z` near or on the negative real axis; Schwarz reflection probes. |
| **G** Refusal              | ~3  | Quarantine band; `p > q+1 ∧ \|z\| < 1`; degenerate-shape; symbolic-required-no-match. Asserts the right `tagged "meijer-g/<class>"` envelope. |
| **H** Speed-gate           | cross-cutting | Tier H is *not* a separate set of cases; it's a re-use of Tier C/D/E/F cases with an additional `elapsed_ms ≤ 1500` assertion at 50 dps.  Listed in `tier-h.json` by id. |

Total: ~95 unique cases.  Smaller than the bead-spec's "~250" because
many of those would be redundant H cases (rule-of-thumb 2.5× cases per
tier vs. the actual non-H content).  The H tier itself is structured as
a cross-cutting subset, not 200 separate cases — this is the
`bench/hypergeometric-pfq` precedent.

## Why these tolerances

Per [VERIFIER-PROTOCOL.md §"Tier-by-tier tolerance table"](../../../tstournament/ts-bench-infra/problems/13-meijer-g/VERIFIER-PROTOCOL.md):

| Tier | Tolerance |
|------|-----------|
| 0    | `1e-(precision − 4)` (e.g. `1e-46` at 50 dps) — RHS-evaluated, lowest noise floor; tightest tolerance the bench admits. |
| A    | `1e-(precision − 4)` to `1e-(precision − 6)` — symbolic match canonicalises to known forms; tightest where the rule is exact. |
| B    | `1e-(precision − 6)` to `1e-(precision − 8)` — multi-residue Slater accumulates roundoff; pFq inner cancellation can eat 6-8 dps. |
| C    | `1e-(precision − 8)` to `1e-(precision − 10)` — generic Slater + cancellation budget. |
| D    | `1e-(precision − 8)` to `1e-(precision − 10)` — anti-Stokes / asymptotic crossover; multiple lanes can produce sub-ulp differences. |
| E    | `1e-(precision − 8)` to `1e-(precision − 12)` — `hmag` perturbation costs a few dps in the limit. |
| F    | `1e-(precision − 8)` to `1e-(precision − 18)` — branch-cut sensitivity; principal-branch convention is pinned but on-cut cases lose dps to the convention's `iε` shim. |
| G    | N/A | envelope check only. |

## Why the wire format uses string-decimals (not JSON numbers)

`arbprec: true` means the value's precision is not bounded by
`Number.MAX_SAFE_INTEGER` or `Number`'s 52-bit mantissa.  JSON numbers
are 64-bit floats; encoding a 50-dps value as a JSON number floors it
to 16 dps.  Every `re` / `im` field in the bench is a decimal-string
(or rational-string `"p/q"`, expanded by the adapter at the working
precision).  This matches PRD §0.1 and the wider workbench's
number-bearing-fields-are-strings convention.

## Why we run via `executeToolDef`, not `wb.run(...)`

`@workbench/compose`'s `runWorkbench` validates the caller's `flags`
against `def.flags ?? {}`.  For an `arbprec: true` tool that declares
no flags of its own, the runner-merged `--precision` flag (ADR-0020)
is invisible to compose, and passing `{ precision: 50n }` is rejected
as an unknown flag.  This is a known gap (filed as follow-up bead
`lc1`/`rn2`); until it lands the bench bypasses the wrapper and
invokes `executeToolDef` directly.  The seven-step contract — schema
validation in/out, provenance write — is byte-identical (ADR-0012);
the in-process and subprocess surfaces both fan out to
`executeToolDef`.  So the bench's results are admissible.

## Why no third-witness adjudication for the Wolfram/mpmath disagreement cases

ADR-0019 §3 names three oracles: Wolfram, SymPy, Sage.  SymPy's
`meijerg._eval_evalf` dispatches to `mpmath.meijerg` — the same
evaluator we're already using; mpmath is mpmath regardless of via
SymPy or directly.  SageMath would be a genuine third witness but is
not currently installed on the workbench host; the workbench's policy
is "drop disagreements rather than pick" (ADR-0019 §3 final paragraph).
The generator records every disagreement with relative magnitude in
`expected.json::cases[i].consensus.rel_disagreement` and writes the
case body to `golden/oracle-disagreements.log`; the disagreement
cases are then *omitted from the golden* per the protocol — the
investigator picks them up via Sage when it's available, otherwise
the case is left quarantined.

For Tier 0 anchors specifically, the elementary RHS at 200 dps acts as
the third witness automatically: even if Wolfram and mpmath disagreed
on the MeijerG codepath, the elementary closed-form value is computed
directly and cannot disagree with itself.

## Mutation-prove discipline (CLAUDE.md Rule 6)

The bench's `golden/test_mutations.py` demonstrates RED on five
characteristic perturbations, proving the verifier *would have caught*
a regression:

  1. flip a Tier 0 truth value → confirm `value_accuracy` failure
  2. flip a Tier G expected refusal to a `value` shape → confirm
     `boundary_envelope` (or `shape`) failure
  3. tighten a Tier C tolerance → confirm `value_accuracy` failure
  4. set `achieved_precision` greater than requested → confirm
     `self_reported_precision` failure
  5. flip a numerical candidate's `method` to `"unknown-method"` →
     confirm `method_admissible` failure

These run as part of the bench (script-level) but do not gate
`bun run check` — that's a workbench-wide policy decision documented
under ADR-0019 §6.

## What this bench does *not* test (deferred)

- **Multi-point AST evaluation for symbolic equality.** The verifier
  currently treats a successful symbolic match (with non-empty `rule`
  / `source`) as adequate; full AST-level numerical evaluation against
  the reference at 20 random sample points (per VERIFIER-PROTOCOL.md
  §"symbolic check") is deferred to a follow-up bead (the AST
  evaluator lives in the tool, not in the verifier).
- **Tier H = 200 cases.** The bead-spec calls for 200 LCG-driven Tier
  H cases.  v0.1 ships a cross-cutting subset (the existing C/D/E/F
  cases with the speed-gate flag); the LCG sweep is a follow-up.  This
  trades coverage breadth for honesty about what's been validated.
- **Method-agreement audit at scale.** The bead-spec calls for a
  `--force-method=<lane>` audit on every applicable case to confirm
  all lanes agree.  v0.1 includes 8 hand-curated agreement cases in
  the tool's unit tests; the bench-wide audit is a follow-up.

## What this bench taught us about the dispatcher

Two things, surfaced during golden generation:

1. **Rational-real BigComplex parameters don't reach the symbolic
   dispatcher.** `tools/meijer-g/tool.ts::bigcomplexToSymbolicValue`
   only converts integer-real to `int(n)`; rationals like `1/2` stay
   as `bigcomplex(1/2)` and the symbolic table's `lit-rat` slot match
   doesn't fire.  Filed as a follow-up — widening
   `bigcomplexToSymbolicValue` to recognise rational-real BigComplex
   would unlock several extra rule matches.  The bench documents the
   *current* behaviour: Tier-A symbolic-required cases with rational
   parameters refuse with `symbolic-required-no-match` (per Honest
   Scope, Rule 8).

2. **mpmath's `meijerg` ordering check trips on complex parameters
   that look real.**  `mpmath.libmp.libhyper._check_need_perturb`
   compares parameters to integers via `>=`, which raises
   `TypeError: no ordering relation is defined for complex numbers`
   when the parameter was an `mpc`.  Workaround: promote real-valued
   complex parameters to `mpf` before the call.  Documented in
   `reference/generate-truth.py::mpmath_truth`.
