// =============================================================================
// linalg-svd verifier — invariant-based, language-neutral.
// =============================================================================
//
// stdin:
//   { input:     { A: number[][], mode?: "reduced" | "complete" },
//     candidate: { U, S, Vt, mode, reconstruction_error,
//                  orthogonality_error_U, orthogonality_error_Vt,
//                  condition_number, rank_estimate, method, warnings },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// Runs 8 independent checks per case (see verifier_protocol.md):
//   1. shape                       — U, S, Vt dimensions match (m, n, mode)
//   2. finite_entries              — no NaN, no ±Inf in factors or scalars
//   3. S_nonneg_descending         — singular values ≥ 0 and non-increasing
//   4. U_orthonormal               — ||UᵀU − I|| within tol_orth
//   5. Vt_orthonormal              — ||Vt·Vtᵀ − I|| within tol_orth
//   6. factorisation_residual      — ||U·diag(S)·Vt − A||_F within tol_recon
//   7. self_reported_residual      — candidate's recon claim is honest
//   8. self_reported_orthogonality — candidate's orth claims (U + Vt) honest
//
// TS port of the Python verifier from scientist-workbench/bench/linalg-svd.
// Tolerances match byte-for-byte (verify.py values preserved per ADR-0028 §4
// — do not tighten or loosen during migration).
//
// The matrix operations (transpose, matmul, Frobenius norm) are inlined
// because the tournament-protocol contract is "single executable, JSON in,
// JSON out" — no external linalg dependency.

const EPS                 = 2.220446049250313e-16;
const SAFETY              = 100.0;
const SELF_REPORT_REL_TOL = 1e-6;

// ─── small linalg helpers ────────────────────────────────────────────────────

function transpose(A: number[][]): number[][] {
  const m = A.length;
  const n = m === 0 ? 0 : (A[0]?.length ?? 0);
  const out: number[][] = Array.from({ length: n }, () => new Array<number>(m).fill(0));
  for (let i = 0; i < m; i++) {
    const row = A[i]!;
    for (let j = 0; j < n; j++) out[j]![i] = row[j]!;
  }
  return out;
}

function matmul(A: number[][], B: number[][]): number[][] {
  const m = A.length;
  const k = m === 0 ? 0 : (A[0]?.length ?? 0);
  const n = B.length === 0 ? 0 : (B[0]?.length ?? 0);
  const out: number[][] = Array.from({ length: m }, () => new Array<number>(n).fill(0));
  for (let i = 0; i < m; i++) {
    const arow = A[i]!;
    const orow = out[i]!;
    for (let p = 0; p < k; p++) {
      const aip = arow[p]!;
      if (aip === 0) continue;
      const brow = B[p]!;
      for (let j = 0; j < n; j++) orow[j]! += aip * brow[j]!;
    }
  }
  return out;
}

function frobNorm(A: number[][]): number {
  let s = 0;
  for (const row of A) for (const v of row) s += v * v;
  return Math.sqrt(s);
}

function matSub(A: number[][], B: number[][]): number[][] {
  return Array.from({ length: A.length }, (_, i) =>
    A[i]!.map((v, j) => v - B[i]![j]!),
  );
}

// Build the rectangular Sigma matrix: q_U × q_Vt, with S along the diagonal.
function buildSigma(S: number[], qU: number, qVt: number): number[][] {
  const out: number[][] = Array.from({ length: qU }, () => new Array<number>(qVt).fill(0));
  for (let i = 0; i < S.length; i++) out[i]![i] = S[i]!;
  return out;
}

// UᵀU − I_q (gram matrix minus identity, for orthonormality check).
function gramMinusEye(M: number[][]): number[][] {
  const Mt = transpose(M);
  const gram = matmul(Mt, M);
  const q = gram.length;
  for (let i = 0; i < q; i++) gram[i]![i]! -= 1;
  return gram;
}

// Vt · Vtᵀ − I_q (row-gram matrix minus identity, for Vt orthonormality).
function rowGramMinusEye(Vt: number[][]): number[][] {
  const q = Vt.length;
  const VtT = transpose(Vt);
  const gram = matmul(Vt, VtT);
  for (let i = 0; i < q; i++) gram[i]![i]! -= 1;
  return gram;
}

function shapeOf(mat: unknown): [number, number] | null {
  if (!Array.isArray(mat)) return null;
  if (mat.length === 0) return [0, 0];
  for (const row of mat) if (!Array.isArray(row)) return null;
  const ncols = (mat[0] as number[]).length;
  for (const row of mat as number[][]) {
    if (row.length !== ncols) return null;
    for (const v of row) if (typeof v !== "number") return null;
  }
  return [mat.length, ncols];
}

