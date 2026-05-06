// =============================================================================
// linalg-eigh verifier — invariant-based, language-neutral.
// =============================================================================
//
// stdin:
//   { input:     { A: number[][] },
//     candidate: { Q, eigenvalues, reconstruction_error, orthogonality_error,
//                  condition_number, method, warnings }
//                OR a tagged-boundary { kind: "tagged", tag, payload },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// Runs 7 independent checks per case (see verifier_protocol.md).  TS port of
// the Python verifier from scientist-workbench/bench/linalg-eigh; tolerances
// and structure match byte-for-byte except for floating-point text formatting.
//
// The matrix operations (transpose, matmul, Frobenius norm) are inlined
// because the tournament-protocol contract is "single executable, JSON in,
// JSON out" — no external linalg dependency.  Performance is fine: even the
// 500×500 stress case runs the verifier in well under a second.

const EPS                  = 2.220446049250313e-16;
const SAFETY               = 100.0;
const SELF_REPORT_REL_TOL  = 1e-6;
const SYMMETRY_TOL_FACTOR  = 100.0 * EPS;

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

function matSubInPlace(A: number[][], B: number[][]): void {
  for (let i = 0; i < A.length; i++) {
    const ar = A[i]!;
    const br = B[i]!;
    for (let j = 0; j < ar.length; j++) ar[j]! -= br[j]!;
  }
}

function frobNorm(A: number[][]): number {
  let s = 0;
  for (const row of A) for (const v of row) s += v * v;
  return Math.sqrt(s);
}

function eyeMinusGram(Q: number[][]): number[][] {
  // Returns QᵀQ − I.
  const Qt = transpose(Q);
  const QtQ = matmul(Qt, Q);
  for (let i = 0; i < QtQ.length; i++) QtQ[i]![i]! -= 1;
  return QtQ;
}

function maxAbs(A: number[][]): number {
  let m = 0;
  for (const row of A) for (const v of row) {
    const a = Math.abs(v);
    if (a > m) m = a;
  }
  return m;
}

function isSymmetric(A: number[][]): boolean {
  const n = A.length;
  if (n === 0) return true;
  if ((A[0]?.length ?? 0) !== n) return false;
  const m = maxAbs(A);
  if (m === 0) return true;
  let asym = 0;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const d = Math.abs(A[i]![j]! - A[j]![i]!);
    if (d > asym) asym = d;
  }
  return asym <= SYMMETRY_TOL_FACTOR * m;
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

