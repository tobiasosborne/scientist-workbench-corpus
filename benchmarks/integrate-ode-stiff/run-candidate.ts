// =============================================================================
// benchmarks/integrate-ode-stiff/run-candidate.ts — corpus-resident bench adapter
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
// Diff from benchmarks/integrate-ode-ivp/run-candidate.ts:
//   - Tool name: "integrate-ode-stiff" (not "integrate-ode-ivp").
//   - RawInput.options adds "method" and "jacobian" fields (stiff accepts
//     these; ivp does not).
//   - decodeOutput adds n_jacobian_evals and n_lu_decompositions (both
//     decoded via decodeInt).
//   - The output wire-format comment lists all 4 boundary tags.
//
// Input wire format (matches bench/integrate-ode-stiff/golden/inputs.json):
//   {
//     "f_str":  string[],          -- RHS component expressions as strings
//     "vars":   string[],          -- state variable names (e.g. ["y0","y1"])
//     "t_var":  string,            -- time variable name (e.g. "t")
//     "y0":     number[],          -- initial state
//     "t_span": { "t0": number, "tf": number },
//     "options"?: {
//       "rtol"?: number, "atol"?: number,
//       "max_step"?: number, "t_eval"?: number[],
//       "method"?: string,         -- "bdf" triggers method-not-implemented
//       "jacobian"?: string[][]    -- analytic Jacobian rows (expr strings)
//     }
//   }
//
// Output wire format (success path):
//   {
//     "trajectory":            number[][],
//     "t_values":              number[],
//     "error_estimate":        number,
//     "n_evals":               number,
//     "n_steps_accepted":      number,
//     "n_steps_rejected":      number,
//     "n_jacobian_evals":      number,    -- NEW vs ode-ivp
//     "n_lu_decompositions":   number,    -- NEW vs ode-ivp
//     "converged":             boolean,
//     "status":                string,
//     "method":                string,
//     "warnings":              string[],
//   }
//
// Tagged-boundary outputs are surfaced as { kind:"tagged", tag, payload }.
// Tags: degenerate-tspan, non-finite-during-eval, jacobian-singular,
//       method-not-implemented.
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
  f_str:  readonly string[];
  vars:   readonly string[];
  t_var:  string;
  y0:     readonly number[];
  t_span: { t0: number; tf: number };
  options?: {
    rtol?:     number;
    atol?:     number;
    max_step?: number;
    t_eval?:   readonly number[];
    method?:   string;
    jacobian?: readonly (readonly string[])[];
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
  const fields: Record<string, unknown> = {
    f:     list(await Promise.all(raw.f_str.map((s) => parseExpressionString(wb, s)))),
    vars:  list(raw.vars.map((v) => sym(v))),
    t_var: sym(raw.t_var),
    y0:    list(raw.y0.map((x) => float64FromNumber(x))),
    t_span: record({
      t0: float64FromNumber(raw.t_span.t0),
      tf: float64FromNumber(raw.t_span.tf),
    }),
  };

  if (raw.options !== undefined) {
    const optFields: Record<string, unknown> = {};
    if (raw.options.rtol     !== undefined) optFields["rtol"]     = float64FromNumber(raw.options.rtol);
    if (raw.options.atol     !== undefined) optFields["atol"]     = float64FromNumber(raw.options.atol);
    if (raw.options.max_step !== undefined) optFields["max_step"] = float64FromNumber(raw.options.max_step);
    if (raw.options.t_eval   !== undefined) {
      optFields["t_eval"] = list(raw.options.t_eval.map((x) => float64FromNumber(x)));
    }
    if (raw.options.method   !== undefined) optFields["method"]   = str(raw.options.method);
    if (raw.options.jacobian !== undefined) {
      // Analytic Jacobian: list of rows, each row is a list of expression Values.
      const jacRows: unknown[] = [];
      for (const row of raw.options.jacobian) {
        jacRows.push(list(await Promise.all(row.map((s) => parseExpressionString(wb, s)))));
      }
      optFields["jacobian"] = list(jacRows);
    }
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
    trajectory:           decodeFloatMatrix(f["trajectory"]!),
    t_values:             decodeFloatList(f["t_values"]!),
    error_estimate:       decodeFloat(f["error_estimate"]!),
    n_evals:              decodeInt(f["n_evals"]!),
    n_steps_accepted:     decodeInt(f["n_steps_accepted"]!),
    n_steps_rejected:     decodeInt(f["n_steps_rejected"]!),
    n_jacobian_evals:     decodeInt(f["n_jacobian_evals"]!),     // NEW vs ode-ivp
    n_lu_decompositions:  decodeInt(f["n_lu_decompositions"]!),  // NEW vs ode-ivp
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
  out = await wb.run("integrate-ode-stiff", input) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "Error", message: e.message ?? String(e) }) + "\n",
  );
  process.exit(0);
}

const decoded = decodeOutput(out);
process.stdout.write(JSON.stringify(decoded) + "\n");
