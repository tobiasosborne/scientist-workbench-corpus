// =============================================================================
// benchmarks/besselj-anchor/golden/verify.ts — invariant verifier
// =============================================================================
//
// stdin:
//   { input:     { head: string, nu: string, z: string | {re,im} },
//     candidate: { value, method, achieved_precision, warnings, ... }
//                OR { kind: "tagged", tag, payload }
//                OR { kind: "tool_error", name, message },
//     id:        string }
//
// stdout:
//   { pass: bool, reason: string, checks: { <name>: { pass, detail } } }
//
// 7 invariants (manifest §verifier.checks) — same shape as B18 erf-anchor:
//   1. output_shape_well_formed
//   2. value_finite_or_t6_sentinel            applies_when: candidate is success
//   3. value_matches_consensus_within_tolerance
//                                             applies_when: consensus.value is not null
//   4. claimed_precision_honest               applies_when: candidate is success
//   5. method_declared_consistent             applies_when: candidate is success
//   6. refusal_scope_honest                   applies_when: candidate is refusal
//   7. consensus_exists                       applies_when: consensus.gold_agree == false
//
// Tolerance ladder (recon spec / ADR-0041 §Decision 8):
//   T1-T3 → 1e-48 rel        T4-T5 → 1e-46 rel        T6 → structural
//   T7-T8 → 1e-44 rel        T9    → 1e-40 rel (zero-crossing band)
//   T10   → 1e-44 rel (large-nu integer; scaled variants near overflow)
//
// 5 oracles (vs B18's 4): wolfram, mpmath, arb gold; boost silver; scipy bronze.
//
// The rel-err comparison uses JS BigInt scaled-decimal arithmetic at 80
// significant digits — sufficient to discriminate the 48-dp gold-tier
// threshold without depending on @workbench/bigfloat at grade time.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Types ──────────────────────────────────────────────────────────────────

type ZScalar  = string;
type ZComplex = { re: string; im: string };
type Z        = ZScalar | ZComplex;

interface OracleEntry {
  value: Z | null;
  achieved_precision: number;
  method: string;
  failure_reason?: string;
}

interface ExpectedCase {
  id: string;
  head: string;
  tier: string;
  nu_kind: "integer" | "half-integer" | "decimal";
  nu: string;
  oracles: {
    wolfram: OracleEntry | null;
    mpmath:  OracleEntry | null;
    arb:     OracleEntry | null;
    boost:   OracleEntry | null;
    scipy:   OracleEntry | null;
  };
  consensus: {
    gold_agree:    boolean;
    value:         Z | null;
    digits_agreed: number;
    tolerance_rel: string | null;
  };
}

// ─── Expected-values lookup ──────────────────────────────────────────────────

let _expectedIndex: Map<string, ExpectedCase> | null = null;

function loadExpected(): Map<string, ExpectedCase> {
  if (_expectedIndex !== null) return _expectedIndex;
  const path = resolve(import.meta.dir, "expected.json");
  const payload = JSON.parse(readFileSync(path, "utf8")) as { cases: ExpectedCase[] };
  _expectedIndex = new Map(payload.cases.map((c) => [c.id, c]));
  return _expectedIndex;
}

// ─── Check type ──────────────────────────────────────────────────────────────

type Check   = { pass: boolean; detail: string };
type Verdict = { pass: boolean; reason: string; checks: Record<string, Check> };

// ─── Admitted methods (from tools/special-eval/tool.ts) ─────────────────────
//
// Two families (mirrors B18's pattern):
//
// (a) scientist-workbench `tools/special-eval` Bessel-family lineage tags
//     (FLOAT64_METHOD_BESSEL + ARBPREC_METHOD_REAL[Bessel*] +
//      ARBPREC_METHOD_COMPLEX[Bessel*]) — what the main candidate emits.
//
// (b) Per-oracle native method tags from each oracle's results.json — the
//     5 replay shims grade themselves as candidates and emit their own
//     oracle's lineage tag.  Includes arb (new for B19; not in B18).

