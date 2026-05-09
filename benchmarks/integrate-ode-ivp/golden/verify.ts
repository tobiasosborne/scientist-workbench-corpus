// =============================================================================
// integrate-ode-ivp verifier — invariant-based, language-neutral.
// =============================================================================
//
// stdin:
//   { input:     { f_str, vars, t_var, y0, t_span, options? },
//     candidate: <success record> | { kind:"tagged", tag, payload } |
//                { kind:"tool_error", name, message },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// This is the TypeScript port of the Python verifier in verify.py.
// Tolerances are preserved byte-for-byte per ADR-0028 §4.  Do NOT
// tighten or loosen them during migration — file a separate bead if
// any tolerance turns out to be wrong.
//
// There are 7 named success-path checks plus a boundary check for
// tagged outputs and a category check for mismatched output kinds.
// See verifier_protocol.md for the formal specification.
//
// ─── Reusability note for stiff/symplectic siblings ─────────────────────────
// The following functions are TOOL-AGNOSTIC and should be copied verbatim
// into the stiff and symplectic verifiers:
//   - shapeOfTrajectory(), vecFinite(), supNorm()      — trajectory structure
//   - verifyTaggedDegenerate(), verifyTaggedNonFinite() — boundary helpers
//   - checkShape(), checkFiniteEntries(), checkMonotoneTValues(),
//     checkStatusConsistency()                         — structural checks
//   - _wrap()                                          — output wrapper
//
// The following are TOOL-SPECIFIC to integrate-ode-ivp and require
// per-tool adaptation:
//   - conservedQuantity() with its 4 kinds                — physics is shared
//     with symplectic but stiff may need different kinds
//   - checkTrajectoryAccuracy() tolerance formula         — shared across trio
//   - checkSelfReportedErrorEstimate()                    — shared across trio
//   - checkConservation()                                 — shared across trio
//
// The conservation-kinds supported here (harmonic_energy, kepler_energy,
// kepler_angular_momentum, lotka_volterra_h) are expected to appear in all
// three ODE benches.  The stiff bench may add new kinds; add them to the
// conservedQuantity() function without changing existing cases.

import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";

// ─── Constants (must match verify.py byte-for-byte) ──────────────────────────

const EPS                 = 2.220446049250313e-16;
const SAFETY              = 100.0;
// SELF_REPORT_REL_TOL is defined in verifier_protocol.md as 10.0, but the
// Python verify.py's check_self_reported_error_estimate() implements a much
// simpler structural check (non-negative + bounded above by `max(1, atol*1e6)`
// on success path) rather than the full two-sided honesty bound.  We replicate
// the Python implementation faithfully; the looser structural check is what the
// bench actually enforces today.
const ATOL_DEFAULT        = 1e-6;
const RTOL_DEFAULT        = 1e-3;

// ─── Expected-output index (loaded once from adjacent expected.json) ─────────
//
// The verifier is invoked once per case with stdin = {input, candidate, id}.
// We read expected.json from the directory adjacent to this script, which in
// the corpus is benchmarks/integrate-ode-ivp/golden/.  Bun exposes the
// running script's directory via import.meta.dir.

type ExpectedEntry = {
  kind?: "tagged" | "tool_error";
  tag?: string;
  payload?: unknown;
  expected_class?: string;
  // Success-path fields:
  trajectory?: number[][];
  t_values?: number[];
  rtol?: number;
  atol?: number;
  oracle_source?: string;
  conservation?: ConservationSpec;
  chaotic_until_t?: number;
  traj_tol_factor?: number;
};

type ConservationSpec = {
  kind: string;
  alpha?: number;
  beta?: number;
  gamma?: number;
  delta?: number;
};

let _expectedIndex: Map<string, ExpectedEntry> | null = null;

function loadExpected(): Map<string, ExpectedEntry> {
  if (_expectedIndex !== null) return _expectedIndex;
  const path = resolve(import.meta.dir, "expected.json");
  if (!existsSync(path)) {
    _expectedIndex = new Map();
    return _expectedIndex;
  }
  const raw = JSON.parse(readFileSync(path, "utf8")) as {
    cases: Array<{ id: string; expected: ExpectedEntry }>;
  };
  _expectedIndex = new Map(raw.cases.map((c) => [c.id, c.expected]));
  return _expectedIndex;
}

