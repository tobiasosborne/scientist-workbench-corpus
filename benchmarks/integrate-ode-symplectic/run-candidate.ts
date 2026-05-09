// =============================================================================
// benchmarks/integrate-ode-symplectic/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges raw JSON wire format to the tool's canonical Value protocol.
// This file lives in scientist-workbench-corpus (ADR-0028).
//
// The @workbench/* workspace packages live in the workbench repo, not the
// corpus.  Bun resolves workspace aliases by walking upward from the *file's
// directory*, so a corpus-resident script cannot use @workbench/compose or
// @workbench/protocol as bare specifiers.  Instead we import directly from
// the workbench package source files using the WORKBENCH_ROOT environment
// variable, which the corpus grader makes available (and which defaults to
// the conventional side-by-side sibling path).
//
// Diff from benchmarks/integrate-ode-stiff/run-candidate.ts:
//   - Tool name: "integrate-ode-symplectic" (not "integrate-ode-stiff").
//   - RawInput is structurally different: H_str (single expression, not f_str[]),
//     q_vars + p_vars (separate lists of symbols, not vars[]),
//     q0 + p0 (separate initial conditions, not y0[]),
//     n_steps: integer (not adaptive; no t_eval, max_step, rtol),
//     options?: { scheme?, atol? }.
//   - encodeInput calls expr-parse once for H (not per-component as in stiff);
//     builds q_vars/p_vars as symbol lists; q0/p0 as float64 lists.
//   - decodeOutput: two float matrices (q_trajectory, p_trajectory), plus
//     energy list, energy_drift_max scalar, energy_drift_secular boolean,
//     n_evals, n_steps, converged, status, method, warnings.
//   - No n_jacobian_evals, n_lu_decompositions, n_steps_accepted, n_steps_rejected,
//     error_estimate (Verlet/Yoshida are explicit fixed-step methods).
//   - Boundary tags: degenerate-tspan, non-separable-hamiltonian,
//     non-finite-during-eval.
//
// Input wire format (matches bench/integrate-ode-symplectic/golden/inputs.json):
//   {
//     "H_str":   string,          -- single Hamiltonian expression
//     "q_vars":  string[],        -- position variable names (e.g. ["qx","qy"])
//     "p_vars":  string[],        -- momentum variable names (e.g. ["px","py"])
//     "t_var":   string,          -- time variable name (e.g. "t")
//     "q0":      number[],        -- initial position state
//     "p0":      number[],        -- initial momentum state
//     "t_span":  { "t0": number, "tf": number },
//     "n_steps": number,          -- fixed number of integration steps
//     "options"?: {
//       "scheme"?: string,        -- "velocity-verlet" (default) or "yoshida-4"
//       "atol"?:   number         -- absolute tolerance for non-finite detection
//     }
//   }
//
// Output wire format (success path):
//   {
//     "q_trajectory":          number[][],  -- [n_steps+1 × n_q]
//     "p_trajectory":          number[][],  -- [n_steps+1 × n_p]
//     "t_values":              number[],    -- length n_steps+1
//     "energy":                number[],    -- H(q,p) at each step
//     "energy_drift_max":      number,      -- max |H(t) - H(0)|
//     "energy_drift_secular":  boolean,     -- true if drift grows with horizon
//     "n_evals":               number,
//     "n_steps":               number,
//     "converged":             boolean,
//     "status":                string,
//     "method":                string,
//     "warnings":              string[],
//   }
//
// Tagged-boundary outputs are surfaced as { kind:"tagged", tag, payload }.
// Tags: degenerate-tspan, non-separable-hamiltonian, non-finite-during-eval.
// Thrown ToolErrors are wrapped into { kind:"tool_error", name, message }.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Resolve workbench root ──────────────────────────────────────────────────
//
// The corpus grader sets WORKBENCH_ROOT when it spawns run-candidate.ts.
// The default fallback assumes the conventional side-by-side layout:
//   ~/Projects/scientist-workbench/
//   ~/Projects/scientist-workbench-corpus/  ← this repo

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "..", "scientist-workbench");

// ─── Dynamic imports (file paths, resolved through workbench) ────────────────

const composePath  = resolve(WORKBENCH_ROOT, "packages/compose/src/index.ts");
const protocolPath = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");

const { loadWorkbench } = await import(composePath);
const proto = await import(protocolPath);

const float64FromNumber: (n: number)                         => unknown = proto.float64FromNumber;
const float64ToNumber:   (v: unknown)                        => number  = proto.float64ToNumber;
const int:     (v: bigint)                                    => unknown = proto.int;
const list:    (items: unknown[])                             => unknown = proto.list;
const record:  (fields: Record<string, unknown>)             => unknown = proto.record;
const sym:     (name: string)                                => unknown = proto.sym;
const str:     (s: string)                                   => unknown = proto.str;

// ─── Local structural type for decoded Value nodes ───────────────────────────

type V = {
  kind: string;
  items?:  V[];
  fields?: Record<string, V>;
  value?:  unknown;
  name?:   string;
  tag?:    string;
  payload?: V;
};

// ─── raw JSON → canonical Value (input encoding) ─────────────────────────────

interface RawInput {
  H_str:  string;
  q_vars: readonly string[];
  p_vars: readonly string[];
  t_var:  string;
  q0:     readonly number[];
  p0:     readonly number[];
  t_span: { t0: number; tf: number };
  n_steps: number;
  options?: {
    scheme?: string;
    atol?:   number;
  };
}

