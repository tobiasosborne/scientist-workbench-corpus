// =============================================================================
// sdp-sdplib verifier — invariant-based, dual-witness oracle backed.
// =============================================================================
//
// stdin per case (raw JSON, ADR-0030 §C wire with PSDCone):
//   {
//     input:     { minimize: { c: number[] },
//                  subjectTo: { Ax_eq_b?: { A: number[][], b: number[] },
//                               cones:   { head: "PSDCone", size: int,
//                                          indices: number[] }[] },
//                  precision: number, max_iter?: number },
//     candidate: <success record OR tagged refusal envelope>,
//     id:        string
//   }
//
// stdout per case:
//   { pass: boolean, reason: string,
//     checks: { <name>: { pass: boolean, detail: string } } }
//
// Exit 0 always (failed check is data, not a verifier crash).
//
// SDP-specific invariants (in addition to the LP-suite shape/status):
//
//   primal_feasibility       ‖A·x − b‖_∞ ≤ 1e-7 · max(1, ‖b‖_∞)
//                            (looser than LP's 1e-8 — SDP IPMs reach
//                             1e-7 to 1e-9 in practice)
//   primal_psd               every PSDCone block X_b ⪰ 0
//   dual_feasibility         ‖Aᵀ·y + s − c‖_∞ ≤ 1e-7 · max(1, ‖c‖_∞)
//   dual_psd                 every PSDCone block S_b ⪰ 0
//   complementary_slackness  |xᵀ·s| ≤ 1e-7 · max(1, |cᵀ·x|)
//   optimality_gap           |cᵀ·x − bᵀ·y| ≤ 1e-7 · max(1, |cᵀ·x|)
//   oracle_agreement         |obj − consensus.objective| ≤ 1e-5 · max(1, |obj|)
//                            (SDP precision floor; matches generate.py
//                             agreement_tol)
//
// PSD-ness is tested by attempted Cholesky factorisation with small
// jitter: M is "PSD within tol" iff `chol(M + tol·I)` succeeds. The
// jitter absorbs round-off from svec ↔ matrix conversion and the
// IPM's interior-point convergence floor.
//
// svec convention (ADR-0030 §C, Mosek format):
//   svec(M)[k] for k = 0 .. n*(n+1)/2 - 1 in row-major upper-tri order
//   svec(M)[k(i,i)] = M[i,i]
//   svec(M)[k(i,j)] = sqrt(2) * M[i,j]   (i < j)
//
// To recover X_b from x[indices_b]: divide off-diagonals by sqrt(2).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Types ────────────────────────────────────────────────────────────────

interface Cone {
  head: string;
  size?: number;
  indices: number[];
}

interface ProblemInput {
  minimize: { c: number[] };
  subjectTo: {
    Ax_eq_b?: { A: number[][]; b: number[] };
    cones: Cone[];
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
  objective?: number;
  achieved_precision?: number;
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
    agreement_tol?: number;
    objective_mosek?: number;
    objective_copt?: number;
    rel_diff?: number;
    reason?: string;
    rationale?: string;
    mosek_error?: string;
    copt_error?: string;
  };
}

interface CheckResult { pass: boolean; detail: string }
interface Verdict     { pass: boolean; reason: string; checks: Record<string, CheckResult> }

// ─── Tolerances ───────────────────────────────────────────────────────────
//
// SDP convergence is one to two decades looser than LP. We pick:
//   TOL_KKT      1e-7 — the verifier's "sound" gate for primal/dual
//                       feasibility, complementary slackness, optimality
//                       gap. Tighter than the IPM's worst-case 1e-6
//                       drift; loose enough to absorb svec round-off.
//   TOL_PSD      1e-7 — eigenvalue floor for "PSD within numerical
//                       tolerance"; matches the IPM's interior floor.
//   TOL_ORACLE   1e-5 — relative tolerance for oracle objective match.
//                       Matches the SDP precision floor documented in
//                       generate.py's build_consensus comment.
//   TOL_FINITE   1e-15 — absolute backstop for the self-reported
//                       precision check (catches near-zero residuals).

const TOL_KKT    = 1e-7;
const TOL_PSD    = 1e-7;
const TOL_ORACLE = 1e-5;