// ─── Conservation invariants ──────────────────────────────────────────────────
//
// Each supported kind maps a state vector y (array of numbers, same order as
// y0 in the input) to a scalar conserved quantity H(y).
// These match the Python implementations in verify.py exactly.

function conservedQuantity(spec: ConservationSpec, y: number[]): number {
  switch (spec.kind) {
    case "harmonic_energy": {
      // H = (q² + p²)/2; state (q, p) = (y[0], y[1])
      const q = y[0]!;
      const p = y[1]!;
      return 0.5 * (q * q + p * p);
    }
    case "kepler_energy": {
      // H = (px² + py²)/2 − 1/r; state (qx, qy, px, py) = y[0..3]
      const qx = y[0]!, qy = y[1]!, px = y[2]!, py = y[3]!;
      const r = Math.sqrt(qx * qx + qy * qy);
      if (r === 0.0) return Infinity;
      return 0.5 * (px * px + py * py) - 1.0 / r;
    }
    case "kepler_angular_momentum": {
      const qx = y[0]!, qy = y[1]!, px = y[2]!, py = y[3]!;
      return qx * py - qy * px;
    }
    case "lotka_volterra_h": {
      // H(x, y) = δ·x − γ·ln(x) + β·y − α·ln(y)
      const alpha = spec.alpha!;
      const beta  = spec.beta!;
      const gamma = spec.gamma!;
      const delta = spec.delta!;
      const x = y[0]!, yy = y[1]!;
      if (x <= 0.0 || yy <= 0.0) return Infinity;
      return delta * x - gamma * Math.log(x) + beta * yy - alpha * Math.log(yy);
    }
    default:
      throw new Error(`unknown conservation kind: ${JSON.stringify(spec.kind)}`);
  }
}

// ─── Structural helpers ───────────────────────────────────────────────────────
//
// These are reusable across the ODE trio.  Each function is self-contained and
// free of tool-specific assumptions.

/** Returns [n_rows, n_cols] if `v` is a rectangular 2-D list of numbers,
 *  or null if the structure is invalid.  An empty outer list returns [0, 0]. */
function shapeOfTrajectory(v: unknown): [number, number] | null {
  if (!Array.isArray(v)) return null;
  if (v.length === 0) return [0, 0];
  if (!v.every((row) => Array.isArray(row))) return null;
  const nCols = (v[0] as unknown[]).length;
  for (const row of v as unknown[][]) {
    if (row.length !== nCols) return null;
    for (const x of row) if (typeof x !== "number") return null;
  }
  return [v.length, nCols];
}

/** True iff v is a list where every element is a finite number. */
function vecFinite(v: unknown): boolean {
  if (!Array.isArray(v)) return false;
  return (v as unknown[]).every(
    (x) => typeof x === "number" && Number.isFinite(x),
  );
}

/** sup-norm of a numeric array (returns 0 for empty). */
function supNorm(v: number[]): number {
  if (v.length === 0) return 0.0;
  let best = 0.0;
  for (const x of v) {
    const a = Math.abs(x);
    if (a > best) best = a;
  }
  return best;
}

function fmt(x: number): string {
  if (!Number.isFinite(x)) return String(x);
  return x.toExponential(3);
}

// ─── Check types ─────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

// ─── Per-check implementations ────────────────────────────────────────────────

