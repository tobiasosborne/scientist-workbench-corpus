// =============================================================================
// src/grade.ts — run a candidate × every case × verifier; collect per-check results.
// =============================================================================
//
// This is the tournament-protocol runner, ported from
// scientist-workbench/bench/infra/run-bench.sh into TS.  The contract:
//
//   1. Read inputs.json → { cases: [{ id, input }, ...] }.
//   2. For each case:
//      a. Pipe `input` to the candidate adapter (subprocess).  Capture stdout.
//      b. Pipe `{input, candidate, id}` to the verifier (subprocess).
//         Capture {pass, reason, checks}.
//      c. Record one row per check in `grade_results`.
//
// The runner produces a `grade_runs` row plus `grade_results` rows; both are
// returned to the caller (build.ts / cli.ts) which writes them into DuckDB.
//
// Substitutions:
//   - In adapter args/cwd: ${WORKBENCH_ROOT} → process.env.WORKBENCH_ROOT
//   - In verifier args:    ${SUITE_ROOT}     → absolute path of the suite dir
// Both substitutions are simple string replace; no shell parsing.

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import type { AdapterDoc, BenchCase, BenchmarkSuite, VerifierVerdict } from "./schema.ts";
import { loadAdapters, loadSuites, ROOT } from "./loader.ts";

export interface GradeRun {
  id: string;                       // synthetic
  candidate_target: string;
  candidate_version: string | null;
  suite_id: string;
  run_at: string;
  runner_cmd: string;
  platform_arch: string;
  platform_os: string;
  platform_runtime: string;
  cases_total: number;
  cases_passed: number;
  invariants_total: number;
  invariants_passed: number;
}

export interface GradeResult {
  run_id: string;
  case_id: string;
  check_name: string;
  pass: boolean;
  detail: string;
  // Trajectory checkpoints reserved per ADR-0030 §F / bead 1few.  v0.1
  // populates only `runtime_sec` (measured in the runner around the
  // candidate spawn); iter_* fields are reserved for future trajectory-
  // emitting candidates and remain null until then.  All five are
  // per-(run, case): every check row for the same case carries identical
  // values (denormalisation matches the bead's literal placement).
  runtime_sec?:         number | null;
  iter_count?:          number | null;
  iter_5_residual?:     number | null;
  iter_25_residual?:    number | null;
  iter_final_residual?: number | null;
}

interface SpawnOk  { ok: true; stdout: string }
interface SpawnErr { ok: false; reason: string; stderr?: string }

function resolveCmd(cmd: string): string {
  // Snap-Bun's `/snap/bin/bun` wrapper isn't directly spawnable from inside
  // another snap-confined Bun process (mount-namespace confinement; see
  // scientist-workbench ADR-0001). When the caller asks for "bun" without a
  // path, use the running Bun's own execPath, which always resolves.
  if (cmd === "bun" && process.execPath.endsWith("/bun")) return process.execPath;
  return cmd;
}

async function spawnPipe(cmd: string, args: string[], stdinBytes: Uint8Array, cwd: string, env: Record<string, string> | undefined, timeoutMs: number): Promise<SpawnOk | SpawnErr> {
  const proc = Bun.spawn([resolveCmd(cmd), ...args], {
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
    cwd,
    env: { ...process.env, ...(env ?? {}) },
  });
  proc.stdin.write(stdinBytes);
  await proc.stdin.end();

  const timer = setTimeout(() => { try { proc.kill(); } catch { /* noop */ } }, timeoutMs);
  const exit = await proc.exited;
  clearTimeout(timer);

  const stdout = await new Response(proc.stdout).text();
  const stderr = await new Response(proc.stderr).text();
  if (exit !== 0) return { ok: false, reason: `candidate exited ${exit}`, stderr };
  return { ok: true, stdout };
}

function substitute(s: string, vars: Record<string, string>): string {
  return s.replace(/\$\{([A-Z_][A-Z0-9_]*)\}/g, (_, k) => vars[k] ?? "");
}

const TIMEOUT_MS = Number(process.env["GRADE_TIMEOUT_MS"] ?? "120000");

export interface GradeOptions {
  caseIds?: string[];     // restrict to these case ids
  maxCases?: number;      // first N cases (after caseIds filter)
}

