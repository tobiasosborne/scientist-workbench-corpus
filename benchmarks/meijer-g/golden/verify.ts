// =============================================================================
// benchmarks/meijer-g/golden/verify.ts — invariant verifier (TS port)
// =============================================================================
//
// stdin:
//   { input:     <input row from inputs.json::cases[i].input>,
//     candidate: <candidate output — see PROMPT.md for shapes>,
//     id:        string }
//
// stdout:
//   { pass: bool, reason: string,
//     checks: { <name>: { pass: bool, detail: string } } }
//
// This is the TypeScript port of verify.py per ADR-0028 §4.
// Tolerances and logic are preserved byte-for-byte with verify.py.
// Do NOT tighten or loosen during migration.
//
// The verifier is self-contained: value-accuracy comparisons use
// @workbench/bigfloat (arbitrary-precision BigInt substrate, ADR-0020)
// imported dynamically from the workbench repo via WORKBENCH_ROOT.
// No mpmath / Python runtime is required at grading time.
//
// ─── Candidate shapes ────────────────────────────────────────────────────────
//
//   symbolic success:
//     { kind: "symbolic", rule: string, source: string, note: string,
//       method: string, expr: <opaque> }
//
//   numerical success:
//     { kind: "numerical", value: {re: string, im: string},
//       achieved_precision: int, method: string,
//       working_precision: int, warnings: string[],
//       diagnostics: {...} }
//
//   tagged refusal:
//     { kind: "tagged", tag: "meijer-g/<class>",
//       payload: { reason: string, ruled_out_methods: [...] } }
//
//   tool error:
//     { kind: "tool_error", name: string, message: string }
//
// ─── Checks (matching verify.py exactly) ─────────────────────────────────────
//
//   no_tool_error            — never admissible; always checked first.
//   shape                    — output kind matches expected.kind
//                               (value allows symbolic OR numerical
//                                unless request_mode constrains)
//   finite_value             — numerical: re/im parse as finite BigFloat.
//   method_admissible        — numerical ∈ {slater-series-1|2, mellin-barnes,
//                               braaksma-algebraic}; symbolic == 'symbolic-dispatch'
//   self_reported_precision  — numerical: 0 ≤ achieved_precision ≤ requested.
//   value_accuracy           — numerical: rel-err vs pinned truth ≤ tolerance_rel.
//                               Skipped when expected.truth is null.
//   symbolic_rule_present    — symbolic: non-empty rule field.
//   boundary_envelope        — refusal-path: tag matches expected.tag.
//
// Speed gate (Tier H): only active when MEIJERG_BENCH_CHECK_SPEED=1.
// Default is off per ADR-0028 migration instructions and bead rp05.
//
// Determinism: BigInt-substrate arithmetic (ADR-0020 arbprec tier) is
// bit-identical cross-platform forever.  The expected.json truth values are
// pinned at ~80 dps; comparisons at 128 dps (≈385 bits) clear every case's
// tolerance by many orders of magnitude.
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
// and to clear the loosest tolerance by many orders of magnitude.
const VERIFY_DPS = 128;
const VERIFY_PREC = bf.decimalToBinaryPrecision(VERIFY_DPS);

// ─── Check type ───────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

// ─── expected.json types ──────────────────────────────────────────────────────

