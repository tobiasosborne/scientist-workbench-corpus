# scientist-workbench-corpus

Sister repo to [`scientist-workbench`](../scientist-workbench/). A queryable
catalog of v1 Mathematica / MATLAB / Macsyma capabilities, plus
tournament-protocol benchmark suites that grade *any* candidate
implementation against those capabilities.

The capability list comes from public sources (the 1988 Mathematica book on
archive.org, Cleve Moler's 1984 MATLAB HELP dump, the 1977 Macsyma
Reference Manual on bitsavers). The benchmark suites adapt the
brutal-and-punishing golden-master protocol from `tstournament`. Together
they form a single artifact that is both **planning backlog** ("what
capabilities exist that aren't yet in scientist-workbench?") and
**grading harness** ("does scientist-workbench's `linalg-eigh` actually
pass v1 MATLAB's `eig` invariants?").

License: same as scientist-workbench (AGPL-3.0-or-later).

---

## Status: tracer-bullet complete

One capability (`matlab-1.0/eig`), one benchmark suite (`linalg-eigh`,
46 cases × 7 invariants), one adapter (`scientist-workbench`), graded
end-to-end. Result on first run: 46/46 cases, 316/316 invariants — pass.

---

## Layout

```
schema/                  JSON Schemas — canonical contracts
  capability.schema.json
  benchmark-suite.schema.json
  adapter.schema.json

capabilities/            source-of-truth, one TOML per capability
  matlab-v1/eig.toml

benchmarks/              tournament-protocol suites
  linalg-eigh/
    DESCRIPTION.md  REFERENCES.md
    manifest.toml          declares verifier cmd, checks, golden hashes
    golden/
      inputs.json          (sha256 pinned; 46 cases)
      expected.json        (sha256 pinned)
      verify.ts            (TS-on-Bun default verifier)
      verify.py            (Python escape hatch, kept for parity)
      generate.py          (regeneration script — calls SciPy as oracle)
      verifier_protocol.md

adapters/                per-implementation bridges
  scientist-workbench/linalg-eigh.toml

src/                     TypeScript pipeline
  schema.ts loader.ts validator.ts
  build.ts grade.ts cli.ts

queries/                 canonical SQL against build/corpus.duckdb
  grade-vs-corpus.sql
  failing-invariants.sql

build/                   gitignored — corpus.duckdb + grade-runs/*.json
docs/worklog/            shards (scientist-workbench convention)
```

---

## Pipeline

```sh
bun install                                    # one-time
bun src/cli.ts validate                        # JSON-Schema check every TOML
bun src/cli.ts list                            # show what's in the corpus
bun src/cli.ts grade scientist-workbench linalg-eigh
                                               # run the candidate × every case × verifier
bun src/cli.ts build                           # rebuild build/corpus.duckdb
bun src/cli.ts query grade-vs-corpus           # the scoreboard
bun src/cli.ts query failing-invariants        # what's broken right now
bun src/cli.ts query-sql "SELECT * FROM …"     # ad-hoc SQL
```

The `grade` subcommand also accepts `--max-cases=<N>` and
`--case-id=<id>` for inner-loop iteration during verifier development.

---

## Verifier substrate: TS-on-Bun default, multiple escape hatches

Every benchmark suite declares a verifier in its `manifest.toml`:

```toml
[verifier]
kind = "bun"
cmd  = "bun"
args = ["run", "${SUITE_ROOT}/golden/verify.ts"]
```

The default is TS-on-Bun (the workbench is TS-native; a TS-expert reaches
for TS reflexively). The `cmd` / `args` pair is fully generic, so any
oracle a verifier needs can be invoked instead:

| escape hatch | example `cmd` | when |
|---|---|---|
| Python  | `python3`           | symbolic verifiers needing SymPy / SciPy / `fractions.Fraction` exact-rational ground truth |
| Wolfram | `wolframscript`     | Mathematica-specific identity checks |
| Octave  | `octave --no-gui`   | MATLAB-format verification |
| R       | `Rscript`           | statistics / mixed-effects models |
| Julia   | `julia --project`   | high-performance numerics where Bun won't keep up |
| Sage    | `sage`              | algebraic-geometry, number-theoretic verification |
| GAP     | `gap -q`            | finite-group / discrete-algebra verification |

The grade runner is verifier-agnostic. Each suite picks the right tool;
the corpus aggregates pass/fail rows uniformly.

---

## DuckDB schema

Built fresh on every `bun corpus build`; every run is reproducible from
the TOML tree alone (the DB is a *view*, never the source of truth).

| table | purpose |
|---|---|
| `_metadata`         | schema version, build timestamp, row counts |
| `capabilities`      | one row per capability TOML |
| `benchmark_suites`  | one row per `manifest.toml` |
| `verifier_checks`   | one row per declared check (e.g. 7 rows for linalg-eigh) |
| `adapters`          | one row per per-implementation bridge |
| `provenance`        | append-only field-level provenance with SHA-256 |
| `mappings`          | capability → downstream tool, with status |
| `grade_runs`        | one row per `corpus grade` invocation |
| `grade_results`     | one row per (run × case × check) |

The provenance and conflict-flagging patterns are borrowed from
[`QuantumHardware.jl`](../QuantumHardware.jl/); the V0–V4 verification
lattice from [`Integralis`](../Integralis/); the silent-drop /
monolithic-loader anti-patterns to avoid are from
[`fundingscape`](../fundingscape/)'s lessons.

---

## Roadmap (post-tracer)

- **Ingestors.** One TS module per source (`ingestors/matlab-v1.ts`,
  `ingestors/wolfram-v1.ts`, `ingestors/macsyma-v9.ts`) emitting one TOML
  per discovered capability. The MATLAB v1 surface is ~71 functions —
  smallest first.
- **Aliases.** Cross-system equivalence groups (`aliases/determinant.toml`).
  The intersection (capabilities present in ≥2 systems) becomes the
  priority queue for scientist-workbench's tool catalog.
- **Adapters for other implementations.** The same suite can grade
  SymPy, Maxima, NumPy, Octave — each as a separate adapter TOML.
  Cross-implementation invariant divergence is itself a finding.
- **Wholesale migration of `scientist-workbench/bench/<tool>/`** into
  `benchmarks/<tool>/`. The corpus repo absorbs the bench corpus.
  ~10 tools today, port-cost estimated <1 hr/tool.