export async function gradeAdapterAgainstSuite(adapter: AdapterDoc, suite: BenchmarkSuite, suitePath: string, opts: GradeOptions = {}): Promise<{ run: GradeRun; results: GradeResult[] }> {
  const suiteRoot = dirname(suitePath);
  const inputsPath = join(suiteRoot, suite.golden.inputs);
  const inputsDoc = JSON.parse(readFileSync(inputsPath, "utf8")) as { cases: BenchCase[] };

  const filtered = opts.caseIds ? inputsDoc.cases.filter((c) => opts.caseIds!.includes(c.id)) : inputsDoc.cases;
  const cases = opts.maxCases !== undefined ? filtered.slice(0, opts.maxCases) : filtered;

  const adapterVars: Record<string, string> = {
    WORKBENCH_ROOT: process.env["WORKBENCH_ROOT"] ?? resolve(ROOT, "..", "scientist-workbench"),
    SUITE_ROOT: suiteRoot,
    CORPUS_ROOT: ROOT,
  };
  const verifierVars = adapterVars;

  const candidateCmd  = adapter.adapter.cmd;
  const candidateArgs = adapter.adapter.args.map((a) => substitute(a, adapterVars));
  const candidateCwd  = adapter.adapter.cwd ? substitute(adapter.adapter.cwd, adapterVars) : ROOT;

  const verifierCmd  = suite.verifier.cmd;
  const verifierArgs = suite.verifier.args.map((a) => substitute(a, verifierVars));
  const verifierCwd  = suiteRoot;

  const runId = crypto.randomUUID();
  const results: GradeResult[] = [];
  let casesPassed = 0;
  let invariantsTotal = 0;
  let invariantsPassed = 0;

  const enc = new TextEncoder();

  process.stderr.write(`grading ${cases.length} cases through ${candidateCmd} ${candidateArgs.join(" ")}\n`);

  for (let i = 0; i < cases.length; i++) {
    const c = cases[i]!;
    const candidateInput = enc.encode(JSON.stringify(c.input));

    // Merge adapterVars into the candidate's environment so that corpus-
    // resident scripts (e.g. benchmarks/*/run-candidate.ts) can read
    // WORKBENCH_ROOT, SUITE_ROOT, and CORPUS_ROOT directly from process.env
    // instead of relying only on template substitution in args strings.
    // The adapter's own TOML env overrides these, and process.env (from
    // spawnPipe) provides the rest of the host environment.
    const candidateEnv = { ...adapterVars, ...(adapter.adapter.env ?? {}) };
    const t0 = performance.now();
    const candResp = await spawnPipe(candidateCmd, candidateArgs, candidateInput, candidateCwd, candidateEnv, TIMEOUT_MS);
    const runtimeSec = (performance.now() - t0) / 1000;
    if (!candResp.ok) {
      // Synthesise a single-check failure row so the verdict is captured.
      results.push({ run_id: runId, case_id: c.id, check_name: "_candidate_exec", pass: false, detail: `${candResp.reason}: ${candResp.stderr?.slice(0, 500) ?? ""}`, runtime_sec: runtimeSec });
      invariantsTotal += 1;
      process.stderr.write(`  FAIL  ${c.id}: ${candResp.reason}\n`);
      continue;
    }

    let candidateValue: unknown;
    try { candidateValue = JSON.parse(candResp.stdout); }
    catch (e) {
      results.push({ run_id: runId, case_id: c.id, check_name: "_candidate_json", pass: false, detail: `candidate stdout not JSON: ${(e as Error).message}`, runtime_sec: runtimeSec });
      invariantsTotal += 1;
      process.stderr.write(`  FAIL  ${c.id}: candidate stdout not JSON\n`);
      continue;
    }

    const verifierPayload = enc.encode(JSON.stringify({ input: c.input, candidate: candidateValue, id: c.id }));
    const vResp = await spawnPipe(verifierCmd, verifierArgs, verifierPayload, verifierCwd, suite.verifier.env, TIMEOUT_MS);
    if (!vResp.ok) {
      results.push({ run_id: runId, case_id: c.id, check_name: "_verifier_exec", pass: false, detail: `${vResp.reason}: ${vResp.stderr?.slice(0, 500) ?? ""}`, runtime_sec: runtimeSec });
      invariantsTotal += 1;
      process.stderr.write(`  FAIL  ${c.id}: verifier crashed (${vResp.reason})\n`);
      continue;
    }

    let verdict: VerifierVerdict;
    try { verdict = JSON.parse(vResp.stdout) as VerifierVerdict; }
    catch (e) {
      results.push({ run_id: runId, case_id: c.id, check_name: "_verifier_json", pass: false, detail: `verifier stdout not JSON: ${(e as Error).message}`, runtime_sec: runtimeSec });
      invariantsTotal += 1;
      process.stderr.write(`  FAIL  ${c.id}: verifier stdout not JSON\n`);
      continue;
    }

    // Optional trajectory fields are read off the candidate's response if
    // present.  Today no candidate emits them; reserved per ADR-0030 §F.
    const traj = (candidateValue as { trajectory?: Record<string, number> } | null)?.trajectory ?? {};
    const trajFields = {
      runtime_sec:         runtimeSec,
      iter_count:          traj.iter_count          ?? null,
      iter_5_residual:     traj.iter_5_residual     ?? null,
      iter_25_residual:    traj.iter_25_residual    ?? null,
      iter_final_residual: traj.iter_final_residual ?? null,
    };
    for (const [name, ck] of Object.entries(verdict.checks)) {
      results.push({ run_id: runId, case_id: c.id, check_name: name, pass: ck.pass, detail: ck.detail, ...trajFields });
      invariantsTotal += 1;
      if (ck.pass) invariantsPassed += 1;
    }
    if (verdict.pass) {
      casesPassed += 1;
      process.stderr.write(`  pass  ${c.id}\n`);
    } else {
      process.stderr.write(`  FAIL  ${c.id}: ${verdict.reason}\n`);
    }
  }

  const run: GradeRun = {
    id: runId,
    candidate_target:  adapter.adapter.target,
    candidate_version: adapter.adapter.version ?? null,
    suite_id:          suite.meta.name,
    run_at:            new Date().toISOString(),
    runner_cmd:        `${candidateCmd} ${candidateArgs.join(" ")}`,
    platform_arch:     process.arch,
    platform_os:       process.platform,
    platform_runtime:  `bun-${Bun.version}`,
    cases_total:       cases.length,
    cases_passed:      casesPassed,
    invariants_total:  invariantsTotal,
    invariants_passed: invariantsPassed,
  };

  return { run, results };
}

export async function gradeOne(target: string, capabilityId: string, opts: GradeOptions = {}): Promise<{ run: GradeRun; results: GradeResult[] }> {
  const adapters = loadAdapters();
  const suites = loadSuites();
  const adapter = adapters.find((a) => a.doc.adapter.target === target && a.doc.adapter.capability_id === capabilityId);
  if (!adapter) throw new Error(`no adapter for target=${target} capability=${capabilityId}`);
  const suite = suites.find((s) => s.doc.meta.name === capabilityId);
  if (!suite) throw new Error(`no benchmark suite named ${capabilityId}`);
  return gradeAdapterAgainstSuite(adapter.doc, suite.doc, suite.path, opts);
}
