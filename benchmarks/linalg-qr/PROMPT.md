# Bench — `linalg-qr` (Householder QR factorisation)

## ⚠ How you will be graded

You will be graded on **CORRECTNESS** and **NUMERICAL HONESTY**.

Produce the most elegant, most efficient, most numerically faithful
TypeScript implementation you can. This bench is the *floor*, not the
ceiling — passing it is necessary but not sufficient. The tool must
also conform to the scientist-workbench seven-artefact contract (see
`CLAUDE.md`, `README.md`, `PRD-v0.2.md`).

The verifier checks invariants, not byte-equality: QR is not unique
(column signs can flip), and floating-point reconstruction differs at
the LSB across LAPACK builds. Every check has a tolerance derived from
backward-stability theory (Higham 2002, ch. 19); no check has more
slack than the algorithm itself can promise.

## Problem statement

Implement reduced (economy) QR factorisation of a real `m × n` matrix
`A` by Householder reflections.

For `m ≥ n`: `A = Q · R` with `Q ∈ ℝ^{m×n}` having orthonormal columns
and `R ∈ ℝ^{n×n}` upper triangular.

For `m < n`: `A = Q · R` with `Q ∈ ℝ^{m×m}` orthogonal and
`R ∈ ℝ^{m×n}` upper trapezoidal. (When `m < n`, "reduced" and
"complete" coincide.)

Default mode is `"reduced"`. Optional mode `"complete"` returns the
full orthogonal `Q ∈ ℝ^{m×m}` (when `m ≥ n`); `R` is then `m × n`
with the bottom `m − n` rows zero.

Algorithm: Householder reflections (Golub & Van Loan, 4th ed., §5.2).
Modified Gram-Schmidt is *not* admissible — its `‖QᵀQ − I‖_F` grows
like `O(κ(A) · ε)`, which fails the orthogonality check on the
ill-conditioned tier (Hilbert / Vandermonde). Householder is
`O(ε)` independent of `κ` (Wilkinson 1965; Higham 2002, Thm 19.4).

The implementation is expected to operate on flat `Float64Array`
storage (the `@workbench/linalg-core` precedent set by `linalg-solve`,
ADR-0014). No FFI. No WASM. Pure TypeScript, single platform per
ADR-0015 (`numerical: true`).

## I/O contract (JSON)

### Bench wire format

The bench passes raw JSON (numbers as JSON numbers, not hex bits).
A small adapter (`bench/linalg-qr/run-candidate.ts`) wraps the
`tools/linalg-qr` tool and translates between the bench wire format
and the canonical-value protocol the tool itself speaks. The
adapter is provided; the candidate to run is the tool.

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
  "Q":                       [[<float>, ...], ...],
  "R":                       [[<float>, ...], ...],
  "mode":                    "reduced" | "complete",
  "diagonal_R":              [<float>, ...],   // diag(R), top min(m,n) entries
  "reconstruction_error":    <float>,          // ||Q·R − A||_F / max(||A||_F, 1)
  "orthogonality_error":     <float>,          // ||Qᵀ·Q − I||_F
  "method":                  "householder",
  "warnings":                [<string>, ...]   // possibly empty
}
```

`reconstruction_error` and `orthogonality_error` are the candidate's
own self-report. They are sanity-checked by the verifier (the
verifier recomputes them and accepts the candidate's value if it
agrees within `1e-6` relative). Self-reporting these is the
agent-honest output discipline (see `tools/linalg-solve/tool.ts` for
the precedent).

`warnings` may include strings like `"reconstruction error 3.4e-11
exceeds the soft floor 1e-12"` — soft warnings, not failures. The
verifier records `warnings` for diagnostic purposes only.

## Invariants checked

The verifier runs **7 independent checks** per case. Each check is
scored independently; a case passes iff every check passes. The
7 checks (full prose: `golden/verifier_protocol.md`):

1. `shape` — `Q` and `R` have the dimensions implied by `m`, `n`,
   and `mode`; entries are JSON numbers.
2. `finite_entries` — every entry of `Q` and `R` is finite (no
   `NaN`, no `±Inf`).
3. `R_upper_triangular` — `|R[i,j]| ≤ tol_struct · max(||A||_F, 1)`
   for `i > j` (within `min(m,n)` block), with
   `tol_struct = 100 · ε · max(m,n)`.
4. `Q_orthonormal` — `||QᵀQ − I_k||_F ≤ tol_orth` where `k` is
   `Q`'s column count and `tol_orth = 100 · ε · m · sqrt(k)`.
5. `factorisation_residual` — `||Q·R − A||_F ≤ tol_recon · ||A||_F`
   with `tol_recon = 100 · ε · max(m,n)·sqrt(min(m,n))` (Higham
   Thm 19.4: backward error of Householder QR is
   `O(ε · m · n)`; we add a safety factor of 100).
6. `self_reported_residual` — candidate's `reconstruction_error`
   field agrees with the verifier's recomputation to `1e-6`
   relative. Catches honesty regressions.
7. `self_reported_orthogonality` — candidate's
   `orthogonality_error` field agrees with the verifier's
   recomputation to `1e-6` relative.

These 7 invariants are necessary AND sufficient for a valid QR
factorisation. (An earlier draft included a `singular_values_match`
check; dropped because `|diag(R)|` is not equal to the singular
values of `A` in general — they coincide only when `A` is itself
upper triangular. The factorisation residual + R_upper_triangular
+ Q_orthonormal trio is already tight.)

