// =============================================================================
// src/cli.ts — `bun corpus <subcommand>` entry point.
// =============================================================================

import { existsSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { DuckDBInstance } from "@duckdb/node-api";
import { build } from "./build.ts";
import { gradeOne, type GradeOptions } from "./grade.ts";
import { loadCapabilities, loadSuites, loadAdapters, ROOT } from "./loader.ts";
import { validateAll } from "./validator.ts";

const BUILD_DIR = join(ROOT, "build");
const RUNS_DIR  = join(BUILD_DIR, "grade-runs");
const DB_PATH   = join(BUILD_DIR, "corpus.duckdb");

const HELP = `
bun corpus <command> [args]

Commands:
  validate                              JSON-Schema validate every TOML.
  list                                  List capabilities / suites / adapters.
  build                                 Rebuild build/corpus.duckdb from TOMLs + grade-runs.
  grade <target> <capability> [opts]    Run a candidate adapter through a benchmark suite.
                                          --case-id=<id>          restrict to one case
                                          --max-cases=<N>         first N cases only
                                          --no-build              skip post-grade rebuild
  query <name>                          Run queries/<name>.sql against build/corpus.duckdb.
  query-sql "<sql>"                     Run an inline SQL query.
  help                                  This text.
`.trim();

async function cmdValidate(): Promise<number> {
  const r = validateAll();
  if (r.ok) { process.stdout.write(`OK — ${r.counts.capabilities} caps, ${r.counts.suites} suites, ${r.counts.adapters} adapters.\n`); return 0; }
  process.stderr.write(`FAIL — ${r.issues.length} issue(s):\n`);
  for (const i of r.issues) {
    process.stderr.write(`  ${i.path}\n`);
    for (const e of i.errors) process.stderr.write(`    ${e}\n`);
  }
  return 1;
}

function cmdList(): number {
  const caps = loadCapabilities();
  const suites = loadSuites();
  const adapters = loadAdapters();
  process.stdout.write(`capabilities (${caps.length}):\n`);
  for (const c of caps) process.stdout.write(`  ${c.id.padEnd(36)}  ${c.rel}\n`);
  process.stdout.write(`\nbenchmark suites (${suites.length}):\n`);
  for (const s of suites) process.stdout.write(`  ${s.id.padEnd(36)}  ${s.rel}  (${s.doc.golden.n_cases} cases, ${s.doc.verifier.checks.length} checks)\n`);
  process.stdout.write(`\nadapters (${adapters.length}):\n`);
  for (const a of adapters) process.stdout.write(`  ${a.id.padEnd(36)}  ${a.rel}\n`);
  return 0;
}

async function cmdBuild(): Promise<number> {
  const r = await build();
  process.stdout.write(`built ${r.db}\n  ${JSON.stringify(r.counts)}\n`);
  return 0;
}

async function cmdGrade(args: string[]): Promise<number> {
  if (args.length < 2) { process.stderr.write("usage: corpus grade <target> <capability> [--case-id=<id>] [--max-cases=<N>] [--no-build]\n"); return 2; }
  const [target, capability, ...rest] = args;
  const opts: GradeOptions = {};
  let doBuild = true;
  for (const a of rest) {
    if (a === "--no-build") { doBuild = false; continue; }
    const m = /^--([a-z-]+)=(.*)$/.exec(a);
    if (!m) { process.stderr.write(`unknown arg: ${a}\n`); return 2; }
    if (m[1] === "case-id")   opts.caseIds  = [...(opts.caseIds ?? []), m[2]!];
    else if (m[1] === "max-cases") opts.maxCases = Number(m[2]);
    else { process.stderr.write(`unknown flag: ${m[1]}\n`); return 2; }
  }
  const r = await gradeOne(target!, capability!, opts);
  if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
  const path = join(RUNS_DIR, `${r.run.id}.json`);
  writeFileSync(path, JSON.stringify({ run: r.run, results: r.results }, null, 2));
  process.stdout.write(`\nrecorded ${path}\n`);
  process.stdout.write(`cases: ${r.run.cases_passed}/${r.run.cases_total}    invariants: ${r.run.invariants_passed}/${r.run.invariants_total}\n`);
  if (doBuild) await cmdBuild();
  return r.run.cases_passed === r.run.cases_total ? 0 : 1;
}

async function runQuery(sql: string): Promise<number> {
  if (!existsSync(DB_PATH)) { process.stderr.write(`no DB at ${DB_PATH} — run 'corpus build' first\n`); return 2; }
  const instance = await DuckDBInstance.create(DB_PATH);
  const conn = await instance.connect();
  const reader = await conn.runAndReadAll(sql);
  const rows = reader.getRowObjects();
  if (rows.length === 0) {
    process.stdout.write("(empty result)\n");
  } else {
    const cols = Object.keys(rows[0]!);
    process.stdout.write(cols.join("\t") + "\n");
    for (const r of rows) {
      process.stdout.write(cols.map((k) => formatCell((r as Record<string, unknown>)[k])).join("\t") + "\n");
    }
  }
  conn.closeSync();
  return 0;
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "string") return v.replace(/\n/g, "\\n").slice(0, 200);
  if (typeof v === "bigint") return v.toString();
  return String(v);
}

async function cmdQuery(args: string[]): Promise<number> {
  if (args.length < 1) { process.stderr.write("usage: corpus query <name>\n"); return 2; }
  const name = args[0]!;
  const path = join(ROOT, "queries", `${name}.sql`);
  if (!existsSync(path)) { process.stderr.write(`no query at ${path}\n`); return 2; }
  const sql = readFileSync(path, "utf8");
  return runQuery(sql);
}

async function cmdQuerySql(args: string[]): Promise<number> {
  if (args.length < 1) { process.stderr.write("usage: corpus query-sql \"<sql>\"\n"); return 2; }
  return runQuery(args.join(" "));
}

async function main(): Promise<void> {
  const [cmd, ...rest] = Bun.argv.slice(2);
  let code: number;
  switch (cmd) {
    case "validate":  code = await cmdValidate(); break;
    case "list":      code = cmdList(); break;
    case "build":     code = await cmdBuild(); break;
    case "grade":     code = await cmdGrade(rest); break;
    case "query":     code = await cmdQuery(rest); break;
    case "query-sql": code = await cmdQuerySql(rest); break;
    case "help":
    case undefined:   process.stdout.write(HELP + "\n"); code = 0; break;
    default:          process.stderr.write(`unknown command: ${cmd}\n${HELP}\n`); code = 2;
  }
  process.exit(code);
}

await main();
