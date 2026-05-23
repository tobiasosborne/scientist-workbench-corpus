// =============================================================================
// benchmarks/gamma-anchor/golden/verify.ts -- invariant verifier
// =============================================================================
//
// stdin:
//   { input:     { head: string, z?: ..., a?: {kind,value}, b?: ..., m?: ..., n?: ..., id: string },
//     candidate: { value, method, achieved_precision, warnings, ... }
//                OR { kind: "tagged", tag, payload }
//                OR { kind: "tool_error", name, message },
//     id:        string }
//
// stdout:
//   { pass: bool, reason: string, checks: { <name>: { pass, detail } } }
//
// 8 invariants (manifest §verifier.checks):
//   1. output_shape_well_formed
//   2. value_finite_or_pole_sentinel       applies_when: candidate is success
//   3. value_matches_consensus_within_tolerance
//                                          applies_when: consensus.value is not null
//   4. claimed_precision_honest            applies_when: candidate is success
//   5. method_declared_consistent          applies_when: candidate is success
//   6. refusal_scope_honest                applies_when: candidate is refusal
//   7. consensus_exists                    applies_when: consensus.gold_agree == false
//   8. landmine_handled_correctly          applies_when: landmine_flags is not empty
//
// Tolerance ladder (ADR-0042 §Decision 8) is per-case in expected.json's
// consensus.tolerance_rel; the verifier never hardcodes tier numbers.
//
// 5 oracles: wolfram, mpmath, arb gold; scipy bronze; boost silver.
//
// Landmine handling is data-driven via expected.json's landmine_flags
// per-case field (G6 / ADR-0042 §L12-L18).  No hardcoded tier/head
// conditionals -- the generator computed the flags from corpus.json's
// notes + per-head per-tier rules and the verifier reads them.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// -- Types --------------------------------------------------------------------

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
  oracles: {
    wolfram: OracleEntry;
    mpmath:  OracleEntry;
    arb:     OracleEntry;
    boost:   OracleEntry;
    scipy:   OracleEntry;
  };
  consensus: {
    gold_agree:    boolean;
    value:         Z | null;
    digits_agreed: number;
    tolerance_rel: string | null;
  };
  landmine_flags: string[];
}

// -- Expected-values lookup ---------------------------------------------------

let _expectedIndex: Map<string, ExpectedCase> | null = null;

function loadExpected(): Map<string, ExpectedCase> {
  if (_expectedIndex !== null) return _expectedIndex;
  const path = resolve(import.meta.dir, "expected.json");
  const payload = JSON.parse(readFileSync(path, "utf8")) as { cases: ExpectedCase[] };
  _expectedIndex = new Map(payload.cases.map((c) => [c.id, c]));
  return _expectedIndex;
}

// -- Check type ---------------------------------------------------------------

type Check   = { pass: boolean; detail: string };
type Verdict = { pass: boolean; reason: string; checks: Record<string, Check> };

// -- Admitted methods (from tools/special-eval/tool.ts) ----------------------
//
// Two families:
// (a) scientist-workbench tools/special-eval Gamma-family lineage tags
//     (FLOAT64_METHOD + FLOAT64_COMPLEX_METHOD + ARBPREC_METHOD_REAL +
//      ARBPREC_METHOD_COMPLEX) -- what the main candidate emits.
// (b) Per-oracle native method tags from each oracle's results.json -- the
//     5 replay shims grade themselves as candidates and emit their own
//     oracle's lineage tag.

