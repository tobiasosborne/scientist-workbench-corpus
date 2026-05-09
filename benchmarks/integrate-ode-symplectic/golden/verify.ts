// =============================================================================
// integrate-ode-symplectic verifier — invariant-based, language-neutral.
// =============================================================================
//
// Symplectic adaptation of ode-stiff verifier — see the q/p split note below.
//
// stdin:
//   { input:     { H_str, q_vars, p_vars, t_var, q0, p0, t_span, n_steps,
//                  options? },
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
// Structural diff from integrate-ode-stiff/golden/verify.ts:
//   - Tool prefix "integrate-ode-symplectic/" throughout.
//   - REMOVED: checkSelfReportedErrorEstimate, checkStiffnessHandled,
//     checkJacobianConsumed, checkConservation, conservedQuantity.
//   - REMOVED: verifyTaggedJacobianSingular, verifyTaggedMethodNotImplemented.
//   - ADDED: verifyTaggedNonSeparableHamiltonian.
//   - REWROTE checkShape: validates q_trajectory [n_steps+1 × n_q] and
//     p_trajectory [n_steps+1 × n_p] independently; also validates energy
//     list length and energy_drift_secular boolean presence.
//   - REWROTE checkFiniteEntries: scans q_trajectory, p_trajectory, energy,
//     and energy_drift_max (no single `trajectory` field).
//   - REWROTE checkMonotoneTValues: symplectic uses a fixed uniform step-grid
//     (h = (tf-t0)/n_steps), so we check against the expected grid directly
//     rather than monotone order alone.
//   - REWROTE checkTrajectoryAccuracy: takes combined q/p error; tolerance
//     is traj_tol_factor * h^p with p=2 (velocity-verlet) or p=4 (yoshida-4).
//     This is the HLW §VI.3 global-error bound (not L-stable Radau).
//   - ADDED: checkEnergyDriftBounded — energy_drift_max ≤ 100·h^p·drift_constant
//     (SAFETY=100 from verify.py, HLW §VI.6 backward error).
//   - ADDED: checkEnergyDriftNotSecular — on long_time cases (Kepler 100 orbits,
//     Hénon-Heiles), energy_drift_secular must be false (bounded drift is the
//     symplectic headline invariant).
//
// ─── q/p split note ──────────────────────────────────────────────────────────
// The structurally hardest part of this port is that all prior ODE benchmarks
// had a single `trajectory` field.  Here the state is split: q_trajectory has
// shape [n_steps+1 × n_q] and p_trajectory has shape [n_steps+1 × n_p].  The
// two halves must be validated independently (q_vars/p_vars may differ in
// length for non-square Hamiltonians) and their accuracy compared jointly
// (worst rel-err over both).  See checkShape and checkTrajectoryAccuracy.

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

// ─── Constants (must match verify.py byte-for-byte) ──────────────────────────

const SAFETY = 100.0;

// ─── Expected-output index (loaded once from adjacent expected.json) ─────────