async function parseExpressionString(
  wb: Awaited<ReturnType<typeof loadWorkbench>>,
  s: string,
): Promise<unknown> {
  return await wb.run("expr-parse", str(s));
}

async function encodeInput(
  wb: Awaited<ReturnType<typeof loadWorkbench>>,
  raw: RawInput,
): Promise<unknown> {
  // H is a single expression (not a list of RHS components as in ode-stiff/ivp).
  const H = await parseExpressionString(wb, raw.H_str);

  const fields: Record<string, unknown> = {
    H:      H,
    q_vars: list(raw.q_vars.map((v) => sym(v))),
    p_vars: list(raw.p_vars.map((v) => sym(v))),
    t_var:  sym(raw.t_var),
    q0:     list(raw.q0.map((x) => float64FromNumber(x))),
    p0:     list(raw.p0.map((x) => float64FromNumber(x))),
    t_span: record({
      t0: float64FromNumber(raw.t_span.t0),
      tf: float64FromNumber(raw.t_span.tf),
    }),
    n_steps: int(BigInt(raw.n_steps)),
  };

  if (raw.options !== undefined) {
    const optFields: Record<string, unknown> = {};
    if (raw.options.scheme !== undefined) optFields["scheme"] = str(raw.options.scheme);
    if (raw.options.atol   !== undefined) optFields["atol"]   = float64FromNumber(raw.options.atol);
    fields["options"] = record(optFields);
  }

  return record(fields);
}

// ─── canonical Value → raw JSON (output decoding) ────────────────────────────

function decodeFloatList(v: V): number[] {
  if (v.kind !== "list") throw new Error(`expected list, got kind=${v.kind}`);
  return v.items!.map((it) => {
    if (it.kind !== "float64") throw new Error(`expected float64, got kind=${it.kind}`);
    return float64ToNumber(it);
  });
}

function decodeFloatMatrix(v: V): number[][] {
  if (v.kind !== "list") throw new Error(`expected list-of-list, got kind=${v.kind}`);
  return v.items!.map(decodeFloatList);
}

function decodeStringList(v: V): string[] {
  if (v.kind !== "list") throw new Error(`expected list, got kind=${v.kind}`);
  return v.items!.map((it) => {
    if (it.kind !== "string") throw new Error(`expected string, got kind=${it.kind}`);
    return it.value as string;
  });
}

function decodeFloat(v: V): number {
  if (v.kind !== "float64") throw new Error(`expected float64, got kind=${v.kind}`);
  return float64ToNumber(v);
}

function decodeInt(v: V): number {
  if (v.kind !== "integer") throw new Error(`expected integer, got kind=${v.kind}`);
  return Number(v.value);
}

function decodeBool(v: V): boolean {
  if (v.kind !== "boolean") throw new Error(`expected boolean, got kind=${v.kind}`);
  return v.value as boolean;
}

function decodeString(v: V): string {
  if (v.kind !== "string") throw new Error(`expected string, got kind=${v.kind}`);
  return v.value as string;
}

function decodeAny(v: V): unknown {
  switch (v.kind) {
    case "string":  return v.value;
    case "integer": return Number(v.value);
    case "float64": return float64ToNumber(v);
    case "boolean": return v.value;
    case "symbol":  return v.name;
    case "list":    return v.items!.map(decodeAny);
    case "record": {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v.fields!)) out[k] = decodeAny(val);
      return out;
    }
    case "tagged":  return { kind: "tagged", tag: v.tag, payload: decodeAny(v.payload!) };
    default:        return null;
  }
}

function decodeOutput(v: V): Record<string, unknown> {
  if (v.kind === "tagged") {
    return { kind: "tagged", tag: v.tag, payload: decodeAny(v.payload!) };
  }
  if (v.kind !== "record") {
    throw new Error(`expected record, got kind=${v.kind}`);
  }
  const f = v.fields!;
  return {
    q_trajectory:         decodeFloatMatrix(f["q_trajectory"]!),   // [n_steps+1 × n_q]
    p_trajectory:         decodeFloatMatrix(f["p_trajectory"]!),   // [n_steps+1 × n_p]
    t_values:             decodeFloatList(f["t_values"]!),
    energy:               decodeFloatList(f["energy"]!),
    energy_drift_max:     decodeFloat(f["energy_drift_max"]!),
    energy_drift_secular: decodeBool(f["energy_drift_secular"]!),
    n_evals:              decodeInt(f["n_evals"]!),
    n_steps:              decodeInt(f["n_steps"]!),
    converged:            decodeBool(f["converged"]!),
    status:               decodeString(f["status"]!),
    method:               decodeString(f["method"]!),
    warnings:             decodeStringList(f["warnings"]!),
  };
}

// ─── main ─────────────────────────────────────────────────────────────────────

const raw = JSON.parse(readFileSync(0, "utf8")) as RawInput;
const wb  = await loadWorkbench();

let input: unknown;
try {
  input = await encodeInput(wb, raw);
} catch (err) {
  const e = err as Error;
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "ExpressionParseFailed", message: e.message }) + "\n",
  );
  process.exit(0);
}

let out: V;
try {
  out = await wb.run("integrate-ode-symplectic", input) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "Error", message: e.message ?? String(e) }) + "\n",
  );
  process.exit(0);
}

const decoded = decodeOutput(out);
process.stdout.write(JSON.stringify(decoded) + "\n");
