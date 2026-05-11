# sdp-sdplib — Semidefinite Programming on the SDPLIB collection

Phase 0 corpus bench for the convex-cone solver tier (epic
`scientist-workbench-eg9j`, ADR-0030 §F). Sibling: `lp-netlib` and
`lp-small` for LP. The SDP specialist of the cone-solver tier is
`tools/sdp-solve` (bead `v4jd`); this suite grades it.

## What this suite grades

The candidate solver receives a semidefinite program in **canonical
cone-solver form** (ADR-0030 §C with PSDCone):

```
minimise    cᵀx
subject to  A x = b
            x ∈ K = product of PSDCone[size_b, indices_b] for each block b
```

where each `PSDCone[size_b, indices_b]` declares that the slice
`x[indices_b]` is the **svec** (symmetric vectorisation) of an
`size_b × size_b` PSD matrix `X_b ⪰ 0`. The wire uses **strict-
Mosek-format with √2 off-diagonal scaling** so that
`<C, X>_F = svec(C)ᵀ svec(X)` holds exactly.

Output: status, primal `x` (svec packing), dual `y`, slack `s` (svec
of the dual S blocks), objective, achieved precision, plus the
ADR-0030 §A.3 termination taxonomy and tagged-refusal envelope.

## Source

SDPLIB 1.2 (Borchers 1999) — the canonical SDP test set, used in
every serious SDP solver evaluation since SDPT3's first benchmark
runs (Tütüncü-Toh-Todd 2003). Original collection: 92 problems
covering Lovász θ, MAXCUT relaxations, control LMIs, eigenvalue
minimisation, truss design.

Files retrieved from the `vsdp/SDPLIB` GitHub mirror in SDPA-sparse
(`.dat-s`) format. Per-file SHA-256 is recorded in `data/sdp-sdplib/
MANIFEST.toml` and per case in `meta.sha256_dat_s` of
`golden/inputs.json`.

## v0.1 problem set (6 pure-PSD classics)

| problem  | m   | blocks                      | natural family            |
|----------|-----|-----------------------------|---------------------------|
| control1 | 21  | PSD 10 + PSD 5              | linear matrix inequality |
| control2 | 66  | PSD 20 + PSD 10             | LMI / Lyapunov           |
| control3 | 136 | PSD 30 + PSD 15             | LMI / Lyapunov, larger   |
| hinf2    | 13  | PSD 5 + PSD 5 + PSD 6       | H-∞ control              |
| theta1   | 104 | PSD 50                      | Lovász θ function        |
| mcp100   | 100 | PSD 100                     | MAXCUT relaxation        |

Reference SDPLIB optima (in SDPLIB's `max <C, X>` convention; our
canonical wire flips sign to `min`):

| problem  | reference optimum | Mosek (this suite) | COPT (this suite) |
|----------|-------------------|---------------------|-------------------|
| control1 | 17.7846           | 17.78463            | 17.78463          |
| control2 | 8.3000            | 8.30000             | 8.30001           |
| control3 | 13.6333           | 13.63327            | 13.63331          |
| hinf2    | 10.967            | 10.96706            | 10.96727          |
| theta1   | 23.0000           | 23.00000            | 23.00000          |
| mcp100   | 226.157           | 226.15735           | 226.15735         |

Mosek reaches reference precision on all 6; COPT agrees to ≤ 1e-5
relative on 5/6 (hinf2 single-witness because COPT free-license drift
exceeds the agreement gate). These are the 6 cases shipped in
`golden/inputs.json`.

### Why the 600,000-entry dense gate

Same physical reason as `lp-netlib`'s gate: the canonical wire encodes
A as dense `list<list<float64>>`. For SDP, `n_total = Σ_b svec_len(b)
= Σ_b size_b·(size_b+1)/2`. Cases with `n_total × m > DENSE_LIMIT`
exceed practical JSON sizes (~10 MB at 600k). The gate excludes large
SDPLIB problems (`thetaG`-series, `maxG32`, `qpG`-series) which
have `n_total × m ≈ 10⁶ to 10⁹`. v0.2 sparse wire (deferred per
ADR-0030 §"Open questions #5") will lift this gate.

### What's deferred to v0.2

SDPLIB problems with **LP blocks** (negative `blockSize` in the SDPA
format) — the truss-* series, `hinf1`, several `control-*` variants —
are skipped here. v0.2 of `tools/sdp-solve` (bead `67nj`) will
support `NonNegCone` by reformulation as a diagonal SDP block, at
which point those problems re-enter this suite.

Large pure-PSD problems above the 600k gate (`thetaG*`, `qpG*`,
`maxG32+`) re-enter when the v0.2 sparse wire format lands.

## Wire format

Each case carries:

```json
{
  "id": "control1",
  "input": {
    "minimize": { "c": [c_0, c_1, …, c_{n_total-1}] },
    "subjectTo": {
      "Ax_eq_b": {
        "A": [[A_00, A_01, …], …],
        "b": [b_0, b_1, …, b_{m-1}]
      },
      "cones": [
        { "head": "PSDCone", "size": 10, "indices": [0, 1, …, 54] },
        { "head": "PSDCone", "size":  5, "indices": [55, 56, …, 69] }
      ]
    },
    "precision": 1e-8
  },
  "meta": {
    "source":         "SDPLIB (Borchers 1999)",
    "format":         "SDPA-sparse (.dat-s)",
    "convention_in":  "maximize <C, X> ...",
    "convention_out": "minimize <-C, X> ...",
    "n_total":        70,
    "m":              21,
    "blocks":         [{ "size": 10, "n_indices": 55 }, { "size": 5, "n_indices": 15 }],
    "encoding":       "sdp-sdplib-v0.1",
    "sha256_dat_s":   "<64 hex of original .dat-s bytes>"
  }
}
```