// ─── Numeric helpers ──────────────────────────────────────────────────────

const SQRT2 = Math.SQRT2;

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
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) s += (a[i] ?? 0) * (b[i] ?? 0);
  return s;
}

function isFiniteNumber(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

function allFinite(xs: number[]): boolean {
  for (const x of xs) if (!Number.isFinite(x)) return false;
  return true;
}

// ─── svec ↔ symmetric matrix ──────────────────────────────────────────────
//
// svecToIJ inverts the row-major upper-tri ordering used throughout the
// cone-solver wire. unsvecIntoFull recovers the symmetric matrix from
// `vec[indices]`, dividing off-diagonals by sqrt(2).

function svecToIJ(k: number, n: number): { i: number; j: number } {
  let i = 0;
  let rem = k;
  while (rem >= n - i) {
    rem -= n - i;
    i += 1;
  }
  return { i, j: i + rem };
}

function unsvecIntoFull(vec: number[], indices: number[], size: number): number[][] {
  const M: number[][] = [];
  for (let i = 0; i < size; i++) M.push(new Array<number>(size).fill(0));
  for (let k = 0; k < indices.length; k++) {
    const { i, j } = svecToIJ(k, size);
    const v = vec[indices[k]!] ?? 0;
    if (i === j) {
      M[i]![i] = v;
    } else {
      const half = v / SQRT2;
      M[i]![j] = half;
      M[j]![i] = half;
    }
  }
  return M;
}

// ─── Cholesky-based PSD test ──────────────────────────────────────────────
//
// `M ⪰ 0` iff Cholesky factorisation of `M + tol · I` succeeds (no
// negative pivot). The jitter absorbs the IPM's interior floor and the
// svec round-off, so a "barely PSD" matrix still passes. Returns the
// achieved minimum-pivot magnitude as detail, in case the caller wants
// to log it.

function isPsd(M: number[][], tol: number): { ok: boolean; minPivot: number } {
  const n = M.length;
  // Copy, add tol to diagonal.
  const L: number[][] = [];
  for (let i = 0; i < n; i++) {
    L.push(M[i]!.slice());
    L[i]![i] = (L[i]![i] ?? 0) + tol;
  }
  let minPivot = Infinity;
  for (let j = 0; j < n; j++) {
    let s = L[j]![j]!;
    for (let k = 0; k < j; k++) s -= L[j]![k]! * L[j]![k]!;
    if (s < minPivot) minPivot = s;
    if (s <= 0) {
      return { ok: false, minPivot };
    }
    const Ljj = Math.sqrt(s);
    L[j]![j] = Ljj;
    for (let i = j + 1; i < n; i++) {
      let t = L[i]![j]!;
      for (let k = 0; k < j; k++) t -= L[i]![k]! * L[j]![k]!;
      L[i]![j] = t / Ljj;
    }
  }
  return { ok: true, minPivot };
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
const KNOWN_REFUSAL_PREFIXES = ["sdp-solve/", "cone-solve/"];

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
  if (!Array.isArray(candidate.x))     return { pass: false, detail: `x is not an array` };
  if (!Array.isArray(candidate.dual))  return { pass: false, detail: `dual is not an array` };
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

function checkPrimalFeasibility(input: ProblemInput, x: number[]): CheckResult {
  const Ab = input.subjectTo.Ax_eq_b;
  if (!Ab || Ab.A.length === 0) return { pass: true, detail: `no equality constraints` };
  const r = infNorm(vecMinusVec(matVec(Ab.A, x), Ab.b));
  const bound = TOL_KKT * Math.max(1, infNorm(Ab.b));
  return r <= bound
    ? { pass: true,  detail: `r_p = ${r.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `r_p = ${r.toExponential(3)} > ${bound.toExponential(3)}` };
}

function checkPrimalPsd(input: ProblemInput, x: number[]): CheckResult {
  let worstPivot = Infinity;
  let worstBlock = -1;
  for (let b = 0; b < input.subjectTo.cones.length; b++) {
    const cone = input.subjectTo.cones[b]!;
    if (cone.head !== "PSDCone") continue;
    if (cone.size === undefined) continue;
    const M = unsvecIntoFull(x, cone.indices, cone.size);
    const { ok, minPivot } = isPsd(M, TOL_PSD);
    if (!ok) {
      return {
        pass: false,
        detail: `block ${b} (size ${cone.size}) not PSD: Cholesky failed at pivot ${minPivot.toExponential(3)}`,
      };
    }
    if (minPivot < worstPivot) {
      worstPivot = minPivot;
      worstBlock = b;
    }
  }
  return {
    pass: true,
    detail: `all PSD blocks ⪰ 0; worst min-pivot block ${worstBlock} = ${worstPivot.toExponential(3)}`,
  };
}

function checkDualFeasibility(input: ProblemInput, y: number[], s: number[]): CheckResult {
  const c = input.minimize.c;
  const Ab = input.subjectTo.Ax_eq_b;
  const ATy = Ab ? matTVec(Ab.A, y) : new Array<number>(c.length).fill(0);
  const rd = new Array<number>(c.length);
  for (let j = 0; j < c.length; j++) rd[j] = (ATy[j] ?? 0) + (s[j] ?? 0) - (c[j] ?? 0);
  const r = infNorm(rd);
  const bound = TOL_KKT * Math.max(1, infNorm(c));
  return r <= bound
    ? { pass: true,  detail: `r_d = ${r.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `r_d = ${r.toExponential(3)} > ${bound.toExponential(3)}` };
}

function checkDualPsd(input: ProblemInput, slack: number[]): CheckResult {
  let worstPivot = Infinity;
  let worstBlock = -1;
  for (let b = 0; b < input.subjectTo.cones.length; b++) {
    const cone = input.subjectTo.cones[b]!;
    if (cone.head !== "PSDCone") continue;
    if (cone.size === undefined) continue;
    const S = unsvecIntoFull(slack, cone.indices, cone.size);
    const { ok, minPivot } = isPsd(S, TOL_PSD);
    if (!ok) {
      return {
        pass: false,
        detail: `dual block ${b} (size ${cone.size}) not PSD: Cholesky failed at pivot ${minPivot.toExponential(3)}`,
      };
    }
    if (minPivot < worstPivot) {
      worstPivot = minPivot;
      worstBlock = b;
    }
  }
  return {
    pass: true,
    detail: `all dual blocks ⪰ 0; worst min-pivot block ${worstBlock} = ${worstPivot.toExponential(3)}`,
  };
}

function checkComplementarySlackness(input: ProblemInput, x: number[], s: number[]): CheckResult {
  // Wire-vector form already accounts for the sqrt(2) scaling: the
  // Frobenius inner product <X, S>_F = svec(X)^T svec(S) by design.
  const xtS = Math.abs(dot(x, s));
  const cTx = Math.abs(dot(input.minimize.c, x));
  const bound = TOL_KKT * Math.max(1, cTx);
  return xtS <= bound
    ? { pass: true,  detail: `|<X, S>| = ${xtS.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `|<X, S>| = ${xtS.toExponential(3)} > ${bound.toExponential(3)}` };
}

function checkOptimalityGap(input: ProblemInput, x: number[], y: number[]): CheckResult {
  const cTx = dot(input.minimize.c, x);
  const bTy = input.subjectTo.Ax_eq_b ? dot(input.subjectTo.Ax_eq_b.b, y) : 0;
  const gap = Math.abs(cTx - bTy);
  const bound = TOL_KKT * Math.max(1, Math.abs(cTx));
  return gap <= bound
    ? { pass: true,  detail: `|cᵀx − bᵀy| = ${gap.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `|cᵀx − bᵀy| = ${gap.toExponential(3)} > ${bound.toExponential(3)}` };
}

function checkOracleAgreement(candObj: number, expected: Expected): CheckResult {
  if (!expected.consensus.agreement || expected.objective === undefined) {
    return { pass: true, detail: `oracle_disagreement_at_generation; case dropped from gating` };
  }
  const ref  = expected.objective;
  const diff = Math.abs(candObj - ref);
  const bound = TOL_ORACLE * Math.max(1, Math.abs(ref));
  return diff <= bound
    ? { pass: true,  detail: `|Δ| = ${diff.toExponential(3)} ≤ ${bound.toExponential(3)}` }
    : { pass: false, detail: `|Δ| = ${diff.toExponential(3)} > ${bound.toExponential(3)} (candidate=${candObj}, oracle=${ref})` };
}

function checkSelfReportedPrecision(claimed: number, recomputed: number): CheckResult {
  // Honest-scope check: candidate must not under-claim its precision
  // by more than 2× (matches LP suite). The 1e-15 absolute tail catches
  // near-zero residuals on trivial cases.
  const acceptable = recomputed / 2;
  return claimed >= acceptable - 1e-15
    ? { pass: true,  detail: `claimed ${claimed.toExponential(3)} ≥ ${acceptable.toExponential(3)} (recomputed ${recomputed.toExponential(3)})` }
    : { pass: false, detail: `claimed ${claimed.toExponential(3)} < ${acceptable.toExponential(3)} (recomputed ${recomputed.toExponential(3)})` };
}

// ─── Top-level dispatch ───────────────────────────────────────────────────

function statusConsistency(candidate: Candidate, expected: Expected): CheckResult {
  if (isTagged(candidate)) {
    if (expected.status === "optimal") return { pass: false, detail: `tagged refusal on a known-optimal case` };
    return { pass: true, detail: `tagged refusal acceptable on expected.status=${expected.status}` };
  }
  return candidate.status === expected.status
    ? { pass: true,  detail: `status=${candidate.status} matches consensus` }
    : { pass: false, detail: `status=${candidate.status} vs consensus=${expected.status}` };
}

function finiteEntries(candidate: Candidate): CheckResult {
  if (isTagged(candidate)) return { pass: true, detail: `n/a (tagged refusal)` };
  if (!allFinite(candidate.x))     return { pass: false, detail: `x has non-finite entry` };
  if (!allFinite(candidate.dual))  return { pass: false, detail: `dual has non-finite entry` };
  if (!allFinite(candidate.slack)) return { pass: false, detail: `slack has non-finite entry` };
  return { pass: true, detail: `all numeric fields finite` };
}

function verifyCase(input: ProblemInput, candidate: Candidate, expected: Expected): Verdict {
  const checks: Record<string, CheckResult> = {};

  checks.shape              = shapeCheck(candidate);
  checks.finite_entries     = finiteEntries(candidate);
  checks.status_consistency = statusConsistency(candidate, expected);

  if (isTagged(candidate)) {
    for (const name of [
      "primal_feasibility", "primal_psd", "dual_feasibility", "dual_psd",
      "complementary_slackness", "optimality_gap", "oracle_agreement",
      "self_reported_precision",
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
    checks.primal_psd               = checkPrimalPsd(input, x);
    checks.dual_feasibility         = checkDualFeasibility(input, y, s);
    checks.dual_psd                 = checkDualPsd(input, s);
    checks.complementary_slackness  = checkComplementarySlackness(input, x, s);
    checks.optimality_gap           = checkOptimalityGap(input, x, y);
    checks.oracle_agreement         = checkOracleAgreement(candidate.objective!, expected);
    // Recompute residuals for self-reported precision.
    const Ab = input.subjectTo.Ax_eq_b;
    const rp = Ab ? infNorm(vecMinusVec(matVec(Ab.A, x), Ab.b)) : 0;
    const ATy = Ab ? matTVec(Ab.A, y) : new Array<number>(input.minimize.c.length).fill(0);
    const rdVec = ATy.map((v, j) => v + (s[j] ?? 0) - (input.minimize.c[j] ?? 0));
    const rd = infNorm(rdVec);
    const rc = Math.abs(dot(x, s));
    checks.self_reported_precision  = checkSelfReportedPrecision(candidate.achieved_precision!, Math.max(rp, rd, rc));
  } else {
    // Non-optimal terminations — KKT/oracle checks N/A.
    for (const name of [
      "primal_feasibility", "primal_psd", "dual_feasibility", "dual_psd",
      "complementary_slackness", "optimality_gap", "oracle_agreement",
      "self_reported_precision",
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

// ─── Driver ───────────────────────────────────────────────────────────────

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
