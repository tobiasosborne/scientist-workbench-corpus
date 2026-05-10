// =============================================================================
// benchmarks/hypergeometric-pfq/golden/verify.ts — invariant verifier (TS port)
// =============================================================================
//
// stdin:
//   { input:     <input row from inputs.json>,
//     candidate: <candidate output — see PROMPT.md for shapes>,
//     id:        string }
//
// stdout:
//   { pass: bool, reason: string,
//     checks: { <name>: { pass: bool, detail: string } } }
//
// This is the TypeScript port of verify.py per ADR-0028 §4.
// Tolerances are preserved byte-for-byte with verify.py.
// Do NOT tighten or loosen during migration.
//
// The verifier is self-contained: value-accuracy comparisons use
// @workbench/bigfloat (arbitrary-precision BigInt substrate, ADR-0020)
// imported dynamically from the workbench repo via WORKBENCH_ROOT.
// No mpmath / Python runtime is required at grading time.
//
// ─── Checks ──────────────────────────────────────────────────────────────────
//
//   no_tool_error            — never admissible; always checked first.
//   shape                    — success-path: required fields, types.
//   finite_value             — success-path: re/im parse as finite BigFloat.
//   method_admissible        — success-path: method ∈ admitted set.
//   self_reported_precision  — success-path: 0 ≤ achieved_precision ≤ requested.
//   value_accuracy           — success-path: rel-err vs pinned truth ≤ tolerance_rel.
//   boundary_envelope        — refusal-path: kind=tagged, tag matches, payload predicate.
//
// Determinism: BigInt-substrate arithmetic (ADR-0020 arbprec tier) is
// bit-identical cross-platform forever.  The expected.json truth values are
// pinned at ~80 dps; comparisons at 128 dps (≈385 bits) clear every case's
// tolerance by ≥ 20 orders of magnitude.
//
// =============================================================================

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Resolve workbench root ───────────────────────────────────────────────────
//
// The corpus grader sets WORKBENCH_ROOT when it spawns this verifier.
// The default fallback assumes the conventional side-by-side layout:
//   ~/Projects/scientist-workbench/         ← WORKBENCH_ROOT
//   ~/Projects/scientist-workbench-corpus/  ← this repo

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "..", "scientist-workbench");

// ─── Dynamic imports (resolved through workbench) ────────────────────────────

const bigfloatPath = resolve(WORKBENCH_ROOT, "packages/bigfloat/src/index.ts");
// We import at runtime from the workbench path, typed structurally.
type BfModule = {
  decimalToBinaryPrecision: (dps: number) => number;
  fromString: (s: string, prec: number) => unknown;
  add: (a: unknown, b: unknown, prec: number) => unknown;
  sub: (a: unknown, b: unknown, prec: number) => unknown;
  mul: (a: unknown, b: unknown, prec: number) => unknown;
  div: (a: unknown, b: unknown, prec: number) => unknown;
  sqrt: (a: unknown, prec: number) => unknown;
  le: (a: unknown, b: unknown) => boolean;
  isZero: (a: unknown) => boolean;
  toFloat64: (a: unknown) => { value: number; overflow: null | string };
};
const bf = await import(bigfloatPath) as BfModule;

// BigFloat comparison precision: 128 decimal digits = ~425 binary bits.
// Large enough to distinguish any pair of truth/candidate values the bench uses
// and to clear the loosest tolerance (1e-40) by many orders of magnitude.
const VERIFY_DPS = 128;
const VERIFY_PREC = bf.decimalToBinaryPrecision(VERIFY_DPS);

// ─── Check type ───────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

// ─── expected.json loader ────────────────────────────────────────────────────

type ExpectedCase = {
  id: string;
  tier: string;
  tolerance_rel: string;
  expected: {
    kind: "value" | "tagged";
    truth?: { re: string; im: string };
    tag?: string;
    payload_predicate?: { reason_substr?: string };
  };
};

type ExpectedIndex = Map<string, ExpectedCase>;

let _expectedIndex: ExpectedIndex | null = null;