/** check_shape: structural completeness and type validation. */
function checkShape(
  inp: {
    y0: number[];
    options?: { t_eval?: number[] };
  },
  cand: Record<string, unknown>,
): Check {
  const n = inp.y0.length;
  const tEval = inp.options?.t_eval;
  const expectedM = tEval !== undefined ? tEval.length : 2;

  const required = [
    "trajectory", "t_values", "error_estimate",
    "n_evals", "n_steps_accepted", "n_steps_rejected",
    "converged", "status", "method", "warnings",
  ];
  const missing = required.filter((k) => !(k in cand));
  if (missing.length > 0) {
    return { pass: false, detail: `missing fields: [${missing.sort().join(",")}]` };
  }

  const s = shapeOfTrajectory(cand["trajectory"]);
  if (s === null) {
    return { pass: false, detail: "trajectory is not a 2-D list of numbers" };
  }
  if (s[0] !== expectedM || s[1] !== n) {
    return {
      pass: false,
      detail: `trajectory shape [${s[0]},${s[1]}], expected [${expectedM},${n}]`,
    };
  }

  if (!Array.isArray(cand["t_values"])) {
    return { pass: false, detail: "t_values must be a list" };
  }
  const tVals = cand["t_values"] as unknown[];
  if (tVals.length !== expectedM) {
    return {
      pass: false,
      detail: `t_values length ${tVals.length}, expected ${expectedM}`,
    };
  }

  if (typeof cand["method"] !== "string") {
    return { pass: false, detail: "method must be a string" };
  }

  const validStatus = new Set(["success", "max_step_exceeded", "tspan_exhausted", "stiffness-detected"]);
  if (typeof cand["status"] !== "string" || !validStatus.has(cand["status"])) {
    return {
      pass: false,
      detail: `status ${JSON.stringify(cand["status"])} not in {success,max_step_exceeded,tspan_exhausted,stiffness-detected}`,
    };
  }

  const warnings = cand["warnings"];
  if (!Array.isArray(warnings) || !(warnings as unknown[]).every((s) => typeof s === "string")) {
    return { pass: false, detail: "warnings must be list[str]" };
  }

  for (const f of ["n_evals", "n_steps_accepted", "n_steps_rejected"] as const) {
    if (typeof cand[f] !== "number" || !Number.isInteger(cand[f])) {
      return { pass: false, detail: `${f} must be an integer` };
    }
  }

  if (typeof cand["converged"] !== "boolean") {
    return { pass: false, detail: "converged must be a bool" };
  }

  return {
    pass: true,
    detail: `trajectory [${s[0]},${s[1]}], t_values length ${expectedM}`,
  };
}

/** check_finite_entries: no NaN/Inf in trajectory or error_estimate. */
function checkFiniteEntries(cand: Record<string, unknown>): Check {
  const traj = cand["trajectory"] as number[][];
  for (let i = 0; i < traj.length; i++) {
    if (!vecFinite(traj[i]!)) {
      return { pass: false, detail: `non-finite entry in trajectory row ${i}` };
    }
  }
  const ee = cand["error_estimate"];
  if (typeof ee !== "number" || !Number.isFinite(ee)) {
    return { pass: false, detail: "error_estimate non-finite" };
  }
  return { pass: true, detail: "all entries finite" };
}

/** check_monotone_t_values: strictly monotone in the integration direction;
 *  if t_eval provided, must match element-wise. */
function checkMonotoneTValues(
  inp: {
    t_span: { t0: number; tf: number };
    options?: { t_eval?: number[] };
  },
  cand: Record<string, unknown>,
): Check {
  const t0 = inp.t_span.t0;
  const tf = inp.t_span.tf;
  const tVals = cand["t_values"] as number[];
  const tEval = inp.options?.t_eval;

  if (tEval !== undefined) {
    // Element-wise match against input t_eval.
    if (tEval.length !== tVals.length) {
      return {
        pass: false,
        detail: `t_values length ${tVals.length} != options.t_eval length ${tEval.length}`,
      };
    }
    for (let i = 0; i < tEval.length; i++) {
      const a = tEval[i]!, b = tVals[i]!;
      if (Math.abs(a - b) > 1e-12 * Math.max(Math.abs(a), 1.0)) {
        return {
          pass: false,
          detail: `t_values[${i}]=${b} differs from options.t_eval[${i}]=${a}`,
        };
      }
    }
  }

  const forward = tf >= t0;
  if (forward) {
    for (let i = 0; i < tVals.length - 1; i++) {
      if (tVals[i]! > tVals[i + 1]! + 1e-12) {
        return {
          pass: false,
          detail: `forward integration but t_values[${i}]=${tVals[i]} > t_values[${i + 1}]=${tVals[i + 1]}`,
        };
      }
    }
  } else {
    for (let i = 0; i < tVals.length - 1; i++) {
      if (tVals[i]! < tVals[i + 1]! - 1e-12) {
        return {
          pass: false,
          detail: `reverse integration but t_values[${i}]=${tVals[i]} < t_values[${i + 1}]=${tVals[i + 1]}`,
        };
      }
    }
  }
  return {
    pass: true,
    detail: `t_values monotone over ${tVals.length} points`,
  };
}

