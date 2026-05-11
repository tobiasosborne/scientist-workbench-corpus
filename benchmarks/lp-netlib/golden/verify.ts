// =============================================================================
// lp-netlib / lp-small verifier — invariant-based, dual-witness oracle backed.
// =============================================================================
//
// One verifier serves both LP suites; lp-small/golden/verify.ts re-exports
// from this file.  The two suites share wire format (ADR-0030 §C), check
// definitions (`verifier_protocol.md`), and the consensus-objective scheme
// (Gurobi + Mosek triangulation).
//
// stdin per case:
//   {
//     input:     { minimize: { c: number[] },
//                  subjectTo: { Ax_eq_b?: { A: number[][], b: number[] },
//                               cones:   { head: string, indices: number[],
//                                          size?: number }[] },
//                  precision: number, max_iter?: number },
//     candidate: <success record OR tagged refusal envelope>,
//     id:        string
//   }
//
// stdout per case:
//   { pass: boolean, reason: string,
//     checks: { <name>: { pass: boolean, detail: string } } }
//
// Exit 0 always.  A failed check is data, not a verifier crash.
//
// ─── Why this verifier is shaped the way it is ────────────────────────────
//
// The verifier reads `expected.json` once at startup and indexes cases by
// `id`.  Per-case, it consults `expected[id]` for two things only:
//
//   1. The consensus *status* — what we expect the candidate to declare.
//      Status mismatches are hard fails (a candidate that returns
//      `optimal` on a known-infeasible problem is lying; CLAUDE.md Rule 8).
//
//   2. The consensus *objective* — the dual-witness pinned value from
//      Gurobi+Mosek (and COPT once installed).  This is the only check
//      that consults the oracle's actual numerical answer; every other
//      check is invariant-based, computed from the candidate's own
//      `(x, y, s)` and the case input.
//
// Field-absence semantics drive several decisions:
//
//   - The candidate's `objective` field is *present iff status === "optimal"*.
//     Non-optimal statuses omit `objective` rather than carry ±Inf / NaN
//     (raw JSON forbids those; widening number to number|"Infinity"
//     widens every consumer's typing).
//   - `expected[id].objective` follows the same convention.  When
//     consensus.agreement is false, the expected record carries no
//     objective and the oracle_agreement check passes by N/A.
//
// The Farkas / unboundedness certificates are *sign-ambiguous by
// definition* — y and -y are both valid Farkas certificates if either
// is.  Gurobi and Mosek pick different conventions.  The verifier
// accepts both signs (tries each, passes if either works); the
// candidate is free to follow whichever convention its algorithm
// produces.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Types ────────────────────────────────────────────────────────────────

interface ProblemInput {
  minimize: { c: number[] };
  subjectTo: {
    Ax_eq_b?: { A: number[][]; b: number[] };
    cones: { head: string; indices?: number[]; size?: number }[];
  };
  precision: number;
  max_iter?: number;
}

interface SuccessRecord {
  status: "optimal" | "infeasible" | "unbounded" | "iter-cap" | "numerical-breakdown";
  x: number[];
  dual: number[];
  slack: number[];
  iterations: number;
  method: string;
  condition_estimate: number;
  warnings: string[];
  objective?: number;            // present iff status === "optimal"
  achieved_precision?: number;   // present iff status === "optimal"
}

interface TaggedRefusal {
  kind: "tagged";
  tag: string;
  payload: Record<string, unknown>;
}

type Candidate = SuccessRecord | TaggedRefusal;

interface Expected {
  status: SuccessRecord["status"];
  objective?: number;
  consensus: {
    agreement: boolean;
    agreement_tol: number;
    objective_gurobi?: number;
    objective_mosek?: number;
    rel_diff?: number;
    reason?: string;
    rationale?: string;
  };
}

interface CheckResult { pass: boolean; detail: string }
interface Verdict     { pass: boolean; reason: string; checks: Record<string, CheckResult> }

// ─── Numeric helpers ──────────────────────────────────────────────────────
//
// Inlined rather than depending on @workbench/* (the corpus is a sibling repo,
// not a workspace member of scientist-workbench).  All vectors are number[].