The expected record carries the dual-witness consensus:

```json
{
  "id": "control1",
  "expected": {
    "status":    "optimal",
    "objective": -17.78463,
    "consensus": {
      "objective_mosek": -17.784626728659862,
      "objective_copt":  -17.784630838814177,
      "agreement":       true,
      "agreement_tol":   1e-5,
      "rel_diff":        2.31e-7
    }
  }
}
```

X, S, y are **not** pinned in `expected.json` — they are generically
non-unique on the optimal face. The objective is the only numeric
oracle; KKT residuals are reconstructed by the verifier from the
candidate's own (x, y, s).

## SDPA-sparse → canonical wire reduction

SDPA-sparse encodes `maximize <C, X>` with C, A_i symmetric matrices
specified as upper-triangular sparse triplets `(i, k, l, v)` (i = 0
for C; i = 1..m for A_i). The reduction:

1. **Convention flip.** Multiply C by −1 (canonical form is minimise).
2. **Block layout.** For each PSD block of size n_b, allocate
   `n_b·(n_b+1)/2` consecutive slots in the global x vector.
3. **svec packing.** For each (i,k,l,v) with k ≤ l, write into slot
   `svec_pos(k, l, n_b) + offset_b`:
   - if k == l (diagonal): value `v`
   - else (off-diagonal): value `√2 · v` (Mosek-format scaling)
4. **Constraints.** RHS `b` passes through unchanged (no sign flip
   on b — only the objective C carries the maximise→minimise flip).
5. **Cone declaration.** One `PSDCone[size_b, indices_b]` per block.

The reduction is verbatim in code we own (`golden/generate.py`); see
the file's docstring for line-by-line provenance.

## Oracle pattern

Dual-witness for v0.1: **Mosek + COPT**. Gurobi is excluded — it does
not support semidefinite programming (LP/QP/SOCP/MIQP only).

COPT runs in **non-commercial size-limited mode** (n ≤ 2000 PSD
dimension), which comfortably covers all 6 v0.1 cases (largest PSD
block: 100 in mcp100). The license-warning output is redirected from
fd 1 to fd 2 by `copt-sdp.py` via `os.dup2` so the JSON response
on stdout stays clean.

Per case, the generator runs both oracle adapters via subprocess on
the canonical wire. The dual-witness consensus checks objective
agreement at relative tolerance **1e-5** (the SDP precision floor —
looser than LP's 1e-8 because IPM SDP solvers don't reach machine
precision on objective; this matches Tütüncü-Toh-Todd 2003 and the
empirical SDPLIB reference table). Disagreements drop the case from
the `oracle_agreement` gating; KKT-residual gating still applies.

## v0.1 bench gate (current state)

`tools/sdp-solve` v0.1 grades **3/6 cases on sdp-sdplib**, **63/66
invariants** as of 2026-05-11:

| problem  | candidate status        | pass | failing check     |
|----------|-------------------------|------|-------------------|
| control1 | optimal                 | ✓    |                   |
| control2 | numerical-breakdown     | ✗    | status_consistency |
| control3 | numerical-breakdown     | ✗    | status_consistency |
| hinf2    | numerical-breakdown     | ✗    | status_consistency |
| theta1   | optimal                 | ✓    |                   |
| mcp100   | optimal                 | ✓    |                   |

The three failures are **substrate convergence gaps**, tracked in
workbench bead `qmrv` (the SDP analog of LP's NETLIB-`brandy`
algorithm-hygiene work, bead `j1gd`). The IPM (NT direction) hits
either a Cholesky breakdown on the Schur complement (control3 at
iter 53) or a stall in mu-decrease (control2 at 324 iter; hinf2 at
108 iter). AHO direction also fails; both directions are tried
internally via the sdp-solve substrate's two engines.

Mosek and COPT both reach optimal on all three failing cases — the
problems are well-posed; the gap is in `@workbench/solver-ipm`'s
numerical hygiene, not in problem formulation. The bench grade
honestly surfaces this: status_consistency check fails loudly when
the candidate returns `numerical-breakdown` on a case the consensus
calls optimal.

Acceptance: when `qmrv` ships (σ-clip, tighter Cholesky regularisation,
adaptive jitter ramp, AHO/NT crossover fallback), the bench grade
should advance to 6/6.

When v0.2 (NonNegCone reformulation per bead `67nj`, sparse wire per
ADR-0030 §"Open questions #5") lands, the gate re-engages at the
SDPLIB-wide 80+/92 threshold from the original ADR-0030 §G
white-whale criterion.

## Boundary tags

The boundary-tag refusal envelope (`tagged "sdp-solve/non-sdp-cone"`,
`"sdp-solve/malformed-cone"`, etc.) is exercised by the *workbench*
goldens (`tools/sdp-solve/goldens/`) — out-of-scope cone heads,
malformed PSDCone arity, etc. The corpus suite focuses on
algorithmic correctness on well-posed SDPLIB problems; refusal-
envelope behaviour is per-tool and tested at the tool boundary.