/** check_status_consistency: converged iff status==="success"; counters sane. */
function checkStatusConsistency(cand: Record<string, unknown>): Check {
  const converged = cand["converged"] as boolean;
  const status    = cand["status"] as string;
  if (converged !== (status === "success")) {
    return {
      pass: false,
      detail: `converged=${converged} but status=${JSON.stringify(status)}`,
    };
  }
  const nEvals = cand["n_evals"] as number;
  const nAcc   = cand["n_steps_accepted"] as number;
  const nRej   = cand["n_steps_rejected"] as number;
  if (nAcc < 0 || nEvals < 0 || nRej < 0) {
    return {
      pass: false,
      detail: `negative counter: n_evals=${nEvals}, n_steps_accepted=${nAcc}`,
    };
  }
  if (nAcc > 0 && nEvals < nAcc) {
    return {
      pass: false,
      detail: `n_evals=${nEvals} < n_steps_accepted=${nAcc} (must be ≥ 1 eval per step)`,
    };
  }
  return {
    pass: true,
    detail: `converged=${converged}, status=${JSON.stringify(status)}, n_evals/n_acc=${nEvals}/${nAcc}`,
  };
}

/** check_trajectory_accuracy: per-timestep sup-norm error vs reference,
 *  with HNW §II.10 tolerance + 100× safety factor.
 *
 *  For chaotic problems (chaotic_until_t in expected.json), pointwise
 *  checks are skipped beyond the Lyapunov time; attractor statistics
 *  (mean, std per component) are checked instead with 0.5 relative tol.
 *
 *  The horizon_factor `max(|tf-t0|/10, 1)` bakes in HNW's observation
 *  that global error scales linearly with integration length for stable
 *  adaptive RK.  The per-case traj_tol_factor (default 100) allows
 *  known-hard problems (Kepler long-horizon) to carry extra slack.
 */
function checkTrajectoryAccuracy(
  inp: {
    t_span: { t0: number; tf: number };
    options?: { rtol?: number; atol?: number };
  },
  cand: Record<string, unknown>,
  expected: ExpectedEntry,
): Check {
  const rtol = expected.rtol ?? inp.options?.rtol ?? RTOL_DEFAULT;
  const atol = expected.atol ?? inp.options?.atol ?? ATOL_DEFAULT;
  const trajC = cand["trajectory"] as number[][];
  const tVals = cand["t_values"] as number[];
  const trajR = expected.trajectory!;

  if (trajC.length !== trajR.length) {
    return {
      pass: false,
      detail: `trajectory length ${trajC.length} != reference ${trajR.length}`,
    };
  }

  const chaoticUntil = expected.chaotic_until_t;
  const t0 = inp.t_span.t0;
  const tf = inp.t_span.tf;
  const horizonFactor = Math.max(Math.abs(tf - t0) / 10.0, 1.0);
  // Per-case override (e.g. D_kepler_long uses 250 instead of 100).
  const safety = expected.traj_tol_factor ?? SAFETY;

  let worstIdx = -1;
  let worstErr = 0.0;
  let worstTol = 0.0;

  for (let i = 0; i < trajC.length; i++) {
    const t_i = tVals[i]!;
    if (chaoticUntil !== undefined && t_i > chaoticUntil) {
      // Skip pointwise check beyond Lyapunov time.
      continue;
    }
    const ref  = trajR[i]!;
    const cndV = trajC[i]!;
    // sup-norm of (candidate - reference)
    let err = 0.0;
    for (let j = 0; j < ref.length; j++) {
      const d = Math.abs(cndV[j]! - ref[j]!);
      if (d > err) err = d;
    }
    const refNorm = supNorm(ref);
    const tol = Math.max(
      safety * rtol * refNorm * horizonFactor,
      safety * atol * horizonFactor,
    );
    if (err > tol) {
      // Keep the worst relative violation.
      const rel = err / Math.max(tol, EPS);
      const worstRel = worstErr / Math.max(worstTol, EPS);
      if (rel > worstRel || worstIdx < 0) {
        worstIdx = i;
        worstErr = err;
        worstTol = tol;
      }
    }
  }

  if (worstIdx >= 0) {
    return {
      pass: false,
      detail:
        `trajectory[${worstIdx}] sup-norm error ${fmt(worstErr)} > tol ${fmt(worstTol)}` +
        ` (t=${tVals[worstIdx]!.toFixed(4)}, rtol=${rtol}, atol=${atol})`,
    };
  }

  // Beyond chaotic time: attractor-statistics check (mean + std per component).
  if (chaoticUntil !== undefined) {
    const postC: number[][] = [];
    const postR: number[][] = [];
    for (let i = 0; i < trajC.length; i++) {
      if (tVals[i]! > chaoticUntil) {
        postC.push(trajC[i]!);
        postR.push(trajR[i]!);
      }
    }
    if (postC.length > 0) {
      const nComp = postC[0]!.length;
      const statTol = 0.5; // 50% relative — generous for short attractor windows
      for (let j = 0; j < nComp; j++) {
        const cVals = postC.map((row) => row[j]!);
        const rVals = postR.map((row) => row[j]!);
        const meanC = cVals.reduce((a, b) => a + b, 0) / cVals.length;
        const meanR = rVals.reduce((a, b) => a + b, 0) / rVals.length;
        const meanDiff = Math.abs(meanC - meanR) / Math.max(Math.abs(meanR), 1.0);
        if (meanDiff > statTol) {
          return {
            pass: false,
            detail: `chaotic-window mean_rel_diff[${j}]=${fmt(meanDiff)} > tol=${statTol}`,
          };
        }
        const stdC = Math.sqrt(cVals.map((x) => (x - meanC) ** 2).reduce((a, b) => a + b, 0) / cVals.length);
        const stdR = Math.sqrt(rVals.map((x) => (x - meanR) ** 2).reduce((a, b) => a + b, 0) / rVals.length);
        const stdDiff = Math.abs(stdC - stdR) / Math.max(Math.abs(stdR), 1.0);
        if (stdDiff > statTol) {
          return {
            pass: false,
            detail: `chaotic-window std_rel_diff[${j}]=${fmt(stdDiff)} > tol=${statTol}`,
          };
        }
      }
    }
  }

  return {
    pass: true,
    detail: `trajectory matches reference to tol within ${trajC.length} points`,
  };
}

