# Bench — `linsolve-q` (Exact linear solving over ℚ)

## ⚠ How you will be graded

You will be graded on **EXACT CORRECTNESS**. No tolerances — every
arithmetic operation in this tool is over the rationals and the
verifier checks bit-equality of the residual `A·x − b` to *zero*, not
to a small number.

This bench is the **floor**, not the ceiling. Passing it is necessary
but not sufficient. The tool must also conform to the scientist-
workbench seven-artefact contract (CLAUDE.md, README.md, PRD-v0.2.md).

## Problem statement

Implement the exact linear-system solver over ℚ:

  given `A ∈ ℚ^{m×n}`, `b ∈ ℚ^m`, find `x ∈ ℚ^n` such that `A·x = b`,

returning a structured answer that distinguishes:

- **Unique solution** (full column rank, consistent system).
- **Underdetermined** (consistent, free variables — return a
  parametric form).
- **Inconsistent** (no solution — `tagged "linsolve-q/inconsistent"`).

Algorithm: **Bareiss fraction-free integer-preserving Gaussian
elimination** (Bareiss 1968, *Math. Comp.* 22(103) 565-578; the local
PDF is at `docs/ground-truth/linear/bareiss-1968-mathcomp.pdf`).

The one-step variant (Eq. (8) of §II.A.2 on p.569) is the
recommendation for v1 — half the bookkeeping of the two-step variant
and `O(n³)` arithmetic ops with controlled bit growth via Sylvester's
identity (§I). The two-step variant (Eq. (7) p.568) is a constant-
factor speedup deferred to a v2 bead.

## Why Bareiss and not "just solve over ℚ directly"

Naive Gaussian elimination over ℚ blows up the bit-length of
intermediate fractions. Bareiss exploits Sylvester's identity to
guarantee that each intermediate value is itself a determinant of an
integer submatrix — its bit length is bounded by Hadamard's bound,
not by accumulated denominator growth. For dense `n×n` integer
inputs this is `O(n³)` arithmetic ops at `O(n · log(‖A‖))` bits per
op, vs naive `O(n³)` ops at `O(2^n)`-bit-blown-up rationals.

The substrate is `packages/cas-core`'s rational-number arithmetic
(`Q` ring with bigint num/den). Pure exact; no floats.

## I/O contract (JSON)

### Bench wire format

Raw JSON. Adapter `bench/linsolve-q/run-candidate.ts` bridges to the
tool's canonical `Value` protocol.

### Input (one JSON object on stdin)

```jsonc
{
  "A": [["3/2", "-1", "0"],
        ["1",   "2",  "1/3"],
        ["0",   "0",  "1"]],
  "b": ["5", "-1", "2/7"]
}
```

Coefficients are **decimal-string rationals** in canonical form
(no leading `+`, no whitespace, denominator stripped if `1`). The
zero coefficient `"0"` is permitted. The matrix `A` is `m × n`; `b`
has length `m`.

### Output (one JSON object on stdout)

```jsonc
{
  "kind":      "unique" | "underdetermined" | "inconsistent",
  "x":         ["7/3", "-1", "1/4"],   // unique: length n
  "free_vars": [],                     // unique: empty
  "rank":      3,                      // always present
  "method":    "bareiss-one-step",
  "warnings":  []
}
```

For `kind: "underdetermined"`: `x` carries length-`n` *parametric*
substitutions where free variables are encoded as `"t_0"`, `"t_1"`,
… — their indices are listed in `free_vars`. A bound variable's
value contains free-var symbols additively combined with rational
coefficients, encoded as a string like `"2 - 3*t_0 + 1/2*t_1"`.

For `kind: "inconsistent"`: `x` and `free_vars` are absent; the
output additionally carries

```jsonc
{
  "kind":      "inconsistent",
  "rank":      <int>,
  "augmented_rank": <int>,         // > rank ⇒ inconsistent (Rouché-Capelli)
  "method":    "bareiss-one-step",
  "warnings":  []
}
```

The wire form for `inconsistent` mirrors a `tagged "linsolve-q/
inconsistent"` value at the workbench-Value-protocol layer; the
adapter converts.

## Invariants the verifier checks

The verifier runs **6 independent checks** per case:

1. **`shape`** — `A` is rectangular `m × n` with `m, n ≥ 1`; `b`
   length `m`; rationals parse cleanly; `kind ∈ {unique, under-
   determined, inconsistent}`; required fields present per kind.
2. **`exact_satisfaction_unique`** *(when `kind = unique`)* —
   `A · x ≡ b` exactly in ℚ. Computed by exact rational matrix-
   vector multiply; residual must be the all-zero vector.
3. **`free_var_basis_underdetermined`** *(when `kind = under-
   determined`)* — substitute *every* free variable with a random
   rational from `{-3, -1, 0, 1, 2, 5/3, -7/4}`; the resulting
   concrete `x` must satisfy `A · x ≡ b` exactly. Repeat 10× per
   case with different substitutions; all must pass.
4. **`rank_consistent`** *(always)* — the reported `rank` matches
   the rank of `A` computed by an independent Bareiss-on-A
   reference (using `sympy.Matrix(A).rank()` as the oracle).
