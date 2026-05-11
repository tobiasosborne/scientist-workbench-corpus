// =============================================================================
// benchmarks/sdp-sdplib/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges raw JSON wire format ↔ scientist-workbench canonical Value
// protocol for the SDP cone-solver tier (ADR-0030 §C with PSDCone).
// Mirrors benchmarks/lp-netlib/run-candidate.ts in pattern; the
// per-cone encoding differs (PSDCone carries `[size: integer,
// indices: list<integer>]`).
//
// Pipeline per case:
//
//   stdin (raw JSON):
//       { minimize:  { c: number[] },
//         subjectTo: { Ax_eq_b?: { A: number[][], b: number[] },
//                      cones:   [{ head: "PSDCone", size, indices }, ...] },
//         precision?: number,
//         max_iter?:  number }
//
//   ↓ encodeInput → canonical Value protocol
//
//   wb.run(<tool>, value, flags?)
//       — <tool> defaults to "sdp-solve"; override via env CANDIDATE_TOOL
//
//   ↓ decodeOutput → raw JSON
//
//   stdout:
//       success record { status, x, dual, slack, objective?,
//                        achieved_precision?, iterations, method,
//                        condition_estimate, warnings }
//       OR tagged refusal { kind: "tagged", tag, payload }
//
// Tool-not-registered envelope: emitted as
// `scientist-workbench/tool-not-registered` so the verifier records a
// useful detail string when the candidate hasn't been built yet.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "scientist-workbench");

const CANDIDATE_TOOL: string = process.env["CANDIDATE_TOOL"] ?? "sdp-solve";

const composePath  = resolve(WORKBENCH_ROOT, "packages/compose/src/index.ts");
const protocolPath = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");

const { loadWorkbench } = await import(composePath);
const proto = await import(protocolPath);

const float64FromNumber: (n: number) => unknown = proto.float64FromNumber;
const float64ToNumber:   (v: unknown) => number = proto.float64ToNumber;
const list:    (items: unknown[]) => unknown                       = proto.list;
const record:  (fields: Record<string, unknown>) => unknown        = proto.record;
const expr:    (head: string, args: readonly unknown[]) => unknown = proto.expr;
const int:     (v: bigint | number | string) => unknown            = proto.int;
const tagged:  (tag: string, payload: unknown) => unknown          = proto.tagged;

type V = {
  kind: string;
  items?: V[];
  fields?: Record<string, V>;
  value?: unknown;
  head?: string;
  args?: V[];
  tag?: string;
  payload?: V;
};

interface RawCone {
  head: string;
  indices?: number[];
  size?: number;
  i?: number;
  j?: number;
  k?: number;
  alpha?: { num: string; den: string } | string;
}

interface RawInput {
  minimize: { c: number[]; Q?: number[][] };
  subjectTo: {
    Ax_eq_b?: { A: number[][]; b: number[] };
    cones: RawCone[];
  };
  precision?: number;
  max_iter?: number;
}

function encodeFloatList(xs: readonly number[]): unknown {
  return list(xs.map((x) => float64FromNumber(x)));
}

function encodeFloatMatrix(M: readonly (readonly number[])[]): unknown {
  return list(M.map(encodeFloatList));
}

function encodeIntList(xs: readonly number[]): unknown {
  return list(xs.map((i) => int(BigInt(i))));
}

function encodeCone(c: RawCone): unknown {
  switch (c.head) {
    case "PSDCone":
      if (c.size === undefined || !c.indices)
        throw new Error(`PSDCone missing size or indices`);
      return expr("PSDCone", [int(BigInt(c.size)), encodeIntList(c.indices)]);
    case "NonNegCone":
    case "SOCone":
    case "ZeroCone":
      if (!c.indices) throw new Error(`${c.head} missing indices`);
      return expr(c.head, [encodeIntList(c.indices)]);
    case "ExpCone":
      if (c.i === undefined || c.j === undefined || c.k === undefined)
        throw new Error(`ExpCone missing i/j/k`);
      return expr("ExpCone", [int(BigInt(c.i)), int(BigInt(c.j)), int(BigInt(c.k))]);
    case "PowCone":
      throw new Error(`PowCone encoding not yet wired`);
    default:
      throw new Error(`unknown cone head: ${c.head}`);
  }
}

