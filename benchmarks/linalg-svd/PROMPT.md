# Bench — `linalg-svd` (Singular Value Decomposition)

## ⚠ How you will be graded

You will be graded on **CORRECTNESS** and **NUMERICAL HONESTY**.

Produce the most elegant, most efficient, most numerically faithful
TypeScript implementation you can. This bench is the *floor*, not the
ceiling — passing it is necessary but not sufficient. The tool must
also conform to the scientist-workbench seven-artefact contract (see
`CLAUDE.md`, `README.md`, `PRD-v0.2.md`).

The verifier checks invariants, not byte-equality: SVD is unique only
up to sign convention on `U`/`Vt` columns and (for repeated singular
values) up to rotation in the corresponding subspace. Every check
has a tolerance derived from backward-stability theory (Demmel-Kahan
1990; Higham 2002 §20.3), no slacker than the algorithm itself can
promise.

## Problem statement

Implement reduced (economy) SVD of a real `m × n` matrix `A`:

  `A = U · diag(S) · Vᵀ`

with:
- `U ∈ ℝ^{m×k}` having orthonormal columns,
- `S ∈ ℝ^k` non-negative and non-increasing,
- `Vᵀ ∈ ℝ^{k×n}` having orthonormal rows (equivalently `V` has
  orthonormal columns),
- `k = min(m, n)`.

Default mode is `"reduced"`. Optional mode `"complete"` returns
`U ∈ ℝ^{m×m}`, `S ∈ ℝ^k`, `Vᵀ ∈ ℝ^{n×n}` — the extra `m − k` columns
of `U` span the orthogonal complement of `A`'s column space; the extra
`n − k` rows of `Vᵀ` span the null space of `A`. (When `m = n`, the
two modes coincide.)

Algorithm choice is up to you; both of these pass the bench's
tolerance regime:

- **Golub-Reinsch via implicit-shift QR sweeps on the bidiagonal**
  (Demmel-Kahan 1990, the LAPACK DGESVD path). Asymptotically
  fastest. Requires Householder bidiagonalization then
  Demmel-Kahan-style sweeps for accurate small singular values.
- **One-sided Jacobi** (Demmel-Veselić 1992, the LAPACK DGEJSV
  path). Simpler to implement; superior accuracy for small
  singular values; slower (O(n³ log n) sweeps). At `n ≤ 200` the
  speed gap doesn't matter.

Either is admissible; the bench tests the result, not the algorithm.

The implementation operates on flat `Float64Array` storage (the
`@workbench/linalg-core` precedent set by `linalg-solve`, ADR-0014).
No FFI. No WASM. Pure TypeScript, single platform per ADR-0015
(`numerical: true`).

## I/O contract (JSON)

### Bench wire format

Raw JSON (numbers as JSON numbers, not hex bits). The adapter
`bench/linalg-svd/run-candidate.ts` bridges to the tool's canonical
`Value` protocol.

### Input (one JSON object on stdin)

```jsonc
{
  "A":    [[<float>, ...], ...],   // m × n, m,n ≥ 1, m·n ≤ 200·200
  "mode": "reduced" | "complete"   // optional; default "reduced"
}
```

### Output (one JSON object on stdout)

```jsonc
{
  "U":                       [[<float>, ...], ...],   // m × k (reduced) or m × m (complete)
  "S":                       [<float>, ...],          // length k (singular values, descending)
  "Vt":                      [[<float>, ...], ...],   // k × n (reduced) or n × n (complete)
  "mode":                    "reduced" | "complete",
  "reconstruction_error":    <float>,                  // ||U·diag(S)·Vt − A||_F / max(||A||_F, 1)
  "orthogonality_error_U":   <float>,                  // ||UᵀU − I||_F
  "orthogonality_error_Vt":  <float>,                  // ||Vt·Vtᵀ − I||_F
  "condition_number":        <float>,                  // S[0] / max(S[k-1], EPS · S[0])
  "rank_estimate":           <int>,                    // count of S_i > rtol · S[0], rtol = max(m,n)·EPS
  "method":                  "golub-reinsch" | "one-sided-jacobi",
  "warnings":                [<string>, ...]
}
```

