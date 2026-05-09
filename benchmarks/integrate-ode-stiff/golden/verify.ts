// =============================================================================
// integrate-ode-stiff verifier — invariant-based, language-neutral.
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
// Diff from integrate-ode-ivp/golden/verify.ts:
//   - Tool prefix is "integrate-ode-stiff/" throughout.
//   - checkTrajectoryAccuracy has NO horizon scaling (Radau is L-stable
//     and stiffly bounded; verify.py uses safety*rtol*||ref|| + safety*atol
//     with no horizonFactor multiplier).
//   - checkShape requires two additional integer fields: n_jacobian_evals,
//     n_lu_decompositions. Status set is {success, max_step_exceeded,
//     tspan_exhausted, newton-divergence} (not stiffness-detected).
//   - checkStatusConsistency validates the two extra integer counters
//     (non-negativity only — no ratio bounds, per verify.py §status_consistency).
//   - checkStiffnessHandled (NEW): n_evals > 0 and n_jacobian_evals > 0
//     on success path — Radau is implicit, zero Jacobian evals is a bug.
//   - checkJacobianConsumed (NEW): if options.jacobian provided, then
//     n_jacobian_evals >= 1.
//   - verifyTagged handles 4 boundary tags (adds jacobian-singular and
//     method-not-implemented on top of the ivp pair).
//
// ─── Reusability note for symplectic sibling ─────────────────────────────────
// The following functions remain TOOL-AGNOSTIC and can be copied verbatim:
//   shapeOfTrajectory(), vecFinite(), supNorm(), _wrap()
//   verifyTaggedDegenerate(), verifyTaggedNonFinite()
//   checkFiniteEntries(), checkMonotoneTValues()
//   conservedQuantity(), checkConservation()
//   checkSelfReportedErrorEstimate() (shared structural bound)
//
// Symplectic adds separate q_trajectory + p_trajectory fields, different
// conservation kinds (energy_drift_secular, energy_drift_max), and has
// no jacobian-related invariants.

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

// ─── Constants (must match verify.py byte-for-byte) ──────────────────────────

const EPS          = 2.220446049250313e-16;
const SAFETY       = 100.0;
const ATOL_DEFAULT = 1e-6;
const RTOL_DEFAULT = 1e-3;

// ─── Expected-output index (loaded once from adjacent expected.json) ─────────

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
// Identical to ode-ivp's conservedQuantity(); the stiff bench's D- and
// F-group cases do not use conservation specs, but the function is kept
// for structural completeness and to allow future stiff conservation tests.

