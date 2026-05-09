// =============================================================================
// linalg-qr verifier — invariant-based, language-neutral.
// =============================================================================
//
// stdin:
//   { input:     { A: number[][], mode?: "reduced" | "complete" },
//     candidate: { Q, R, mode, diagonal_R, reconstruction_error,
//                  orthogonality_error, method, warnings },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// Runs 7 independent checks per case (see verifier_protocol.md):
//   1. shape                          — Q, R dimensions match (m, n, mode)
//   2. finite_entries                 — no NaN, no ±Inf
//   3. R_upper_triangular             — sub-diagonal entries within tol_struct
//   4. Q_orthonormal                  — ||QᵀQ − I|| within tol_orth
//   5. factorisation_residual         — ||QR − A||_F within tol_recon · ||A||_F
//   6. self_reported_residual         — candidate's recon claim is honest
//   7. self_reported_orthogonality    — candidate's orth claim is honest
//
// TS port of the Python verifier from scientist-workbench/bench/linalg-qr.
// Tolerances and structure match byte-for-byte (verify.py values preserved
// per ADR-0028 §4 — do not tighten or loosen during migration).
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
  const out: number[][] = Array.from({ length: A.length }, (_, i) =>
    A[i]!.map((v, j) => v - B[i]![j]!),
  );
  return out;
}

function eyeMinusGram(Q: number[][]): number[][] {
  // Returns QᵀQ − I.
  const Qt = transpose(Q);
  const QtQ = matmul(Qt, Q);
  for (let i = 0; i < QtQ.length; i++) QtQ[i]![i]! -= 1;
  return QtQ;
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

  const required = [
    "Q", "R", "mode", "diagonal_R",
    "reconstruction_error", "orthogonality_error",
    "method", "warnings",
  ];
  const missing = required.filter((k) => !(k in cand));
  if (missing.length > 0) {
    return { pass: false, detail: `missing fields: [${missing.sort().join(",")}]` };
  }

  if (cand["mode"] !== mode) {
    return { pass: false, detail: `mode mismatch: candidate=${JSON.stringify(cand["mode"])}, expected=${JSON.stringify(mode)}` };
  }

  if (cand["method"] !== "householder") {
    return { pass: false, detail: `method must be 'householder'; got ${JSON.stringify(cand["method"])}` };
  }

  const warnings = cand["warnings"];
  if (!Array.isArray(warnings) || !warnings.every((s) => typeof s === "string")) {
    return { pass: false, detail: "warnings must be list[str]" };
  }

  const kReduced = Math.min(m, n);
  const expectedQ: [number, number] = mode === "reduced" ? [m, kReduced] : [m, m];
  const expectedR: [number, number] = mode === "reduced" ? [kReduced, n] : [m, n];

  const sQ = shapeOf(cand["Q"]);
  if (!sQ || sQ[0] !== expectedQ[0] || sQ[1] !== expectedQ[1]) {
    return { pass: false, detail: `Q shape ${JSON.stringify(sQ)}, expected ${JSON.stringify(expectedQ)} for mode=${JSON.stringify(mode)}` };
  }

  const sR = shapeOf(cand["R"]);
  if (!sR || sR[0] !== expectedR[0] || sR[1] !== expectedR[1]) {
    return { pass: false, detail: `R shape ${JSON.stringify(sR)}, expected ${JSON.stringify(expectedR)} for mode=${JSON.stringify(mode)}` };
  }

  const diagLen = Math.min(sR[0], sR[1]);
  const dLen = vecLen(cand["diagonal_R"]);
  if (dLen !== diagLen) {
    return { pass: false, detail: `diagonal_R must be list[float] of length ${diagLen}` };
  }

  return { pass: true, detail: `Q ${JSON.stringify(sQ)}, R ${JSON.stringify(sR)}, mode ${JSON.stringify(mode)}` };
}

function checkFinite(cand: Record<string, unknown>): Check {
  // Q and R are matrices (list of lists); diagonal_R is a flat list.
  // Python's verify.py uses np.asarray which handles both shapes uniformly.
  // We replicate with allFiniteMat for Q/R and allFiniteVec for diagonal_R.
  for (const field of ["Q", "R"] as const) {
    if (!allFiniteMat(cand[field])) {
      return { pass: false, detail: `non-finite entry in ${field}` };
    }
  }
  if (!allFiniteVec(cand["diagonal_R"])) {
    return { pass: false, detail: "non-finite entry in diagonal_R" };
  }
  for (const field of ["reconstruction_error", "orthogonality_error"] as const) {
    const v = cand[field];
    if (typeof v !== "number" || !Number.isFinite(v)) {
      return { pass: false, detail: `non-finite or non-numeric ${field}` };
    }
  }
  return { pass: true, detail: "all entries finite" };
}