/** check_self_reported_error_estimate: structural sanity on error_estimate.
 *
 *  The full two-sided honesty bound from verifier_protocol.md is deferred to
 *  a v0.2 bench.  Today we replicate what verify.py actually enforces: the
 *  field must be non-negative, and if status==="success" it must be ≤
 *  max(1.0, atol * 1e6) (a generous structural bound, not a tight accuracy
 *  claim).  This matches the comment in verify.py exactly.
 */
function checkSelfReportedErrorEstimate(
  inp: { options?: { atol?: number } },
  cand: Record<string, unknown>,
): Check {
  const reported = cand["error_estimate"] as number;
  if (reported < 0) {
    return { pass: false, detail: `error_estimate=${fmt(reported)} is negative` };
  }
  const status = cand["status"] as string;
  if (status === "success") {
    const atol = inp.options?.atol ?? ATOL_DEFAULT;
    const bound = Math.max(1.0, atol * 1e6);
    if (reported > bound) {
      return {
        pass: false,
        detail: `error_estimate=${fmt(reported)} > ${fmt(bound)} despite status=success`,
      };
    }
  }
  return { pass: true, detail: `error_estimate=${fmt(reported)} (status=${JSON.stringify(status)})` };
}

/** check_conservation: drift in conserved quantity H(y) over the trajectory.
 *
 *  Tolerance: SAFETY · rtol · max(|tf-t0|, 1.0) (relative to H(y[0])).
 *  Skipped if expected.conservation is absent.
 */