function loadExpected(): ExpectedIndex {
  if (_expectedIndex !== null) return _expectedIndex;
  const path = resolve(import.meta.dir, "expected.json");
  const payload = JSON.parse(readFileSync(path, "utf8")) as {
    cases: ExpectedCase[];
  };
  _expectedIndex = new Map(payload.cases.map((c) => [c.id, c]));
  return _expectedIndex;
}

// ─── Per-check helpers ────────────────────────────────────────────────────────

const ADMITTED_METHODS = new Set([
  "closed-form-0F0",
  "closed-form-1F0",
  "direct-series",
]);

function checkShape(candidate: Record<string, unknown>): Check {
  // Required success-record fields:
  //   value: { re: string, im: string }
  //   achieved_precision: number (int)
  //   method: string
  //   n_terms: number (int)
  //   working_precision: number (int)
  //   warnings: string[]
  const required = [
    "value",
    "achieved_precision",
    "method",
    "n_terms",
    "working_precision",
    "warnings",
  ];
  const missing = required.filter((k) => !(k in candidate));
  if (missing.length > 0) {
    return { pass: false, detail: `missing fields: ${JSON.stringify(missing.sort())}` };
  }

  const v = candidate["value"];
  if (
    typeof v !== "object" ||
    v === null ||
    Array.isArray(v) ||
    !("re" in v) ||
    !("im" in v)
  ) {
    return {
      pass: false,
      detail: `value must be {re, im}; got ${typeof v === "object" ? JSON.stringify(v) : typeof v}`,
    };
  }
  const vObj = v as Record<string, unknown>;
  if (typeof vObj["re"] !== "string" || typeof vObj["im"] !== "string") {
    return { pass: false, detail: "value.re and value.im must be strings" };
  }
  if (typeof candidate["achieved_precision"] !== "number") {
    return { pass: false, detail: "achieved_precision must be int" };
  }
  if (typeof candidate["n_terms"] !== "number") {
    return { pass: false, detail: "n_terms must be int" };
  }
  if (typeof candidate["working_precision"] !== "number") {
    return { pass: false, detail: "working_precision must be int" };
  }
  if (typeof candidate["method"] !== "string") {
    return { pass: false, detail: "method must be str" };
  }
  const warnings = candidate["warnings"];
  if (
    !Array.isArray(warnings) ||
    !(warnings as unknown[]).every((w) => typeof w === "string")
  ) {
    return { pass: false, detail: "warnings must be list[str]" };
  }
  return { pass: true, detail: "all required fields present" };
}

function checkFiniteValue(candidate: Record<string, unknown>): Check {
  // Parse value.re and value.im as BigFloat at VERIFY_PREC.
  // A finite value parses without exception and is not ±∞ (bigfloat
  // doesn't represent infinities; a parse failure is caught as thrown).
  const v = candidate["value"] as { re: string; im: string };
  try {
    const re = bf.fromString(v.re, VERIFY_PREC);
    const im = bf.fromString(v.im, VERIFY_PREC);
    // BigFloat has no ±∞ representation — any successful fromString is finite.
    void re;
    void im;
  } catch (e) {
    return { pass: false, detail: `could not parse value: ${(e as Error).message}` };
  }
  return { pass: true, detail: "value parses, finite" };
}

function checkMethodAdmissible(candidate: Record<string, unknown>): Check {
  const m = candidate["method"] as string;
  if (!ADMITTED_METHODS.has(m)) {
    return {
      pass: false,
      detail: `method=${JSON.stringify(m)} not in admitted set ${JSON.stringify([...ADMITTED_METHODS].sort())}`,
    };
  }
  return { pass: true, detail: `method=${JSON.stringify(m)}` };
}

function checkSelfReportedPrecision(
  candidate: Record<string, unknown>,
  requestedPrecision: number,
): Check {
  // Per verify.py: 0 ≤ achieved_precision ≤ requested_precision.
  // Under-reporting is permitted (graceful degradation); over-reporting is not.
  const ap = candidate["achieved_precision"] as number;
  if (ap < 0) {
    return { pass: false, detail: `achieved_precision=${ap} is negative` };
  }
  if (ap > requestedPrecision) {
    return {
      pass: false,
      detail: `achieved_precision=${ap} exceeds requested precision ${requestedPrecision} — tool over-reports`,
    };
  }
  return {
    pass: true,
    detail: `achieved=${ap}, requested=${requestedPrecision}`,
  };
}