function shapeOf(mat: unknown): [number, number] | null {
  if (!Array.isArray(mat)) return null;
  if (mat.length === 0) return [0, 0];
  for (const row of mat) {
    if (!Array.isArray(row)) return null;
  }
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

function fmt(x: number): string {
  if (!Number.isFinite(x)) return String(x);
  return x.toExponential(3);
}

// ─── per-check implementations ───────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

function checkShape(A: number[][], cand: Record<string, unknown>): Check {
  const n = A.length;
  const expectedQ: [number, number] = [n, n];
  const required = [
    "Q", "eigenvalues",
    "reconstruction_error", "orthogonality_error",
    "condition_number", "method", "warnings",
  ];
  const missing = required.filter((k) => !(k in cand));
  if (missing.length > 0) return { pass: false, detail: `missing fields: [${missing.sort().join(",")}]` };

  if (typeof cand["method"] !== "string") return { pass: false, detail: "method must be a string" };
  const warnings = cand["warnings"];
  if (!Array.isArray(warnings) || !warnings.every((s) => typeof s === "string")) {
    return { pass: false, detail: "warnings must be list[str]" };
  }
  const sQ = shapeOf(cand["Q"]);
  if (!sQ || sQ[0] !== expectedQ[0] || sQ[1] !== expectedQ[1]) {
    return { pass: false, detail: `Q shape ${JSON.stringify(sQ)}, expected ${JSON.stringify(expectedQ)}` };
  }
  const sEV = vecLen(cand["eigenvalues"]);
  if (sEV !== n) return { pass: false, detail: `eigenvalues length ${sEV}, expected ${n}` };
  return { pass: true, detail: `Q ${JSON.stringify(sQ)}, eigenvalues length ${sEV}` };
}

function checkFinite(cand: Record<string, unknown>): Check {
  if (!allFiniteMat(cand["Q"])) return { pass: false, detail: "non-finite entry in Q" };
  if (!allFiniteVec(cand["eigenvalues"])) return { pass: false, detail: "non-finite entry in eigenvalues" };
  for (const f of ["reconstruction_error", "orthogonality_error", "condition_number"] as const) {
    const v = cand[f];
    if (typeof v !== "number" || !Number.isFinite(v)) {
      return { pass: false, detail: `non-finite or non-numeric ${f}` };
    }
  }
  return { pass: true, detail: "all entries finite" };
}

function checkEigenvaluesAscending(eigenvalues: number[]): Check {
  if (eigenvalues.length === 0) return { pass: true, detail: "empty eigenvalues" };
  let lamMax = 0;
  for (const v of eigenvalues) { const a = Math.abs(v); if (a > lamMax) lamMax = a; }
  const tol = SAFETY * EPS * Math.max(lamMax, 1.0);
  for (let i = 0; i < eigenvalues.length - 1; i++) {
    const a = eigenvalues[i]!, b = eigenvalues[i + 1]!;
    if (a > b + tol) {
      return { pass: false, detail:
        `eigenvalues[${i}]=${fmt(a)} > eigenvalues[${i + 1}]=${fmt(b)} (non-ascending; gap ${fmt(a - b)} > tol=${fmt(tol)})` };
    }
  }
  return { pass: true, detail: `eigenvalues ascending: λ_min=${fmt(eigenvalues[0]!)}, λ_max=${fmt(eigenvalues[eigenvalues.length - 1]!)}` };
}

function checkQOrthonormal(A: number[][], Q: number[][]): Check {
  const n = A.length;
  const tol = SAFETY * EPS * n * Math.sqrt(n);
  const err = frobNorm(eyeMinusGram(Q));
  if (err > tol) return { pass: false, detail: `||QᵀQ − I||_F = ${fmt(err)} > tol = ${fmt(tol)}` };
  return { pass: true, detail: `||QᵀQ − I||_F = ${fmt(err)} ≤ tol = ${fmt(tol)}` };
}

function residualRel(A: number[][], Q: number[][], eigenvalues: number[]): { rel: number; err: number; aNorm: number } {
  const aNorm = frobNorm(A);
  // AQ − Q·diag(λ)
  const AQ = matmul(A, Q);
  const n = Q.length;
  const m = (Q[0] ?? []).length;
  const QD: number[][] = Array.from({ length: n }, () => new Array<number>(m).fill(0));
  for (let i = 0; i < n; i++) {
    const qi = Q[i]!;
    const qd = QD[i]!;
    for (let j = 0; j < m; j++) qd[j] = qi[j]! * eigenvalues[j]!;
  }
  matSubInPlace(AQ, QD);
  const err = frobNorm(AQ);
  return { rel: aNorm === 0 ? err : err / aNorm, err, aNorm };
}

function checkEigendecompResidual(A: number[][], Q: number[][], eigenvalues: number[]): Check {
  const n = A.length;
  const tolFactor = SAFETY * EPS * n * Math.sqrt(n);
  const r = residualRel(A, Q, eigenvalues);
  if (r.aNorm === 0.0) {
    if (r.err > 0) return { pass: false, detail: `||AQ − Qdiag(λ)||_F = ${fmt(r.err)} but A is zero` };
    return { pass: true, detail: "zero input, zero residual" };
  }
  if (r.rel > tolFactor) return { pass: false, detail: `||AQ − Qdiag(λ)||_F / ||A||_F = ${fmt(r.rel)} > tol = ${fmt(tolFactor)}` };
  return { pass: true, detail: `||AQ − Qdiag(λ)||_F / ||A||_F = ${fmt(r.rel)} ≤ tol = ${fmt(tolFactor)}` };
}

function checkSelfReportedResidual(A: number[][], Q: number[][], eigenvalues: number[], reported: number): Check {
  const aNorm = frobNorm(A);
  const r = residualRel(A, Q, eigenvalues);
  // Match Python: recomputed = ||AQ − Qdiag(λ)||_F / max(||A||_F, 1)
  const recomputed = aNorm < 1.0 ? r.err : r.err / aNorm;
  const diff = Math.abs(reported - recomputed);
  const rhs = SELF_REPORT_REL_TOL * Math.max(recomputed, EPS);
  if (diff > rhs) return { pass: false, detail: `reported reconstruction_error=${reported.toExponential(6)} vs verifier=${recomputed.toExponential(6)}, |Δ|=${fmt(diff)} > tol=${fmt(rhs)}` };
  return { pass: true, detail: `reported=${reported.toExponential(6)}, verifier=${recomputed.toExponential(6)}` };
}

function checkSelfReportedOrthogonality(Q: number[][], reported: number): Check {
  const recomputed = frobNorm(eyeMinusGram(Q));
  const diff = Math.abs(reported - recomputed);
  const rhs = SELF_REPORT_REL_TOL * Math.max(recomputed, EPS);
  if (diff > rhs) return { pass: false, detail: `reported orthogonality_error=${reported.toExponential(6)} vs verifier=${recomputed.toExponential(6)}, |Δ|=${fmt(diff)} > tol=${fmt(rhs)}` };
  return { pass: true, detail: `reported=${reported.toExponential(6)}, verifier=${recomputed.toExponential(6)}` };
}

// ─── tagged-boundary handling ────────────────────────────────────────────────

function isTagged(c: unknown): c is { kind: "tagged"; tag: string; payload?: unknown } {
  return typeof c === "object" && c !== null && (c as { kind?: unknown }).kind === "tagged";
}

function verifyTagged(A: number[][], cand: { kind: "tagged"; tag: string }):
  { pass: boolean; reason: string; checks: Record<string, Check> } {
  const tag = cand.tag ?? "";
  const wrap = (pass: boolean, reason: string, detail: string): { pass: boolean; reason: string; checks: Record<string, Check> } =>
    ({ pass, reason, checks: { boundary: { pass, detail } } });

  if (tag === "linalg-eigh/non-finite-input") {
    const finite = allFiniteMat(A);
    return finite
      ? wrap(false, "non-finite-input tagged but A is all-finite", "misclassified")
      : wrap(true,  "non-finite-input correctly tagged", tag);
  }
  if (tag === "linalg-eigh/degenerate-shape") {
    const m = A.length;
    const n = m === 0 ? 0 : (A[0]?.length ?? 0);
    return (m === 0 || n === 0)
      ? wrap(true,  "degenerate-shape correctly tagged", tag)
      : wrap(false, "degenerate-shape tagged but A non-empty", "misclassified");
  }
  if (tag === "linalg-eigh/non-symmetric-input") {
    return isSymmetric(A)
      ? wrap(false, "non-symmetric-input tagged but A is symmetric", "misclassified")
      : wrap(true,  "non-symmetric-input correctly tagged", tag);
  }
  return wrap(false, `unknown tag ${JSON.stringify(tag)}`, tag);
}

// ─── top-level verify ────────────────────────────────────────────────────────

function _wrap(checks: Record<string, Check>): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) return { pass: true, reason: "all invariants hold", checks };
  const firstFail = Object.entries(checks).find(([, c]) => !c.pass)!;
  return { pass: false, reason: `failed: ${firstFail[0]} — ${firstFail[1].detail}`, checks };
}