function checkConservation(
  inp: {
    t_span: { t0: number; tf: number };
    options?: { rtol?: number; atol?: number };
  },
  cand: Record<string, unknown>,
  expected: ExpectedEntry,
): Check {
  const spec = expected.conservation;
  if (spec === undefined) {
    return { pass: true, detail: "no conservation invariant for this case" };
  }

  const rtol    = expected.rtol ?? inp.options?.rtol ?? RTOL_DEFAULT;
  const atol    = expected.atol ?? inp.options?.atol ?? ATOL_DEFAULT;
  const t0      = inp.t_span.t0;
  const tf      = inp.t_span.tf;
  const horizon = Math.abs(tf - t0);
  const traj    = cand["trajectory"] as number[][];

  if (traj.length === 0) {
    return { pass: true, detail: "empty trajectory" };
  }

  const H0 = conservedQuantity(spec, traj[0]!);
  if (!Number.isFinite(H0)) {
    return { pass: false, detail: `H0 = ${H0} (non-finite)` };
  }
  const denom = Math.max(Math.abs(H0), atol);
  const tol   = SAFETY * rtol * Math.max(horizon, 1.0);

  let worst   = 0.0;
  let worstI  = 0;
  for (let i = 1; i < traj.length; i++) {
    const H = conservedQuantity(spec, traj[i]!);
    if (!Number.isFinite(H)) {
      return {
        pass: false,
        detail: `H(trajectory[${i}]) = ${H} (non-finite)`,
      };
    }
    const rel = Math.abs(H - H0) / denom;
    if (rel > worst) { worst = rel; worstI = i; }
  }
  if (worst > tol) {
    return {
      pass: false,
      detail:
        `conservation drift max ${fmt(worst)} at i=${worstI} > tol ${fmt(tol)}` +
        ` (${spec.kind}, 100·rtol·|tf-t0| with rtol=${rtol}, tf-t0=${horizon})`,
    };
  }
  return {
    pass: true,
    detail: `max conservation drift ${fmt(worst)} ≤ tol ${fmt(tol)} (${spec.kind})`,
  };
}

// ─── Tagged-boundary checks ───────────────────────────────────────────────────
//
// Two boundary classes for integrate-ode-ivp:
//   1. degenerate-tspan  — t0 == tf at input time; payload {t0, tf}.
//   2. non-finite-during-eval — RHS went non-finite during integration;
//                               payload {at_t, at_y, kind?}.
// The verifier checks payload shape only (see verifier_protocol.md §tagged).