function checkValueAccuracy(
  candidate: Record<string, unknown>,
  caseExpected: ExpectedCase,
): Check {
  // Compare candidate value to pinned truth using BigFloat arithmetic at
  // VERIFY_PREC (128 dps) — the same mathematical operation as verify.py's
  // mpmath comparison at 80 dps, but with a wider precision budget.
  //
  // rel_err = |candidate - truth| / |truth|      (fallback to absolute if truth=0)
  //
  // pass iff rel_err ≤ tolerance_rel (parsed as a BigFloat at VERIFY_PREC)
  const truth = caseExpected.expected.truth!;
  const v = candidate["value"] as { re: string; im: string };

  // Parse everything at VERIFY_PREC.
  const truthRe = bf.fromString(truth.re, VERIFY_PREC);
  const truthIm = bf.fromString(truth.im, VERIFY_PREC);
  const candRe = bf.fromString(v.re, VERIFY_PREC);
  const candIm = bf.fromString(v.im, VERIFY_PREC);

  // diff = candidate - truth  (as complex)
  const diffRe = bf.sub(candRe, truthRe, VERIFY_PREC);
  const diffIm = bf.sub(candIm, truthIm, VERIFY_PREC);

  // |diff| = sqrt(diffRe² + diffIm²)
  const diffAbsSq = bf.add(
    bf.mul(diffRe, diffRe, VERIFY_PREC),
    bf.mul(diffIm, diffIm, VERIFY_PREC),
    VERIFY_PREC,
  );
  const diffAbs = bf.sqrt(diffAbsSq, VERIFY_PREC);

  // |truth| = sqrt(truthRe² + truthIm²)
  const truthAbsSq = bf.add(
    bf.mul(truthRe, truthRe, VERIFY_PREC),
    bf.mul(truthIm, truthIm, VERIFY_PREC),
    VERIFY_PREC,
  );
  const truthAbs = bf.sqrt(truthAbsSq, VERIFY_PREC);

  let rel: ReturnType<typeof bf.fromString>;
  if (bf.isZero(truthAbs)) {
    // Truth is zero — fall back to absolute error.
    rel = diffAbs;
  } else {
    rel = bf.div(diffAbs, truthAbs, VERIFY_PREC);
  }

  // Parse tolerance_rel.  The pinned values are like "1e-48", "1e-40".
  // bf.fromString handles scientific notation.
  const tol = bf.fromString(caseExpected.tolerance_rel, VERIFY_PREC);

  // rel ≤ tol  iff  cmp(rel, tol) ≤ 0
  const pass = bf.le(rel, tol);

  // Format for detail message using float64 approximations (fine for display).
  // bf.toFloat64 returns { value: number, overflow: ... } — extract .value.
  const relFloat = bf.toFloat64(rel).value;
  const tolFloat = bf.toFloat64(tol).value;

  if (pass) {
    return {
      pass: true,
      detail: `rel=${relFloat.toExponential(3)} ≤ tol=${tolFloat.toExponential(3)}`,
    };
  }
  return {
    pass: false,
    detail: `rel=${relFloat.toExponential(3)} > tol=${tolFloat.toExponential(3)}`,
  };
}

function checkBoundaryEnvelope(
  candidate: unknown,
  caseExpected: ExpectedCase,
): Check {
  // The candidate must be { kind:"tagged", tag:"hypergeometric-pfq/<class>", payload:{...} }.
  if (
    typeof candidate !== "object" ||
    candidate === null ||
    (candidate as Record<string, unknown>)["kind"] !== "tagged"
  ) {
    const got =
      typeof candidate === "object" && candidate !== null
        ? ((candidate as Record<string, unknown>)["kind"] ?? typeof candidate)
        : typeof candidate;
    return {
      pass: false,
      detail: `expected tagged refusal but got ${got}`,
    };
  }
  const c = candidate as Record<string, unknown>;
  const expectedTag = caseExpected.expected.tag!;
  const actualTag = (c["tag"] as string) ?? "";
  if (actualTag !== expectedTag) {
    return {
      pass: false,
      detail: `tag ${JSON.stringify(actualTag)} != expected ${JSON.stringify(expectedTag)}`,
    };
  }

  // Optional payload predicate: reason_substr
  const pred = caseExpected.expected.payload_predicate;
  if (pred?.reason_substr) {
    const payload = c["payload"];
    const reason =
      typeof payload === "object" && payload !== null
        ? ((payload as Record<string, unknown>)["reason"] as string) ?? ""
        : "";
    if (!reason.includes(pred.reason_substr)) {
      return {
        pass: false,
        detail: `payload.reason=${JSON.stringify(reason)} does not contain the predicate substring ${JSON.stringify(pred.reason_substr)}`,
      };
    }
  }

  return { pass: true, detail: `tagged ${expectedTag}` };
}