function infNorm(v: number[]): number {
  let m = 0;
  for (const x of v) {
    const a = Math.abs(x);
    if (a > m) m = a;
  }
  return m;
}

function vecMinusVec(a: number[], b: number[]): number[] {
  const out = new Array<number>(a.length);
  for (let i = 0; i < a.length; i++) out[i] = (a[i] ?? 0) - (b[i] ?? 0);
  return out;
}

function matVec(A: number[][], x: number[]): number[] {
  const m = A.length;
  const out = new Array<number>(m).fill(0);
  for (let i = 0; i < m; i++) {
    const row = A[i]!;
    let s = 0;
    for (let j = 0; j < row.length; j++) s += (row[j] ?? 0) * (x[j] ?? 0);
    out[i] = s;
  }
  return out;
}

function matTVec(A: number[][], y: number[]): number[] {
  // Returns A^T · y.  A is m×n; y is length m; result is length n.
  const m = A.length;
  if (m === 0) return [];
  const n = A[0]!.length;
  const out = new Array<number>(n).fill(0);
  for (let i = 0; i < m; i++) {
    const row = A[i]!;
    const yi = y[i] ?? 0;
    for (let j = 0; j < n; j++) out[j]! += (row[j] ?? 0) * yi;
  }
  return out;
}

function dot(a: number[], b: number[]): number {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += (a[i] ?? 0) * (b[i] ?? 0);
  return s;
}