function vecLen(v: unknown): number | null {
  if (!Array.isArray(v)) return null;
  for (const x of v) if (typeof x !== "number") return null;
  return v.length;
}

function allFiniteMat(A: unknown): boolean {
  if (!Array.isArray(A)) return false;
  for (const row of A) {
    if (!Array.isArray(row)) return false;
    for (const v of row) if (typeof v !== "number" || !Number.isFinite(v)) return false;
  }
  return true;
}

function allFiniteVec(v: unknown): boolean {
  if (!Array.isArray(v)) return false;
  for (const x of v) if (typeof x !== "number" || !Number.isFinite(x)) return false;
  return true;
}

function fmt(x: number): string {
  if (!Number.isFinite(x)) return String(x);
  return x.toExponential(3);
}

// ─── per-check implementations ───────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

function checkShape(
  A: number[][],
  cand: Record<string, unknown>,
  mode: string,
): Check {
  const m = A.length;
  const n = m === 0 ? 0 : (A[0]?.length ?? 0);
  const k = Math.min(m, n);

  // Expected shapes differ by mode.
  const expectedU: [number, number] = mode === "reduced" ? [m, k] : [m, m];
  const expectedVt: [number, number] = mode === "reduced" ? [k, n] : [n, n];
  const expectedSLen = k;

  const required = [
    "U", "S", "Vt", "mode",
    "reconstruction_error", "orthogonality_error_U",
    "orthogonality_error_Vt", "condition_number",
    "rank_estimate", "method", "warnings",
  ];
  const missing = required.filter((k) => !(k in cand));
  if (missing.length > 0) {
    return { pass: false, detail: `missing fields: [${missing.sort().join(",")}]` };
  }

  if (cand["mode"] !== mode) {
    return { pass: false, detail: `mode mismatch: candidate=${JSON.stringify(cand["mode"])}, expected=${JSON.stringify(mode)}` };
  }

  if (typeof cand["method"] !== "string") {
    return { pass: false, detail: "method must be a string" };
  }

  const warnings = cand["warnings"];
  if (!Array.isArray(warnings) || !warnings.every((s) => typeof s === "string")) {
    return { pass: false, detail: "warnings must be list[str]" };
  }

  // rank_estimate: non-negative integer (not boolean).
  const re = cand["rank_estimate"];
  if (typeof re !== "number" || typeof re === "boolean" || !Number.isInteger(re) || re < 0) {
    return { pass: false, detail: "rank_estimate must be a non-negative int" };
  }

  const sU = shapeOf(cand["U"]);
  if (!sU || sU[0] !== expectedU[0] || sU[1] !== expectedU[1]) {
    return { pass: false, detail: `U shape ${JSON.stringify(sU)}, expected ${JSON.stringify(expectedU)} for mode=${JSON.stringify(mode)}` };
  }

  const sLen = vecLen(cand["S"]);
  if (sLen !== expectedSLen) {
    return { pass: false, detail: `S length ${sLen}, expected ${expectedSLen}` };
  }

  const sVt = shapeOf(cand["Vt"]);
  if (!sVt || sVt[0] !== expectedVt[0] || sVt[1] !== expectedVt[1]) {
    return { pass: false, detail: `Vt shape ${JSON.stringify(sVt)}, expected ${JSON.stringify(expectedVt)} for mode=${JSON.stringify(mode)}` };
  }

  return { pass: true, detail: `U ${JSON.stringify(sU)}, S ${sLen}, Vt ${JSON.stringify(sVt)}, mode ${JSON.stringify(mode)}` };
}

function checkFinite(cand: Record<string, unknown>): Check {
  for (const field of ["U", "Vt"] as const) {
    if (!allFiniteMat(cand[field])) {
      return { pass: false, detail: `non-finite entry in ${field}` };
    }
  }
  if (!allFiniteVec(cand["S"])) {
    return { pass: false, detail: "non-finite entry in S" };
  }
  for (const field of ["reconstruction_error", "orthogonality_error_U",
                        "orthogonality_error_Vt", "condition_number"] as const) {
    const v = cand[field];
    if (typeof v !== "number" || !Number.isFinite(v)) {
      return { pass: false, detail: `non-finite or non-numeric ${field}` };
    }
  }
  return { pass: true, detail: "all entries finite" };
}

