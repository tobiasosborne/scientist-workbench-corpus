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

const SCHEMA_VERSION = 1;

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
  category_primary VARCHAR,
  description_md  VARCHAR,
  manual_section  VARCHAR,
  benchmark_suite VARCHAR,
  verification_level INTEGER,
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
  file_path        VARCHAR
);

CREATE TABLE verifier_checks (
  suite_name        VARCHAR,
  check_name        VARCHAR,
  description       VARCHAR,
  tolerance_source  VARCHAR,
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
  notes          VARCHAR
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
  invariants_passed  INTEGER
);

CREATE TABLE grade_results (
  run_id      VARCHAR,
  case_id     VARCHAR,
  check_name  VARCHAR,
  pass        BOOLEAN,
  detail      VARCHAR
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
      `INSERT INTO capabilities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        c.id, cap.meta.system, cap.meta.version, cap.meta.name,
        cap.signature.input, cap.signature.output,
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
      await conn.run(
        `INSERT INTO mappings VALUES (?, ?, ?, ?, ?)`,
        [c.id, m.target, m.tool, m.status, m.notes ?? null],
      );
    }
  }

  // suites + verifier_checks
  for (const s of suites) {
    const su = s.doc;
    await conn.run(
      `INSERT INTO benchmark_suites VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        su.meta.name, su.meta.domain, su.meta.description ?? null, su.meta.ported_from ?? null,
        su.verifier.kind ?? null, su.verifier.cmd,
        su.golden.inputs, su.golden.inputs_sha256 ?? null,
        su.golden.expected, su.golden.expected_sha256 ?? null,
        su.golden.n_cases, s.rel,
      ],
    );
    for (const ck of su.verifier.checks) {
      await conn.run(
        `INSERT INTO verifier_checks VALUES (?, ?, ?, ?)`,
        [su.meta.name, ck.name, ck.description, ck.tolerance_source ?? null],
      );
    }
  }

  // adapters
  for (const a of adapters) {
    await conn.run(
      `INSERT INTO adapters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        a.id, a.doc.adapter.target, a.doc.adapter.capability_id,
        a.doc.adapter.cmd, JSON.stringify(a.doc.adapter.args),
        a.doc.adapter.cwd ?? null, a.doc.adapter.version ?? null,
        a.doc.adapter.platform_pinned ?? false, a.rel,
      ],
    );
  }

  // grade runs + results
  for (const r of runs) {
    await conn.run(
      `INSERT INTO grade_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [r.id, r.candidate_target, r.candidate_version, r.suite_id, r.run_at, r.runner_cmd,
       r.platform_arch, r.platform_os, r.platform_runtime,
       r.cases_total, r.cases_passed, r.invariants_total, r.invariants_passed],
    );
  }
  for (const gr of results) {
    await conn.run(
      `INSERT INTO grade_results VALUES (?, ?, ?, ?, ?)`,
      [gr.run_id, gr.case_id, gr.check_name, gr.pass, gr.detail],
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