function verifyTaggedDegenerate(
  inp: { t_span: { t0: number; tf: number } },
  cand: { tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const t0 = inp.t_span.t0;
  const tf = inp.t_span.tf;
  if (t0 !== tf) {
    return {
      pass: false,
      reason: "degenerate-tspan tagged but t0 != tf",
      checks: { boundary: { pass: false, detail: `t0=${t0}, tf=${tf}` } },
    };
  }
  const p = cand.payload;
  if (typeof p !== "object" || p === null ||
      !("t0" in (p as object)) || !("tf" in (p as object))) {
    return {
      pass: false,
      reason: "degenerate-tspan payload missing t0/tf",
      checks: { boundary: { pass: false, detail: String(p) } },
    };
  }
  return {
    pass: true,
    reason: "degenerate-tspan correctly tagged",
    checks: { boundary: { pass: true, detail: cand.tag } },
  };
}

function verifyTaggedNonFinite(
  cand: { tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const p = cand.payload;
  if (typeof p !== "object" || p === null) {
    return {
      pass: false,
      reason: "non-finite-during-eval payload not a dict",
      checks: { boundary: { pass: false, detail: String(p) } },
    };
  }
  for (const req of ["at_t", "at_y"] as const) {
    if (!(req in (p as object))) {
      return {
        pass: false,
        reason: `non-finite-during-eval payload missing ${req}`,
        checks: { boundary: { pass: false, detail: String(p) } },
      };
    }
  }
  const pp = p as Record<string, unknown>;
  if (typeof pp["at_t"] !== "number") {
    return {
      pass: false,
      reason: "non-finite-during-eval payload.at_t must be a number",
      checks: { boundary: { pass: false, detail: String(p) } },
    };
  }
  if (!Array.isArray(pp["at_y"])) {
    return {
      pass: false,
      reason: "non-finite-during-eval payload.at_y must be a list",
      checks: { boundary: { pass: false, detail: String(p) } },
    };
  }
  return {
    pass: true,
    reason: "non-finite-during-eval correctly tagged",
    checks: { boundary: { pass: true, detail: cand.tag } },
  };
}

function verifyTagged(
  inp: { t_span: { t0: number; tf: number } },
  cand: { kind: "tagged"; tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  if (cand.tag === "integrate-ode-ivp/degenerate-tspan") {
    return verifyTaggedDegenerate(inp, cand);
  }
  if (cand.tag === "integrate-ode-ivp/non-finite-during-eval") {
    return verifyTaggedNonFinite(cand);
  }
  return {
    pass: false,
    reason: `unknown tag ${JSON.stringify(cand.tag)}`,
    checks: { boundary: { pass: false, detail: cand.tag } },
  };
}

// ─── Output wrapper ───────────────────────────────────────────────────────────

function _wrap(
  checks: Record<string, Check>,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) return { pass: true, reason: "all invariants hold", checks };
  const firstFail = Object.entries(checks).find(([, c]) => !c.pass)!;
  return {
    pass: false,
    reason: `failed: ${firstFail[0]} — ${firstFail[1].detail}`,
    checks,
  };
}

// ─── Top-level verify ─────────────────────────────────────────────────────────

type Payload = {
  input: {
    f_str?: string[];
    vars?: string[];
    t_var?: string;
    y0: number[];
    t_span: { t0: number; tf: number };
    options?: { rtol?: number; atol?: number; t_eval?: number[] };
  };
  candidate: unknown;
  id?: string;
};

function verify(
  payload: Payload,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const inp = payload.input;
  const cand = payload.candidate;
  const caseId = payload.id ?? "?";

  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }

  const expectedIndex = loadExpected();
  const expected = expectedIndex.get(caseId) ?? {};

  const candR = cand as Record<string, unknown>;
  const candKind = candR["kind"] as string | undefined;

  // ── Tagged boundary path ─────────────────────────────────────────────────
  if (candKind === "tagged") {
    if (expected.kind !== "tagged") {
      return {
        pass: false,
        reason: `candidate emitted tagged but expected was ${JSON.stringify(expected.kind ?? "success")}`,
        checks: { category: { pass: false, detail: "category mismatch" } },
      };
    }
    const candTag = candR["tag"] as string;
    if (candTag !== expected.tag) {
      return {
        pass: false,
        reason: `tag mismatch: candidate=${JSON.stringify(candTag)}, expected=${JSON.stringify(expected.tag)}`,
        checks: { category: { pass: false, detail: "tag mismatch" } },
      };
    }
    return verifyTagged(inp, cand as { kind: "tagged"; tag: string; payload: unknown });
  }

  // ── Tool-error path ──────────────────────────────────────────────────────
  if (candKind === "tool_error") {
    if (expected.kind !== "tool_error") {
      return {
        pass: false,
        reason: `candidate emitted tool_error but expected was ${JSON.stringify(expected.kind ?? "success")}`,
        checks: { category: { pass: false, detail: "category mismatch" } },
      };
    }
    return {
      pass: true,
      reason: "tool_error correctly emitted",
      checks: {
        tool_error_expected: {
          pass: true,
          detail: `name=${JSON.stringify(candR["name"] ?? "?")}`,
        },
      },
    };
  }

  // ── Success path ─────────────────────────────────────────────────────────
  // Reject if expected is a tagged or tool_error category.
  if (expected.kind === "tagged" || expected.kind === "tool_error") {
    return {
      pass: false,
      reason: `candidate emitted success record but expected was ${JSON.stringify(expected.kind)}`,
      checks: { category: { pass: false, detail: "category mismatch" } },
    };
  }
  if (Object.keys(expected).length === 0) {
    return {
      pass: false,
      reason: `no expected entry for id=${JSON.stringify(caseId)}`,
      checks: {},
    };
  }

  const checks: Record<string, Check> = {};

  // 1. shape — must come first; downstream checks dereference cand fields.
  checks["shape"] = checkShape(inp, candR);
  if (!checks["shape"].pass) return _wrap(checks);

  // 2. finite_entries — must come before any numerical checks.
  checks["finite_entries"] = checkFiniteEntries(candR);
  if (!checks["finite_entries"].pass) return _wrap(checks);

  // 3. monotone_t_values
  checks["monotone_t_values"] = checkMonotoneTValues(inp, candR);

  // 4. status_consistency
  checks["status_consistency"] = checkStatusConsistency(candR);

  // 5. trajectory_accuracy
  checks["trajectory_accuracy"] = checkTrajectoryAccuracy(inp, candR, expected);

  // 6. self_reported_error_estimate
  checks["self_reported_error_estimate"] = checkSelfReportedErrorEstimate(inp, candR);

  // 7. conservation (only if expected.json carries a conservation spec)
  checks["conservation"] = checkConservation(inp, candR, expected);

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
    const payload = JSON.parse(stdin) as Payload;
    result = verify(payload);
  } catch (e) {
    const err = e as Error;
    process.stderr.write(`${err.stack ?? err.message}\n`);
    result = {
      pass: false,
      reason: `verifier crashed: ${err.name}: ${err.message}`,
      checks: {},
    };
  }
  process.stdout.write(JSON.stringify(result) + "\n");
}

await main();