function checkSNonnegDescending(S: number[]): Check {
  if (S.length === 0) return { pass: true, detail: "empty S" };
  const sMax = S[0]!;
  const tolStruct = SAFETY * EPS * Math.max(sMax, 1.0);

  for (let i = 0; i < S.length; i++) {
    if (S[i]! < -tolStruct) {
      return {
        pass: false,
        detail: `S[${i}]=${fmt(S[i]!)} < -tol=${fmt(-tolStruct)} (negative singular value)`,
      };
    }
  }
  for (let i = 0; i < S.length - 1; i++) {
    if (S[i]! + tolStruct < S[i + 1]!) {
      return {
        pass: false,
        detail: `S[${i}]=${fmt(S[i]!)} < S[${i + 1}]=${fmt(S[i + 1]!)} (non-monotone, gap ${fmt(S[i + 1]! - S[i]!)} > tol=${fmt(tolStruct)})`,
      };
    }
  }
  return {
    pass: true,
    detail: `S[0]=${fmt(S[0]!)}, S[k-1]=${fmt(S[S.length - 1]!)}, monotone within tol=${fmt(tolStruct)}`,
  };
}

function checkUOrthonormal(A: number[][], U: number[][]): Check {
  const m = A.length;
  const q = U.length === 0 ? 0 : (U[0]?.length ?? 0);
  const tol = SAFETY * EPS * m * Math.sqrt(q);
  const err = frobNorm(gramMinusEye(U));
  if (err > tol) {
    return { pass: false, detail: `||UᵀU − I||_F = ${fmt(err)} > tol = ${fmt(tol)}` };
  }
  return { pass: true, detail: `||UᵀU − I||_F = ${fmt(err)} ≤ tol = ${fmt(tol)}` };
}

function checkVtOrthonormal(A: number[][], Vt: number[][]): Check {
  // Uses m (not n) for the tolerance, matching verify.py's check_Vt_orthonormal.
  const m = A.length;
  const q = Vt.length;
  const tol = SAFETY * EPS * m * Math.sqrt(q);
  const err = frobNorm(rowGramMinusEye(Vt));
  if (err > tol) {
    return { pass: false, detail: `||Vt·Vtᵀ − I||_F = ${fmt(err)} > tol = ${fmt(tol)}` };
  }
  return { pass: true, detail: `||Vt·Vtᵀ − I||_F = ${fmt(err)} ≤ tol = ${fmt(tol)}` };
}

function checkFactorisationResidual(A: number[][], U: number[][], S: number[], Vt: number[][]): Check {
  const m = A.length;
  const n = m === 0 ? 0 : (A[0]?.length ?? 0);
  const qU = U.length === 0 ? 0 : (U[0]?.length ?? 0);
  const qVt = Vt.length;
  const tolFactor = SAFETY * EPS * Math.max(m, n) * Math.sqrt(Math.min(m, n));

  const Sigma = buildSigma(S, qU, qVt);
  const reconstructed = matmul(matmul(U, Sigma), Vt);
  const aNorm = frobNorm(A);
  const err = frobNorm(matSub(reconstructed, A));

  if (aNorm === 0.0) {
    if (err > 0.0) {
      return { pass: false, detail: `||USVt − A||_F = ${fmt(err)} but A is zero` };
    }
    return { pass: true, detail: "zero input, zero residual" };
  }

  const rel = err / aNorm;
  if (rel > tolFactor) {
    return { pass: false, detail: `||USVt − A||_F / ||A||_F = ${fmt(rel)} > tol = ${fmt(tolFactor)}` };
  }
  return { pass: true, detail: `||USVt − A||_F / ||A||_F = ${fmt(rel)} ≤ tol = ${fmt(tolFactor)}` };
}

function checkSelfReportedResidual(
  A: number[][],
  U: number[][],
  S: number[],
  Vt: number[][],
  reported: number,
): Check {
  // Match verify.py: recomputed = ||USVt − A||_F / max(||A||_F, 1)
  const qU = U.length === 0 ? 0 : (U[0]?.length ?? 0);
  const qVt = Vt.length;
  const Sigma = buildSigma(S, qU, qVt);
  const reconstructed = matmul(matmul(U, Sigma), Vt);
  const aNorm = frobNorm(A);
  const err = frobNorm(matSub(reconstructed, A));
  const recomputed = err / Math.max(aNorm, 1.0);
  const diff = Math.abs(reported - recomputed);
  const rhs = SELF_REPORT_REL_TOL * Math.max(recomputed, EPS);
  if (diff > rhs) {
    return {
      pass: false,
      detail: `reported reconstruction_error=${reported.toExponential(6)} vs verifier=${recomputed.toExponential(6)}, |Δ|=${fmt(diff)} > tol=${fmt(rhs)}`,
    };
  }
  return {
    pass: true,
    detail: `reported=${reported.toExponential(6)}, verifier=${recomputed.toExponential(6)}`,
  };
}