`reconstruction_error`, `orthogonality_error_U`, and
`orthogonality_error_Vt` are the candidate's *honest self-report*.
The verifier recomputes them and checks agreement to `1e-6` relative.
Self-reporting these is the agent-honest output discipline (see
`tools/linalg-solve/tool.ts` and `tools/linalg-qr/tool.ts` for the
precedent).

`condition_number` is `S[0] / S[k−1]` when `S[k−1] > 0`, else
`S[0] / (EPS · S[0]) = 1/EPS ≈ 4.5e15`. (Cap at `1/EPS` rather than
`Infinity` because finite numbers compose better.)

`rank_estimate` counts singular values exceeding the LAPACK-standard
relative threshold `max(m, n) · EPS · S[0]`. This is the canonical
numerical-rank definition.

## Invariants checked

The verifier runs **8 independent checks** per case:

1. `shape` — `U`, `S`, `Vt` have dimensions implied by `m, n, mode`;
   output object has all required fields with the right types.
2. `finite_entries` — every entry of `U`, `S`, `Vt`, and the scalar
   diagnostic fields is finite.
3. `S_nonneg_descending` — `S[i] ≥ 0` for all `i`, and `S[i] ≥ S[i+1]`
   for all `i < k−1` (within tolerance `100·EPS·S[0]`).
4. `U_orthonormal` — `||UᵀU − I_q||_F ≤ tol_orth` where `q` is `U`'s
   column count and `tol_orth = 100 · EPS · m · √q`.
5. `Vt_orthonormal` — `||Vt·Vtᵀ − I_q||_F ≤ tol_orth` where `q` is
   `Vt`'s row count.
6. `factorisation_residual` — `||U·diag(S)·Vt − A||_F ≤ tol_recon ·
   ||A||_F` with `tol_recon = 100 · EPS · max(m,n) · √(min(m,n))`.
7. `self_reported_residual` — `reconstruction_error` agrees with
   verifier recomputation to `1e-6` relative.
8. `self_reported_orthogonality` — both `orthogonality_error_U` and
   `orthogonality_error_Vt` agree with verifier recomputation to
   `1e-6` relative. (Combined check; either failure fails this.)

