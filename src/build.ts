// =============================================================================
// src/build.ts — flatten TOMLs + grade-runs into a fresh corpus.duckdb.
// =============================================================================
//
// The TOML tree is the source of truth (QuantumHardware.jl pattern).  This
// module produces the queryable view: drop the existing DB, re-create the
// schema, insert one row per record.  Idempotent — running it twice on
// unchanged inputs yields byte-identical DBs (modulo build timestamps).

import { existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { DuckDBInstance } from "@duckdb/node-api";
import { loadAdapters, loadCapabilities, loadSuites, ROOT } from "./loader.ts";
import type { GradeResult, GradeRun } from "./grade.ts";

const BUILD_DIR  = join(ROOT, "build");
const RUNS_DIR   = join(BUILD_DIR, "grade-runs");
const DB_PATH    = join(BUILD_DIR, "corpus.duckdb");

// 1 → 2: grade_results gains 5 reserved columns (runtime_sec, iter_count,
// iter_{5,25,final}_residual) for the convex-cone tier (ADR-0030 §F).
// 2 → 3: surface the v2 JSON-Schema additions (beads B8/B9/B10) as queryable
// columns -- capability signature.domain/codomain, mapping[].flags +
// precision_tier (+ denormalised dispatch_head convenience column),
// golden.n_heads/n_tiers on benchmark suites, verifier.checks[]
// .machine_checkable + .applies_when, adapter.tool_flags, and a
// grade_runs.candidate_flags column for future per-run flag introspection.
// `verification.method` stays plain VARCHAR (no CHECK constraint), so the
// newly-allowed 'arbprec_oracle' enum value needs no DDL change here.
const SCHEMA_VERSION = 3;

const DDL = `
CREATE TABLE _metadata (
  schema_version INTEGER,
  built_at       VARCHAR,
  capability_count INTEGER,
  suite_count    INTEGER,
  adapter_count  INTEGER,
  grade_run_count INTEGER
);

CREATE TABLE capabilities (
  id              VARCHAR PRIMARY KEY,
  system          VARCHAR,
  version         VARCHAR,
  name            VARCHAR,
  signature_input VARCHAR,
  signature_output VARCHAR,
  -- v3: optional signature.domain / signature.codomain (bead q4r) -- e.g.
  -- R^n, C, PD(n). Nullable -- legacy capabilities omit them.
  signature_domain   VARCHAR,
  signature_codomain VARCHAR,
  category_primary VARCHAR,
  description_md  VARCHAR,
  manual_section  VARCHAR,
  benchmark_suite VARCHAR,
  verification_level INTEGER,
  -- v3: 'arbprec_oracle' is now an accepted value of verification.method
  -- (bead q4r). Column stays a plain VARCHAR — no CHECK constraint exists,
  -- so the enum extension requires no DDL change.
  verification_method VARCHAR,
  file_path       VARCHAR
);

CREATE TABLE benchmark_suites (
  name             VARCHAR PRIMARY KEY,
  domain           VARCHAR,
  description      VARCHAR,
  ported_from     VARCHAR,
  verifier_kind    VARCHAR,
  verifier_cmd     VARCHAR,
  golden_inputs    VARCHAR,
  golden_inputs_sha256 VARCHAR,
  golden_expected  VARCHAR,
  golden_expected_sha256 VARCHAR,
  n_cases          INTEGER,
  -- v3: optional golden.n_heads / golden.n_tiers (bead wr2) for
  -- multi-head, multi-tier mega-anchor corpora (ADR-0040/0041). Nullable
  -- -- single-head/single-tier suites omit them.
  n_heads          INTEGER,
  n_tiers          INTEGER,
  file_path        VARCHAR
);

CREATE TABLE verifier_checks (
  suite_name        VARCHAR,
  check_name        VARCHAR,
  description       VARCHAR,
  tolerance_source  VARCHAR,
  -- v3: optional verifier.checks[].machine_checkable + .applies_when
  -- (bead wr2). machine_checkable mirrors workbench InvariantEntry's flag.
  -- applies_when is a free-form predicate string (real_input,
  -- head=BesselJ, ...) -- kept as VARCHAR so DuckDB stays a queryable
  -- view without parsing semantics.
  machine_checkable BOOLEAN,
  applies_when      VARCHAR,
  PRIMARY KEY (suite_name, check_name)
);

CREATE TABLE adapters (
  id               VARCHAR PRIMARY KEY,
  target           VARCHAR,
  capability_id    VARCHAR,
  cmd              VARCHAR,
  args             VARCHAR,
  cwd              VARCHAR,
  version          VARCHAR,
  platform_pinned  BOOLEAN,
  -- v3: optional adapter.tool_flags (bead 3u3), JSON-serialised
  -- object<string,string>. Drives head/precision dispatch for tools like
  -- special-eval. Nullable -- legacy adapters omit it.
  tool_flags       VARCHAR,
  file_path        VARCHAR
);

CREATE TABLE provenance (
  capability_id  VARCHAR,
  field_path     VARCHAR,
  value          VARCHAR,
  source_url     VARCHAR,
  source_kind    VARCHAR,
  retrieved_at   VARCHAR,
  local_path     VARCHAR,
  page           INTEGER,
  sha256         VARCHAR,
  conflict       BOOLEAN
);

CREATE TABLE mappings (
  capability_id  VARCHAR,
  target         VARCHAR,
  tool           VARCHAR,
  status         VARCHAR,
  notes          VARCHAR,
  -- v3: optional mapping[].flags (bead q4r), JSON-serialised
  -- object<string,string>. dispatch_head is a denormalised convenience
  -- column carrying flags.head when present, so queries can do
  -- WHERE dispatch_head = BesselJ without JSON-parsing every row.
  flags          VARCHAR,
  precision_tier VARCHAR,
  dispatch_head  VARCHAR
);

CREATE TABLE grade_runs (
  id                 VARCHAR PRIMARY KEY,
  candidate_target   VARCHAR,
  candidate_version  VARCHAR,
  suite_id           VARCHAR,
  run_at             VARCHAR,
  runner_cmd         VARCHAR,
  platform_arch      VARCHAR,
  platform_os        VARCHAR,
  platform_runtime   VARCHAR,
  cases_total        INTEGER,
  cases_passed       INTEGER,
  invariants_total   INTEGER,
  invariants_passed  INTEGER,
  -- v3: JSON-serialised flag map captured at grade-invocation time
  -- (e.g. '{"head":"BesselJ","precision":"53"}'). Null for runs that
  -- pre-date this column. Populated once grade.ts learns to forward
  -- adapter.tool_flags into the run record. Future-proofing for
  -- per-run flag introspection.
  candidate_flags    VARCHAR
);

CREATE TABLE grade_results (
  run_id              VARCHAR,
  case_id             VARCHAR,
  check_name          VARCHAR,
  pass                BOOLEAN,
  detail              VARCHAR,
  -- Trajectory checkpoints reserved per ADR-0030 §F (convex-cone tier).
  -- v0.1 populates only runtime_sec measured by grade.ts around the
  -- candidate spawn -- iter_* fields remain null until a candidate
  -- starts emitting a trajectory record.  Per bead 1few -- v0.1 only
  -- consumes the final answer, trajectory unlocks for free later.
  runtime_sec         DOUBLE,
  iter_count          INTEGER,
  iter_5_residual     DOUBLE,
  iter_25_residual    DOUBLE,
  iter_final_residual DOUBLE
);
`;

function loadGradeRuns(): { runs: GradeRun[]; results: GradeResult[] } {
  if (!existsSync(RUNS_DIR)) return { runs: [], results: [] };
  const files = readdirSync(RUNS_DIR).filter((n) => n.endsWith(".json"));
  const runs: GradeRun[] = [];
  const results: GradeResult[] = [];
  for (const f of files) {
    const doc = JSON.parse(readFileSync(join(RUNS_DIR, f), "utf8")) as { run: GradeRun; results: GradeResult[] };
    runs.push(doc.run);
    results.push(...doc.results);
  }
  return { runs, results };
}

export async function build(): Promise<{ db: string; counts: Record<string, number> }> {
  if (!existsSync(BUILD_DIR)) mkdirSync(BUILD_DIR, { recursive: true });
  if (existsSync(DB_PATH)) await Bun.file(DB_PATH).delete();

  const instance = await DuckDBInstance.create(DB_PATH);
  const conn = await instance.connect();
  for (const stmt of DDL.split(";").map((s) => s.trim()).filter(Boolean)) {
    await conn.run(stmt);
  }

  const caps     = loadCapabilities();
  const suites   = loadSuites();
  const adapters = loadAdapters();
  const { runs, results } = loadGradeRuns();

  // capabilities
  for (const c of caps) {
    const cap = c.doc;
    await conn.run(
      `INSERT INTO capabilities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        c.id, cap.meta.system, cap.meta.version, cap.meta.name,
        cap.signature.input, cap.signature.output,
        cap.signature.domain   ?? null,
        cap.signature.codomain ?? null,
        cap.category.primary,
        cap.description.md,
        cap.meta.section ?? null,
        cap.benchmark?.suite ?? null,
        cap.verification?.level ?? null,
        cap.verification?.method ?? null,
        c.rel,
      ],
    );
    for (const p of cap.provenance ?? []) {
      await conn.run(
        `INSERT INTO provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [c.id, p.field_path, p.value, p.source_url, p.source_kind, p.retrieved_at ?? null,
         p.local_path ?? null, p.page ?? null, p.sha256 ?? null, p.conflict ?? false],
      );
    }
    for (const m of cap.mapping ?? []) {
      const flags = m.flags;
      await conn.run(
        `INSERT INTO mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          c.id, m.target, m.tool, m.status, m.notes ?? null,
          flags ? JSON.stringify(flags) : null,
          m.precision_tier ?? null,
          flags?.head ?? null,
        ],
      );
    }
  }

  // suites + verifier_checks
  for (const s of suites) {
    const su = s.doc;
    await conn.run(
      `INSERT INTO benchmark_suites VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        su.meta.name, su.meta.domain, su.meta.description ?? null, su.meta.ported_from ?? null,
        su.verifier.kind ?? null, su.verifier.cmd,
        su.golden.inputs, su.golden.inputs_sha256 ?? null,
        su.golden.expected, su.golden.expected_sha256 ?? null,
        su.golden.n_cases,
        su.golden.n_heads ?? null,
        su.golden.n_tiers ?? null,
        s.rel,
      ],
    );
    for (const ck of su.verifier.checks) {
      await conn.run(
        `INSERT INTO verifier_checks VALUES (?, ?, ?, ?, ?, ?)`,
        [
          su.meta.name, ck.name, ck.description, ck.tolerance_source ?? null,
          ck.machine_checkable ?? null,
          ck.applies_when ?? null,
        ],
      );
    }
  }

  // adapters
  for (const a of adapters) {
    const toolFlags = a.doc.adapter.tool_flags;
    await conn.run(
      `INSERT INTO adapters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        a.id, a.doc.adapter.target, a.doc.adapter.capability_id,
        a.doc.adapter.cmd, JSON.stringify(a.doc.adapter.args),
        a.doc.adapter.cwd ?? null, a.doc.adapter.version ?? null,
        a.doc.adapter.platform_pinned ?? false,
        toolFlags ? JSON.stringify(toolFlags) : null,
        a.rel,
      ],
    );
  }

  // grade runs + results
  for (const r of runs) {
    // candidate_flags arrived in schema v3 (beads q4r/wr2/3u3 → B11/B12).
    // Persisted runs produced by older grade.ts omit the field; passing
    // null is fine. Once grade.ts forwards adapter.tool_flags, the field
    // will be populated as a structured object or pre-serialised string.
    const candFlags = r.candidate_flags;
    const candFlagsSerialised =
      typeof candFlags === "string" ? candFlags :
      candFlags                     ? JSON.stringify(candFlags) : null;
    await conn.run(
      `INSERT INTO grade_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [r.id, r.candidate_target, r.candidate_version, r.suite_id, r.run_at, r.runner_cmd,
       r.platform_arch, r.platform_os, r.platform_runtime,
       r.cases_total, r.cases_passed, r.invariants_total, r.invariants_passed,
       candFlagsSerialised],
    );
  }
  for (const gr of results) {
    await conn.run(
      `INSERT INTO grade_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        gr.run_id, gr.case_id, gr.check_name, gr.pass, gr.detail,
        gr.runtime_sec         ?? null,
        gr.iter_count          ?? null,
        gr.iter_5_residual     ?? null,
        gr.iter_25_residual    ?? null,
        gr.iter_final_residual ?? null,
      ],
    );
  }

  await conn.run(
    `INSERT INTO _metadata VALUES (?, ?, ?, ?, ?, ?)`,
    [SCHEMA_VERSION, new Date().toISOString(), caps.length, suites.length, adapters.length, runs.length],
  );

  conn.closeSync();
  // @duckdb/node-api 1.5 has no instance.terminate(); GC handles cleanup.

  return {
    db: DB_PATH,
    counts: { capabilities: caps.length, suites: suites.length, adapters: adapters.length, grade_runs: runs.length, grade_results: results.length },
  };
}

if (import.meta.main) {
  const r = await build();
  process.stdout.write(`built ${r.db}\n  ${JSON.stringify(r.counts)}\n`);
}