type ExpectedCase = {
  id: string;
  tier: string;
  tolerance_rel: string;
  expected: {
    kind: "value" | "tagged";
    truth?: { re: string; im: string } | null;
    tag?: string;
    truth_method?: string;
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

// ─── Admitted method sets (matching verify.py exactly) ───────────────────────

const ADMITTED_NUMERICAL_METHODS = new Set([
  "slater-series-1",
  "slater-series-2",
  "mellin-barnes",
  "braaksma-algebraic",
]);

const ADMITTED_SYMBOLIC_METHODS = new Set(["symbolic-dispatch"]);

const SPEED_GATE_MS = 1500.0; // tier H

// ─── Per-check helpers ────────────────────────────────────────────────────────

function checkShapeNumerical(candidate: Record<string, unknown>): Check {
  // Required numerical-success fields:
  //   value: { re: string, im: string }
  //   achieved_precision: number (int)
  //   method: string
  //   working_precision: number (int)
  //   warnings: string[]
  //   diagnostics: object
  const required = [
    "value",
    "achieved_precision",
    "method",
    "working_precision",
    "warnings",
    "diagnostics",
  ];
  const missing = required.filter((k) => !(k in candidate));
  if (missing.length > 0) {
    return {
      pass: false,
      detail: `missing fields: ${JSON.stringify(missing.sort())}`,
    };
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
    return { pass: false, detail: "value.re/im must be strings" };
  }
  if (typeof candidate["achieved_precision"] !== "number") {
    return { pass: false, detail: "achieved_precision must be int" };
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
  return { pass: true, detail: "all required numerical fields present" };
}

function checkShapeSymbolic(candidate: Record<string, unknown>): Check {
  // Required symbolic-success fields: rule, source, note, method, expr
  const required = ["rule", "source", "note", "method", "expr"];
  const missing = required.filter((k) => !(k in candidate));
  if (missing.length > 0) {
    return {
      pass: false,
      detail: `missing fields: ${JSON.stringify(missing.sort())}`,
    };
  }
  for (const key of ["rule", "source", "note", "method"] as const) {
    if (typeof candidate[key] !== "string") {
      return { pass: false, detail: `${key} must be str` };
    }
  }
  return { pass: true, detail: "all required symbolic fields present" };
}

function checkFiniteValue(candidate: Record<string, unknown>): Check {
  // Parse value.re and value.im as BigFloat at VERIFY_PREC.
  // BigFloat has no ±∞ representation; any successful fromString is finite.
  const v = candidate["value"] as { re: string; im: string };
  try {
    const re = bf.fromString(v.re, VERIFY_PREC);
    const im = bf.fromString(v.im, VERIFY_PREC);
    void re;
    void im;
  } catch (e) {
    return {
      pass: false,
      detail: `could not parse value: ${(e as Error).message}`,
    };
  }
  return { pass: true, detail: "value parses, finite" };
}

function checkMethodAdmissible(
  candidate: Record<string, unknown>,
  kind: "numerical" | "symbolic",
): Check {
  const m = (candidate["method"] as string) ?? "";
  const admitted =
    kind === "numerical" ? ADMITTED_NUMERICAL_METHODS : ADMITTED_SYMBOLIC_METHODS;
  if (!admitted.has(m)) {
    return {
      pass: false,
      detail: `method=${JSON.stringify(m)} not in admitted set ${JSON.stringify([...admitted].sort())}`,
    };
  }
  return { pass: true, detail: `method=${JSON.stringify(m)}` };
}

function checkSelfReportedPrecision(
  candidate: Record<string, unknown>,
  requestedPrecision: number,
): Check {
  // Per verify.py: 0 ≤ achieved_precision ≤ requested_precision.
  // Under-reporting is permitted; over-reporting is not.
  const ap = candidate["achieved_precision"] as number;
  if (ap < 0) {
    return { pass: false, detail: `achieved_precision=${ap} negative` };
  }
  if (ap > requestedPrecision) {
    return {
      pass: false,
      detail:
        `achieved_precision=${ap} > requested ${requestedPrecision} — over-reporting`,
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
  // VERIFY_PREC (128 dps) — mirrors verify.py's mpmath comparison at 80 dps
  // but with a wider precision budget.
  //
  // rel_err = |candidate − truth| / |truth|     (fallback to absolute if truth = 0)
  // pass iff rel_err ≤ tolerance_rel
  const truth = caseExpected.expected.truth!;
  const v = candidate["value"] as { re: string; im: string };

  const truthRe = bf.fromString(truth.re, VERIFY_PREC);
  const truthIm = bf.fromString(truth.im, VERIFY_PREC);
  const candRe = bf.fromString(v.re, VERIFY_PREC);
  const candIm = bf.fromString(v.im, VERIFY_PREC);

  // diff = candidate − truth  (complex)
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
    // Truth is zero — fall back to absolute error (matching verify.py's
    // `scale = max(|truth|, 1e-300)` with scale→0 branch).
    rel = diffAbs;
  } else {
    rel = bf.div(diffAbs, truthAbs, VERIFY_PREC);
  }

  // Parse tolerance_rel string (e.g. "1e-46", "1e-38").
  const tol = bf.fromString(caseExpected.tolerance_rel, VERIFY_PREC);

  const pass = bf.le(rel, tol);

  // Float64 approximations for the detail message.
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
      detail: `expected tagged refusal but got kind=${String(got)}`,
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
  return { pass: true, detail: `tagged ${expectedTag}` };
}

function checkSpeed(candidate: unknown): Check | null {
  // Tier-H speed gate: only active when MEIJERG_BENCH_CHECK_SPEED=1.
  // Default is off; the corpus migration ships it off by default (bead rp05).
  if (process.env["MEIJERG_BENCH_CHECK_SPEED"] !== "1") return null;
  const c = candidate as Record<string, unknown>;
  const elapsed = c["elapsed_ms"];
  if (elapsed === undefined || elapsed === null) {
    return { pass: false, detail: "no elapsed_ms field on candidate" };
  }
  if ((elapsed as number) > SPEED_GATE_MS) {
    return {
      pass: false,
      detail: `elapsed ${(elapsed as number).toFixed(1)}ms > ${SPEED_GATE_MS}ms speed gate`,
    };
  }
  return {
    pass: true,
    detail: `${(elapsed as number).toFixed(1)}ms ≤ ${SPEED_GATE_MS}ms`,
  };
}

// ─── Wrap helper ──────────────────────────────────────────────────────────────

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
      detail: `tool crashed: ${String(c["name"])}: ${String(c["message"])}`,
    };
    return wrap(checks);
  }
  checks["no_tool_error"] = { pass: true, detail: "tool did not crash" };

  const expectedKind = caseExpected.expected.kind;
  const candKind = (candidate as Record<string, unknown>)["kind"] as string ?? "";

  // ── Refusal-expected case ──────────────────────────────────────────────────
  if (expectedKind === "tagged") {
    checks["boundary_envelope"] = checkBoundaryEnvelope(candidate, caseExpected);
    const spd = checkSpeed(candidate);
    if (spd !== null) checks["speed_gate"] = spd;
    return wrap(checks);
  }

  // ── Value-expected case ────────────────────────────────────────────────────
  // The candidate may be 'symbolic' or 'numerical'. Both are accepted when
  // request_mode = 'auto'; the dispatcher enforces mode constraints itself.

  if (candKind === "tagged") {
    // Unexpected refusal on a value-expected case.
    checks["shape"] = {
      pass: false,
      detail: `expected value (symbolic|numerical) but got tagged refusal ${String((candidate as Record<string, unknown>)["tag"])}`,
    };
    return wrap(checks);
  }

  if (candKind !== "symbolic" && candKind !== "numerical") {
    checks["shape"] = {
      pass: false,
      detail: `expected kind in {symbolic, numerical} but got kind=${JSON.stringify(candKind)}`,
    };
    return wrap(checks);
  }

  const candidateRecord = candidate as Record<string, unknown>;

  // Shape check — different required fields per kind.
  if (candKind === "symbolic") {
    checks["shape"] = checkShapeSymbolic(candidateRecord);
  } else {
    checks["shape"] = checkShapeNumerical(candidateRecord);
  }
  if (!checks["shape"]!.pass) return wrap(checks);

  // Method admissible (both kinds).
  checks["method_admissible"] = checkMethodAdmissible(
    candidateRecord,
    candKind as "numerical" | "symbolic",
  );

  if (candKind === "numerical") {
    // Numerical-specific checks.
    checks["finite_value"] = checkFiniteValue(candidateRecord);
    if (!checks["finite_value"]!.pass) return wrap(checks);

    const requestedPrecision = (inp as { precision?: number })["precision"] ?? 50;
    checks["self_reported_precision"] = checkSelfReportedPrecision(
      candidateRecord,
      requestedPrecision,
    );

    // Value accuracy: only when expected.truth is non-null.
    // Cases with truth_method = "ORACLE-FAILED" have truth = null;
    // the verifier skips value_accuracy for these (per verify.py line 317-319).
    if (caseExpected.expected.truth != null) {
      checks["value_accuracy"] = checkValueAccuracy(candidateRecord, caseExpected);
    }
  } else {
    // Symbolic-specific check: non-empty rule field.
    const ruleId = (candidateRecord["rule"] as string) ?? "";
    if (!ruleId) {
      checks["symbolic_rule_present"] = {
        pass: false,
        detail: "symbolic candidate has empty 'rule' field",
      };
    } else {
      checks["symbolic_rule_present"] = {
        pass: true,
        detail: `rule=${JSON.stringify(ruleId)}`,
      };
    }
  }

  // Speed gate (Tier H, off by default).
  const spd = checkSpeed(candidate);
  if (spd !== null) checks["speed_gate"] = spd;

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
