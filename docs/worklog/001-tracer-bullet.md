# 001 — tracer-bullet end-to-end (2026-05-06)

## Context

Sister repo born today out of a scientist-workbench design conversation:
build a *capability corpus* of v1 Mathematica / MATLAB / Macsyma so
scientist-workbench has a concrete, queryable backlog and grading
target. The conversation surveyed three siblings:

- `Integralis` (relatively good): provenance + V0–V4 verification
  lattice + DuckDB-as-compiled-view.
- `fundingscape` (relatively bad): silent ETL drops, 1k-line monolithic
  dedup module, two parallel HTTP clients, 13-min iteration cycles.
  Anti-patterns to avoid.
- `QuantumHardware.jl`: the closest analogue. TOML on disk as
  source-of-truth, DB compiled view, three-pronged provenance
  (URL + local archive + SHA-256), conflict flag.

Then the user asked: can the tstournament protocol's
brutal-and-punishing golden-master pattern fold into the corpus, so
the corpus is also the test target? Yes, cleanly — tournament's
"case" wire format is exactly an `examples` row plus a verifier.

This shard is the first vertical slice that proves the design end-to-end.

## What changed

A new sibling repo at `../scientist-workbench-corpus/` containing:

- `schema/{capability,benchmark-suite,adapter}.schema.json` — JSON
  Schema canonical contracts.
- `capabilities/matlab-v1/eig.toml` — one capability, hand-typed
  from Cleve Moler's 1984 PC-MATLAB blog post + The Origins of
  MATLAB (provenance rows reference both).
- `benchmarks/linalg-eigh/` — full port of
  `scientist-workbench/bench/linalg-eigh/`:
  `inputs.json` and `expected.json` copied + SHA-256 pinned in the
  manifest, `verify.py` ported to TS-on-Bun (`verify.ts`,
  ~280 lines, tolerances byte-identical), `verify.py` retained as
  the Python escape-hatch demonstrator, `generate.py` retained
  unmodified, `manifest.toml` declares the 7 checks with their
  Higham-2002 tolerance citations.
- `adapters/scientist-workbench/linalg-eigh.toml` — points at the
  pre-existing `bench/linalg-eigh/run-candidate.ts` in
  scientist-workbench. Subprocess contract; corpus has zero TS
  imports of scientist-workbench.
- `src/{schema,loader,validator,build,grade,cli}.ts` — the TypeScript
  pipeline. `Ajv2020` for JSON-Schema-2020-12 validation;
  `@duckdb/node-api` for DuckDB; `smol-toml` for parse.
- `queries/grade-vs-corpus.sql` and `queries/failing-invariants.sql`
  — the scoreboard and the broken-things view.

## Why these choices

- **TOML source-of-truth, DuckDB compiled view** (QuantumHardware.jl
  pattern): git-diffable PRs over capabilities, DB regenerable from
  `git checkout`, schema can evolve freely.
- **Subprocess adapter, not TS import** of scientist-workbench's
  workspace packages: zero coupling at the build-tool level. The
  candidate is just an executable conforming to the tournament
  contract; *anything* can be a candidate.
- **TS-on-Bun verifier as default, with escape hatches in the
  schema**: matches the Two Principles ("what would a TS expert
  reach for"), but the manifest's `verifier.{cmd,args}` is fully
  generic so symbolic verifiers needing SymPy / Mathematica / GAP
  declare them per-suite.
- **`Ajv2020` not `Ajv`**: hit on first validate run — JSON Schema
  draft 2020-12 needs the explicit class. Worth flagging here so
  future ingestor code doesn't re-discover this.
- **`process.execPath` substitution for `cmd === "bun"`** in the
  spawn helper: same snap-Bun mount-namespace bug as
  scientist-workbench ADR-0001. The default `Bun.spawn(["bun", ...])`
  fails ENOENT inside another snap-confined Bun process even though
  `bun --version` works in a shell. Resolved by running Bun's own
  `process.execPath` instead of letting PATH walk hit the
  `/snap/bin/bun` wrapper.

## Frictions surfaced

- **`@duckdb/node-api` versioning mismatch.** The version I had in
  mental cache (`^1.4.0-r.5`) doesn't exist; checked `bun pm view`
  and pinned to `^1.5.2-r.1`. Worth memorising for next session.
- **`@duckdb/node-api 1.5` API surface.** No `instance.terminate()`
  on this version — `closeSync()` on the connection plus GC is
  enough. Initial code used the old API and threw at end of build.
  Removed; tests pass.
- **The 200×200 case in `verify.ts`.** Pure TS double-loops on
  Float64Array work fine, but `J_stress_500x500` is the slowest
  case at a few seconds for the verifier alone (matmul is naive
  triple-loop). Not a blocker; if it ever becomes one,
  `Float64Array` + cache-friendly loop ordering is the cheap win.
- **`expected.json` is 8.5MB committed.** OK for now; we're keeping
  raw inputs and expected outputs as immutable committed data, and
  v1 corpus + benches will plausibly live under 100MB total. If
  size grows, switch to git-LFS for the `golden/` files.

## Acceptance

- `bun corpus validate` → `OK — 1 caps, 1 suites, 1 adapters.`
- `bun corpus list` → enumerates the three.
- `bun corpus grade scientist-workbench linalg-eigh`
  → `cases: 46/46    invariants: 316/316`
  (45 success-path cases × 7 invariants = 315; plus 1 boundary
  case `K_non_symmetric_2x2` × 1 invariant = 316. Matches expectation.)
- `bun corpus build` → builds `build/corpus.duckdb` with 1 cap, 1
  suite, 1 adapter, 2 grade runs, 323 grade_results rows (1-case
  smoke + 46-case full).
- `bun corpus query grade-vs-corpus` → single row showing
  matlab-1.0/eig fully covered by scientist-workbench's linalg-eigh.
- `bun corpus query failing-invariants` → empty (no failures).

## Pointers

- `README.md` for the operational surface.
- `CLAUDE.md` for agent guidance.
- `benchmarks/linalg-eigh/golden/verifier_protocol.md` for the
  prose-form check definitions (unchanged from scientist-workbench).
- The 1984 MATLAB sources cited in `capabilities/matlab-v1/eig.toml`
  provenance rows.
