# 006 — drift-sync (2026-05-23)

## Context

The corpus had fallen out of sync with `scientist-workbench` during 12
days of intense per-head special-function epic work (Erf, Bessel,
Gamma starting). The last HANDOFF in this repo dated 2026-05-11
(LP Phase 0 close-out, worklog 005); since then the workbench landed:

- 15 new tools (special-eval, cas-diff, mod-pow, cone-solve, and the
  10+ supporting heads),
- 85 worklog shards (089-173) covering the v0.2 special-function
  arc, ADR-0028 mega-bench ports, and the per-head ADMITTED_HEADS
  expansion,
- 12 new ADRs (0030-0042) — most relevantly 0030 (LP trajectory
  columns), 0033 (HSDE-NT default flip), 0037 (cone-solve precision
  tier), 0039 (Meijer-G principal-sector refusal), 0040 (Erf-anchor
  consensus rule), 0041 (Bessel ADMITTED_HEADS), and the umbrella
  0028 (mega-bench port direction).

A drift audit at session start catalogued the above and filed a
22-bead plan (scientist-workbench-corpus-{4yv, n4y, 1av, chg, 62f,
0g9, 3pu, q4r, wr2, 3u3, 4gg, 0y8, upd, c3s, seu, 5uz, fpn, m9t,
9bz, 9zr, 0a4, bsk}). Twenty closed in-session (this shard is bead
9zr); HANDOFF.md update (0a4) and final commit/push (bsk) remain.

Five child beads also got filed: 1av.1 (workbench-side lp-solve
overclaim), 3pu.698 (broader 401-TOML arity sweep), chg.1 (snap-bun
sympy import — closed in-session), seu.1 (HankelH1/H2 head coverage),
seu.2 (SphericalBessel head coverage).

## What shipped (organised by phase)

### Phase 0 — verification regrades (no drift detected)

**B1 sdp-sdplib regrade (4yv).** 5/6 cases / 64/66 invariants pass.
Matches worklog 130 baseline exactly. The single failing case
(`hinf2`) is the pre-existing primal-feasibility shortfall
(r_p=1.424e-6 > 1e-7) plus complementary-slackness slack
(|<X,S>|=1.108e-5). ADR-0033's Tier 3 default flip (legacy NT →
HSDE-NT) did **not** propagate into top-level sdp-sdplib goldens for
any case. No child bead needed.

**B2 meijer-g regrade (n4y).** 95/95 cases / 450/450 invariants —
*better* than worklog 005 expected, because the 4 known speed-gate
failures from workbench beads rp05/qlld are now passing. ADR-0039's
principal-sector refusal class and the 15 regenerated asymptotic
goldens did not propagate into top-level meijer-g for any case;
all tD/tG asymptotic cases still pass cleanly. No child bead.

### Phase 1 — catch-ups (stale annotations + ingestor bug)

**B3 lp-netlib + B4 lp-small adapters (1av).** Removed
`PENDING-tools/lp-solve` INERT comments; bumped version from
`PENDING-tools/lp-solve` to `git@9efb8d7` (workbench HEAD; follows
linalg-svd.toml convention). lp-small smoke: 12/12 invariants on
`A_dense_10x10_s001`. lp-netlib smoke: 11/12 on adlittle and afiro
— `self_reported_precision` fails because lp-solve claims
`achieved_precision ≈ 1.229e-5` while the verifier recomputes
`|x^Ts| ≈ 1.696e-3` (~138× under-claim). Filed child **1av.1** for
the workbench team; the corpus verifier behaviour is correct per
Rule 8 ("honest scope, monotonic V-levels").

**B5 groebner-basis adapter (chg).** Version pin updated to
`git@9efb8d7`. Smoke surfaced a host-environment issue: snap-bun
(`/snap/bin/bun`, confinement=strict, base=core22) spawns python3
subprocesses that cannot see `/usr/lib/python3/dist-packages`, so
the verifier's `import sympy` fails. Workbench's tools/groebner-basis
is the **first python3-verifier bench in the corpus** (all others
use TS+Bun verifiers per CLAUDE.md Rule 4); the snap confinement
limitation surfaces here for the first time. Filed and resolved
child **chg.1**: workaround is to install curl-bun at
`~/.bun/bin/bun` and invoke that binary explicitly. Documented in
HANDOFF; mandatory for every subsequent grade run in this session.