const ADMITTED_METHODS = new Set<string>([
  // -- (a) special-eval Gamma-family lineage --
  // FLOAT64 real
  "gamma-cephes-moshier-2000",
  "lgamma-freebsd-sunpro-1993",
  "digamma-boost-2017",
  "polygamma-boost-2017",
  "pochhammer-boost-recurrence",
  "igam-cephes-2000",
  "beta-cephes-2000",
  "logbeta-cephes-2000",
  "barnes-g-adamchik-2007",
  "hyperfactorial-adamchik-2007",
  "gamma-ratio-cephes-via-lgamma",
  "gamma-delta-ratio-cephes-via-lgamma",
  // FLOAT64 complex
  "gamma-scipy-loggamma-pxd",
  "lgamma-scipy-loggamma-pxd",
  "digamma-scipy-loggamma-pxd",
  // ARBPREC real
  "gamma-stirling-recurrence-reflection",
  "lgamma-stirling-recurrence-reflection",
  "digamma-stirling-recurrence-reflection",
  "polygamma-hurwitz-zeta",
  "pochhammer-direct-recurrence-or-gamma-ratio",
  "incgamma-series-or-cf",
  "incgamma-regularised-direct-dispatch",
  "beta-via-gamma",
  "logbeta-via-lgamma",
  "barnes-g-adamchik-via-zeta",
  "hyperfactorial-via-bendersky-barnes-g",
  "gamma-ratio-via-bigfloat-gamma",
  "gamma-delta-ratio-via-bigfloat-gamma",
  // ARBPREC complex (tool.ts ARBPREC_METHOD_COMPLEX for Gamma family)
  "cgamma-stirling-recurrence-reflection",
  "clgamma-stirling-recurrence-reflection",
  "cdigamma-stirling-recurrence-reflection",
  "cpolygamma-hurwitz-zeta-complex",
  "cincgamma-series-or-cf-complex",
  "cbeta-via-cgamma",

  // -- (b) oracle-native tags --
  // wolfram
  "wolfram-N-at-60-decimal",
  "wolfram-limit",
  "wolfram-refused",
  // mpmath
  "mpmath-mpf-at-60-dps",
  "mpmath-mpc-at-60-dps",
  "mpmath-refused",
  "mpmath-unsupported",
  // arb (verbose tag preserved from upstream results.json)
  "python-flint acb.{gamma, lgamma, digamma, polygamma, rising, gamma_upper, gamma_lower, beta_lower, barnes_g, rgamma}; cancellation-driven precision retry per ADR-0042 §Decision 3 (200 → 264 → 328 → 392 → 456 bits)",
  "arb-refused",
  "arb-unsupported",
  // boost
  "boost-cpp_bin_float-50",
  "boost-refused",
  "boost-unsupported",
  // scipy (all distinct method tags from results.json)
  "scipy.special.gamma",
  "scipy.special.gamma-complex",
  "scipy.special.gamma(a)/gamma(b)",
  "scipy.special.loggamma",
  "scipy.special.loggamma-complex",
  "scipy.special.loggamma-via-complex+0j",
  "scipy.special.digamma",
  "scipy.special.digamma-complex",
  "scipy.special.polygamma(1,·)",
  "scipy.special.polygamma(2,·)",
  "scipy.special.polygamma(3,·)",
  "scipy.special.polygamma-complex-refused",
  "scipy.special.poch",
  "scipy.special.poch(b,a-b)=Γ(a)/Γ(b)",
  "1/scipy.special.poch(a,b)=Γ(a)/Γ(a+b)",
  "scipy.special.beta",
  "scipy.special.betaln(log|B|)",
  "scipy.special.betainc(=I_z regularised)",
  "scipy.special.gammainc(=P)",
  "scipy.special.gammaincc(=Q)",
  "scipy.special.gammainc(a,z)·gamma(a)",
  "scipy.special.gammaincc(a,z)·gamma(a)",
  "scipy.special.gammainc-complex-refused",
  "scipy.special.gammaincc-complex-refused",
  "scipy.special.gammaincinv(inverts P)",
  "scipy.special.gammainccinv(inverts Q)",
  "BarnesG-not-in-scipy",
  "DLMF §8.8.2 closed form (exp(-z)·z^(a-1)/Γ(a))",
]);

// -- Admitted refusal classes -------------------------------------------------

const ADMITTED_REFUSAL_CLASSES = new Set<string>([
  // -- (a) special-eval refusal classes --
  "special-eval/unknown-head",
  "special-eval/non-finite-input",
  "special-eval/degenerate-shape",
  "special-eval/no-known-representation",
  // -- (b) per-oracle replay refusal classes --
  "oracle/wolfram-refused",
  "oracle/wolfram-limit",
  "oracle/mpmath-refused",
  "oracle/mpmath-unsupported",
  "oracle/arb-refused",
  "oracle/arb-unsupported",
  "oracle/boost-refused",
  "oracle/boost-unsupported",
  "oracle/scipy-refused",
  "oracle/scipy-unsupported",
]);