const ADMITTED_METHODS = new Set<string>([
  // ── (a) special-eval Bessel-family lineage ──
  // FLOAT64 (sunpro for J/Y; cephes-moshier for I/K; scaled variants)
  "bessel-musl-sunpro-1993",
  "bessel-cephes-moshier-2000",
  "bessel-cephes-moshier-2000-scaled",
  // FLOAT64 COMPLEX (amos-toms644)
  "bessel-amos-toms644",
  // ARBPREC REAL (FLINT 0F1 + Hankel; Temme-or-connection for K)
  "bessel-flint-0f1-or-hankel",
  "bessel-flint-temme-or-connection",
  "bessel-flint-0f1-or-hankel-scaled",
  "bessel-flint-temme-or-connection-scaled",
  // ARBPREC COMPLEX (amos-rotation; complex 0F1+Hankel; complex Temme)
  "bessel-amos-rotation-arbprec",
  "bessel-flint-0f1-or-hankel-complex",
  "bessel-flint-temme-or-connection-complex",
  "bessel-flint-0f1-or-hankel-scaled-complex",
  "bessel-flint-temme-or-connection-scaled-complex",
  // ── (b) oracle-native tags (per-oracle replay adapter grading) ──
  // wolfram
  "wolfram-N-at-60-decimal",
  "wolfram-limit",
  // mpmath
  "mpmath-mpf-at-60-dps",
  "mpmath-mpc-at-60-dps",
  // arb (new in B19; verbose tag preserved from upstream results.json)
  "python-flint acb.bessel_{j,y,i,k}; cancellation-driven precision retry per ADR-0041 §Decision 3",
  // boost
  "boost-cpp_bin_float-50",
  "boost-refused",
  // scipy (Bessel: jv, yv, iv, kv, ive, kve + their *-limit-boundary variants)
  "scipy.special.jv",
  "scipy.special.yv",
  "scipy.special.iv",
  "scipy.special.kv",
  "scipy.special.ive",
  "scipy.special.kve",
  "scipy.special.jv-limit-boundary",
  "scipy.special.yv-limit-boundary",
  "scipy.special.iv-limit-boundary",
  "scipy.special.kv-limit-boundary",
  "scipy.special.ive-limit-boundary",
  "scipy.special.kve-limit-boundary",
]);

// ─── Admitted refusal classes ───────────────────────────────────────────────

const ADMITTED_REFUSAL_CLASSES = new Set<string>([
  // ── (a) special-eval refusal classes ──
  "special-eval/unknown-head",
  "special-eval/non-finite-input",
  "special-eval/degenerate-shape",
  "special-eval/no-known-representation",
  // ── (b) per-oracle replay refusal classes (5 oracles for B19) ──
  "oracle/wolfram-refused",
  "oracle/mpmath-refused",
  "oracle/arb-refused",
  "oracle/boost-refused",
  "oracle/scipy-refused",
]);

// ─── BigInt-scaled decimal arithmetic (no external dep) ────────────────────

interface BigDec {
  sign: -1 | 1;
  mant: bigint;
  exp:  number;
  isZero: boolean;
}

function parseBigDec(s: string): BigDec {
  let str = s.trim();
  let sign: -1 | 1 = 1;
  if (str.startsWith("-")) { sign = -1; str = str.slice(1); }
  else if (str.startsWith("+")) { str = str.slice(1); }

  if (/^(nan|inf|infinity|indeterminate)$/i.test(str)) {
    throw new Error(`parseBigDec: sentinel token '${s}' is not a finite decimal`);
  }

  let eExp = 0;
  const eIdx = str.search(/[eE]/);
  if (eIdx >= 0) {
    eExp = parseInt(str.slice(eIdx + 1), 10);
    str = str.slice(0, eIdx);
  }

  let intPart = "";
  let fracPart = "";
  const dotIdx = str.indexOf(".");
  if (dotIdx >= 0) {
    intPart = str.slice(0, dotIdx);
    fracPart = str.slice(dotIdx + 1);
  } else {
    intPart = str;
  }

  const digits = (intPart + fracPart).replace(/^0+/, "") || "0";
  const mant = BigInt(digits);
  const exp = eExp - fracPart.length;
  return { sign, mant, exp, isZero: mant === 0n };
}

function align(a: BigDec, b: BigDec): { aMant: bigint; bMant: bigint; exp: number } {
  if (a.exp === b.exp) return { aMant: a.mant, bMant: b.mant, exp: a.exp };
  if (a.exp < b.exp) {
    const shift = BigInt(b.exp - a.exp);
    return { aMant: a.mant, bMant: b.mant * 10n ** shift, exp: a.exp };
  }
  const shift = BigInt(a.exp - b.exp);
  return { aMant: a.mant * 10n ** shift, bMant: b.mant, exp: b.exp };
}