function conservedQuantity(spec: ConservationSpec, y: number[]): number {
  switch (spec.kind) {
    case "harmonic_energy": {
      const q = y[0]!;
      const p = y[1]!;
      return 0.5 * (q * q + p * p);
    }
    case "kepler_energy": {
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

/** check_shape: structural completeness and type validation.
 *
 *  Stiff-specific delta from ode-ivp:
 *   - Required fields add n_jacobian_evals and n_lu_decompositions.
 *   - Valid status set is {success, max_step_exceeded, tspan_exhausted,
 *     newton-divergence} — NOT stiffness-detected (that belongs to ode-ivp
 *     which needs to detect stiffness; Radau IS the stiff solver).
 */
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
    "n_jacobian_evals", "n_lu_decompositions",
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

  // Stiff valid status set — newton-divergence replaces stiffness-detected.
  const validStatus = new Set(["success", "max_step_exceeded", "tspan_exhausted", "newton-divergence"]);
  if (typeof cand["status"] !== "string" || !validStatus.has(cand["status"])) {
    return {
      pass: false,
      detail: `status ${JSON.stringify(cand["status"])} not in {success,max_step_exceeded,tspan_exhausted,newton-divergence}`,
    };
  }

  const warnings = cand["warnings"];
  if (!Array.isArray(warnings) || !(warnings as unknown[]).every((s) => typeof s === "string")) {
    return { pass: false, detail: "warnings must be list[str]" };
  }

  // All counter fields must be integers.
  for (const f of [
    "n_evals", "n_steps_accepted", "n_steps_rejected",
    "n_jacobian_evals", "n_lu_decompositions",
  ] as const) {
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

/** check_status_consistency: converged iff status==="success"; counters sane.
 *
 *  Stiff-specific delta from ode-ivp: also checks n_jacobian_evals and
 *  n_lu_decompositions for non-negativity.  Ratio bounds were dropped
 *  because on stiff problems with Newton-divergence retries the Jacobian/LU
 *  counts can exceed step counts by 1-2 orders of magnitude (Robertson,
 *  vdP at high mu).  This matches verify.py's check_status_consistency
 *  comment exactly.
 */
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
  const nJac   = cand["n_jacobian_evals"] as number;
  const nLU    = cand["n_lu_decompositions"] as number;
  for (const [label, val] of [
    ["n_evals", nEvals], ["n_steps_accepted", nAcc], ["n_steps_rejected", nRej],
    ["n_jacobian_evals", nJac], ["n_lu_decompositions", nLU],
  ] as [string, number][]) {
    if (val < 0) {
      return { pass: false, detail: `${label}=${val} negative` };
    }
  }
  if (nAcc > 0 && nEvals === 0) {
    return {
      pass: false,
      detail: `n_steps_accepted=${nAcc} but n_evals=0`,
    };
  }
  return {
    pass: true,
    detail: `converged=${converged}, status=${JSON.stringify(status)}, n_evals/n_acc/n_jac/n_lu=${nEvals}/${nAcc}/${nJac}/${nLU}`,
  };
}

/** check_trajectory_accuracy: per-timestep sup-norm error vs reference.
 *
 *  Stiff-specific: NO horizon scaling.  Radau-IIA(5) is L-stable and
 *  stiffly accurate — global error does NOT grow with integration horizon
 *  the way DOPRI5 does (Hairer-Wanner Vol II §IV.10).  The tolerance is:
 *
 *    tol_i = max(safety * rtol * ||y_ref_i||_∞, safety * atol)
 *
 *  where safety = 100.  Per-case traj_tol_factor overrides 100 (e.g.
 *  Robertson D-cases use 10000 to handle log-timescale reference spread).
 */
function checkTrajectoryAccuracy(
  inp: {
    t_span: { t0: number; tf: number };
    options?: { rtol?: number; atol?: number };
  },
  cand: Record<string, unknown>,
  expected: ExpectedEntry,
): Check {
  const rtol  = expected.rtol  ?? inp.options?.rtol  ?? RTOL_DEFAULT;
  const atol  = expected.atol  ?? inp.options?.atol  ?? ATOL_DEFAULT;
  const trajC = cand["trajectory"] as number[][];
  const tVals = cand["t_values"]   as number[];
  const trajR = expected.trajectory!;

  if (trajC.length !== trajR.length) {
    return {
      pass: false,
      detail: `trajectory length ${trajC.length} != reference ${trajR.length}`,
    };
  }

  // Per-case override (Robertson D/F uses traj_tol_factor=10000).
  const safety = expected.traj_tol_factor ?? SAFETY;
  // No horizonFactor: Radau is L-stable, stiffness-bounded.

  let worstIdx = -1;
  let worstErr = 0.0;
  let worstTol = 0.0;

  for (let i = 0; i < trajC.length; i++) {
    const ref  = trajR[i]!;
    const cndV = trajC[i]!;
    let err = 0.0;
    for (let j = 0; j < ref.length; j++) {
      const d = Math.abs(cndV[j]! - ref[j]!);
      if (d > err) err = d;
    }
    const refNorm = supNorm(ref);
    // Stiff: no horizonFactor multiplication.
    const tol = Math.max(safety * rtol * refNorm, safety * atol);
    if (err > tol) {
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

  return {
    pass: true,
    detail: `trajectory matches reference to tol within ${trajC.length} points`,
  };
}

/** check_self_reported_error_estimate: structural sanity on error_estimate.
 *
 *  Identical to ode-ivp: non-negative; if status==="success", must be ≤
 *  max(1.0, atol * 1e6).  This is a structural floor, not a tight accuracy
 *  claim — matches verify.py faithfully.
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
    const atol  = inp.options?.atol ?? ATOL_DEFAULT;
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

/** check_stiffness_handled (NEW): structural floor confirming implicit integration.
 *
 *  Radau is an implicit method — it MUST evaluate the Jacobian at least once
 *  on any non-trivial integration.  A candidate that secretly uses explicit
 *  steps would set n_jacobian_evals = 0, betraying the method contract.
 *  We also require n_evals > 0 (at least one RHS evaluation happened).
 *
 *  Note: tighter ratio bounds (e.g. n_evals ≤ 100*n_steps_accepted) were
 *  dropped because Newton-divergence retries on Robertson/vdP-high-mu can
 *  push counts far outside any simple ratio.  This matches verify.py's
 *  check_stiffness_handled comment.
 */
function checkStiffnessHandled(cand: Record<string, unknown>): Check {
  const nEvals = cand["n_evals"] as number;
  const nJac   = cand["n_jacobian_evals"] as number;
  if (nEvals === 0) {
    return { pass: false, detail: "n_evals=0 on success path" };
  }
  if (nJac === 0) {
    return {
      pass: false,
      detail: "n_jacobian_evals=0 on Radau (implicit method must use Jacobian)",
    };
  }
  return { pass: true, detail: `n_evals=${nEvals}, n_jac=${nJac}` };
}

/** check_jacobian_consumed (NEW): if options.jacobian was supplied by the
 *  caller, the candidate must actually use it (n_jacobian_evals ≥ 1).
 *  Skipped when no analytic Jacobian is provided in the input.
 */
function checkJacobianConsumed(
  inp: { options?: { jacobian?: unknown } },
  cand: Record<string, unknown>,
): Check {
  if (inp.options?.jacobian === undefined) {
    return { pass: true, detail: "no analytic jacobian provided; check skipped" };
  }
  const nJac = cand["n_jacobian_evals"] as number;
  if (nJac < 1) {
    return {
      pass: false,
      detail: "options.jacobian provided but n_jacobian_evals == 0",
    };
  }
  return { pass: true, detail: `n_jacobian_evals=${nJac}` };
}

/** check_conservation: drift in conserved quantity H(y) over the trajectory.
 *
 *  Identical to ode-ivp.  Skipped if expected.conservation absent.
 *  Tolerance: SAFETY · rtol · max(|tf-t0|, 1.0) (relative to H(y[0])).
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

  let worst  = 0.0;
  let worstI = 0;
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
// Four boundary classes for integrate-ode-stiff:
//   1. degenerate-tspan       — t0 == tf; payload {t0, tf}.
//   2. non-finite-during-eval — RHS went non-finite; payload {at_t, at_y}.
//   3. jacobian-singular      — LU of (γ/h)I−J exactly singular;
//                               payload {at_t, at_y, condition_number?}.
//   4. method-not-implemented — options.method="bdf" (v0.1 ships Radau only);
//                               payload {method}.

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

/** verifyTaggedJacobianSingular (NEW): LU of (γ/h)I−J is exactly singular.
 *
 *  Payload must be a dict with at_t and at_y (the state at the point of
 *  failure).  condition_number is optional (not all implementations compute
 *  it before singular detection).
 */
function verifyTaggedJacobianSingular(
  cand: { tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const p = cand.payload;
  if (typeof p !== "object" || p === null) {
    return {
      pass: false,
      reason: "jacobian-singular payload not a dict",
      checks: { boundary: { pass: false, detail: String(p) } },
    };
  }
  for (const req of ["at_t", "at_y"] as const) {
    if (!(req in (p as object))) {
      return {
        pass: false,
        reason: `jacobian-singular payload missing ${req}`,
        checks: { boundary: { pass: false, detail: String(p) } },
      };
    }
  }
  return {
    pass: true,
    reason: "jacobian-singular correctly tagged",
    checks: { boundary: { pass: true, detail: cand.tag } },
  };
}

/** verifyTaggedMethodNotImplemented (NEW): options.method was set to "bdf"
 *  (or another unimplemented method).  v0.1 ships Radau only.
 *
 *  The input must have options.method = "bdf" for this tag to be valid.
 *  Payload must be a dict with a "method" key.
 */
function verifyTaggedMethodNotImplemented(
  inp: { options?: { method?: string } },
  cand: { tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const method = inp.options?.method;
  if (method !== "bdf") {
    return {
      pass: false,
      reason: `method-not-implemented tagged but options.method=${JSON.stringify(method)} (expected "bdf")`,
      checks: { boundary: { pass: false, detail: `method=${JSON.stringify(method)}` } },
    };
  }
  const p = cand.payload;
  if (typeof p !== "object" || p === null || !("method" in (p as object))) {
    return {
      pass: false,
      reason: "method-not-implemented payload missing 'method' key",
      checks: { boundary: { pass: false, detail: String(p) } },
    };
  }
  return {
    pass: true,
    reason: "method-not-implemented correctly tagged",
    checks: { boundary: { pass: true, detail: cand.tag } },
  };
}

function verifyTagged(
  inp: { t_span: { t0: number; tf: number }; options?: { method?: string } },
  cand: { kind: "tagged"; tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  if (cand.tag === "integrate-ode-stiff/degenerate-tspan") {
    return verifyTaggedDegenerate(inp, cand);
  }
  if (cand.tag === "integrate-ode-stiff/non-finite-during-eval") {
    return verifyTaggedNonFinite(cand);
  }
  if (cand.tag === "integrate-ode-stiff/jacobian-singular") {
    return verifyTaggedJacobianSingular(cand);
  }
  if (cand.tag === "integrate-ode-stiff/method-not-implemented") {
    return verifyTaggedMethodNotImplemented(inp, cand);
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
    options?: {
      rtol?: number;
      atol?: number;
      t_eval?: number[];
      method?: string;
      jacobian?: unknown;
    };
  };
  candidate: unknown;
  id?: string;
};

function verify(
  payload: Payload,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const inp    = payload.input;
  const cand   = payload.candidate;
  const caseId = payload.id ?? "?";

  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }

  const expectedIndex = loadExpected();
  const expected = expectedIndex.get(caseId) ?? {};

  const candR    = cand as Record<string, unknown>;
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

  // 5. trajectory_accuracy (no horizon scaling — Radau is L-stable)
  checks["trajectory_accuracy"] = checkTrajectoryAccuracy(inp, candR, expected);

  // 6. self_reported_error_estimate
  checks["self_reported_error_estimate"] = checkSelfReportedErrorEstimate(inp, candR);

  // 7. stiffness_handled (NEW) — implicit method must use Jacobian
  checks["stiffness_handled"] = checkStiffnessHandled(candR);

  // 8. conservation (skipped if no spec in expected.json)
  checks["conservation"] = checkConservation(inp, candR, expected);

  // 9. jacobian_consumed (NEW) — only active when options.jacobian provided
  checks["jacobian_consumed"] = checkJacobianConsumed(inp, candR);

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
