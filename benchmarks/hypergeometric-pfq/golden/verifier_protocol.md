# Verifier protocol — `hypergeometric-pfq`

This document pins **every check** the verifier runs, with the
derivation of every tolerance. Per ADR-0019 §1 the verifier checks
mathematical invariants, not byte-equality.

## Inputs

The verifier reads one JSON object on stdin:

```jsonc
{
  "input":     <input row from inputs.json>,
  "candidate": <candidate output>,
  "id":        "<case-id>"
}
```

It loads `expected.json` adjacent on disk and looks up the case by
`id`. The case's `expected` field encodes either a `value` truth or
a `tagged` envelope; the case's `tolerance_rel` field is the
relative-error tolerance for the value-accuracy check.

## Output

```jsonc
{
  "pass":   bool,
  "reason": "<short>",
  "checks": {
    "<check-name>": {"pass": bool, "detail": "<short>"},
    ...
  }
}
```

A case passes iff every check passes. The first failure determines
`reason`.

## Common check (both paths)

### `no_tool_error`

A `{"kind": "tool_error", ...}` candidate is *never* admissible. Tool
errors fire on schema-validation failure or unrecoverable runtime
exception; no case in this bench expects either condition. If the
candidate is a tool error the bench reports the failure verbatim.

## Success-path checks

### 1. `shape`

Required success-record fields:

  * `value` — `{re: string, im: string}`.
  * `achieved_precision` — non-negative integer.
  * `method` — string.
  * `n_terms` — non-negative integer.
  * `working_precision` — non-negative integer.
  * `warnings` — `list[string]`.

Strict; missing or wrong-typed fields fail without delegating.

### 2. `finite_value`

`value.re` and `value.im` parse via `mpmath.mpf` and produce finite
numbers (no `nan`, no `±inf`). The verifier uses `mpmath.isfinite`.

### 3. `method_admissible`

`method ∈ {"closed-form-0F0", "closed-form-1F0", "direct-series"}`.
Any other value fails. Future builds may add `"asymptotic-borel"` /
`"connection-z-near-1"` / `"kummer-transform"` to this set; the
verifier is updated in lockstep.

### 4. `self_reported_precision`

`0 ≤ achieved_precision ≤ requested_precision`. The tool may *under-
report* (it converged at a lower precision than requested but still
within the bench's tolerance — graceful degradation, ADR-0007); it
may not over-report. Strict equality is *not* required; the bench's
`value_accuracy` check is the authoritative gate on whether the
returned value meets the precision contract.

### 5. `value_accuracy`

The candidate's value is compared to the pinned truth in `expected.json`:

```
truth     = mpmath.mpc(expected.truth.re, expected.truth.im)   # at 80 dps
candidate = mpmath.mpc(candidate.value.re, candidate.value.im)

rel_err = |candidate − truth| / max(|truth|, 0)         # 0 case below

pass iff rel_err ≤ tolerance_rel
```

If `|truth| == 0` (rare; only when the closed form genuinely vanishes,
which doesn't happen in this corpus) the comparison falls back to
absolute.

#### Per-tier tolerances (in `tolerance_rel`)

  | Tier | Default `tolerance_rel` (precision = 50)              |
  |------|-------------------------------------------------------|
  | 0    | `1e-48`                                                 |
  | A    | `1e-48`; the precision = 100 case uses `1e-98`         |
  | B    | `1e-44` to `1e-46`                                      |
  | C    | `1e-40` to `1e-46`                                      |
  | D    | `1e-44` to `1e-46`                                      |

These are pinned per case in `expected.json`. The derivation:

  * Tier 0 / A — well-conditioned; mpmath at 80 dps clears the
    truth value at relative `~10⁻⁷⁸`. The tool's request precision
    is 50 dps; lose 2 dps to the bench's own roundoff and we have
    `1e-48`.

  * Tier B — 4 to 6 dps of cancellation between Pochhammer numerator
    and denominator (per Pearson-Olver-Porter §3.1). The
    cancellation-driven retry brings the achieved precision back to
    50 dps, but the cumulative roundoff in the BigInt × BigFloat
    arithmetic adds 2-4 dps of noise.

  * Tier C — slow convergence; thousands of summands, each adding
    a constant bit of roundoff. At `|z| = 0.98` we sum `~5700`
    summands; that's `log2(5700) ≈ 12` bits of cumulative summation
    noise. We relax to `1e-40` for the tightest case (6 dps headroom
    above the worst-case noise).

  * Tier D — coalescence cancellation; bounded by the retry's safety
    margin (16 bits in `pFqDirectSeries`). Tolerance picked to clear
    the safety margin's noise envelope.

## Refusal-path check

### 6. `boundary_envelope`

The candidate must be `{"kind": "tagged", "tag": "<expected-tag>", ...}`.
The verifier matches:

  * `kind == "tagged"` exactly.
  * `tag == expected.tag` exactly.
  * If `expected.payload_predicate.reason_substr` is set, the
    candidate's `payload.reason` field must contain that substring
    (loose substring match is the v0.1 verifier discipline; a tighter
    structural match arrives if the refusal payload grows fields).

A candidate that emits a *value* shape on a refusal-expected case
fails: the verifier reports `expected tagged refusal but got record`.

## Per-tier check matrix

| Path     | Check                       | Tier 0 | A | B | C | D | E |
|----------|-----------------------------|--------|---|---|---|---|---|
| common   | `no_tool_error`             | ✓      | ✓ | ✓ | ✓ | ✓ | ✓ |
| success  | `shape`                     | ✓      | ✓ | ✓ | ✓ | ✓ | — |
| success  | `finite_value`              | ✓      | ✓ | ✓ | ✓ | ✓ | — |
| success  | `method_admissible`         | ✓      | ✓ | ✓ | ✓ | ✓ | — |
| success  | `self_reported_precision`   | ✓      | ✓ | ✓ | ✓ | ✓ | — |
| success  | `value_accuracy`            | ✓      | ✓ | ✓ | ✓ | ✓ | — |
| refusal  | `boundary_envelope`         | —      | — | — | — | — | ✓ |

Total per-case: ~6 checks (success-path) or 2 checks (refusal-path).
Across 53 cases: ~282 invariant assertions.

## Mutation-prove discipline

`golden/test_mutations.py` (sibling to this protocol) demonstrates
RED on five characteristic perturbations of the candidate output:

  1. flip a Tier 0 truth value to `2 · truth` → `value_accuracy` RED.
  2. swap a Tier E expected `tagged` envelope for a value record →
     `boundary_envelope` RED (kind mismatch).
  3. tighten a Tier C tolerance from `1e-44` to `1e-50` → `value_
     accuracy` RED.
  4. set `achieved_precision = requested + 5` → `self_reported_
     precision` RED.
  5. change candidate `method` to `"unknown-method"` → `method_
     admissible` RED.

These prove the verifier's *sensitivity*. A verifier that always
returns PASS would be admitted without them; with them, the
verifier's discrimination is on the record.
