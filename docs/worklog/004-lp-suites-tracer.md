# 004 — lp-netlib + lp-small tracer-bullet (2026-05-11)

## Context

Phase 0 of the convex-cone solver epic (scientist-workbench eg9j,
ADR-0030 §F). Two corpus benches need to exist before any of the
three candidate tools (`cone-solve`, `lp-solve`, `qp-solve`) can be
graded: **lp-netlib** (114-problem canonical battery) and
**lp-small** (~40-problem pathology / resource-limited companion).

This shard lands the **tracer-bullet**: one suite's verifier loop
closes end-to-end on 3 cases × 12 invariants × 2 oracles, with
mutation tests proving the verifier catches both honest-scope
violations (negative slack on a NonNeg cone) and oracle-disagreement
lies (candidate.objective ≠ consensus).

Content scaling (lp-small → ~40 problems, lp-netlib MPS ingestion)
is the next shard.

## What changed

`benchmarks/lp-netlib/` + `benchmarks/lp-small/`:

- `manifest.toml` declaring 10 (netlib) / 12 (small) checks per case.
- `DESCRIPTION.md` (wire format spec, case taxonomy, NETLIB →
  canonical-form reduction protocol), `REFERENCES.md`,
  `PROMPT.md`, `golden/verifier_protocol.md`.
- `golden/verify.ts` — the canonical 12-check verifier, lives in
  `lp-netlib/golden/`. Exports `runVerifier(expectedPath?)` per the
  workbench's ADR-0010 import.meta.main pattern.
- `lp-small/golden/verify.ts` — 5-line wrapper importing the canonical
  with its own `expected.json` path.
- `lp-small/golden/generate.py` — tracer generator producing 3
  cases: optimal, infeasible, unbounded. Runs both oracles in
  subprocess and builds the dual-witness consensus.
- `lp-small/golden/inputs.json` + `expected.json` (3 cases).

`adapters/gurobi/oracles/gurobi-lp.py` +
`adapters/mosek/oracles/mosek-lp.py`:

- Two self-contained Python oracle scripts (~190 lines each). Read
  canonical SCS-form LP on stdin (ADR-0030 §C); write candidate-shape
  record on stdout. Strict JSON output (`allow_nan=False`) — special
  floats encoded as field-absence rather than `Infinity` tokens.
- Both compute the dual slack as `s = c − Aᵀ y` directly rather than
  reading the vendor's reduced-cost attribute. Removes
  per-vendor sign-convention concerns; matches the verifier's
  expected formula exactly.

Six adapter TOMLs: `adapters/{gurobi,mosek}/{lp-netlib,lp-small}.toml`
plus two inert stubs at `adapters/scientist-workbench/{lp-netlib,lp-small}.toml`
pointing at the not-yet-existent `tools/{cone-solve,lp-solve}`.

## Why these choices

- **Field-absence for ±∞ / NaN.** JSON.parse rejects raw `Infinity`
  tokens; widening `number → number | "Infinity"` widens every TS
  consumer downstream. Field-absence semantics gives the verifier a
  cleaner predicate (`"candidate has `objective` ⟺ candidate claims
  optimality"`) and matches the workbench's optional-field idiom.

- **`s = c − Aᵀ y` computed in both adapters.** Gurobi reports `RC`
  (reduced cost) and Mosek reports `snx` (only on interior solutions
  — basic solutions throw `err_no_snx_for_bas_sol`). Computing from
  the stationarity equation directly is one formula both oracles can
  always produce, with no sign-convention drift.

- **Verifier reads `expected.json` at startup.** The corpus grader's
  per-case stdin only carries `{input, candidate, id}` — no
  `expected`. The verifier loads the suite's `expected.json` map
  once on entry and indexes by `id`. The `runVerifier(expectedPath?)`
  signature allows lp-small to point at its own expected file when
  re-using lp-netlib's verifier.