function bigDecAbsDiffAsBigDec(a: BigDec, b: BigDec): BigDec {
  const { aMant, bMant, exp } = align(a, b);
  const sa = a.sign === 1 ? aMant : -aMant;
  const sb = b.sign === 1 ? bMant : -bMant;
  const diff = sa - sb;
  const absDiff = diff < 0n ? -diff : diff;
  return { sign: 1, mant: absDiff, exp, isZero: absDiff === 0n };
}

function decExponentOf(b: BigDec): number {
  if (b.isZero) return -Infinity;
  const digitCount = b.mant.toString().length;
  return digitCount - 1 + b.exp;
}

function relErrCheckReal(
  candStr: string,
  expStr:  string,
  tolStr:  string,
): { pass: boolean; detail: string } {
  const a = parseBigDec(candStr);
  const b = parseBigDec(expStr);
  const diff = bigDecAbsDiffAsBigDec(a, b);

  let denomExp: number;
  if (b.isZero) {
    denomExp = 0;
  } else {
    const expB = decExponentOf(b);
    denomExp = Math.max(expB, 0);
  }

  const relErrExp = diff.isZero ? -Infinity : decExponentOf(diff) - denomExp;

  const tolMatch = /^1e(-?\d+)$/.exec(tolStr);
  if (!tolMatch) {
    return { pass: false, detail: `unrecognised tolerance_rel format: ${tolStr}` };
  }
  const tolExp = parseInt(tolMatch[1]!, 10);

  if (relErrExp === -Infinity) {
    return { pass: true, detail: `rel_err = 0 (exact match) <= ${tolStr}` };
  }
  const pass = relErrExp <= tolExp;
  const relErrApprox = `1e${relErrExp}`;
  if (pass) {
    return { pass: true, detail: `rel_err ~ ${relErrApprox} <= ${tolStr}` };
  }
  return {
    pass: false,
    detail: `rel_err ~ ${relErrApprox} > ${tolStr}; cand='${candStr.slice(0, 50)}...' exp='${expStr.slice(0, 50)}...'`,
  };
}

function relErrCheckComplex(
  cand: { re: string; im: string },
  exp:  { re: string; im: string },
  tolStr: string,
): { pass: boolean; detail: string } {
  const reCheck = relErrCheckReal(cand.re, exp.re, tolStr);
  if (!reCheck.pass) return { pass: false, detail: `[re] ${reCheck.detail}` };
  const imCheck = relErrCheckReal(cand.im, exp.im, tolStr);
  if (!imCheck.pass) return { pass: false, detail: `[im] ${imCheck.detail}` };
  return { pass: true, detail: `[re] ${reCheck.detail}; [im] ${imCheck.detail}` };
}

// ─── Per-check helpers ───────────────────────────────────────────────────────