5. **`inconsistency_witness`** *(when `kind = inconsistent`)* —
   `augmented_rank > rank` (Rouché-Capelli); witness produced by
   the verifier independently.
6. **`free_var_count_correct`** *(when `kind = under-determined`)*
   — `len(free_vars) = n − rank`. The parametrisation must be
   minimal.

These are necessary AND sufficient for a valid exact-rational
linear solve.

## Test set tiers

`golden/inputs.json` contains **~50 cases** spanning seven tiers
(per ADR-0019). Coverage:

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 6 | `1×1`, `1×2` (under), `2×1` (over), `2×2` identity, `3×3` identity, `n×0` (zero-cols degenerate) |
| B. random well-conditioned | 8 | `n ∈ {3, 5, 8, 12, 20, 30, 50, 100}` random small-rational entries, full-rank, unique solution |
| C. classical structural | 6 | Hilbert `H_n` for `n ∈ {3, 5, 8, 10}`; Pascal `n=6`; Vandermonde `n=5`. All ill-conditioned over ℝ but exact over ℚ; tests bit-growth control |
| D. integer-coefficient stress | 4 | dense `n=20` with entries from `{-100, …, 100}`; dense `n=30`; sparse banded `n=50`; arrowhead `n=20` |
| E. structural rank-deficient (under-determined) | 8 | `m < n` underdetermined (3 cases); square but rank-deficient with consistent rhs (3 cases); structurally rank-1 (2 cases) |
| F. inconsistent | 6 | `m > n` over-determined contradictory (3 cases); `m = n` rank-deficient with inconsistent rhs (3 cases) |
| G. industrial / literature | 4 | DGEMM-style block tests at `n ∈ {25, 50}` with integer coefficients; cyclotomic-coefficient `n=10`; bordered banded `n=15` |
| H. integer-preserving stress | 8 | Inputs whose naive-rational solution exhibits exponential bit-growth without Bareiss; verifies the algorithm-shape claim. Examples: `n=20` with random `{-3, -2, -1, 0, 1, 2, 3}` entries; `n=15` checkerboard-sign; the Bareiss 1968 §V Table 1 worked example. |

Total: **~50 cases × 6 checks = ~300 invariant assertions**.

## Verifying your solution

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/linsolve-q bun bench/linsolve-q/run-candidate.ts
```

### Files

- `golden/inputs.json` — every test case.
- `golden/expected.json` — reference outputs from SymPy +
  Wolfram (triple-witnessed per ADR-0019; provided for sanity-
  checking, not consulted by the verifier).
- `golden/verify.py` — the 6-check invariant verifier.
- `golden/verifier_protocol.md` — exact tolerances per check.
- `golden/generate.py` — reproducible golden generation.
- `golden/test_mutations.py` — mutation-prove harness (≥5 RED
  perturbations of the reference per ADR-0019 §4).
- `reference/linsolve_q_reference.py` — Python reference (Bareiss
  one-step on `sympy.Rational`).
- `run-candidate.ts` — wire-format adapter to `tools/linsolve-q`.

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun. No FFI.
- Seven-artefact contract.
- Default determinism tier (symbolic, bit-identical cross-platform
  forever — no `numerical: true` annotation; ADR-0015).
- Boundary categories (ADR-0003):
  - `tagged "linsolve-q/inconsistent"` for incompatible systems.
    Payload: `{ rank, augmented_rank }`.
  - `ToolError` for malformed input: ragged `A`, non-rational
    coefficient strings, length mismatch between `b` and rows of
    `A`, `m = 0` or `n = 0`.
- Substrate: extend `@workbench/cas-core` with `bareissSolve()`
  operating on `Rat`-typed matrix/vector representations.
- Output uses ADR-0017's solution-set shape adapted for linear:
  the workbench-Value form has `solutions` length `0` (inconsistent)
  or `1` (unique or under-determined), with `branches` listing free-
  variable symbols when under-determined.

## What you must do

1. Read `docs/adr/0017-solution-set-shape.md`,
   `docs/adr/0019-solve-bench-discipline.md`, the local Bareiss
   PDF (`docs/ground-truth/linear/bareiss-1968-mathcomp.pdf`),
   `packages/cas-core/src/{ratio,poly,ring}.ts`,
   `tools/cas-simplify/tool.ts` (closest-existing pattern).
2. Implement `bareissSolve(A, b)` in `packages/cas-core/src/
   linsolve.ts` — pure function over `Rat` matrix/vector.
3. Implement `tools/linsolve-q/` to seven-artefact contract.
4. Run bench until 100% across 6 checks across all 7 tiers.
5. Run `bun run check`.
6. Report per-check totals.

## What this bench does NOT cover

- **Coefficient fields beyond ℚ** (no `𝔽_p`, no algebraic
  extensions). `linalg-solve` (the numerical-tier float64 sibling)
  covers the float64 case; algebraic-extension solving is a future
  bead after `packages/alg-num` ships (P3-*).
- **Specialised structures** (band solvers, sparse direct, …) —
  out of scope for v1.
- **Iterative refinement** — not applicable to exact arithmetic.
- **Multiple right-hand sides** — `b` is a single column vector
  in v1; multi-rhs is a future bead if needed.