// Heads NOT in tools/special-eval ADMITTED_HEADS (4 corpus heads;
// 22 inputs total) -- candidate honestly returns special-eval/unknown-head.
// Verifier admits this class for these heads with no region restriction.
const UNADMITTED_HEADS = new Set<string>([
  "GammaPDerivative",
  "IncompleteBeta",
  "InverseIncompleteGammaP",
  "InverseIncompleteGammaQ",
]);

// -- BigInt-scaled decimal arithmetic (no external dep) ----------------------

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

  if (/^(nan|inf|infinity|indeterminate|complexinfinity)$/i.test(str)) {
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

// -- Per-check helpers --------------------------------------------------------

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

function isSentinelStr(s: string): boolean {
  return /^[-+]?(nan|inf|infinity|indeterminate|complexinfinity)$/i.test(s.trim());
}

function checkValueFiniteOrPoleSentinel(
  cand: Record<string, unknown>,
  expCase: ExpectedCase,
): Check {
  const v = cand["value"];
  const isPole = expCase.landmine_flags.includes("L17");
  const checkOne = (s: string, label: string): { ok: boolean; detail: string } => {
    if (isSentinelStr(s)) {
      if (isPole) {
        return { ok: true, detail: `${label}='${s}' admitted L17 pole sentinel` };
      }
      return { ok: false, detail: `${label}='${s}' is a sentinel but landmine_flags lacks L17` };
    }
    const parsed = parseFloat(s);
    if (!Number.isFinite(parsed)) {
      // 60-digit decimals may have magnitudes outside float64 range.
      if (/^[-+]?\d+(\.\d*)?([eE][-+]?\d+)?$/.test(s) || /^[-+]?\.\d+([eE][-+]?\d+)?$/.test(s)) {
        return { ok: true, detail: `${label}='${s.slice(0, 30)}...' parses structurally as decimal (magnitude beyond float64)` };
      }
      return { ok: false, detail: `${label}='${s}' not parseable as a finite decimal` };
    }
    return { ok: true, detail: `${label}='${s.slice(0, 30)}...' finite` };
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

  const tol = consensus.tolerance_rel ?? "1e-44";
  const v = cand["value"];

  // If value is a sentinel, that's caught by check 2; here we report skip.
  if (typeof v === "string" && isSentinelStr(v)) {
    return { pass: true, detail: `N/A (candidate value is sentinel; see check 2)` };
  }
  if (typeof v === "object" && v !== null) {
    const o = v as { re: string; im: string };
    if (isSentinelStr(o.re) || isSentinelStr(o.im)) {
      return { pass: true, detail: `N/A (candidate value has sentinel component)` };
    }
  }

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
  // Shape mismatch (e.g. candidate real but consensus complex).  When the
  // candidate is a complex extension of a real value (e.g. cgamma on a real
  // input from a tier other than T4), treat as a soft pass with a warning.
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
  // special-eval/unknown-head: admitted for the 4 corpus heads not in tool.ts
  if (tag === "special-eval/unknown-head" && UNADMITTED_HEADS.has(expCase.head)) {
    return { pass: true, detail: `tag '${tag}' admitted; head='${expCase.head}' not in tools/special-eval ADMITTED_HEADS (honest capability gap)` };
  }
  // special-eval/non-finite-input or no-known-representation: admitted on L17 poles
  if ((tag === "special-eval/non-finite-input" || tag === "special-eval/no-known-representation")
      && expCase.landmine_flags.includes("L17")) {
    return { pass: true, detail: `tag '${tag}' admitted; landmine_flags includes L17 (exact pole)` };
  }
  // T7 carve-out: candidate may refuse on Temme transition cases (substrate may bail)
  if (expCase.landmine_flags.includes("T7-carve-out")) {
    return { pass: true, detail: `tag '${tag}' admitted; landmine_flags includes T7-carve-out (Temme transition)` };
  }
  return {
    pass: false,
    detail: `tag '${tag}' admitted-set member but refusal on head='${expCase.head}' tier='${expCase.tier}' landmine_flags=[${expCase.landmine_flags.join(",")}] is out-of-scope`,
  };
}

function checkConsensusExists(expCase: ExpectedCase): Check {
  if (expCase.consensus.gold_agree) {
    return { pass: true, detail: `gold oracles agreed (digits_agreed=${expCase.consensus.digits_agreed} >= 48 threshold)` };
  }
  return {
    pass: true,
    detail: `WARN -- gold oracles disagree or all refused (digits_agreed=${expCase.consensus.digits_agreed}); consensus.value=${expCase.consensus.value === null ? "null" : "non-null"}; landmine_flags=[${expCase.landmine_flags.join(",")}]`,
  };
}

function checkLandmineHandledCorrectly(
  cand: Record<string, unknown>,
  expCase: ExpectedCase,
  otherChecks: Record<string, Check>,
): Check {
  const flags = expCase.landmine_flags;
  if (flags.length === 0) {
    return { pass: true, detail: "N/A (no landmines flagged)" };
  }
  // Assertion: the other checks accommodated the landmine.  In particular,
  // L17 requires either a sentinel admitted by check 2 OR a refusal admitted
  // by check 6.  All other landmines are passively handled (L13/L14/L16/L18
  // affect oracle availability, not candidate behaviour).
  if (flags.includes("L17")) {
    const isRefusal = cand["kind"] === "tagged";
    const isSuccess = !isRefusal && cand["kind"] !== "tool_error";
    if (isRefusal) {
      const refOk = otherChecks["refusal_scope_honest"]?.pass ?? false;
      return refOk
        ? { pass: true, detail: `L17 pole: candidate honestly refused (refusal_scope_honest passed)` }
        : { pass: false, detail: `L17 pole: refusal not in admitted scope (see refusal_scope_honest)` };
    }
    if (isSuccess) {
      const v = cand["value"];
      const sentinel = (typeof v === "string" && isSentinelStr(v))
        || (typeof v === "object" && v !== null
            && (isSentinelStr((v as { re?: string }).re ?? "")
             || isSentinelStr((v as { im?: string }).im ?? "")));
      return sentinel
        ? { pass: true, detail: `L17 pole: candidate emitted sentinel value (admitted by value_finite_or_pole_sentinel)` }
        : { pass: true, detail: `L17 pole: candidate emitted finite value (gold oracles disagreed; this is informational)` };
    }
  }
  // For other flags, just report the flag set; passive checks handle.
  return { pass: true, detail: `landmine_flags=[${flags.join(",")}] handled passively by other checks` };
}

// -- Top-level verify ---------------------------------------------------------

function _wrap(checks: Record<string, Check>): Verdict {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) return { pass: true, reason: "all invariants hold", checks };
  const firstFail = Object.entries(checks).find(([, c]) => !c.pass)!;
  return { pass: false, reason: `failed: ${firstFail[0]} -- ${firstFail[1].detail}`, checks };
}

function verify(payload: {
  input: { head: string; id: string; [k: string]: unknown };
  candidate: unknown;
  id?: string;
}): Verdict {
  const id = payload.id ?? payload.input.id ?? "<unknown>";
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
    checks["value_finite_or_pole_sentinel"] = checkValueFiniteOrPoleSentinel(candR, expCase);
  } else {
    checks["value_finite_or_pole_sentinel"] = { pass: true, detail: `N/A (candidate is refusal envelope)` };
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
  checks["landmine_handled_correctly"] = checkLandmineHandledCorrectly(candR, expCase, checks);

  return _wrap(checks);
}

// -- main ---------------------------------------------------------------------

async function main(): Promise<void> {
  let stdin = "";
  for await (const chunk of Bun.stdin.stream()) {
    stdin += new TextDecoder().decode(chunk);
  }
  let result: Verdict;
  try {
    const payload = JSON.parse(stdin) as {
      input: { head: string; id: string; [k: string]: unknown };
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
