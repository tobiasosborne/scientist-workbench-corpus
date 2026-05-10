// =============================================================================
// benchmarks/alg-num-arith/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges the bench's raw JSON wire format to the tool's canonical Value
// protocol.  This file lives in scientist-workbench-corpus (ADR-0028).
//
// The @workbench/* workspace packages live in the workbench repo, not the
// corpus.  Bun resolves workspace aliases by walking upward from the *file's
// directory*, so a corpus-resident script cannot use @workbench/compose or
// @workbench/protocol as bare specifiers.  Instead we import directly from
// the workbench package source files using the WORKBENCH_ROOT environment
// variable, which the corpus grader makes available (and which defaults to
// the conventional side-by-side sibling path).
//
// Input wire format (matches bench/alg-num-arith/golden/inputs.json):
//   {
//     "a":  <Root[poly, k] expression>  -- wire-encoded Value (kind:"expression", head:"Root")
//     "b":  <Root[poly, k] expression>  -- present for binary ops; absent for unary ops
//     "op": string                      -- one of: add sub mul div neg inv eq
//   }
//
// Unlike the poly-roots / real-root-isolate adapters (which parse polynomial
// strings into Values), alg-num-arith's inputs.json already contains the
// wire-encoded Root[] expressions directly — no string-to-Value parsing is
// needed.  The adapter forwards `a` and `b` verbatim as the tool's input
// record, with the `op` carried as a flag.
//
// Output wire format:
//   {  "kind":"expression", "head":"Root", "args":[Polynomial[], integer] }
//                                         -- arithmetic happy path
//   { "kind":"boolean", "value": bool }   -- for op="eq"
//   { "kind":"tagged", "tag":"alg-num-arith/<class>", "payload":{...} }
//                                         -- boundary refusal
//   { "kind":"tool_error", "name":..., "message":... }
//                                         -- propagated ToolError
//
// The adapter calls the tool in-process via @workbench/compose's loadWorkbench()
// which invokes the tool's fn() directly (no subprocess).  This is the
// standard corpus adapter pattern (ADR-0028 §3).
//
// Note: alg-num-arith is a symbolic-tier tool (default determinism class —
// no `numerical: true`, no `arbprec: true`).  Output is bit-identical cross-
// platform forever.  Adapter: platform_pinned = false (see adapter TOML).

import { readFileSync } from "node:fs";
import { resolve }      from "node:path";

// ─── Resolve workbench root ──────────────────────────────────────────────────
//
// The corpus grader sets WORKBENCH_ROOT when it spawns run-candidate.ts.
// The default fallback assumes the conventional side-by-side layout:
//   ~/Projects/scientist-workbench/         ← WORKBENCH_ROOT
//   ~/Projects/scientist-workbench-corpus/  ← this repo

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "scientist-workbench");

// ─── Dynamic imports (file paths, resolved through workbench) ────────────────

const composePath = resolve(WORKBENCH_ROOT, "packages/compose/src/index.ts");

const { loadWorkbench } = await import(composePath);

// ─── Local structural type for decoded Value nodes ───────────────────────────

type V = {
  kind:     string;
  items?:   V[];
  fields?:  Record<string, V>;
  value?:   unknown;
  name?:    string;
  tag?:     string;
  payload?: V;
  head?:    string;
  args?:    V[];
};

// ─── Output decoding: Value → bench wire shape ────────────────────────────────
//
// The tool's fn() returns a Value.  We need to project it into the bench's
// expected output shapes:
//
//   Arithmetic happy path: pass through the Root[] expression verbatim.
//   The bench's verify.py / verify.ts consumes the wire-encoded expression
//   directly, so no transformation is needed.
//
//   Eq happy path: { kind:"boolean", value:<bool> }.
//
//   Refusal: { kind:"tagged", tag, payload:<decoded-record> }.
//
//   ToolError: { kind:"tool_error", name, message }.

function decodeAny(v: V): unknown {
  switch (v.kind) {
    case "string":   return { kind: "string",  value: v.value };
    case "integer":  return { kind: "integer", value: v.value };
    case "float64":  return { kind: "float64", value: v.value };
    case "boolean":  return { kind: "boolean", value: v.value };
    case "symbol":   return { kind: "symbol",  name: v.name };
    case "list":
      return { kind: "list", items: (v.items ?? []).map(decodeAny) };
    case "record": {
      const fields: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v.fields ?? {})) {
        fields[k] = decodeAny(val);
      }
      return { kind: "record", fields };
    }
    case "tagged":
      return { kind: "tagged", tag: v.tag, payload: decodeAny(v.payload!) };
    case "expression": {
      // Pass expressions verbatim (covers Root[], Polynomial[], etc.).
      return {
        kind: "expression",
        head: v.head,
        args: (v.args ?? []).map(decodeAny),
      };
    }
    default:
      return { kind: v.kind };
  }
}

function toBenchShape(out: V): unknown {
  switch (out.kind) {
    case "tagged":
      return { kind: "tagged", tag: out.tag, payload: decodeAny(out.payload!) };
    case "boolean":
      return { kind: "boolean", value: out.value };
    case "expression":
      // Root[Polynomial[], k] — pass through verbatim.
      return decodeAny(out);
    default:
      throw new Error(`toBenchShape: unexpected output kind ${out.kind}`);
  }
}

// ─── main ─────────────────────────────────────────────────────────────────────

interface BenchInput {
  a:   unknown;
  b?:  unknown;
  op:  string;
}

const raw = JSON.parse(readFileSync(0, "utf8")) as BenchInput;
const wb  = await loadWorkbench();

// Build the input record for the tool.  The `a` and `b` fields are already
// wire-encoded Root[] expressions — pass them through as-is.  The `op` is
// passed as a flag rather than an input field.
const inputValue = {
  kind:   "record",
  fields: {
    a: raw.a,
    ...(raw.b !== undefined ? { b: raw.b } : {}),
  },
};

let out: V;
try {
  out = await wb.run("alg-num-arith", inputValue, { op: raw.op }) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "Error", message: e.message ?? String(e) }) + "\n",
  );
  process.exit(0);
}

process.stdout.write(JSON.stringify(toBenchShape(out)) + "\n");