**B6 solve adapter (62f).** Pin bumped `d55a72e → 9efb8d7`. Smoke
passes 5/5 invariants on `v1-linear-2x2-unique`. Pure annotation
catch-up; behaviour was already aligned.

**Added scientist-workbench-cone-solve parallel target (0g9).** New
adapters/scientist-workbench-cone-solve/{lp-netlib,lp-small}.toml.
Each pins `env.CANDIDATE_TOOL='cone-solve'` and reuses
`benchmarks/lp-*/run-candidate.ts` (the bridge already keyed off
CANDIDATE_TOOL env). Dashboard now shows lp-solve and cone-solve
side-by-side at their respective tolerance gates (1e-8 vs 1e-6 per
ADR-0037). Smoke: lp-netlib/adlittle 11/12 invariants pass — the
status_consistency failure (cone-solve hit iter-cap vs
consensus=optimal) is the **expected** 1e-6 vs 1e-8 precision-tier
differential per workbench worklog 173.

**B7 wolfram-v1 arity bug (3pu).** Real bug.
`scripts/ingest_wolfram_v1.ts` line 488 emitter template
hard-coded `arity = 1` regardless of signature shape. Patched with
a `computeArity(input)` helper (top-level comma count; `(none)→0`,
`any→null/omit`) and threaded through `Parsed.signature_arity`.
Audit-corrected the 4 Bessel TOMLs (BesselJ/Y/I/K, all arity 2) plus
appended a `[[provenance]]` row (source_kind=manual,
field_path=signature.arity, source_url=bead:3pu) on each. Broader
sweep: of 581 wolfram-v1 TOMLs, **~405 have wrong arity under the
correct rule** (~176 are genuinely arity 1; 145 should be 0, 78
should be 2, 37 should be 3, 12 should be 4, 1 should be 6, 132
should be null/omitted). Per CLAUDE.md scope discipline, deferred
the other 401 to child bead **3pu.698**.

### Phase 2 — schema groundwork (SCHEMA_VERSION 2 → 3)

All four schemas amended additively; every `additionalProperties:
false` wall preserved; every new field documented with
description + `$comment` carrying rationale + bead ID + ADR refs.

**B8 capability.schema.json (q4r).** Added `signature.{domain,
codomain}` enums (real / complex / both / mixed / unspecified),
`mapping[].flags` (object<string,string>), `mapping[].precision_tier`
(float64 / arbprec / both), and extended `verification.method` enum
with `arbprec_oracle`.

**B9 benchmark-suite.schema.json (wr2).** Added
`golden.{n_heads, n_tiers}` (int≥1) for multi-head/multi-tier
corpora (per ADR-0028, ADR-0040, ADR-0041); added
`verifier.checks[].{machine_checkable, applies_when}`.

**B10 adapter.schema.json (3u3).** Added `adapter.tool_flags`
(object<string,string>) for structured representation of flags
currently embedded in `args[]`.

**B11 DuckDB DDL bump 2→3 (4gg).** SCHEMA_VERSION bumped. New
nullable columns: `capabilities.{signature_domain,
signature_codomain}`; `mappings.{flags, precision_tier,
dispatch_head}` (the last is a denormalised convenience column);
`benchmark_suites.{n_heads, n_tiers}`;
`verifier_checks.{machine_checkable, applies_when}`;
`adapters.tool_flags`; `grade_runs.candidate_flags`. INSERTs
populate from TOML with null-coalescing fallbacks. Build clean;
post-bump counts match pre-bump (582 caps / 18 suites / 26
adapters / 38 grade_runs / 8503 grade_results — backwards
compatible). Local `as any` widenings used in build.ts as a B12
hand-off; cleaned up there.

**B12 TS types in src/schema.ts and src/grade.ts (0y8).** Widened
Capability/BenchmarkSuite/Adapter types to mirror the v2 JSON
Schema amendments (Rule 2: TS derives from JSON Schema, not the
other way round). Stripped all 6 `as any` widenings from
src/build.ts. `tsc --noEmit` clean on `src/`. src/loader.ts
needed no change — it reads raw TOML via parseToml<T>; the type
parameter only narrows return type at call sites.