The 100× safety factor on Higham's bounds is empirical: SciPy's
`scipy.linalg.qr` (LAPACK DGEQRF) passes the same checks with
margin to spare across the entire test set.

## Test set tiers

`golden/inputs.json` contains **49 cases** spanning seven tiers
(see `DESCRIPTION.md` for per-tier rationale):

| Tier | Cases | What it probes |
|---|---|---|
| A. shape edges | 10 | `1×1`, `2×1`, `1×2`, identities, `2×2` zero, `5×3`, `3×5`, `100×100`, `200×200` |
| B. random well-conditioned | 8 | `n ∈ {5, 10, 20, 50, 100, 200}` square + `(50,20)`, `(20,50)` rectangular |
| C. Hilbert | 7 | `n ∈ {4, 6, 8, 10, 12, 20, 50}` — `κ(H_n) ≈ (1 + √2)^{4n}/√n`; `H_50` has `κ > 10^{18}` |
| D. Vandermonde | 4 | `n ∈ {5, 10, 15, 20}` — Lagrange node powers; `κ` grows exponentially |
| E. Wilkinson / Pei / Frank | 5 | tridiagonal `W^+_5, W^+_11, W^+_21`, Pei `n=10`, Frank `n=12` |
| F. rank-deficient | 5 | rank-1 outer product, identity-with-zero-column, two near-equal rows, all-zeros, `H_8` with appended zero column |
| G. tall and skinny / short and fat | 6 | `(50,3)`, `(100,5)`, `(200,10)` tall + `(3,50)`, `(5,100)`, `(10,200)` fat |
| H. complete-mode | 4 | a representative case from each of A/B/C/G with `mode: "complete"` |
| I. industrial (NIST harwell-boeing) | 5 | `bcsstk01..05` real structural-engineering matrices, `n ∈ {48, 66, 112, 132, 153}` |
| J. stress (post ADR-0016) | 2 | `n ∈ {500, 1000}` random well-conditioned, in the regime where scale warnings fire |

Total: **56 cases × 7 checks = 392 invariant assertions**.

The Hilbert-50 case is the deliberate stress-tester: `κ(H_{50}) > 10^{18}`,
so any non-Householder algorithm (e.g. classical Gram-Schmidt) will fail
`Q_orthonormal` catastrophically (CGS gives `O(κ²·ε)`, MGS gives
`O(κ·ε)`, Householder gives `O(ε)` independent of `κ`).

## Verifying your solution

```sh
infra/run-bench.sh bench/linalg-qr <your-cmd>
```

Example with the in-tree adapter:

```sh
infra/run-bench.sh bench/linalg-qr bun bench/linalg-qr/run-candidate.ts
```

The harness pipes each test case through your program and through
`golden/verify.py`, prints a per-check summary, and exits 0 only
if every case is `"pass": true`.

### Files

- `golden/inputs.json` — every test case.
- `golden/expected.json` — reference outputs from SciPy LAPACK
  DGEQRF (provided for sanity-checking; **not** consulted by the
  verifier — the verifier recomputes from input).
- `golden/verify.py` — invariant verifier (numpy + scipy.linalg).
- `golden/verifier_protocol.md` — what each check pins, with
  derivations of the tolerances.
- `golden/generate.py` — reproducible golden generation.
- `reference/qr_reference.py` — Python+SciPy reference; runs the
  same JSON I/O contract for a drop-in sanity-check baseline.

## Hard constraints (sci-wb-specific, on top of the bench)

The implementation must conform to all twelve numbered rules in
`CLAUDE.md`. Specifically:

- Pure TypeScript on Bun. No FFI, no WASM, no native binaries.
- Seven-artefact contract: `tool.ts` + schema + ≥1 example per
  branch + invariants + tests + `goldens/` + `README.md`.
- `numerical: true` annotation (ADR-0015) — output records carry
  the `platform` provenance field.
- `n ≤ 200` cap, mirroring `linalg-solve` (ADR-0014). `n > 200`
  → `ToolError` with suggestion pointing to bead `wmm`.
- Boundary categories (ADR-0003):
  - `tagged "linalg-qr/non-finite-input"` for `NaN`/`±Inf` in `A`.
  - `tagged "linalg-qr/degenerate-shape"` for `m=0` or `n=0`.
  - `ToolError` for non-rectangular `A`, `m·n > 200·200`.
- Substrate package: extend `@workbench/linalg-core` with a `qr()`
  function on `Matrix`, mirroring the `solve()` precedent.
- `bun run check` must be green at the end (full 41-phase battery).

## What you must do

1. Read `CLAUDE.md` top-to-bottom and `tools/linalg-solve/tool.ts`
   for the agent-honest output precedent.
2. Implement the bench candidate (`tools/linalg-qr/`) to the
   seven-artefact contract.
3. Run the bench: `infra/run-bench.sh bench/linalg-qr bun bench/linalg-qr/run-candidate.ts`.
4. Run the workbench gate: `bun run check`.
5. Report per-check totals in your final answer (e.g.
   `shape 49/49 · finite_entries 49/49 · R_upper_triangular 49/49
   · Q_orthonormal 49/49 · factorisation_residual 49/49 ·
   self_reported_residual 49/49 · self_reported_orthogonality 49/49`).
6. Ship the implementation **you'd put your name on** (see
   `CLAUDE.md` Rule 10 — literate programming).