function encodeMinimize(min: RawInput["minimize"]): unknown {
  const fields: Record<string, unknown> = { c: encodeFloatList(min.c) };
  if (min.Q !== undefined) fields["Q"] = encodeFloatMatrix(min.Q);
  return record(fields);
}

function encodeSubjectTo(sub: RawInput["subjectTo"]): unknown {
  const fields: Record<string, unknown> = {
    cones: list(sub.cones.map(encodeCone)),
  };
  if (sub.Ax_eq_b !== undefined) {
    fields["Ax_eq_b"] = record({
      A: encodeFloatMatrix(sub.Ax_eq_b.A),
      b: encodeFloatList(sub.Ax_eq_b.b),
    });
  }
  return record(fields);
}

function encodeInput(raw: RawInput): unknown {
  const fields: Record<string, unknown> = {
    minimize:  encodeMinimize(raw.minimize),
    subjectTo: encodeSubjectTo(raw.subjectTo),
  };
  if (raw.precision !== undefined) fields["precision"] = float64FromNumber(raw.precision);
  if (raw.max_iter  !== undefined) fields["max_iter"]  = int(BigInt(raw.max_iter));
  return record(fields);
}

function decodeFloatList(v: V): number[] {
  if (v.kind !== "list") throw new Error(`expected list, got kind=${v.kind}`);
  return v.items!.map((it) => {
    if (it.kind !== "float64") throw new Error(`expected float64, got kind=${it.kind}`);
    return float64ToNumber(it);
  });
}

function decodeFloat(v: V): number {
  if (v.kind !== "float64") throw new Error(`expected float64, got kind=${v.kind}`);
  return float64ToNumber(v);
}

function decodeInteger(v: V): number {
  if (v.kind !== "integer") throw new Error(`expected integer, got kind=${v.kind}`);
  return Number(v.value as string);
}

function decodeString(v: V): string {
  if (v.kind !== "string") throw new Error(`expected string, got kind=${v.kind}`);
  return v.value as string;
}

function decodeStringList(v: V): string[] {
  if (v.kind !== "list") throw new Error(`expected list, got kind=${v.kind}`);
  return v.items!.map(decodeString);
}

function decodeAny(v: V): unknown {
  switch (v.kind) {
    case "string":  return v.value;
    case "integer": return v.value as string;
    case "float64": return float64ToNumber(v);
    case "boolean": return v.value;
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
    throw new Error(`expected record (success path), got kind=${v.kind}`);
  }
  const f = v.fields!;
  const out: Record<string, unknown> = {
    status:             decodeString(f["status"]!),
    x:                  decodeFloatList(f["x"]!),
    dual:               decodeFloatList(f["dual"]!),
    slack:              decodeFloatList(f["slack"]!),
    iterations:         decodeInteger(f["iterations"]!),
    method:             decodeString(f["method"]!),
    condition_estimate: decodeFloat(f["condition_estimate"]!),
    warnings:           decodeStringList(f["warnings"]!),
  };
  if ("objective"          in f) out["objective"]          = decodeFloat(f["objective"]!);
  if ("achieved_precision" in f) out["achieved_precision"] = decodeFloat(f["achieved_precision"]!);
  return out;
}

const raw = JSON.parse(readFileSync(0, "utf8")) as RawInput;
const input = encodeInput(raw);

// loadWorkbench's default cwd-walk to find `tools/` doesn't find it
// when the corpus runner spawns this script from the corpus repo
// root. Pass `toolsRoot` explicitly so the registry resolves
// deterministically regardless of cwd.
const wb = await loadWorkbench({ toolsRoot: resolve(WORKBENCH_ROOT, "tools") });

let outValue: V;
try {
  outValue = await wb.run(CANDIDATE_TOOL, input) as V;
} catch (e) {
  const detail = (e as Error).message ?? String(e);
  outValue = tagged("scientist-workbench/tool-not-registered", record({
    tool:   { kind: "string", value: CANDIDATE_TOOL } as unknown,
    detail: { kind: "string", value: detail.slice(0, 500) } as unknown,
  })) as V;
}

const decoded = decodeOutput(outValue);
process.stdout.write(JSON.stringify(decoded) + "\n");
