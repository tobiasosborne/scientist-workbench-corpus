// =============================================================================
// benchmarks/special-eval-smoke/golden/verify.ts — invariant verifier
// =============================================================================
//
// stdin:
//   { input:     { head: string, args: number[] },
//     candidate: { value, method, achieved_precision, warnings, ... }
//                OR { kind: "tagged", tag, payload }
//                OR { kind: "tool_error", name, message },
//     id:        string }
//
// stdout:
//   { pass: bool, reason: string, checks: { <name>: { pass, detail } } }
//
// 6 invariants (manifest §verifier.checks):
//   1. output_shape_well_formed       — required fields, well-typed
//   2. value_finite                   — value parses as finite decimal
//   3. value_matches_oracle_within_tolerance
//                                      — |cand - expected| ≤ tol_rel · |expected|
//                                        (abs fallback when expected = 0)
//   4. claimed_precision_honest       — achieved_precision == 53 (float64 lane)
//   5. method_declared_consistent     — method ∈ ADMITTED_METHODS
//   6. warnings_well_typed            — list[string]
//
// Tolerances: 1e-12 relative or absolute; aligns with float64 ULP × ~4 dp
// safety (Higham 2002 §1.2: float64 ULP ≈ 1.1e-16; the 4-dp pad covers
// algorithm-port residuals like Sun-Pro / Cephes / AMOS rounding).
//
// References: lp-netlib/golden/verify.ts (small-bench invariant structure);
//   hypergeometric-pfq/golden/verify.ts (per-case expected.json lookup);
//   special-eval/tool.ts (admitted method strings).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Tolerances (matched to manifest) ────────────────────────────────────────

const TOL_REL = 1e-12;
const TOL_ABS = 1e-12;

// Admitted method tags drawn from tools/special-eval/tool.ts
// (FLOAT64_METHOD + ARBPREC_METHOD_REAL).  The smoke runs the float64
// lane, so FLOAT64_METHOD is the active set; we also accept arb-prec
// method tags so the verifier doesn't drift if the run-candidate lifts
// to arb-prec for the InverseErf / InverseErfc cases in a future bead.
const ADMITTED_METHODS = new Set<string>([
  // FLOAT64_METHOD (tool.ts:342-355)
  "erf-sunpro-1993",
  "erf-blair-1976-inverse",
  "bessel-musl-sunpro-1993",
  "bessel-cephes-moshier-2000",
  "bessel-cephes-moshier-2000-scaled",
  // ARBPREC_METHOD_REAL (tool.ts:372-389) — for forward-compat
  "erf-borel-series-or-asymptotic",
  "erf-karbach-weideman",
  "bessel-flint-0f1-or-hankel",
  "bessel-flint-temme-or-connection",
  "bessel-flint-0f1-or-hankel-scaled",
  "bessel-flint-temme-or-connection-scaled",
]);

// ─── Expected-values lookup ──────────────────────────────────────────────────

interface ExpectedCase {
  id: string;
  expected_value: string; // decimal string
  source: string;
}

let _expectedIndex: Map<string, ExpectedCase> | null = null;

function loadExpected(): Map<string, ExpectedCase> {
  if (_expectedIndex !== null) return _expectedIndex;
  const path = resolve(import.meta.dir, "expected.json");
  const payload = JSON.parse(readFileSync(path, "utf8")) as { cases: ExpectedCase[] };
  _expectedIndex = new Map(payload.cases.map((c) => [c.id, c]));
  return _expectedIndex;
}

// ─── Check type ──────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };
type Verdict = { pass: boolean; reason: string; checks: Record<string, Check> };

// ─── Per-check helpers ───────────────────────────────────────────────────────

