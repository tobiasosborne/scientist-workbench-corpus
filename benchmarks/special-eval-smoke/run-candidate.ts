// =============================================================================
// benchmarks/special-eval-smoke/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges the smoke-bench's raw JSON wire format
//
//   in:  { "head": "<Erf|Erfc|Erfcx|Erfi|InverseErf|InverseErfc|BesselJ|BesselY|
//                   BesselI|BesselK>",
//          "args": [<number>, ...]   // arity 1 for Erf family, 2 for Bessel
//        }
//
//   out (success):
//        { "value": "<decimal-string>",
//          "method": "<lineage-tag>",
//          "achieved_precision": <int>,
//          "warnings": [<string>, ...] }
//
//   out (refusal):
//        { "kind": "tagged",
//          "tag": "special-eval/<class>",
//          "payload": {...} }
//
//   out (tool-err):
//        { "kind": "tool_error", "name": "...", "message": "..." }
//
// to the canonical Value protocol that `tools/special-eval/` speaks.
//
// Precision: we hardcode `precision = 10` (decimal digits), which the
// `special-eval` dispatcher reads as a positive integer in
// `flags.precision` (bigint) and routes via the boundary
// `precisionDecimal <= 15 ⇒ float64 lane` (see tool.ts:658, :785, :877,
// :984).  The B14 adapter's `tool_flags.precision = "53"` is the
// IEEE-754 mantissa-width labeling — not forwarded by the grader (yet),
// and not the value the tool's `flags.precision` argument expects.
// This run-candidate is the authoritative site for the lane decision
// until grade.ts wires `tool_flags` through (schema-v3 follow-up).
//
// Output decoding: at the float64 lane the tool returns
//   record{ value: tagged("bigfloat", record{mantissa,exponent,precision}),
//           method: string, achieved_precision: int, warnings: list<string> }
// (per `realSuccess` in tool.ts:497).  We decode the bigfloat via
// `bf.valueToBigFloat` + `bf.toString` to a decimal string at 20 dp
// (comfortably above float64's 15-16 dp).
//
// Workbench-package import strategy mirrors hypergeometric-pfq /
// meijer-g run-candidate.ts (ADR-0028 corpus-resident-bridge pattern):
// dynamic imports via WORKBENCH_ROOT, in-process via
// `executeToolDef(specialEvalDef, input, flags)`.
//
// Pairing: adapter at adapters/scientist-workbench/special-eval-smoke.toml
// (B14); verifier at golden/verify.ts (this bead, B15).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Resolve workbench root ──────────────────────────────────────────────────

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "scientist-workbench");

// ─── Dynamic imports through the workbench package tree ─────────────────────

const contractPath  = resolve(WORKBENCH_ROOT, "packages/contract/src/index.ts");
const protocolPath  = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");
const bigfloatPath  = resolve(WORKBENCH_ROOT, "packages/bigfloat/src/index.ts");
const specialEvalPath = resolve(WORKBENCH_ROOT, "tools/special-eval/tool.ts");

const contract = await import(contractPath) as {
  executeToolDef: (
    def: unknown,
    input: unknown,
    flags: Record<string, unknown>,
  ) => Promise<{ output: unknown }>;
};

const proto = await import(protocolPath) as {
  list: (items: unknown[]) => unknown;
  record: (fields: Record<string, unknown>) => unknown;
  str: (s: string) => unknown;
  float64FromNumber: (n: number) => unknown;
};

const bf = await import(bigfloatPath) as {
  valueToBigFloat: (v: unknown) => unknown;
  toString: (a: unknown, digits: number) => string;
};

const { def: specialEvalDef } = await import(specialEvalPath) as { def: unknown };

// ─── Raw-JSON input shape ───────────────────────────────────────────────────

interface RawInput {
  head: string;
  args: number[];
}

// ─── Output structural type (for decoding) ──────────────────────────────────

type V = {
  kind: string;
  items?: V[];
  fields?: Record<string, V>;
  value?: unknown;
  tag?: string;
  payload?: V;
};

// ─── Encode raw input → canonical Value protocol ────────────────────────────

function encodeInput(raw: RawInput): unknown {
  // special-eval's input schema: record{ head: string,
  //   args: list<float64> (real)  | record{re,im: list<float64>} (complex) }
  // All 10 smoke cases are real-axis; we always emit the list shape.
  const argValues = raw.args.map((x) => proto.float64FromNumber(x));
  return proto.record({
    head: proto.str(raw.head),
    args: proto.list(argValues),
  });
}