- **Sign-tolerant Farkas certificate check.** Gurobi's `FarkasDual`
  follows `Aᵀ y ≤ 0, bᵀ y < 0`; Mosek's follows `Aᵀ y ≤ 0, bᵀ y > 0`.
  Both are valid (Farkas is unique up to sign). The verifier tries
  both signs and accepts if either passes. Candidate solvers are
  free to use whichever convention their algorithm produces.

- **Dual-witness via subprocess, not in-process binding.** Gurobi
  and Mosek are licence-constrained commercial libraries with
  Python-first SDKs. Wrapping them in TS via `bun:ffi` is
  multi-week work for negligible gain; subprocess JSON is the
  ergonomic interface that scales to COPT additively when installed.
  Cost is ~50ms per oracle invocation; acceptable for a Phase-0
  infrastructure piece.

## Frictions surfaced

- **Bun missing from PATH.** `/snap/bin/bun` was the previous Bun
  install vehicle (snap-based; ADR-0001 addresses the mount-namespace
  bug). Snap was uninstalled from this machine at some point — the
  binary lived in `~/.bun/bin/bun` (from a parallel installer) but
  `~/.bun/bin` was never added to PATH. Re-ran `curl -fsSL
  https://bun.sh/install | bash`, picked up the binary, used the
  absolute path `~/.bun/bin/bun` for all corpus operations this
  session. **Lesson:** Check Bun path probes (`which bun`,
  `~/.bun/bin/bun --version`) early — silent failure mode is "agent
  spawns Bash, Bash has clipped env, nothing runs".

- **Mosek `snx` unavailable on basic solutions.** Caught when the
  first cross-oracle probe (3-var LP) returned identical results from
  Gurobi but crashed Mosek with `err_no_snx_for_bas_sol(2953)`. Fix
  was structural: switched both oracles to `s = c − Aᵀ y` directly,
  bypassing the vendor-specific reduced-cost attributes entirely.

- **Verifier protocol "expected" injection.** The shared
  `grade.ts` runner passes `{input, candidate, id}` on stdin, not
  `expected`. Initial verifier draft assumed `expected` was on
  stdin; refactored to load `expected.json` at startup. This is
  what the linalg-eigh verifier should also be doing (it doesn't
  consult expected.json at all — it recomputes the eigendecomp via
  fresh Jacobi from input). LP can't recompute the consensus
  on-the-fly (would require calling the oracles again) so the
  expected-file pattern is load-bearing for LP specifically.

## Acceptance

- `bun src/cli.ts validate` → 17 suites + 21 adapters clean
  (was 15 + 15).
- `bun src/cli.ts grade gurobi lp-small` → 3/3 cases, 36/36
  invariants.
- `bun src/cli.ts grade mosek lp-small` → 3/3 cases, 36/36
  invariants.
- Mutation probe (deliberately wrong candidate via /tmp/lie.json):
  verifier returns `pass: false` with `failed: dual_feasibility,
  oracle_agreement`. Honest-scope catches both lies.

Both oracles produce bit-identical (x, dual, slack, objective) on
the trivial LP differing only in `method` and `condition_estimate`
metadata. The KKT-residual checks all pass with `r_p = r_d = r_c =
0.000e+0` because the optimum lands on an integer-coordinate vertex.

## Pointers

- `benchmarks/lp-netlib/DESCRIPTION.md` — full wire format spec
  including the NETLIB MPS → canonical-form reduction protocol
  (var_map / slack_intro / free_split records in `meta`).
- `benchmarks/lp-netlib/golden/verifier_protocol.md` — the 12 checks
  in prose; cross-references Wright 1997 §2.4 and CLAUDE.md Rule 8.
- ADR-0030 (workbench) — wire format, status taxonomy, bench gating
  numerics.
- Beads `scientist-workbench-1few` (lp-netlib) +
  `scientist-workbench-oz67` (lp-small).