function checkRUpperTriangular(A: number[][], R: number[][]): Check {
  const m = A.length;
  const n = m === 0 ? 0 : (A[0]?.length ?? 0);
  const aNorm = Math.max(frobNorm(A), 1.0);
  const tol = SAFETY * EPS * Math.max(m, n) * aNorm;

  const rR = R.length;
  const cR = rR === 0 ? 0 : (R[0]?.length ?? 0);
  const k = Math.min(rR, cR);

  // Sub-diagonal of the min(m,n) × min(m,n) leading block must be ~0.
  for (let i = 0; i < k; i++) {
    for (let j = 0; j < i; j++) {
      const v = Math.abs(R[i]![j]!);
      if (v > tol) {
        return { pass: false, detail: `R[${i},${j}]=${fmt(R[i]![j]!)} > tol=${fmt(tol)}` };
      }
    }
  }

  // For mode="complete" and m > n, the bottom (m − n) rows of R must be ~0.
  if (rR > cR) {
    for (let i = cR; i < rR; i++) {
      for (let j = 0; j < cR; j++) {
        const v = Math.abs(R[i]![j]!);
        if (v > tol) {
          return { pass: false, detail: `R[${i},${j}]=${fmt(R[i]![j]!)} > tol=${fmt(tol)} (bottom block)` };
        }
      }
    }
  }

  return { pass: true, detail: `sub-diagonal max < tol=${fmt(tol)}` };
}

function checkQOrthonormal(A: number[][], Q: number[][]): Check {
  const m = A.length;
  const k = Q.length === 0 ? 0 : (Q[0]?.length ?? 0);
  const tol = SAFETY * EPS * m * Math.sqrt(k);
  const err = frobNorm(eyeMinusGram(Q));
  if (err > tol) {
    return { pass: false, detail: `||QᵀQ − I||_F = ${fmt(err)} > tol = ${fmt(tol)}` };
  }
  return { pass: true, detail: `||QᵀQ − I||_F = ${fmt(err)} ≤ tol = ${fmt(tol)}` };
}

function checkFactorisationResidual(A: number[][], Q: number[][], R: number[][]): Check {
  const m = A.length;
  const n = m === 0 ? 0 : (A[0]?.length ?? 0);
  const aNorm = frobNorm(A);
  const tolFactor = SAFETY * EPS * Math.max(m, n) * Math.sqrt(Math.min(m, n));
  const err = frobNorm(matSub(matmul(Q, R), A));

  if (aNorm === 0.0) {
    if (err > 0.0) {
      return { pass: false, detail: `||QR − A||_F = ${fmt(err)} but A is zero` };
    }
    return { pass: true, detail: "zero input, zero residual" };
  }

  const rel = err / aNorm;
  if (rel > tolFactor) {
    return { pass: false, detail: `||QR − A||_F / ||A||_F = ${fmt(rel)} > tol = ${fmt(tolFactor)}` };
  }
  return { pass: true, detail: `||QR − A||_F / ||A||_F = ${fmt(rel)} ≤ tol = ${fmt(tolFactor)}` };
}

function checkSelfReportedResidual(
  A: number[][],
  Q: number[][],
  R: number[][],
  reported: number,
): Check {
  // Match Python: recomputed = ||QR − A||_F / max(||A||_F, 1)
  const aNorm = frobNorm(A);
  const err = frobNorm(matSub(matmul(Q, R), A));
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
    detail: `reported=${reported.toExponential(6)}, verifier=${recomputed.toExponential(6)} (within ${SELF_REPORT_REL_TOL} rel)`,
  };
}

function checkSelfReportedOrthogonality(Q: number[][], reported: number): Check {
  const recomputed = frobNorm(eyeMinusGram(Q));
  const diff = Math.abs(reported - recomputed);
  const rhs = SELF_REPORT_REL_TOL * Math.max(recomputed, EPS);
  if (diff > rhs) {
    return {
      pass: false,
      detail: `reported orthogonality_error=${reported.toExponential(6)} vs verifier=${recomputed.toExponential(6)}, |Δ|=${fmt(diff)} > tol=${fmt(rhs)}`,
    };
  }
  return {
    pass: true,
    detail: `reported=${reported.toExponential(6)}, verifier=${recomputed.toExponential(6)} (within ${SELF_REPORT_REL_TOL} rel)`,
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

  const Q = candR["Q"] as number[][];
  const R = candR["R"] as number[][];

  // 3. R_upper_triangular
  checks["R_upper_triangular"] = checkRUpperTriangular(A, R);

  // 4. Q_orthonormal
  checks["Q_orthonormal"] = checkQOrthonormal(A, Q);

  // 5. factorisation_residual
  checks["factorisation_residual"] = checkFactorisationResidual(A, Q, R);

  // 6. self_reported_residual
  checks["self_reported_residual"] = checkSelfReportedResidual(A, Q, R, candR["reconstruction_error"] as number);

  // 7. self_reported_orthogonality
  checks["self_reported_orthogonality"] = checkSelfReportedOrthogonality(Q, candR["orthogonality_error"] as number);

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