type ExpectedEntry = {
  kind?: "tagged" | "tool_error";
  tag?: string;
  // Success-path fields:
  q_trajectory?:       number[][];
  p_trajectory?:       number[][];
  t_values?:           number[];
  energy_drift_max_ref?: number;
  oracle_source?:      string;
  drift_constant?:     number;
  traj_tol_factor?:    number;
  long_time?:          boolean;
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

// ─── Structural helpers ───────────────────────────────────────────────────────
//
// shapeOfTrajectory(), vecFinite(), supNorm() are tool-agnostic and carried
// verbatim from the ode-ivp/ode-stiff verifiers.

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
 *  Symplectic-specific:
 *   - Required fields include q_trajectory, p_trajectory (NOT trajectory),
 *     energy (list length n_steps+1), energy_drift_max (scalar float),
 *     energy_drift_secular (boolean), n_evals, n_steps, converged, status,
 *     method, warnings.
 *   - q_trajectory shape must be [n_steps+1 × n_q] where n_q = len(q0).
 *   - p_trajectory shape must be [n_steps+1 × n_p] where n_p = len(p0).
 *   - Valid status set is {success, non-finite-during-eval}.
 */
function checkShape(
  inp: {
    q0: number[];
    p0: number[];
    n_steps: number;
  },
  cand: Record<string, unknown>,
): Check {
  const nQ = inp.q0.length;
  const nP = inp.p0.length;
  const nSteps = inp.n_steps;
  const expectedM = nSteps + 1;

  const required = [
    "q_trajectory", "p_trajectory", "t_values", "energy",
    "energy_drift_max", "energy_drift_secular",
    "n_evals", "n_steps", "converged", "status", "method", "warnings",
  ];
  const missing = required.filter((k) => !(k in cand));
  if (missing.length > 0) {
    return { pass: false, detail: `missing fields: [${missing.sort().join(",")}]` };
  }

  // ── q_trajectory shape ───────────────────────────────────────────────────
  const sQ = shapeOfTrajectory(cand["q_trajectory"]);
  if (sQ === null) {
    return { pass: false, detail: "q_trajectory is not a 2-D list of numbers" };
  }
  if (sQ[0] !== expectedM || sQ[1] !== nQ) {
    return {
      pass: false,
      detail: `q_trajectory shape [${sQ[0]},${sQ[1]}], expected [${expectedM},${nQ}]`,
    };
  }

  // ── p_trajectory shape ───────────────────────────────────────────────────
  const sP = shapeOfTrajectory(cand["p_trajectory"]);
  if (sP === null) {
    return { pass: false, detail: "p_trajectory is not a 2-D list of numbers" };
  }
  if (sP[0] !== expectedM || sP[1] !== nP) {
    return {
      pass: false,
      detail: `p_trajectory shape [${sP[0]},${sP[1]}], expected [${expectedM},${nP}]`,
    };
  }

  // ── t_values length ──────────────────────────────────────────────────────
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

  // ── energy list length ───────────────────────────────────────────────────
  if (!Array.isArray(cand["energy"])) {
    return { pass: false, detail: "energy must be a list" };
  }
  const energyList = cand["energy"] as unknown[];
  if (energyList.length !== expectedM) {
    return {
      pass: false,
      detail: `energy length ${energyList.length}, expected ${expectedM}`,
    };
  }

  // ── energy_drift_secular boolean ─────────────────────────────────────────
  if (typeof cand["energy_drift_secular"] !== "boolean") {
    return { pass: false, detail: "energy_drift_secular must be a bool" };
  }

  // ── status ───────────────────────────────────────────────────────────────
  const validStatus = new Set(["success", "non-finite-during-eval"]);
  if (typeof cand["status"] !== "string" || !validStatus.has(cand["status"])) {
    return {
      pass: false,
      detail: `status ${JSON.stringify(cand["status"])} not in {success,non-finite-during-eval}`,
    };
  }

  // ── warnings ─────────────────────────────────────────────────────────────
  const warnings = cand["warnings"];
  if (!Array.isArray(warnings) || !(warnings as unknown[]).every((s) => typeof s === "string")) {
    return { pass: false, detail: "warnings must be list[str]" };
  }

  // ── integer counters ─────────────────────────────────────────────────────
  for (const f of ["n_evals", "n_steps"] as const) {
    if (typeof cand[f] !== "number" || !Number.isInteger(cand[f])) {
      return { pass: false, detail: `${f} must be an integer` };
    }
  }

  if (typeof cand["converged"] !== "boolean") {
    return { pass: false, detail: "converged must be a bool" };
  }

  return {
    pass: true,
    detail: `q ${JSON.stringify(sQ)}, p ${JSON.stringify(sP)}`,
  };
}

/** check_finite_entries: no NaN/Inf in q_trajectory, p_trajectory, energy,
 *  or energy_drift_max.
 *
 *  Symplectic-specific: scans both trajectory halves independently and checks
 *  energy_drift_max as a scalar (no error_estimate field exists here).
 */
function checkFiniteEntries(cand: Record<string, unknown>): Check {
  for (const field of ["q_trajectory", "p_trajectory"] as const) {
    const traj = cand[field] as number[][];
    for (let i = 0; i < traj.length; i++) {
      if (!vecFinite(traj[i]!)) {
        return { pass: false, detail: `non-finite entry in ${field} row ${i}` };
      }
    }
  }
  const energy = cand["energy"] as unknown[];
  for (let i = 0; i < energy.length; i++) {
    if (typeof energy[i] !== "number" || !Number.isFinite(energy[i] as number)) {
      return { pass: false, detail: `non-finite entry in energy[${i}]` };
    }
  }
  const driftMax = cand["energy_drift_max"];
  if (typeof driftMax !== "number" || !Number.isFinite(driftMax)) {
    return { pass: false, detail: "energy_drift_max non-finite" };
  }
  return { pass: true, detail: "all entries finite" };
}

/** check_monotone_t_values: symplectic uses a fixed uniform grid with step
 *  h = (tf − t0) / n_steps.  We compare each t_values[i] against the expected
 *  grid point t0 + i·h within a 1e-10 relative tolerance (mirrors verify.py's
 *  check_monotone_t_values; the tolerance is 1e-10 * max(|a|, 1.0)).
 */
function checkMonotoneTValues(
  inp: {
    t_span: { t0: number; tf: number };
    n_steps: number;
  },
  cand: Record<string, unknown>,
): Check {
  const t0     = inp.t_span.t0;
  const tf     = inp.t_span.tf;
  const nSteps = inp.n_steps;
  const h      = (tf - t0) / nSteps;
  const tVals  = cand["t_values"] as number[];

  for (let i = 0; i < tVals.length; i++) {
    const expected = t0 + i * h;
    const diff     = Math.abs(expected - tVals[i]!);
    const tol      = 1e-10 * Math.max(Math.abs(expected), 1.0);
    if (diff > tol) {
      return {
        pass: false,
        detail: `t_values[${i}]=${tVals[i]} != expected ${expected} (diff=${fmt(diff)} > tol=${fmt(tol)})`,
      };
    }
  }
  return {
    pass: true,
    detail: `uniform grid over ${tVals.length} points`,
  };
}

/** check_status_consistency: converged iff status==="success"; counters sane.
 *
 *  Symplectic: n_steps (the output counter) must equal input n_steps;
 *  n_evals must be non-negative.  These match verify.py's
 *  check_status_consistency for integrate-ode-symplectic.
 */
function checkStatusConsistency(
  inp: { n_steps: number },
  cand: Record<string, unknown>,
): Check {
  const converged = cand["converged"] as boolean;
  const status    = cand["status"] as string;
  if (converged !== (status === "success")) {
    return {
      pass: false,
      detail: `converged=${converged} but status=${JSON.stringify(status)}`,
    };
  }
  const nStepsOut = cand["n_steps"] as number;
  if (nStepsOut !== inp.n_steps) {
    return {
      pass: false,
      detail: `n_steps output ${nStepsOut} != input n_steps ${inp.n_steps}`,
    };
  }
  const nEvals = cand["n_evals"] as number;
  if (nEvals < 0) {
    return { pass: false, detail: `n_evals=${nEvals} negative` };
  }
  return {
    pass: true,
    detail: `converged=${converged}, status=${JSON.stringify(status)}, n_steps=${nStepsOut}, n_evals=${nEvals}`,
  };
}

/** check_trajectory_accuracy: combined q/p sup-norm error vs reference.
 *
 *  The tolerance formula follows HLW §VI.3 global-error bound for geometric
 *  integrators on separable Hamiltonians:
 *
 *    tol = traj_tol_factor × h^p
 *
 *  where p = 2 (velocity-verlet, default) or p = 4 (yoshida-4), and
 *  traj_tol_factor = 1000 (from expected.json, matching verify.py's default).
 *  The error is normalised row-by-row: rel-err_i = err_i / max(||ref_i||, 1).
 *
 *  Note: unlike ode-stiff there is NO absolute-tolerance floor — symplectic
 *  integrators on short-horizon problems bound error in h^p units only.
 *  This matches verify.py's check_trajectory_accuracy exactly.
 *
 *  q/p split: both halves are checked jointly.  The worst relative error over
 *  all rows of both halves is compared against the single tolerance.
 */
function checkTrajectoryAccuracy(
  inp: {
    t_span: { t0: number; tf: number };
    n_steps: number;
  },
  cand: Record<string, unknown>,
  expected: ExpectedEntry,
): Check {
  if (!("q_trajectory" in expected)) {
    return { pass: true, detail: "no reference trajectory; check skipped" };
  }

  const t0     = inp.t_span.t0;
  const tf     = inp.t_span.tf;
  const nSteps = inp.n_steps;
  const h      = (tf - t0) / nSteps;

  const method = (typeof cand["method"] === "string") ? cand["method"] : "velocity-verlet";
  const p      = method === "yoshida-4" ? 4 : 2;

  const qC = cand["q_trajectory"] as number[][];
  const qR = expected.q_trajectory!;
  const pC = cand["p_trajectory"] as number[][];
  const pR = expected.p_trajectory!;

  if (qC.length !== qR.length) {
    return { pass: false, detail: `q_trajectory length ${qC.length} != reference ${qR.length}` };
  }
  if (pC.length !== pR.length) {
    return { pass: false, detail: `p_trajectory length ${pC.length} != reference ${pR.length}` };
  }

  const tolFactor = expected.traj_tol_factor ?? 1000.0;
  const tolH      = tolFactor * Math.pow(h, p);

  let worstQ = 0.0;
  let worstP = 0.0;

  for (let i = 0; i < qC.length; i++) {
    const normQ = Math.max(supNorm(qR[i]!), 1.0);
    const normP = Math.max(supNorm(pR[i]!), 1.0);
    const errQ  = supNorm((qC[i]!).map((v, j) => v - (qR[i]![j] ?? 0))) / normQ;
    const errP  = supNorm((pC[i]!).map((v, j) => v - (pR[i]![j] ?? 0))) / normP;
    if (errQ > worstQ) worstQ = errQ;
    if (errP > worstP) worstP = errP;
  }

  const worst = Math.max(worstQ, worstP);
  if (worst > tolH) {
    return {
      pass: false,
      detail: `trajectory rel-err max ${fmt(worst)} > tol ${fmt(tolH)} (h^${p}=${fmt(Math.pow(h, p))}, factor=${tolFactor})`,
    };
  }
  return {
    pass: true,
    detail: `trajectory rel-err max ${fmt(worst)} ≤ tol ${fmt(tolH)}`,
  };
}

/** check_energy_drift_bounded: energy_drift_max ≤ SAFETY · h^p · drift_constant.
 *
 *  This is the HLW §VI.6 backward-error bound for symplectic integrators:
 *  energy drift is O(h^p) independent of integration horizon — bounded, not
 *  growing.  SAFETY = 100 (matches verify.py's constant).  drift_constant is
 *  problem-specific and stored in expected.json (default 1.0 if absent).
 *
 *  The tolerance is NOT horizon-scaled: that is the core distinction between
 *  symplectic integrators and Runge-Kutta methods.  An RK candidate would see
 *  its energy drift grow as O(T), failing long-time Kepler / Hénon-Heiles.
 */
function checkEnergyDriftBounded(
  inp: {
    t_span: { t0: number; tf: number };
    n_steps: number;
  },
  cand: Record<string, unknown>,
  expected: ExpectedEntry,
): Check {
  const driftMax    = cand["energy_drift_max"] as number;
  const t0          = inp.t_span.t0;
  const tf          = inp.t_span.tf;
  const nSteps      = inp.n_steps;
  const h           = (tf - t0) / nSteps;
  const method      = (typeof cand["method"] === "string") ? cand["method"] : "velocity-verlet";
  const p           = method === "yoshida-4" ? 4 : 2;
  const driftConst  = expected.drift_constant ?? 1.0;
  const tol         = SAFETY * Math.pow(h, p) * driftConst;

  if (driftMax > tol) {
    return {
      pass: false,
      detail: `energy_drift_max=${fmt(driftMax)} > tol=${fmt(tol)} (100·h^${p}·${driftConst})`,
    };
  }
  return {
    pass: true,
    detail: `energy_drift_max=${fmt(driftMax)} ≤ tol=${fmt(tol)}`,
  };
}

/** check_energy_drift_not_secular: on long-time tier-C cases, the candidate
 *  must report energy_drift_secular === false.
 *
 *  This is the headline symplecticity test.  A symplectic integrator on a
 *  separable Hamiltonian conserves a modified Hamiltonian H̃ = H + O(h^p)
 *  for all time, so energy drift is bounded (not secular/growing).  A naïve
 *  Runge-Kutta method would fail this test on the Kepler 100-orbit case
 *  or the 100-time-unit Hénon-Heiles case.
 *
 *  Skipped for non-long-time cases (expected.long_time !== true).
 *  Mutation: setting energy_drift_secular = true on a long-time case must RED.
 */
function checkEnergyDriftNotSecular(
  expected: ExpectedEntry,
  cand: Record<string, unknown>,
): Check {
  if (!expected.long_time) {
    return { pass: true, detail: "non-long-time case; check skipped" };
  }
  const secular = cand["energy_drift_secular"] as boolean;
  if (secular) {
    return {
      pass: false,
      detail: "energy_drift_secular: true on long-time case (non-symplectic candidate?)",
    };
  }
  return { pass: true, detail: "energy_drift bounded across long horizon" };
}

// ─── Tagged-boundary checks ───────────────────────────────────────────────────
//
// Three boundary classes for integrate-ode-symplectic:
//   1. degenerate-tspan          — t0 == tf or n_steps == 0; payload may vary.
//   2. non-separable-hamiltonian — H is not separable into T(p) + V(q);
//                                  no payload fields required.
//   3. non-finite-during-eval    — Hamiltonian went non-finite;
//                                  payload {at_t, at_q, at_p}.

function verifyTaggedDegenerate(
  inp: { t_span: { t0: number; tf: number }; n_steps: number },
  cand: { tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const t0     = inp.t_span.t0;
  const tf     = inp.t_span.tf;
  const nSteps = inp.n_steps;
  // Degenerate-tspan fires when t0 == tf OR n_steps == 0.
  if (t0 !== tf && nSteps !== 0) {
    return {
      pass: false,
      reason: "degenerate-tspan tagged but neither t0==tf nor n_steps==0",
      checks: { boundary: { pass: false, detail: `t0=${t0}, tf=${tf}, n_steps=${nSteps}` } },
    };
  }
  return {
    pass: true,
    reason: "degenerate-tspan correctly tagged",
    checks: { boundary: { pass: true, detail: cand.tag } },
  };
}

/** verifyTaggedNonSeparableHamiltonian (NEW): H(q,p) cannot be split as
 *  T(p) + V(q).  Symplectic integrators require separability — velocity
 *  Verlet and Yoshida-4 both require explicit ∇_q H and ∇_p H maps that
 *  decouple into kinetic / potential halves.
 *
 *  The verifier accepts any payload (the Python verify.py checks sympy
 *  separability; we trust that the candidate correctly identifies non-
 *  separable inputs since the TS verifier has no sympy access).
 */
function verifyTaggedNonSeparableHamiltonian(
  cand: { tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  // Structural check only: payload presence not required.
  return {
    pass: true,
    reason: "non-separable-hamiltonian correctly tagged",
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
  // Symplectic payload uses at_t, at_q, at_p (not at_y as in ode-stiff/ivp).
  for (const req of ["at_t", "at_q", "at_p"] as const) {
    if (!(req in (p as object))) {
      return {
        pass: false,
        reason: `non-finite-during-eval payload missing ${req}`,
        checks: { boundary: { pass: false, detail: String(p) } },
      };
    }
  }
  return {
    pass: true,
    reason: "non-finite-during-eval correctly tagged",
    checks: { boundary: { pass: true, detail: cand.tag } },
  };
}

function verifyTagged(
  inp: { t_span: { t0: number; tf: number }; n_steps: number },
  cand: { kind: "tagged"; tag: string; payload: unknown },
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  if (cand.tag === "integrate-ode-symplectic/degenerate-tspan") {
    return verifyTaggedDegenerate(inp, cand);
  }
  if (cand.tag === "integrate-ode-symplectic/non-separable-hamiltonian") {
    return verifyTaggedNonSeparableHamiltonian(cand);
  }
  if (cand.tag === "integrate-ode-symplectic/non-finite-during-eval") {
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
    H_str?:  string;
    q_vars?: string[];
    p_vars?: string[];
    t_var?:  string;
    q0:      number[];
    p0:      number[];
    t_span:  { t0: number; tf: number };
    n_steps: number;
    options?: {
      scheme?: string;
      atol?:   number;
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

  // 3. monotone_t_values (uniform grid check)
  checks["monotone_t_values"] = checkMonotoneTValues(inp, candR);

  // 4. status_consistency
  checks["status_consistency"] = checkStatusConsistency(inp, candR);

  // 5. trajectory_accuracy (combined q/p; HLW §VI.3 h^p bound)
  checks["trajectory_accuracy"] = checkTrajectoryAccuracy(inp, candR, expected);

  // 6. energy_drift_bounded (HLW §VI.6 h^p horizon-independent bound)
  checks["energy_drift_bounded"] = checkEnergyDriftBounded(inp, candR, expected);

  // 7. energy_drift_not_secular (headline symplecticity test; skipped for short cases)
  checks["energy_drift_not_secular"] = checkEnergyDriftNotSecular(expected, candR);

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
