# CLAUDE.md — agent guidance for scientist-workbench-corpus

Read top-to-bottom every session. Sister repo to scientist-workbench;
shares its discipline, narrows its scope.

## The Laws (from scientist-workbench, restated)

1. **Ground truth before code.** The capability TOML and benchmark
   manifest *are* the spec. Write or open them before any TS
   implementation that depends on them. The corpus is data; the data
   is the spec.

2. **Docs in lockstep with code.** Schemas + capability TOMLs +
   benchmark manifests + worklog shards travel together. A new
   ingestor without a worklog shard explaining provenance is
   incomplete work.

## Rules specific to this repo

1. **TOML on disk is the source of truth; DuckDB is a compiled view.**
   Never hand-edit `build/corpus.duckdb`. Always regenerate from the
   TOML tree via `bun corpus build`. If you need to fix a row, fix
   the TOML and rebuild.

2. **JSON Schema is the canonical contract.** TS types in `src/schema.ts`
   are *derived* from the schemas, not the other way round.
   Cross-language consumers (Python ingestors, Julia adapters) read
   the JSON Schemas directly. Keep TS types and JSON Schemas in lock
   step when either changes.

3. **Validate, never silently drop.** Every TOML passes
   `bun corpus validate` before any build / grade / commit. The
   fundingscape anti-pattern (401K abstracts silently dropped by an
   ETL with no validation) is the named failure mode. If a field
   doesn't conform, fail loud and halt.

4. **Verifier substrate: TS-on-Bun default, escape hatches honoured.**
   New benchmark suites default to `verify.ts` running under Bun.
   When SymPy / Mathematica / SageMath / GAP / R / Julia are the
   right oracle, declare them in the manifest's `verifier.cmd` /
   `args`; the runner is verifier-agnostic. Don't port a
   genuinely-Python-shaped verifier to TS just to enforce the
   default — that's discipline at the wrong level.

5. **Adapter contract: tournament protocol exactly.** A candidate
   reads one JSON object on stdin, writes one JSON value on stdout,
   exits 0 on success. Never re-shape the contract per-tool; if a
   downstream impl needs encoding work, the *adapter* does it, not
   the corpus runner.

6. **Provenance every field, with SHA-256.** Borrow QuantumHardware.jl's
   `(field_path, value, source_url, source_kind, sha256, conflict)`
   shape. URLs rot; archives don't. Never delete provenance rows;
   add new ones with `conflict = true` if disagreement surfaces.

7. **Verification lattice (Integralis-derived).** V0 raw / V1 manual
   cite / V2 numerical / V3 oracle_run / V4 cross-system consensus.
   Levels are monotonic. Don't claim V3 without an oracle; don't
   claim V4 without two independent systems agreeing on a shared
   example.

8. **The DB schema is regeneratable.** It evolves freely.
   `_metadata.schema_version` exists from day 1; bump it when the
   shape changes. No ad-hoc `ALTER TABLE` calls scattered across
   ingestors (fundingscape lesson).

9. **Beads in this repo.** Run `bd bootstrap --yes` at first sight;
   never `bd init` (destructive). Per-device setup mirrors
   scientist-workbench. (Tracker setup deferred until the corpus
   has more than tracer-scope work.)

10. **Re-read this file at session start, after `/clear`, after any
    context compression.**

## Practical guidance

- Substrate: TypeScript on Bun. No build step.
- One subcommand per concept: `validate`, `list`, `build`, `grade`,
  `query`, `query-sql`. Don't accrete flags onto these — add a new
  subcommand if the surface grows.
- The adapter for scientist-workbench points at
  `${WORKBENCH_ROOT}/bench/<tool>/run-candidate.ts`. The migration
  end-state is to *move* those files into this repo's
  `benchmarks/<tool>/`. Until then the adapter is the bridge.
- When porting a Python verifier to TS, port the tolerance constants
  *exactly* as written. The Higham-bound tolerances are not
  approximate — they're calibrated to the algorithms' backward-stability
  proofs.

## Session close

1. `bun corpus validate && bun corpus build` — leave the working
   tree in a consistent state.
2. Worklog shard if a meaningful chunk shipped.
3. `git add` + `git commit` + `git push` (when remote exists).


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