function checkOutputShapeWellFormed(cand: Record<string, unknown>): Check {
  const required = ["value", "method", "achieved_precision", "warnings"];
  const missing = required.filter((k) => !(k in cand));
  if (missing.length > 0) {
    return { pass: false, detail: `missing fields: [${missing.sort().join(", ")}]` };
  }
  if (typeof cand["value"] !== "string") {
    return { pass: false, detail: `value must be string (decimal); got ${typeof cand["value"]}` };
  }
  if (typeof cand["method"] !== "string") {
    return { pass: false, detail: `method must be string; got ${typeof cand["method"]}` };
  }
  if (typeof cand["achieved_precision"] !== "number") {
    return { pass: false, detail: `achieved_precision must be number; got ${typeof cand["achieved_precision"]}` };
  }
  const w = cand["warnings"];
  if (!Array.isArray(w) || !w.every((s) => typeof s === "string")) {
    return { pass: false, detail: "warnings must be list[string]" };
  }
  return { pass: true, detail: `4 required fields present, well-typed (value=string, method=string, achieved_precision=number, warnings=list[string])` };
}

function checkValueFinite(cand: Record<string, unknown>): Check {
  const s = cand["value"] as string;
  // parseFloat handles standard decimal + scientific notation.  Reject
  // any non-finite (NaN, Infinity, -Infinity) literals or unparseable
  // strings.
  const v = parseFloat(s);
  if (!Number.isFinite(v)) {
    return { pass: false, detail: `value='${s}' parses as ${v} (non-finite)` };
  }
  // Also catch the "Infinity" / "NaN" literal forms that parseFloat
  // happens to handle (some).  Belt-and-braces.
  const lowered = s.trim().toLowerCase();
  if (lowered.includes("nan") || lowered.includes("inf")) {
    return { pass: false, detail: `value='${s}' contains non-finite literal token` };
  }
  return { pass: true, detail: `value='${s}' parses to ${v} (finite)` };
}

function checkValueMatchesOracle(cand: Record<string, unknown>, id: string): Check {
  const expected = loadExpected().get(id);
  if (!expected) {
    return { pass: false, detail: `no expected_value for case id='${id}' in expected.json` };
  }
  const candVal = parseFloat(cand["value"] as string);
  const expVal = parseFloat(expected.expected_value);
  if (!Number.isFinite(expVal)) {
    return { pass: false, detail: `expected_value='${expected.expected_value}' not finite — corpus bug` };
  }
  const absExp = Math.abs(expVal);
  const absDiff = Math.abs(candVal - expVal);
  if (absExp < 1) {
    // Use absolute tolerance for near-zero expecteds (Erf(0), Erfi(0),
    // InverseErf(0), InverseErfc(1) all expect exactly 0).
    if (absDiff > TOL_ABS) {
      return { pass: false, detail: `|cand - exp| = ${absDiff.toExponential(3)} > abs_tol = ${TOL_ABS.toExponential(0)}; cand=${candVal}, exp=${expVal}` };
    }
    return { pass: true, detail: `abs_err = ${absDiff.toExponential(3)} ≤ ${TOL_ABS.toExponential(0)} (abs path); cand=${candVal}, exp=${expVal}` };
  }
  const relErr = absDiff / absExp;
  if (relErr > TOL_REL) {
    return { pass: false, detail: `rel_err = ${relErr.toExponential(3)} > rel_tol = ${TOL_REL.toExponential(0)}; cand=${candVal}, exp=${expVal}` };
  }
  return { pass: true, detail: `rel_err = ${relErr.toExponential(3)} ≤ ${TOL_REL.toExponential(0)}; cand=${candVal}, exp=${expVal} (${expected.source.slice(0, 60)}...)` };
}

function checkClaimedPrecisionHonest(cand: Record<string, unknown>): Check {
  const ap = cand["achieved_precision"] as number;
  // Float64 lane convention (tool.ts:684, :686, :918): every realSuccess
  // wraps the float64 result in a 53-bit BigFloat and declares
  // achieved_precision = 53.
  if (ap !== 53) {
    return { pass: false, detail: `expected achieved_precision = 53 (float64 lane); got ${ap}` };
  }
  return { pass: true, detail: `achieved_precision = 53 (float64 lane, 53-bit BigFloat wrap per realSuccess)` };
}

