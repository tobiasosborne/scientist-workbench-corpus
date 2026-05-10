# Verifier protocol — `linsolve-q`

`verify.py` consumes `{input, candidate}` JSON on stdin and emits
`{pass, reason, checks}` on stdout. Tolerance regime: **none**.
Every check is exact-rational equality (or rank-equality). A
candidate that is "correct within `1e-12`" is wrong.

## Invocation

```sh
cat <case>.json | python3 verify.py
```

Stdin shape:

```jsonc
{
  "input": {
    "A": [["3/2", "-1", "0"], ...],
    "b": ["5", "-1", ...]
  },
  "candidate": {
    "kind": "unique" | "under-determined" | "inconsistent",
    "x":         [...],          // present iff kind ∈ {unique, under-determined}
    "free_vars": [...],          // present iff kind ∈ {unique, under-determined}
    "rank":      <int>,
    "augmented_rank": <int>,     // present iff kind = inconsistent
    "method":    "<str>",
    "warnings":  [<str>, ...]
  },
  "id": "<case-id>"
}
```

Stdout:

```jsonc
{
  "pass":   true,
  "reason": "all invariants hold",
  "checks": {
    "shape":                              {"pass": ..., "detail": "..."},
    "exact_satisfaction_unique":          {"pass": ..., "detail": "..."},
    "free_var_basis_underdetermined":     {"pass": ..., "detail": "..."},
    "rank_consistent":                    {"pass": ..., "detail": "..."},
    "inconsistency_witness":              {"pass": ..., "detail": "..."},
    "free_var_count_correct":             {"pass": ..., "detail": "..."}
  }
}
```

A check that doesn't apply to the candidate's `kind` is reported
as `{"pass": true, "detail": "n/a for kind=<kind>"}`.

## The 6 checks — exact specifications

### 1. `shape`

Structure-only static check.

PASS iff:

- `input.A` is a list of lists of strings, all rows length `n` for
  some `n ≥ 0`, total rows `m ≥ 0`. (Both can be 0; see boundary
  cases.)
- `input.b` is a list of strings, length `m`.
- Every coefficient string parses as a rational via `Fraction(s)`
  with no error.
- `candidate.kind ∈ {"unique", "under-determined", "inconsistent"}`.
- For `kind ∈ {"unique", "under-determined"}`: `candidate.x` is a
  list of length `n` of strings.
- For `kind = "under-determined"`: `candidate.free_vars` is a list
  of strings, all of form `"t_<int>"`.
- For `kind = "inconsistent"`: `candidate.augmented_rank` is an
  integer.
- `candidate.rank` is an integer, `0 ≤ rank ≤ min(m, n)`.

### 2. `exact_satisfaction_unique` (only when `kind = unique`)

Compute `r_i = (Σ_j Fraction(A[i][j]) * Fraction(x[j])) − Fraction(b[i])`
for `i = 0, …, m-1`.

PASS iff every `r_i == 0` exactly.

### 3. `free_var_basis_underdetermined` (only when `kind = under-determined`)

Parse each entry of `candidate.x` as a linear combination of free
variables `t_0, …, t_{free_count - 1}` plus a rational constant.
Specifically: parse as a SymPy expression in those symbols, verify
that `degree(x[i], t_j) ≤ 1` for all `i, j` (linearity), and that
no symbol other than the declared `free_vars` appears.

Generate **10 random rational substitutions** of the free variables
from the pool `{-3, -1, 0, 1, 2, 5/3, -7/4, 11, -19/4, 6/7}` (one
substitution per t_j per trial; the same trial uses the same
substitution across all `x[i]`).

For each of the 10 substitutions, compute the concrete `x_concrete`
and verify `A · x_concrete == b` exactly.

PASS iff:

- Linearity check: yes for all `x[i]` and `t_j`.
- All 10 trials satisfy exactly.

The 10-trial check, while not a proof of universality, catches
every off-by-constant or missing-free-variable bug in practice.
For a *complete* proof we'd substitute symbolically and simplify;
we delegate that to the rank-consistency check (which forces the
correct number of free variables) plus the affine-shape check
above.

### 4. `rank_consistent` (always)

Compute `expected_rank = sympy.Matrix(A).rank()` where the matrix
entries are `sympy.Rational`-parsed from the input strings.

PASS iff `candidate.rank == expected_rank`.

### 5. `inconsistency_witness` (only when `kind = inconsistent`)

Compute `rank_A` and `rank_aug = rank([A | b])` via SymPy.

PASS iff `rank_aug > rank_A`. (Rouché-Capelli; this is necessary
AND sufficient for inconsistency.)

A bonus check: `candidate.augmented_rank == rank_aug` (the candidate
correctly identifies the augmented rank). This is folded into the
PASS condition.

### 6. `free_var_count_correct` (only when `kind = under-determined`)

PASS iff `len(candidate.free_vars) == n - candidate.rank`.

The free-variable count is forced by rank-nullity; mis-reporting
is a structural bug.

## The bit-budget self-report (Tier H only)

Tier H cases ship with an additional input-side field
`expected_max_bit_length` (a positive integer). The candidate's
`warnings` field is expected to include a string of the form
`"max intermediate bit length: <integer>"`. The verifier extracts
this and PASSES iff `<integer> ≤ expected_max_bit_length`.

This catches "got the right answer with naive ℚ-Gaussian" — those
implementations would report a bit length 10-1000× larger than
Bareiss's bound.

For Tier-A through Tier-G cases, this check is `n/a`.

## Aggregation

Case PASS = all applicable checks PASS.
Case FAIL = at least one applicable check FAIL.

The verifier exits 0 if PASS, 1 if FAIL. The full breakdown is
in stdout's `checks` object regardless of exit code.

## What is *not* checked

- **Internal algorithm.** Bareiss vs Bareiss-two-step vs LU vs
  Cramer's rule are all admissible — only the output and its
  exact-rational properties are checked.
- **Output formatting style.** `"3/2"` vs `"1.5"` is *not*
  permitted (Tier 1: `1.5` would fail `shape`'s strict-rational
  parse), but `"3/2"` vs `"-(-3)/2"` is permitted as long as both
  parse to the same `Fraction`. The candidate is encouraged to
  emit canonical lowest-terms form (e.g., `"3"` not `"3/1"`).
- **Performance.** Wall-clock not measured; warnings on
  size-thresholds are permitted but the run must complete within
  60s per case (the bench harness's hard timeout).
- **Method name.** `candidate.method` is informational; the
  verifier does not gate on its value, but the PROMPT recommends
  `"bareiss-one-step"` for the v1 implementation.