// ─── Decode candidate output → raw JSON ─────────────────────────────────────

function decodePayloadAny(v: V): unknown {
  switch (v.kind) {
    case "string":  return v.value;
    case "integer": return (v.value as bigint).toString();
    case "float64": return v.value;
    case "boolean": return v.value;
    case "list":    return (v.items ?? []).map(decodePayloadAny);
    case "record": {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v.fields ?? {})) {
        out[k] = decodePayloadAny(val);
      }
      return out;
    }
    case "tagged":
      return { kind: "tagged", tag: v.tag, payload: decodePayloadAny(v.payload!) };
    default:
      return null;
  }
}

function decodeStringList(v: V): string[] {
  if (v.kind !== "list") throw new Error(`expected list, got kind=${v.kind}`);
  return (v.items ?? []).map((it) => {
    if (it.kind !== "string") throw new Error(`expected string, got kind=${it.kind}`);
    return it.value as string;
  });
}

function decodeInteger(v: V): number {
  if (v.kind !== "integer") throw new Error(`expected integer, got kind=${v.kind}`);
  return Number(v.value);
}

function decodeString(v: V): string {
  if (v.kind !== "string") throw new Error(`expected string, got kind=${v.kind}`);
  return v.value as string;
}

function decodeBigFloatAsString(v: unknown, digits: number): string {
  // value field is tagged("bigfloat", record{mantissa, exponent, precision}).
  const bigfloat = bf.valueToBigFloat(v);
  return bf.toString(bigfloat, digits);
}

function decodeOutput(v: unknown): Record<string, unknown> {
  const vTyped = v as V;

  // Tagged refusal envelope (special-eval/{unknown-head, non-finite-input,
  // degenerate-shape, no-known-representation}).
  if (vTyped.kind === "tagged") {
    return {
      kind: "tagged",
      tag: vTyped.tag,
      payload: decodePayloadAny(vTyped.payload!),
    };
  }

  if (vTyped.kind !== "record") {
    throw new Error(`expected record or tagged output, got kind=${vTyped.kind}`);
  }

  const f = vTyped.fields!;
  // Success envelope: realSuccess() in tool.ts:497.
  // {value: bigfloat-tagged, method: string, achieved_precision: int,
  //  warnings: list<string>}
  const valueField = f["value"];
  if (!valueField) {
    throw new Error("output record missing 'value' field");
  }
  return {
    value: decodeBigFloatAsString(valueField, 20),
    method: decodeString(f["method"]!),
    achieved_precision: decodeInteger(f["achieved_precision"]!),
    warnings: decodeStringList(f["warnings"]!),
  };
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const raw = JSON.parse(readFileSync(0, "utf8")) as RawInput;

  let encoded: unknown;
  try {
    encoded = encodeInput(raw);
  } catch (err) {
    const e = err as Error;
    process.stdout.write(
      JSON.stringify({
        kind: "tool_error",
        name: "InputEncodeFailed",
        message: e.message,
      }) + "\n",
    );
    return;
  }

  // Float64 lane: precision=10 decimal digits ⇒ dispatchReal /
  // dispatchRealBessel routes to the float64 arm; achieved_precision=53.
  // See tool.ts:658 + :877 for the dispatch threshold.
  const flags: Record<string, unknown> = {
    precision: 10n,
  };

  let out: unknown;
  try {
    const result = await contract.executeToolDef(specialEvalDef, encoded, flags);
    out = result.output;
  } catch (err) {
    const e = err as Error & { name?: string };
    process.stdout.write(
      JSON.stringify({
        kind: "tool_error",
        name: e.name ?? "Error",
        message: e.message ?? String(e),
      }) + "\n",
    );
    return;
  }

  let decoded: Record<string, unknown>;
  try {
    decoded = decodeOutput(out);
  } catch (err) {
    const e = err as Error;
    process.stdout.write(
      JSON.stringify({
        kind: "tool_error",
        name: "OutputDecodeFailed",
        message: e.message,
      }) + "\n",
    );
    return;
  }

  process.stdout.write(JSON.stringify(decoded) + "\n");
}

await main();