function checkSelfReportedOrthogonality(
  U: number[][],
  Vt: number[][],
  reportedU: number,
  reportedVt: number,
): Check {
  const rU = frobNorm(gramMinusEye(U));
  const rV = frobNorm(rowGramMinusEye(Vt));
  const diffU = Math.abs(reportedU - rU);
  const diffV = Math.abs(reportedVt - rV);
  const rhsU = SELF_REPORT_REL_TOL * Math.max(rU, EPS);
  const rhsV = SELF_REPORT_REL_TOL * Math.max(rV, EPS);
  if (diffU > rhsU) {
    return {
      pass: false,
      detail: `orthogonality_error_U: reported=${reportedU.toExponential(6)} vs verifier=${rU.toExponential(6)}, |Δ|=${fmt(diffU)} > tol=${fmt(rhsU)}`,
    };
  }
  if (diffV > rhsV) {
    return {
      pass: false,
      detail: `orthogonality_error_Vt: reported=${reportedVt.toExponential(6)} vs verifier=${rV.toExponential(6)}, |Δ|=${fmt(diffV)} > tol=${fmt(rhsV)}`,
    };
  }
  return {
    pass: true,
    detail: `U: reported=${fmt(reportedU)}, verifier=${fmt(rU)}; Vt: reported=${fmt(reportedVt)}, verifier=${fmt(rV)}`,
  };
}

// ─── top-level verify ────────────────────────────────────────────────────────

function _wrap(checks: Record<string, Check>): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) return { pass: true, reason: "all invariants hold", checks };
  const firstFail = Object.entries(checks).find(([, c]) => !c.pass)!;
  return { pass: false, reason: `failed: ${firstFail[0]} — ${firstFail[1].detail}`, checks };
}

function verify(payload: {
  input: { A: number[][]; mode?: string };
  candidate: unknown;
  id?: string;
}): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const cand = payload.candidate;
  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }

  const mode = payload.input.mode ?? "reduced";
  if (mode !== "reduced" && mode !== "complete") {
    return { pass: false, reason: `unknown mode ${JSON.stringify(mode)}`, checks: {} };
  }

  const A = payload.input.A;
  if (!Array.isArray(A)) {
    return { pass: false, reason: "A must be a 2-D list", checks: {} };
  }

  const checks: Record<string, Check> = {};
  const candR = cand as Record<string, unknown>;

  // 1. shape (must come first; downstream checks dereference fields)
  checks["shape"] = checkShape(A, candR, mode);
  if (!checks["shape"].pass) return _wrap(checks);

  // 2. finite_entries (must come before any numerical checks)
  checks["finite_entries"] = checkFinite(candR);
  if (!checks["finite_entries"].pass) return _wrap(checks);

  const U  = candR["U"]  as number[][];
  const S  = candR["S"]  as number[];
  const Vt = candR["Vt"] as number[][];

  // 3. S_nonneg_descending
  checks["S_nonneg_descending"] = checkSNonnegDescending(S);

  // 4. U_orthonormal
  checks["U_orthonormal"] = checkUOrthonormal(A, U);

  // 5. Vt_orthonormal
  checks["Vt_orthonormal"] = checkVtOrthonormal(A, Vt);

  // 6. factorisation_residual
  checks["factorisation_residual"] = checkFactorisationResidual(A, U, S, Vt);

  // 7. self_reported_residual
  checks["self_reported_residual"] = checkSelfReportedResidual(A, U, S, Vt, candR["reconstruction_error"] as number);

  // 8. self_reported_orthogonality
  checks["self_reported_orthogonality"] = checkSelfReportedOrthogonality(
    U, Vt,
    candR["orthogonality_error_U"] as number,
    candR["orthogonality_error_Vt"] as number,
  );

  return _wrap(checks);
}

// ─── main ────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  let stdin = "";
  for await (const chunk of Bun.stdin.stream()) {
    stdin += new TextDecoder().decode(chunk);
  }
  let result: { pass: boolean; reason: string; checks: Record<string, Check> };
  try {
    const payload = JSON.parse(stdin) as {
      input: { A: number[][]; mode?: string };
      candidate: unknown;
      id?: string;
    };
    result = verify(payload);
  } catch (e) {
    const err = e as Error;
    process.stderr.write(`${err.stack ?? err.message}\n`);
    result = { pass: false, reason: `verifier crashed: ${err.name}: ${err.message}`, checks: {} };
  }
  process.stdout.write(JSON.stringify(result) + "\n");
}

await main();