function verify(payload: { input: { A: number[][] }; candidate: unknown; id?: string }):
  { pass: boolean; reason: string; checks: Record<string, Check> } {
  const cand = payload.candidate;
  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }
  const A = payload.input.A;
  if (!Array.isArray(A)) return { pass: false, reason: "A must be a 2-D list", checks: {} };

  if (isTagged(cand)) return verifyTagged(A, cand);

  const m = A.length;
  const n = m === 0 ? 0 : (A[0]?.length ?? 0);
  if (m !== n) return { pass: false, reason: `A must be square; got shape (${m}, ${n})`, checks: {} };

  const checks: Record<string, Check> = {};
  const candR = cand as Record<string, unknown>;

  checks["shape"] = checkShape(A, candR);
  if (!checks["shape"].pass) return _wrap(checks);

  checks["finite_entries"] = checkFinite(candR);
  if (!checks["finite_entries"].pass) return _wrap(checks);

  const Q = candR["Q"] as number[][];
  const eigenvalues = candR["eigenvalues"] as number[];

  checks["eigenvalues_ascending"]      = checkEigenvaluesAscending(eigenvalues);
  checks["Q_orthonormal"]              = checkQOrthonormal(A, Q);
  checks["eigendecomp_residual"]       = checkEigendecompResidual(A, Q, eigenvalues);
  checks["self_reported_residual"]     = checkSelfReportedResidual(A, Q, eigenvalues, candR["reconstruction_error"] as number);
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
    const payload = JSON.parse(stdin) as { input: { A: number[][] }; candidate: unknown; id?: string };
    result = verify(payload);
  } catch (e) {
    const err = e as Error;
    process.stderr.write(`${err.stack ?? err.message}\n`);
    result = { pass: false, reason: `verifier crashed: ${err.name}: ${err.message}`, checks: {} };
  }
  process.stdout.write(JSON.stringify(result) + "\n");
}

await main();