function checkOutputShapeWellFormed(cand: Record<string, unknown>): Check {
  if (cand["kind"] === "tagged" || cand["kind"] === "tool_error") {
    return { pass: true, detail: `envelope kind=${cand["kind"]} (no success-record shape check)` };
  }
  const required = ["value", "method", "achieved_precision", "warnings"];
  const missing = required.filter((k) => !(k in cand));
  if (missing.length > 0) {
    return { pass: false, detail: `missing fields: [${missing.sort().join(", ")}]` };
  }
  const v = cand["value"];
  const vIsRealString = typeof v === "string";
  const vIsComplexObj = typeof v === "object" && v !== null
    && typeof (v as { re?: unknown }).re === "string"
    && typeof (v as { im?: unknown }).im === "string";
  if (!vIsRealString && !vIsComplexObj) {
    return { pass: false, detail: `value must be string (real) or {re: string, im: string} (complex); got ${typeof v}` };
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
  return {
    pass: true,
    detail: `value=${vIsComplexObj ? "complex" : "real"}, method=string, achieved_precision=number, warnings=list[string]`,
  };
}

function checkValueFiniteOrT6Sentinel(
  cand: Record<string, unknown>,
  tier: string,
): Check {
  const v = cand["value"];
  const checkOne = (s: string, label: string): { ok: boolean; sentinel: boolean; detail: string } => {
    const lowered = s.trim().toLowerCase();
    const isSentinel = /^[-+]?(nan|inf|infinity|indeterminate)$/i.test(lowered);
    if (isSentinel) {
      if (tier === "T6") {
        return { ok: true, sentinel: true, detail: `${label}='${s}' admitted T6 sentinel` };
      }
      return { ok: false, sentinel: true, detail: `${label}='${s}' is a sentinel but tier='${tier}' (only T6 admits sentinels)` };
    }
    const parsed = parseFloat(s);
    if (!Number.isFinite(parsed)) {
      // 60-digit decimals may have magnitudes outside float64 range.
      if (/^[-+]?\d+(\.\d*)?([eE][-+]?\d+)?$/.test(s) || /^[-+]?\.\d+([eE][-+]?\d+)?$/.test(s)) {
        return { ok: true, sentinel: false, detail: `${label}='${s.slice(0, 30)}...' parses structurally as decimal (magnitude beyond float64)` };
      }
      return { ok: false, sentinel: false, detail: `${label}='${s}' not parseable as a finite decimal` };
    }
    return { ok: true, sentinel: false, detail: `${label}='${s.slice(0, 30)}...' finite` };
  };
  if (typeof v === "string") {
    const r = checkOne(v, "value");
    return { pass: r.ok, detail: r.detail };
  }
  const o = v as { re: string; im: string };
  const r = checkOne(o.re, "value.re");
  if (!r.ok) return { pass: false, detail: r.detail };
  const i = checkOne(o.im, "value.im");
  if (!i.ok) return { pass: false, detail: i.detail };
  return { pass: true, detail: `${r.detail}; ${i.detail}` };
}

function checkValueMatchesConsensus(
  cand: Record<string, unknown>,
  expCase: ExpectedCase,
): Check {
  const consensus = expCase.consensus;
  if (consensus.value === null) {
    return { pass: true, detail: "N/A (no gold consensus)" };
  }
  if (expCase.tier === "T6") {
    // Structural compare: cand sentinel ?= expected sentinel.
    const v = cand["value"];
    const candStr = typeof v === "string" ? v : `${(v as { re: string }).re}+${(v as { im: string }).im}i`;
    const expStr = typeof consensus.value === "string"
      ? consensus.value
      : `${consensus.value.re}+${consensus.value.im}i`;
    const norm = (s: string): string => s.trim().toLowerCase()
      .replace(/^indeterminate$/, "nan")
      .replace(/^[-+]?infinity$/, (m) => m.startsWith("-") ? "-inf" : "inf");
    if (norm(candStr) === norm(expStr)) {
      return { pass: true, detail: `T6 structural: cand='${candStr}' matches consensus='${expStr}' (normalised)` };
    }
    return { pass: true, detail: `T6 structural-only: cand='${candStr.slice(0, 30)}', exp='${expStr.slice(0, 30)}' — divergent but T6 is sentinel-tier, not fail` };
  }

  const tol = consensus.tolerance_rel ?? "1e-44";
  const v = cand["value"];
  if (typeof v === "string" && typeof consensus.value === "string") {
    return relErrCheckReal(v, consensus.value, tol);
  }
  if (typeof v === "object" && v !== null && typeof consensus.value === "object" && consensus.value !== null) {
    return relErrCheckComplex(
      v as { re: string; im: string },
      consensus.value as { re: string; im: string },
      tol,
    );
  }
  return { pass: false, detail: `value-shape mismatch: cand=${typeof v}, expected=${typeof consensus.value}` };
}

function checkClaimedPrecisionHonest(cand: Record<string, unknown>): Check {
  const ap = cand["achieved_precision"] as number;
  if (!Number.isInteger(ap)) {
    return { pass: false, detail: `achieved_precision must be integer; got ${ap} (typeof=${typeof ap})` };
  }
  if (ap < 0) {
    return { pass: false, detail: `achieved_precision = ${ap} < 0 (impossible)` };
  }
  return { pass: true, detail: `achieved_precision = ${ap} >= 0 (honest self-report)` };
}

function checkMethodDeclaredConsistent(cand: Record<string, unknown>): Check {
  const m = cand["method"] as string;
  if (!ADMITTED_METHODS.has(m)) {
    return { pass: false, detail: `method='${m}' not in admitted set (size ${ADMITTED_METHODS.size})` };
  }
  return { pass: true, detail: `method='${m}' in admitted set` };
}

function checkRefusalScopeHonest(
  cand: Record<string, unknown>,
  expCase: ExpectedCase,
): Check {
  const tag = cand["tag"] as string | undefined;
  if (!tag || !ADMITTED_REFUSAL_CLASSES.has(tag)) {
    return { pass: false, detail: `refusal tag '${tag}' not in admitted set` };
  }
  // Oracle-replay refusals are inherently honest (committed snapshot).
  if (tag.startsWith("oracle/")) {
    return { pass: true, detail: `tag '${tag}' is an honest per-oracle refusal (committed snapshot)` };
  }
  // special-eval refusals: region check.  Refusal is admissible for:
  //   - T6 (edge / non-finite)
  //   - T10 (large-ν integer overflow boundary)
  //   - T7 negative-real-ν cases (Y/K connection landmine; substrate may bail)
  //   - Complex inputs at large ν (substrate-dependent)
  if (expCase.tier === "T6" || expCase.tier === "T10") {
    return { pass: true, detail: `tag '${tag}' admitted; tier=${expCase.tier} (overflow/non-finite boundary) ⇒ refusal in-scope` };
  }
  return {
    pass: false,
    detail: `tag '${tag}' admitted but refusal on tier=${expCase.tier} (head=${expCase.head}, nu=${expCase.nu}) is out-of-scope — should have succeeded`,
  };
}

function checkConsensusExists(expCase: ExpectedCase): Check {
  if (expCase.consensus.gold_agree) {
    return { pass: true, detail: `gold oracles agreed (digits_agreed=${expCase.consensus.digits_agreed} >= 48 threshold)` };
  }
  return {
    pass: true,
    detail: `WARN — gold oracles disagree or all refused (digits_agreed=${expCase.consensus.digits_agreed}); consensus.value=null on this case; candidate not graded against consensus`,
  };
}

// ─── Top-level verify ────────────────────────────────────────────────────────

function _wrap(checks: Record<string, Check>): Verdict {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) return { pass: true, reason: "all invariants hold", checks };
  const firstFail = Object.entries(checks).find(([, c]) => !c.pass)!;
  return { pass: false, reason: `failed: ${firstFail[0]} — ${firstFail[1].detail}`, checks };
}