// ─── Wrap helper ─────────────────────────────────────────────────────────────

function wrap(checks: Record<string, Check>): {
  pass: boolean;
  reason: string;
  checks: Record<string, Check>;
} {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) {
    return { pass: true, reason: "all invariants hold", checks };
  }
  const firstFail = Object.entries(checks).find(([, v]) => !v.pass)!;
  return {
    pass: false,
    reason: `failed: ${firstFail[0]} — ${firstFail[1].detail}`,
    checks,
  };
}

// ─── Top-level verify ─────────────────────────────────────────────────────────

function verify(payload: {
  id?: string;
  input?: Record<string, unknown>;
  candidate?: unknown;
}) {
  const caseId = payload["id"] ?? "";
  const candidate = payload["candidate"] ?? {};
  const inp = payload["input"] ?? {};

  if (!payload["id"]) {
    return { pass: false, reason: "missing id in payload", checks: {} };
  }

  const expectedIndex = loadExpected();
  const caseExpected = expectedIndex.get(caseId);
  if (!caseExpected) {
    return {
      pass: false,
      reason: `id ${JSON.stringify(caseId)} not in expected.json`,
      checks: {},
    };
  }

  const checks: Record<string, Check> = {};

  // Tool errors are never expected — always fail immediately.
  if (
    typeof candidate === "object" &&
    candidate !== null &&
    (candidate as Record<string, unknown>)["kind"] === "tool_error"
  ) {
    const c = candidate as Record<string, unknown>;
    checks["no_tool_error"] = {
      pass: false,
      detail: `tool crashed: ${c["name"]}: ${c["message"]}`,
    };
    return wrap(checks);
  }
  checks["no_tool_error"] = { pass: true, detail: "tool did not crash" };

  const expectedKind = caseExpected.expected.kind;

  if (expectedKind === "tagged") {
    checks["boundary_envelope"] = checkBoundaryEnvelope(candidate, caseExpected);
    return wrap(checks);
  }

  // Success path — candidate must be a plain object (record), not tagged.
  const candidateRecord = candidate as Record<string, unknown>;

  checks["shape"] = checkShape(candidateRecord);
  if (!checks["shape"]!.pass) return wrap(checks);

  checks["finite_value"] = checkFiniteValue(candidateRecord);
  if (!checks["finite_value"]!.pass) return wrap(checks);

  checks["method_admissible"] = checkMethodAdmissible(candidateRecord);

  const requestedPrecision = (inp as { precision?: number })["precision"] ?? 50;
  checks["self_reported_precision"] = checkSelfReportedPrecision(
    candidateRecord,
    requestedPrecision,
  );

  checks["value_accuracy"] = checkValueAccuracy(candidateRecord, caseExpected);

  return wrap(checks);
}

// ─── Main ─────────────────────────────────────────────────────────────────────

try {
  const payload = JSON.parse(readFileSync(0, "utf8")) as {
    id?: string;
    input?: Record<string, unknown>;
    candidate?: unknown;
  };
  const result = verify(payload);
  process.stdout.write(JSON.stringify(result) + "\n");
} catch (e) {
  process.stderr.write(String(e) + "\n");
  process.stdout.write(
    JSON.stringify({
      pass: false,
      reason: `verifier crashed: ${(e as Error).constructor?.name}: ${(e as Error).message}`,
      checks: {},
    }) + "\n",
  );
}