function checkMethodDeclaredConsistent(cand: Record<string, unknown>): Check {
  const m = cand["method"] as string;
  if (!ADMITTED_METHODS.has(m)) {
    return { pass: false, detail: `method='${m}' not in admitted set (size ${ADMITTED_METHODS.size})` };
  }
  return { pass: true, detail: `method='${m}' ∈ admitted set` };
}

function checkWarningsWellTyped(cand: Record<string, unknown>): Check {
  // Already checked structurally in checkOutputShapeWellFormed; this
  // check restates the invariant for trace clarity and inspects the
  // content.  Smoke cases are well-conditioned so an empty list is
  // expected; we don't fail on non-empty warnings (saturation /
  // anomaly surfacing per ADR-0007) but we note them.
  const w = cand["warnings"] as string[];
  if (w.length === 0) {
    return { pass: true, detail: "warnings = [] (clean float64 path)" };
  }
  return { pass: true, detail: `warnings = [${w.length} entry(ies)]: ${w.map((s) => JSON.stringify(s)).join(", ").slice(0, 200)}` };
}

// ─── Top-level verify ────────────────────────────────────────────────────────

function _wrap(checks: Record<string, Check>): Verdict {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) return { pass: true, reason: "all invariants hold", checks };
  const firstFail = Object.entries(checks).find(([, c]) => !c.pass)!;
  return { pass: false, reason: `failed: ${firstFail[0]} — ${firstFail[1].detail}`, checks };
}

function verify(payload: {
  input: { head: string; args: number[] };
  candidate: unknown;
  id?: string;
}): Verdict {
  const cand = payload.candidate;
  const id = payload.id ?? "<unknown>";

  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }

  const candR = cand as Record<string, unknown>;

  // Reject tool_error / tagged-refusal envelopes up front — the smoke
  // exercises only success paths.  A tagged refusal at any of the 10
  // smoke heads at float64 lane indicates a real bug (e.g. complex
  // InverseErf refusing — but no smoke case feeds complex inputs).
  if (candR["kind"] === "tool_error") {
    return {
      pass: false,
      reason: `candidate emitted tool_error: ${candR["name"]} — ${candR["message"]}`,
      checks: {
        output_shape_well_formed: {
          pass: false,
          detail: `tool_error envelope: name=${candR["name"]}, message=${candR["message"]}`,
        },
      },
    };
  }
  if (candR["kind"] === "tagged") {
    return {
      pass: false,
      reason: `candidate emitted tagged refusal: ${candR["tag"]}`,
      checks: {
        output_shape_well_formed: {
          pass: false,
          detail: `tagged-refusal envelope: tag=${candR["tag"]}, payload=${JSON.stringify(candR["payload"])}`,
        },
      },
    };
  }

  const checks: Record<string, Check> = {};

  checks["output_shape_well_formed"] = checkOutputShapeWellFormed(candR);
  if (!checks["output_shape_well_formed"].pass) return _wrap(checks);

  checks["value_finite"] = checkValueFinite(candR);
  checks["value_matches_oracle_within_tolerance"] = checkValueMatchesOracle(candR, id);
  checks["claimed_precision_honest"] = checkClaimedPrecisionHonest(candR);
  checks["method_declared_consistent"] = checkMethodDeclaredConsistent(candR);
  checks["warnings_well_typed"] = checkWarningsWellTyped(candR);

  return _wrap(checks);
}

// ─── main ────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  let stdin = "";
  for await (const chunk of Bun.stdin.stream()) {
    stdin += new TextDecoder().decode(chunk);
  }
  let result: Verdict;
  try {
    const payload = JSON.parse(stdin) as {
      input: { head: string; args: number[] };
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