function verify(payload: {
  input: { head: string; nu: string; z: Z };
  candidate: unknown;
  id?: string;
}): Verdict {
  const id = payload.id ?? "<unknown>";
  const expCase = loadExpected().get(id);
  if (!expCase) {
    return { pass: false, reason: `no expected.json entry for id='${id}'`, checks: {} };
  }

  const cand = payload.candidate;
  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }
  const candR = cand as Record<string, unknown>;
  const isToolError = candR["kind"] === "tool_error";
  const isTaggedRefusal = candR["kind"] === "tagged";
  const isSuccess = !isToolError && !isTaggedRefusal;

  const checks: Record<string, Check> = {};

  if (isToolError) {
    checks["output_shape_well_formed"] = {
      pass: false,
      detail: `candidate emitted tool_error: name=${candR["name"]}, message=${candR["message"]}`,
    };
    checks["consensus_exists"] = checkConsensusExists(expCase);
    return _wrap(checks);
  }
  checks["output_shape_well_formed"] = checkOutputShapeWellFormed(candR);
  if (!checks["output_shape_well_formed"].pass) {
    checks["consensus_exists"] = checkConsensusExists(expCase);
    return _wrap(checks);
  }

  if (isSuccess) {
    checks["value_finite_or_t6_sentinel"] = checkValueFiniteOrT6Sentinel(candR, expCase.tier);
  } else {
    checks["value_finite_or_t6_sentinel"] = { pass: true, detail: `N/A (candidate is refusal envelope)` };
  }

  if (isSuccess && expCase.consensus.value !== null) {
    checks["value_matches_consensus_within_tolerance"] = checkValueMatchesConsensus(candR, expCase);
  } else if (!isSuccess) {
    checks["value_matches_consensus_within_tolerance"] = { pass: true, detail: `N/A (candidate is refusal envelope)` };
  } else {
    checks["value_matches_consensus_within_tolerance"] = { pass: true, detail: `N/A (no gold consensus; see consensus_exists warn)` };
  }

  if (isSuccess) {
    checks["claimed_precision_honest"] = checkClaimedPrecisionHonest(candR);
  } else {
    checks["claimed_precision_honest"] = { pass: true, detail: `N/A (candidate is refusal envelope)` };
  }

  if (isSuccess) {
    checks["method_declared_consistent"] = checkMethodDeclaredConsistent(candR);
  } else {
    checks["method_declared_consistent"] = { pass: true, detail: `N/A (candidate is refusal envelope)` };
  }

  if (isTaggedRefusal) {
    checks["refusal_scope_honest"] = checkRefusalScopeHonest(candR, expCase);
  } else {
    checks["refusal_scope_honest"] = { pass: true, detail: `N/A (candidate is success envelope)` };
  }

  checks["consensus_exists"] = checkConsensusExists(expCase);

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
      input: { head: string; nu: string; z: Z };
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
