# 008 — coverage shape post gamma-sync (2026-05-23)

Short shard, no code change.  Captures the coverage-shape analysis
the user asked for after gamma-sync closed (worklog 007), so the
synthesis lives somewhere durable beyond the bead notes.

## Context

After 31 beads across two same-day sessions (drift-sync 22 beads +
gamma-sync 9 beads), the user asked "what is the shape of the missing
capability re wolfram then" — i.e. re-run the R2/D2 coverage shape
query against the current state.

## State queried

```sql
SELECT m.tool, m.status, count(*)
FROM mappings m JOIN capabilities c ON m.capability_id=c.id
WHERE c.system='mathematica' GROUP BY m.tool, m.status;
```

| tool | status | rows |
|---|---|---|
| special-eval | implemented | 13 |
| linalg-eigh | partial | 1 |
| cas-diff | partial | 1 |
| mod-pow | implemented | 1 |

**17 mapping rows; 12 unique capabilities mapped** (Gamma has 3 rows,
PolyGamma has 3 rows — one v2 capability backing multiple workbench
dispatch heads via the new `flags={head=...}` schema).

## Headline progression

| metric | pre-drift-sync | post-drift-sync | post-gamma-sync |
|---|---|---|---|
| mapping rows | 2 | 9 | **17** |
| unique caps mapped | 2 | 9 | **12** |
| % of in-scope ~170 | ~1% | ~5% | **~7%** |

## Per-category density

| category | total | unique mapped | % | delta vs R2 |
|---|---|---|---|---|
| **special-functions** | 34 | 8 | **24%** | +3 (Gamma, Beta, Pochhammer) — Gamma sync's locus |
| calculus | 14 | 1 | 7% | unchanged |
| linalg | 18 | 1 | 6% | unchanged |
| discrete | 29 | 1 | 3% | unchanged |
| uncategorized | 177 | 1 | 0.6% | +1 (Pochhammer; most of the 177 still invisible) |
| graphics, logic, structure, lists, algebra, trigonometric, system, patterns, io, rules, constants, assignment, arithmetic, numerical, strings, complex | 263 | 0 | 0% | unchanged |

Only special-functions moved.  The Gamma sync's locus.

## The 5-bucket refresh

| bucket | R2 (pre-sync) | post-gamma |
|---|---|---|
| (A+B) mapped | 9 | **12** |
| (C) cheap wins (corpus TOML + tool, no mapping) | ~60 | **~56** |
| (D) needs new tool | ~40 | **~28** (Gamma family moved out) |
| (E) out of scope | ~390 | ~390 |
| (C-) tool exists, no v2 TOML (ingest-first) | unknown | **0** for Gamma family (G3 honesty recon); other clusters unaudited |

## The G3 lesson applied to cheap-wins

R2 projected ~60 cheap wins.  G3 discovered that ⅔ of the workbench
Gamma dispatch heads were composite/non-v2 (Digamma is
PolyGamma[0,z], BarnesG is post-v2, etc.).  Honest count: 4 cheap
wins from Gamma family, not 6-8.

For the other clusters, the v2-presence priors are stronger but
unverified:

| cluster | unmapped | likely real cheap wins | recon need |
|---|---|---|---|
| linalg → `linalg-{eigh,qr,svd,solve}` | 17 | ~10 (Inverse, LinearSolve, Det, Transpose, NullSpace, RowReduce, PseudoInverse, SingularValues, Eigenvectors, Eigensystem) | low — v2 has these names verbatim |
| algebra → `poly-{factor,roots}, solve, groebner-basis, cas-simplify` | 26 | ~10 (Factor, Roots, Solve, NSolve, Simplify, Apart, Expand, Together, Coefficient, Resultant) | low |
| calculus → `integrate-*, cas-diff` | 13 | ~6 (NIntegrate, NDSolve, Sum, Product, symbolic Integrate) | medium — symbolic Integrate is hard |
| discrete → `mod-*, alg-num-arith` | 28 | ~6 (GCD, LCM, EulerPhi, Mod, ExtendedGCD, JacobiSymbol) | low |
| numerical → `integrate-1d, optimize-lbfgs-projected` | 9 | ~4 (FindMinimum, FindRoot, NSum, NProduct) | low |
| constants → `expr-parse` dispatch | 15 | ~6 (Pi, E, EulerGamma, GoldenRatio, Catalan, Infinity) | low |
| special-functions remainder → existing tools | 26 | ~5 (Hypergeometric0F1/1F1/2F1/U, ExpIntegralEi etc.) | medium |

Re-projected if cheap wins are wired with cluster-recon discipline:

- **linalg + algebra + discrete + constants only:** ~7% → **~26%**
  of in-scope (+32 mappings, one focused session)
- **plus calculus + numerical + special-functions remainder:** ~26% → **~43%**

## What's left in bucket D (~28)

- Orthogonal polynomials (~7-8): LegendreP/Q, ChebyshevT/U, JacobiP,
  HermiteH, LaguerreL, GegenbauerC — `hypergeometric-pfq` could
  dispatch some via reduction; not wired
- Elliptic integrals (~8-10): EllipticK/E/F/Pi, EllipticTheta,
  JacobiSN, EllipticLog/Exp — no substrate
- Formal power series (~4-5): Series, symbolic Sum/Product,
  InverseSeries — needs new CAS layer
- Advanced number theory (~4): WeierstrassP, PartitionsP/Q,
  LatticeReduce
- Misc (~3-4): Zeta, PolyLog, LerchPhi, Fourier/InverseFourier,
  Limit — Zeta/PolyLog are natural 4th per-head epic candidates

## Recommendation for next session focus

The gamma-sync moved coverage 5% → 7% via deep epic-style work
(special-eval Gamma family).  The same +2pp could come from wiring
~5 mappings in linalg + ~5 in algebra in well under an hour.  Best
leverage right now is **cluster cheap-wins**, NOT another per-head
epic — Bessel and Gamma have already harvested the high-density
clusters, and the next D-bucket candidates (orthopoly, elliptic,
formal power series) need real new substrate that's many sessions
of work each.

The TL;DR for the next agent: **don't trust a single drift-catalogue
projection of N cheap wins per cluster without G3-style recon**
(check the canonical Mathematica v2 index `data/wolfram-v2/raw/contents/B.8.html`
for each candidate workbench dispatch head).  But the linalg /
algebra / discrete clusters look high-confidence.

## References

- Workbench HEAD: `af1baa3`
- Corpus DuckDB SCHEMA_VERSION: 3
- Prior shards: 006 (drift-sync) for buckets framework, 007
  (gamma-sync) for the G3 honesty discipline
- Bead R2 (`scientist-workbench-corpus-2ra`) for the original
  pre-gamma shape, D2 (`scientist-workbench-corpus-e4a`) for the
  post-gamma projection that this shard now grounds in actual
  post-sync data