These 8 are necessary AND sufficient for a valid SVD up to sign-
convention freedom. Notably absent: a `singular_values_match` check
(comparing `S` to LAPACK's reference `S`). The reason: that check
fails for *correct* SVDs because LAPACK and a clean-room
implementation may legitimately differ at the LSB, and the
relative-tolerance band needed to admit honest disagreement is wide
enough to also admit some incorrect implementations. The 6-invariant
core (`shape`, `finite`, `S_nonneg_descending`, both orthonormality
checks, and reconstruction residual) is provably tight: a candidate
passing all 6 *is* a valid SVD of `A`.

The 100× safety factor on Higham's bounds is empirical — SciPy's
`scipy.linalg.svd` (LAPACK DGESDD) clears the tolerances by ≥1
order of magnitude on every case.

## Test set tiers

`golden/inputs.json` contains **49 cases** spanning seven tiers
(see `DESCRIPTION.md` for per-tier rationale):

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 10 | `1×1`, `2×1`, `1×2`, `2×2` identity/zero, `5×3`, `3×5`, `100×100` and `200×200` identities |
| B. random well-conditioned | 8 | n ∈ {5, 10, 20, 50, 100, 200} square + (50,20), (20,50) rectangular |
| C. Hilbert | 7 | n ∈ {4, 6, 8, 10, 12, 20, 50} — `H_50` has `κ > 10^{18}` |
| D. Vandermonde | 4 | n ∈ {5, 10, 15, 20} |
| E. Wilkinson / Pei / Frank | 5 | structural test matrices |
| F. rank-deficient | 5 | rank-1 outer product, identity-with-zero-column, near-equal-rows, all-zeros, Hilbert with appended zero column |
| G. tall and skinny / short and fat | 6 | (50,3), (100,5), (200,10) tall + (3,50), (5,100), (10,200) fat |
| H. complete-mode | 4 | a representative case from each of A/B/C/G with `mode: "complete"` |
| I. industrial (NIST harwell-boeing) | 5 | `bcsstk01..05` real structural-engineering matrices, `n ∈ {48, 66, 112, 132, 153}` |
| J. stress (post ADR-0016) | 1 | `n = 500` random well-conditioned (Jacobi at n=1000 is ~3.5 min — deferred to a future Golub-Reinsch port) |

Total: **55 cases × 8 checks = 440 invariant assertions**.

The Hilbert-50 case is the deliberate stress-tester:
`κ(H_{50}) > 10^{18}`. Both Golub-Reinsch and one-sided Jacobi pass
the orthogonality bounds independent of `κ`; a buggy bidiagonalization
or a non-convergent implicit-shift sweep will fail
`U_orthonormal` / `Vt_orthonormal`.

## Verifying your solution

```sh
bash bench/infra/run-bench.sh bench/linalg-svd <your-cmd>
```

Example with the in-tree adapter (after the tool ships):

```sh
PATH=/home/tobias/.amp/bin:$PATH bash bench/infra/run-bench.sh \
    bench/linalg-svd bun bench/linalg-svd/run-candidate.ts
```

### Files

- `golden/inputs.json` — every test case.
- `golden/expected.json` — reference outputs from SciPy LAPACK
  DGESDD (provided for sanity-checking; **not** consulted by the
  verifier — the verifier recomputes from input).
- `golden/verify.py` — invariant verifier (numpy + scipy).
- `golden/verifier_protocol.md` — what each check pins, with
  derivations of the tolerances.
- `golden/generate.py` — reproducible golden generation.
- `reference/svd_reference.py` — Python+SciPy reference; runs the
  same JSON I/O contract for a drop-in baseline.
- `run-candidate.ts` — wire-format adapter (raw JSON ↔ canonical
  `Value` via `@workbench/compose`).

## Hard constraints (sci-wb-specific, on top of the bench)

The implementation must conform to all twelve numbered rules in
`CLAUDE.md`. Specifically:

- Pure TypeScript on Bun. No FFI, no WASM, no native binaries.
- Seven-artefact contract: `tool.ts` + schema + ≥1 example per
  branch + invariants + tests + `goldens/` + `README.md`.
- `numerical: true` annotation (ADR-0015).
- `m·n ≤ 200·200` cap (ADR-0014). `n > 200` → `ToolError` with
  suggestion pointing to bead `wmm`.
- Boundary categories (ADR-0003):
  - `tagged "linalg-svd/non-finite-input"` for `NaN`/`±Inf` in `A`.
  - `tagged "linalg-svd/degenerate-shape"` for `m=0` or `n=0`.
  - `ToolError` for non-rectangular `A`, `m·n > 200·200`.
- Substrate: extend `@workbench/linalg-core` with `svd()` on
  `Matrix`, mirroring the `qr()` precedent.
- `bun run check` must be green.

## What you must do

1. Read `CLAUDE.md` top-to-bottom. Read `tools/linalg-qr/tool.ts`
   and `packages/linalg-core/src/qr.ts` — your work mirrors them.
2. Implement the bench candidate (`tools/linalg-svd/`) to the
   seven-artefact contract.
3. Run the bench until 49/49 across 8 checks.
4. Run `bun run check`.
5. Report per-check totals in your final answer.
6. Ship the implementation **you'd put your name on** (CLAUDE.md
   Rule 10 — literate programming).