**B13 validate + build gate (upd).** Inline (small enough to skip
subagent overhead): validate 582/18/26 clean; build clean (38
grade_runs, 8503 grade_results, 0 net delta from Phase 1
baseline); `SELECT schema_version FROM _metadata` returns 3.
Phase 2 schema groundwork solid.

### Phase 3 — special-eval landing

**B14 special-eval-smoke adapter (c3s).** Created
`adapters/scientist-workbench/special-eval-smoke.toml` — first
user of the new tool_flags schema. Picked option (A): single
multi-head adapter per suite, head per-case via `input.head`,
`tool_flags = {precision = "53"}` pins the suite-wide float64
lane. Sets precedent for B18/B19 (erf-anchor / besselj-anchor),
where head is also per-case. **Friction surfaced:**
`src/validator.ts:50-55` enforces that every `adapter.capability_id`
must match a known suite name in `benchmarks/<name>/manifest.toml`
— so this TOML could not validate clean until B15 landed the
matching suite. Acceptance gated; closed when B15 finished.

**B15 benchmarks/special-eval-smoke/ (seu).** 10 cases — one per
admitted head in tools/special-eval v0.2 (6 Erf + 4 Bessel).
10/10 grade green at float64 lane (60/60 invariants). The brief's
recon expected 14 heads; HankelH1, HankelH2, SphericalBesselJ,
SphericalBesselY are **not** in v0.2's ADMITTED_HEADS — filed
child beads **seu.1** (Hankel) and **seu.2** (Spherical Bessel)
to track each gap. **Friction noted:** the B14 adapter's
`tool_flags.precision = "53"` is a mantissa-width label, not the
tool's decimal-digit flag (which reads ≤15 → float64, >15 →
arb-prec); since `src/grade.ts` does not yet forward tool_flags
to candidates, `run-candidate.ts` is the authoritative site for
the lane decision (it pins `precision = 10n` to route float64
unambiguously). Forward-compat; no separate bead.

**B16 wire 7 capability mappings (5uz).** Added `[[mapping]]` rows
to `capabilities/wolfram-v1/{BesselJ,BesselY,BesselI,BesselK,Erf}.
toml` (each with `status=implemented`, `flags={head=...}`,
`precision_tier=both`, `tool=special-eval`), plus
`D.toml → cas-diff` (status=partial; cas-diff doesn't cover
`D[f,{x,n}]`, multi-var partials, or NonConstants) and
`PowerMod.toml → mod-pow`. Also fixed D's arity 1→2 and
PowerMod's arity 1→3 with bead-5uz provenance rows (the same
ingest bug as B7 — Bessels were already fixed). Mappings table
grew 2 → 9 rows; the new `mappings.dispatch_head` column
populated for all 5 head-dispatched rows. All 3 referenced
workbench tools (special-eval, cas-diff, mod-pow) present in
`~/Projects/scientist-workbench/tools/`.

### Phase 4 — mega-bench port (per ADR-0028)

**B17 erf-anchor port recon (fpn).** Read workbench
`bench/erf-anchor/corpus.json` structure (271 inputs × 6 heads ×
8 tiers × 4 oracles). Headline decisions for the port:
- **gold-tier consensus** (wolfram + mpmath agreement ≥48 digits)
  as the canonical `expected.json` value; null when gold-gold
  disagrees (8 flagged cases, all T5 complex or T8 InverseErfc);
- **per-oracle expected.json shape** preserving full provenance
  per case, enabling per-oracle adapter grading
  (`oracles.{wolfram,mpmath,boost,scipy}.{value, achieved_precision,
  method}` + `consensus.{gold_agree, value, digits_agreed,
  tolerance_rel}`);
- **no sampling needed**: 271 cases × 4 oracles × ~120 chars =
  ~700 KB, well under 50 MB git limit (contrast lp-netlib's 100k
  entries gate);