function isFiniteNumber(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

function allFinite(xs: number[]): boolean {
  for (const x of xs) if (!Number.isFinite(x)) return false;
  return true;
}

// ─── Expected.json loader ─────────────────────────────────────────────────

function loadExpectedIndex(verifyTsDir: string): Map<string, Expected> {
  const path = resolve(verifyTsDir, "expected.json");
  const doc = JSON.parse(readFileSync(path, "utf8")) as { cases: { id: string; expected: Expected }[] };
  const map = new Map<string, Expected>();
  for (const c of doc.cases) map.set(c.id, c.expected);
  return map;
}

// ─── Shape predicates ─────────────────────────────────────────────────────

const KNOWN_STATUSES = new Set<SuccessRecord["status"]>([
  "optimal", "infeasible", "unbounded", "iter-cap", "numerical-breakdown",
]);
const KNOWN_REFUSAL_PREFIXES = ["cone-solve/", "lp-solve/"];

function isTagged(c: Candidate): c is TaggedRefusal {
  return typeof c === "object" && c !== null && (c as { kind?: string }).kind === "tagged";
}

function shapeCheck(candidate: Candidate): CheckResult {
  if (isTagged(candidate)) {
    if (typeof candidate.tag !== "string") return { pass: false, detail: `tagged envelope missing string tag` };
    const known = KNOWN_REFUSAL_PREFIXES.some((p) => candidate.tag.startsWith(p));
    if (!known) return { pass: false, detail: `tag ${JSON.stringify(candidate.tag)} outside known refusal prefixes` };
    if (typeof candidate.payload !== "object" || candidate.payload === null) return { pass: false, detail: `tagged envelope missing payload object` };
    return { pass: true, detail: `tagged refusal: ${candidate.tag}` };
  }
  const required: (keyof SuccessRecord)[] = ["status", "x", "dual", "slack", "iterations", "method", "condition_estimate", "warnings"];
  for (const k of required) {
    if (!(k in candidate)) return { pass: false, detail: `missing required field ${k}` };
  }
  if (!KNOWN_STATUSES.has(candidate.status)) return { pass: false, detail: `unknown status ${JSON.stringify(candidate.status)}` };
  if (!Array.isArray(candidate.x))    return { pass: false, detail: `x is not an array` };
  if (!Array.isArray(candidate.dual)) return { pass: false, detail: `dual is not an array` };
  if (!Array.isArray(candidate.slack)) return { pass: false, detail: `slack is not an array` };
  if (!Number.isInteger(candidate.iterations) || candidate.iterations < 0) return { pass: false, detail: `iterations not non-negative integer` };
  if (typeof candidate.method !== "string") return { pass: false, detail: `method not a string` };
  if (!Number.isFinite(candidate.condition_estimate)) return { pass: false, detail: `condition_estimate not finite` };
  if (candidate.status === "optimal") {
    if (!isFiniteNumber(candidate.objective))          return { pass: false, detail: `optimal status must carry finite objective` };
    if (!isFiniteNumber(candidate.achieved_precision)) return { pass: false, detail: `optimal status must carry finite achieved_precision` };
  }
  return { pass: true, detail: `success record (${candidate.status})` };
}

// ─── Per-status verifiers ─────────────────────────────────────────────────

const TOL_REL = 1e-8;
const TOL_ABS = 1e-8;

function nonNegIndices(input: ProblemInput): number[] {
  const out: number[] = [];
  for (const k of input.subjectTo.cones) {
    if (k.head === "NonNegCone" && Array.isArray(k.indices)) out.push(...k.indices);
  }
  return out;
}

function checkPrimalFeasibility(input: ProblemInput, x: number[]): CheckResult {
  const Ab = input.subjectTo.Ax_eq_b;
  if (!Ab || Ab.A.length === 0) return { pass: true, detail: `no equality constraints` };
  const r = infNorm(vecMinusVec(matVec(Ab.A, x), Ab.b));
  const bound = TOL_REL * Math.max(1, infNorm(Ab.b));
  return r <= bound
    ? { pass: true,  detail: `r_p = ${r.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `r_p = ${r.toExponential(3)} > ${bound.toExponential(3)}` };
}

function checkPrimalNonNeg(input: ProblemInput, x: number[]): CheckResult {
  const idx = nonNegIndices(input);
  if (idx.length === 0) return { pass: true, detail: `no NonNegCone slices` };
  let minVal = Infinity;
  for (const i of idx) if ((x[i] ?? 0) < minVal) minVal = x[i] ?? 0;
  return minVal >= -TOL_ABS
    ? { pass: true,  detail: `min(x|NonNeg) = ${minVal.toExponential(3)} ≥ -${TOL_ABS}` }
    : { pass: false, detail: `min(x|NonNeg) = ${minVal.toExponential(3)} < -${TOL_ABS}` };
}

function checkDualFeasibility(input: ProblemInput, y: number[], s: number[]): CheckResult {
  const c = input.minimize.c;
  const Ab = input.subjectTo.Ax_eq_b;
  const ATy = Ab ? matTVec(Ab.A, y) : new Array<number>(c.length).fill(0);
  // r_d_vec = A^T y + s − c
  const rd = new Array<number>(c.length);
  for (let j = 0; j < c.length; j++) rd[j] = (ATy[j] ?? 0) + (s[j] ?? 0) - (c[j] ?? 0);
  const r = infNorm(rd);
  const bound = TOL_REL * Math.max(1, infNorm(c));
  if (r > bound) return { pass: false, detail: `r_d = ${r.toExponential(3)} > ${bound.toExponential(3)}` };
  const idx = nonNegIndices(input);
  let minS = Infinity;
  for (const i of idx) if ((s[i] ?? 0) < minS) minS = s[i] ?? 0;
  if (idx.length > 0 && minS < -TOL_ABS) return { pass: false, detail: `min(s|NonNeg) = ${minS.toExponential(3)} < -${TOL_ABS}` };
  return { pass: true, detail: `r_d = ${r.toExponential(3)}; min(s) = ${minS === Infinity ? "n/a" : minS.toExponential(3)}` };
}

function checkComplementarySlackness(input: ProblemInput, x: number[], s: number[]): CheckResult {
  const xtS = Math.abs(dot(x, s));
  const cTx = Math.abs(dot(input.minimize.c, x));
  const bound = TOL_REL * Math.max(1, cTx);
  return xtS <= bound
    ? { pass: true,  detail: `|xᵀs| = ${xtS.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `|xᵀs| = ${xtS.toExponential(3)} > ${bound.toExponential(3)}` };
}

function checkOptimalityGap(input: ProblemInput, x: number[], y: number[]): CheckResult {
  const cTx = dot(input.minimize.c, x);
  const bTy = input.subjectTo.Ax_eq_b ? dot(input.subjectTo.Ax_eq_b.b, y) : 0;
  const gap = Math.abs(cTx - bTy);
  const bound = TOL_REL * Math.max(1, Math.abs(cTx));
  return gap <= bound
    ? { pass: true,  detail: `|cᵀx − bᵀy| = ${gap.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `|cᵀx − bᵀy| = ${gap.toExponential(3)} > ${bound.toExponential(3)}` };
}

function checkOracleAgreement(candObj: number, expected: Expected): CheckResult {
  if (!expected.consensus.agreement || expected.objective === undefined) {
    return { pass: true, detail: `oracle_disagreement_at_generation; dropped from gating` };
  }
  const ref  = expected.objective;
  const diff = Math.abs(candObj - ref);
  const bound = TOL_REL * Math.max(1, Math.abs(ref));
  return diff <= bound
    ? { pass: true,  detail: `|Δ| = ${diff.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `|Δ| = ${diff.toExponential(3)} > ${bound.toExponential(3)} (candidate=${candObj}, oracle=${ref})` };
}

function checkSelfReportedPrecision(claimed: number, recomputed: number): CheckResult {
  return claimed >= recomputed - 1e-15   // 1e-15 absorbs final-bit rounding
    ? { pass: true,  detail: `claimed ${claimed.toExponential(3)} ≥ recomputed ${recomputed.toExponential(3)}` }
    : { pass: false, detail: `claimed ${claimed.toExponential(3)} < recomputed ${recomputed.toExponential(3)} (under-claimed!)` };
}

// ─── Infeasibility / unboundedness certificate checks ─────────────────────
//
// A Farkas infeasibility certificate is y with A^T y ≤ 0 and b^T y > 0.
// Sign is arbitrary: ±y both work if either does.  We accept whichever sign
// produces a valid certificate; vendor convention (Gurobi's vs Mosek's) is
// not the candidate's problem.
//
// An unboundedness certificate is d with A d = 0, d ≥ 0, c^T d < 0.  No
// sign ambiguity here — d is a direction in the primal feasible cone.

function isFarkasCertificate(input: ProblemInput, y: number[]): boolean {
  const Ab = input.subjectTo.Ax_eq_b;
  if (!Ab) return false;
  const ATy   = matTVec(Ab.A, y);
  const maxAT = Math.max(0, ...ATy);
  const bTy   = dot(Ab.b, y);
  // Accept the certificate with a 1e-8 absolute envelope.  Scale-free
  // check on bᵀy > 0 because we don't know what scale the cert was
  // produced at.  A useful Farkas cert satisfies bᵀy ≫ ||y|| · tol;
  // we check bᵀy > 1e-8 to reject trivially-zero certificates.
  return maxAT <= TOL_ABS && bTy > TOL_ABS;
}

function checkInfeasibilityCertificate(input: ProblemInput, candidate: SuccessRecord): CheckResult {
  if (candidate.status !== "infeasible") return { pass: true, detail: `n/a (status=${candidate.status})` };
  const y = candidate.dual;
  if (isFarkasCertificate(input, y))               return { pass: true, detail: `Farkas cert verified (Aᵀy ≤ 1e-8, bᵀy > 1e-8)` };
  if (isFarkasCertificate(input, y.map((v) => -v))) return { pass: true, detail: `Farkas cert verified after sign flip (vendor convention)` };
  return { pass: false, detail: `no Farkas certificate in either sign` };
}

function checkUnboundednessCertificate(input: ProblemInput, candidate: SuccessRecord): CheckResult {
  if (candidate.status !== "unbounded") return { pass: true, detail: `n/a (status=${candidate.status})` };
  const d = candidate.x;
  const Ab = input.subjectTo.Ax_eq_b;
  const Ad = Ab ? matVec(Ab.A, d) : new Array<number>(0);
  const rAd = infNorm(Ad);
  const minD = Math.min(0, ...d);
  const cTd = dot(input.minimize.c, d);
  // ||A d|| ≤ 1e-8 · max(1, ||d||) absorbs scale; d ≥ -1e-8 absorbs round-off.
  const adBound = TOL_REL * Math.max(1, infNorm(d));
  if (rAd > adBound) return { pass: false, detail: `||Ad|| = ${rAd.toExponential(3)} > ${adBound.toExponential(3)}` };
  if (minD < -TOL_ABS) return { pass: false, detail: `min(d) = ${minD.toExponential(3)} < -${TOL_ABS} (ray not in non-neg cone)` };
  if (cTd >= 0) return { pass: false, detail: `cᵀd = ${cTd.toExponential(3)} ≥ 0 (not a descent direction)` };
  return { pass: true, detail: `Ad ≈ 0 (${rAd.toExponential(3)}), d ≥ 0, cᵀd = ${cTd.toExponential(3)} < 0` };
}

// ─── Top-level dispatch ───────────────────────────────────────────────────

function statusConsistency(candidate: Candidate, expected: Expected): CheckResult {
  if (isTagged(candidate)) {
    // Tagged refusal is admissible only on cases the consensus deems
    // pathological — never on a known-optimal problem.
    if (expected.status === "optimal") return { pass: false, detail: `tagged refusal on a known-optimal case` };
    return { pass: true, detail: `tagged refusal acceptable on expected.status=${expected.status}` };
  }
  return candidate.status === expected.status
    ? { pass: true,  detail: `status=${candidate.status} matches consensus` }
    : { pass: false, detail: `status=${candidate.status} vs consensus=${expected.status}` };
}

function finiteEntries(candidate: Candidate): CheckResult {
  if (isTagged(candidate)) return { pass: true, detail: `n/a (tagged refusal)` };
  if (!allFinite(candidate.x))    return { pass: false, detail: `x has non-finite entry` };
  if (!allFinite(candidate.dual)) return { pass: false, detail: `dual has non-finite entry` };
  if (!allFinite(candidate.slack)) return { pass: false, detail: `slack has non-finite entry` };
  return { pass: true, detail: `all numeric fields finite` };
}

function verifyCase(input: ProblemInput, candidate: Candidate, expected: Expected): Verdict {
  const checks: Record<string, CheckResult> = {};

  checks.shape              = shapeCheck(candidate);
  checks.finite_entries     = finiteEntries(candidate);
  checks.status_consistency = statusConsistency(candidate, expected);

  // For a tagged refusal we stop here — the KKT-residual checks are
  // not applicable.  Mark them N/A so DuckDB still records a row each.
  if (isTagged(candidate)) {
    for (const name of [
      "primal_feasibility", "primal_nonneg", "dual_feasibility",
      "complementary_slackness", "optimality_gap", "oracle_agreement",
      "self_reported_precision",
      "infeasibility_certificate", "unboundedness_certificate",
    ]) {
      checks[name] = { pass: true, detail: `n/a (tagged refusal)` };
    }
    const allPass = Object.values(checks).every((c) => c.pass);
    return { pass: allPass, reason: allPass ? "tagged refusal accepted" : "tagged refusal not accepted", checks };
  }

  if (candidate.status === "optimal") {
    const x = candidate.x;
    const y = candidate.dual;
    const s = candidate.slack;
    checks.primal_feasibility       = checkPrimalFeasibility(input, x);
    checks.primal_nonneg            = checkPrimalNonNeg(input, x);
    checks.dual_feasibility         = checkDualFeasibility(input, y, s);
    checks.complementary_slackness  = checkComplementarySlackness(input, x, s);
    checks.optimality_gap           = checkOptimalityGap(input, x, y);
    checks.oracle_agreement         = checkOracleAgreement(candidate.objective!, expected);
    // Recompute residuals for the self-reported-precision check.
    const Ab = input.subjectTo.Ax_eq_b;
    const rp = Ab ? infNorm(vecMinusVec(matVec(Ab.A, x), Ab.b)) : 0;
    const ATy = Ab ? matTVec(Ab.A, y) : new Array<number>(input.minimize.c.length).fill(0);
    const rdVec = ATy.map((v, j) => v + (s[j] ?? 0) - (input.minimize.c[j] ?? 0));
    const rd = infNorm(rdVec);
    const rc = Math.abs(dot(x, s));
    checks.self_reported_precision  = checkSelfReportedPrecision(candidate.achieved_precision!, Math.max(rp, rd, rc));
    checks.infeasibility_certificate = { pass: true, detail: `n/a (status=optimal)` };
    checks.unboundedness_certificate = { pass: true, detail: `n/a (status=optimal)` };
  } else if (candidate.status === "infeasible") {
    checks.primal_feasibility       = { pass: true, detail: `n/a (status=infeasible)` };
    checks.primal_nonneg            = { pass: true, detail: `n/a (status=infeasible)` };
    checks.dual_feasibility         = { pass: true, detail: `n/a (status=infeasible)` };
    checks.complementary_slackness  = { pass: true, detail: `n/a (status=infeasible)` };
    checks.optimality_gap           = { pass: true, detail: `n/a (status=infeasible)` };
    checks.oracle_agreement         = { pass: true, detail: `n/a (status=infeasible)` };
    checks.self_reported_precision  = { pass: true, detail: `n/a (status=infeasible)` };
    checks.infeasibility_certificate = checkInfeasibilityCertificate(input, candidate);
    checks.unboundedness_certificate = { pass: true, detail: `n/a (status=infeasible)` };
  } else if (candidate.status === "unbounded") {
    checks.primal_feasibility       = { pass: true, detail: `n/a (status=unbounded)` };
    checks.primal_nonneg            = { pass: true, detail: `n/a (status=unbounded)` };
    checks.dual_feasibility         = { pass: true, detail: `n/a (status=unbounded)` };
    checks.complementary_slackness  = { pass: true, detail: `n/a (status=unbounded)` };
    checks.optimality_gap           = { pass: true, detail: `n/a (status=unbounded)` };
    checks.oracle_agreement         = { pass: true, detail: `n/a (status=unbounded)` };
    checks.self_reported_precision  = { pass: true, detail: `n/a (status=unbounded)` };
    checks.infeasibility_certificate = { pass: true, detail: `n/a (status=unbounded)` };
    checks.unboundedness_certificate = checkUnboundednessCertificate(input, candidate);
  } else {
    // iter-cap / numerical-breakdown — no certificates expected.
    for (const name of [
      "primal_feasibility", "primal_nonneg", "dual_feasibility",
      "complementary_slackness", "optimality_gap", "oracle_agreement",
      "self_reported_precision",
      "infeasibility_certificate", "unboundedness_certificate",
    ]) {
      checks[name] = { pass: true, detail: `n/a (status=${candidate.status})` };
    }
  }

  const failed = Object.entries(checks).filter(([, c]) => !c.pass).map(([k]) => k);
  const allPass = failed.length === 0;
  return {
    pass: allPass,
    reason: allPass ? "all checks pass" : `failed: ${failed.join(", ")}`,
    checks,
  };
}

// ─── Driver (exported for cross-suite reuse) ──────────────────────────────
//
// The driver is exported so lp-small/golden/verify.ts can reuse the
// verification logic with a different expected.json path.  Gated by
// import.meta.main in the same idiom as the workbench's tool modules
// (ADR-0010) — a single source file is both a script and a library.

export async function runVerifier(expectedPath?: string): Promise<void> {
  const stdin = await new Response(Bun.stdin.stream()).text();
  const payload = JSON.parse(stdin) as { input: ProblemInput; candidate: Candidate; id: string };
  const expDir = expectedPath ? resolve(expectedPath, "..") : import.meta.dir;
  const expectedMap = loadExpectedIndex(expDir);
  const expected = expectedMap.get(payload.id);
  if (!expected) {
    const verdict: Verdict = {
      pass: false,
      reason: `no expected record for id=${payload.id}`,
      checks: { _expected_lookup: { pass: false, detail: `id missing from ${expDir}/expected.json` } },
    };
    process.stdout.write(JSON.stringify(verdict));
    return;
  }
  const verdict = verifyCase(payload.input, payload.candidate, expected);
  process.stdout.write(JSON.stringify(verdict));
}

if (import.meta.main) await runVerifier();