- 4 oracles (arb deferred to B19 because workbench's erf-anchor
  arb adapter wasn't ready at the recon point);
- **read-from-committed-results.json pattern** for oracle adapters
  rather than re-running python3/wolframscript at grade time.

**B18 erf-anchor port (m9t).** 14 files: 1 bench suite (`manifest.
toml`, `golden/{inputs.json, expected.json, generate.ts}`,
`golden/verify.ts`, `run-candidate.ts`, `DESCRIPTION.md`); 4
oracle adapters (boost, mpmath, scipy, wolfram) with their
replay shims under `adapters/<oracle>/oracles/erf-anchor-replay.ts`
and snapshotted `data/erf-anchor-results.json`; 1 candidate
adapter (`adapters/scientist-workbench/erf-anchor.toml` with
`tool_flags.precision = "200"`). 271 cases × 4 oracles. Smoke:
scientist-workbench 5/5 (35/35 invariants) on mixed
T1/T4/T5/T8; wolfram 5/5; mpmath 5/5; boost 2/2 (T1 + a T5
refusal); scipy passes T6 sentinels and T5 disagreement
(structural-only branch) but fails T1 well-defined cases on
rel_err (1e-19 > 1e-48 gold tol — expected for bronze-tier
float64; documented in adapter toml). Validate 582/19/27 →
582/20/32 (+1 suite, +5 adapters). 3 themed commits: 6238bd1
(suite), 20cd2f8 (4 oracle adapters), 5197bff (candidate
adapter). Decisions on top of recon spec: (a) T6 edge tier
treated as `gold_agree=true` with structural-only verifier
branch (else 7 sentinel-token T6 cases would inflate
disagreement count); (b) precision flag hardcoded `200n` in
run-candidate (mirrors B15's lane-decision-in-run-candidate
pattern); (c) admitted method set includes both special-eval
tags and per-oracle native tags so replay shims grade green;
(d) `refusal_scope_honest` admits `oracle/*` tags unconditionally
(snapshot-honest by construction); (e) scipy results.json
augmented with `z` fields by id-join (workbench scipy adapter
dropped z from records).

**B19 besselj-anchor port (9bz).** 15 files mirroring B18: 1 bench
suite + 5 oracle adapters (arb is **new in corpus** — first arb
target) + 5 oracle replay shims + 1 candidate adapter. 1766
cases × 6 heads × 10 tiers × 3 ν-classes × 5 oracles. Smoke:
scientist-workbench 10/10 (70/70 invariants) covering
T1/T3/T4/T5/T6/T6-all-refused/T7/T8/T9/T10; wolfram 5/5; arb 5/5;
mpmath 5/5; boost 3/3 (T5 complex refusal admitted); scipy 1/2
(T1 rel_err 1e-17 > 1e-48 bronze-tier-expected). Total
git-tracked size ~6.6 MB (recon's 15-25 MB estimate was
conservative — JSON whitespace overhead lower than projected).
Validate 582/20/32 → 582/21/38 (+1 suite, +6 adapters from B18
baseline). 21 known-disagreement edge cases, **all T6** (every
gold refused/limited/sentinel-only on float64-limit inputs;
L9-L10 landmine class per workbench's agreement-matrix.md).
T7-besselk-020 mpmath-timeout resolved by 2-of-3 gold fallback
(wolfram + arb agree). 3 themed commits: a169a44 (suite), 2dec887
(5 oracle adapters), 15bde65 (candidate adapter). Decisions on
top of B18 precedents: (a) 3-of-3 strict + 2-of-3 fallback gold
consensus (was 2-of-2 in B18); (b) T9/T10 tolerance ladder
entries (1e-40 zero-crossing, 1e-44 large-nu); (c) ν threaded
through wire as `args=[nu, z]` for arity-2 dispatch with `a/b`
half-integer fraction parser (sound for ≤7/2 in this corpus);
(d) arb's verbose method tag preserved verbatim in
`ADMITTED_METHODS` rather than aliased; (e) T10 admitted as
refusal-in-scope (overflow boundary); (f) build-script normaliser
collapses 5 divergent per-oracle status taxonomies (success /
limit / refused / honest-special-token / timeout /
complex-success) to `{success, refused, limit, timeout, error}`
on disk for replay-shim uniformity. No child beads filed — all
B18-precedent extensions are mechanical scale-ups, not
adjudication-needing decisions.

## Adapter and suite counts (before → after)

| metric             | pre-session | post-session | delta |
|--------------------|-------------|--------------|-------|
| capabilities       | 582         | 582          | 0     |
| benchmark suites   | 18          | 21           | +3    |
| adapters           | 24          | 38           | +14   |
| mapping rows       | 2           | 9            | +7    |
| SCHEMA_VERSION     | 2           | 3            | +1    |
| grade_runs         | 38          | unchanged*   |       |

\* B14-B19 ran grades through their own subagent contexts; the
grade_runs accumulated in this session were transient validation
runs and not all reflected in the build/corpus.duckdb baseline
snapshot at session start.

Suite delta: +special-eval-smoke, +erf-anchor, +besselj-anchor.

Adapter delta (+14): +2 for scientist-workbench-cone-solve target
(B6); +1 special-eval-smoke (B14); +5 for erf-anchor (4 oracles +
1 sw-candidate, B18); +6 for besselj-anchor (5 oracles incl. new
arb target + 1 sw-candidate, B19).

## Frictions surfaced (worth knowing for future agents)

- **snap-bun host-env trap.** Snap-confined bun cannot import system
  Python packages from subprocess. Workaround: install curl-bun at
  `~/.bun/bin/bun` and **use that absolute path explicitly in every
  grade command** — `bun` alone (via PATH) may resolve to the snap
  binary. HANDOFF documents this. Surfaced by B5 (groebner-basis);
  recorded in **chg.1**.

- **src/grade.ts does not forward adapter.tool_flags to candidates
  yet.** Informational only. `run-candidate.ts` files hard-code
  the flag values they need (e.g., `precision = 10n` for float64,
  `precision = 200n` for arb-prec) until grade.ts wires it
  through. Tracked as forward-compat; no bead filed because no
  caller actually needs it today.

- **tools/lp-solve overclaims `achieved_precision` on NETLIB by
  ~138×.** Workbench-side issue; out of scope for the corpus
  drift-sync. The corpus verifier behaves correctly (Rule 8 / Rule
  3 — fail loud, never silently drop). Tracked as **1av.1**.

- **~401/581 wolfram-v1 TOMLs have wrong arity** from the original
  ingest emitter bug. Bessel family + D + PowerMod fixed in this
  session (with provenance rows); rest deferred to **3pu.698** for
  a single focused sweep.

- **4 Bessel-family heads admitted by cas-core (ADR-0041) but not
  yet in tools/special-eval ADMITTED_HEADS**: HankelH1, HankelH2,
  SphericalBesselJ, SphericalBesselY. Tracked as **seu.1** + **seu.2**.

- **The validator cross-reference gotcha.** `src/validator.ts:50-55`
  requires every `adapter.capability_id` to match an existing
  benchmark suite name. New adapters cannot validate clean until
  the matching suite lands. B14 → B15 ordering was forced by this;
  document it in HANDOFF so future Phase-3-like work doesn't get
  surprised.

## What's not done in this session

- **B21 / 0a4 — HANDOFF.md update** to reflect the post-drift-sync
  state.
- **B22 / bsk — final validate + build + commit + push** of this
  shard (which is itself the close of B20 / 9zr).

Both planned as the immediate next two beads after this shard.

## References

- Workbench worklog shards 089-173 (the source of drift).
- Workbench ADRs 0030, 0033, 0037, 0039, 0040, 0041, 0028.
- Corpus beads scientist-workbench-corpus-{4yv (B1), n4y (B2),
  1av (B3), chg (B5), 62f (B6), 0g9 (B6'), 3pu (B7), q4r (B8),
  wr2 (B9), 3u3 (B10), 4gg (B11), 0y8 (B12), upd (B13), c3s (B14),
  seu (B15), 5uz (B16), fpn (B17), m9t (B18), 9bz (B19), 9zr (B20)
  + open: 0a4 (B21), bsk (B22)}.
- Child beads: 1av.1, 3pu.698 (filed open); chg.1 (closed in-
  session); seu.1, seu.2 (filed open).
- Commits this session: 20e986c (B3-B6), ecf7f86 (B7), 2a4dc76
  (B8-B12 schema bundle), 135469a (B14+B15+B16 wiring), 73495a0
  (B15 mid-session), 6238bd1 + 20cd2f8 + 5197bff (B18 trio),
  a169a44 + 2dec887 + 15bde65 (B19 trio). Final shard commit:
  this one.
