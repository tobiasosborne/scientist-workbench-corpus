// =============================================================================
// benchmarks/erf-anchor/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges the erf-anchor bench's raw JSON wire format
//
//   in:  { "head": "Erf" | "Erfc" | "Erfcx" | "Erfi" | "InverseErf" | "InverseErfc",
//          "z":    "<60-char decimal string>"
//                  | { "re": "<60-char>", "im": "<60-char>" } }
//
//   out (success):
//        { "value":             "<decimal-string>"
//                                | { "re": "<decimal>", "im": "<decimal>" },
//          "method":            "<lineage-tag>",
//          "achieved_precision": <int>,
//          "warnings":          [<string>, ...] }
//
//   out (refusal):
//        { "kind": "tagged",
//          "tag":  "special-eval/<class>",
//          "payload": { ... } }
//
//   out (tool-err):
//        { "kind": "tool_error", "name": "...", "message": "..." }
//
// to the canonical Value protocol that `tools/special-eval/` speaks.
//
// Lane choice: precision = 200 (decimal digits) routes the arb-prec
// lane in special-eval's dispatcher (`precisionDecimal > 15 ⇒ arb-prec`).
// 200 dp is the workbench's gold-tier match-or-beat target; the bench's
// 48-digit gold-gold threshold sits comfortably inside that.  The
// adapter file's `tool_flags.precision = "200"` documents this
// informationally — `grade.ts` does not yet forward `tool_flags` to
// run-candidates (schema-v3 follow-up), so this run-candidate is the
// authoritative site for the lane decision.
//
// z encoding (real):    args = list<float64>([Number(z)]).  The
//                       60-char decimal string is the IEEE-754 exact
//                       representation of the original float64 input
//                       (workbench bench generator uses
//                       `Number(z).toString(60)` round-trip
//                       construction), so `Number(z)` recovers it
//                       losslessly.
//
// z encoding (complex): args = record{re: list<float64>([Number(z.re)]),
//                                     im: list<float64>([Number(z.im)])}.
//                       Same round-trip lossless logic, applied
//                       component-wise.
//
// Sentinel tokens (T6 edge tier): "Infinity" / "-Infinity" / "NaN"
// strings are passed straight through to Number().  The tool's
// non-finite-input filter (TAG_NON_FINITE) catches these and emits a
// tagged-refusal envelope, which is the honest behaviour the verifier
// expects for T6 cases.
//
// Output decoding: at arb-prec the tool returns
//   record{ value: tagged("bigfloat", ...) | tagged("bigcomplex", ...),
//           method: string, achieved_precision: int, warnings: list<string> }
// (per `realSuccess` / `complexSuccess` in tool.ts).  We decode bigfloat
// via `bf.valueToBigFloat` + `bf.toString` at 60 dp (matches the gold
// oracles' precision; the verifier compares at 60 dp).  For bigcomplex
// we decode the {re, im} bigfloat pair and emit `{re: str, im: str}`.
//
// Mirrors benchmarks/special-eval-smoke/run-candidate.ts (B15) at the
// arb-prec lane.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Resolve workbench root ──────────────────────────────────────────────────

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "scientist-workbench");

// ─── Dynamic imports through the workbench package tree ─────────────────────

const contractPath    = resolve(WORKBENCH_ROOT, "packages/contract/src/index.ts");
const protocolPath    = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");
const bigfloatPath    = resolve(WORKBENCH_ROOT, "packages/bigfloat/src/index.ts");
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

type ZScalar = string;
type ZComplex = { re: string; im: string };

interface RawInput {
  head: string;
  z: ZScalar | ZComplex;
}

function isComplex(z: ZScalar | ZComplex): z is ZComplex {
  return typeof z === "object" && z !== null && "re" in z && "im" in z;
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
  if (isComplex(raw.z)) {
    return proto.record({
      head: proto.str(raw.head),
      args: proto.record({
        re: proto.list([proto.float64FromNumber(Number(raw.z.re))]),
        im: proto.list([proto.float64FromNumber(Number(raw.z.im))]),
      }),
    });
  }
  return proto.record({
    head: proto.str(raw.head),
    args: proto.list([proto.float64FromNumber(Number(raw.z))]),
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

// bigcomplex on the wire: tagged("bigcomplex", record{re: bigfloat,
// im: bigfloat}).  Decode each side via the bigfloat helper.
function decodeBigComplexAsObject(v: V, digits: number): { re: string; im: string } {
  if (v.kind !== "tagged" || v.tag !== "bigcomplex") {
    throw new Error(`expected tagged 'bigcomplex', got kind=${v.kind} tag=${v.tag}`);
  }
  const payload = v.payload!;
  if (payload.kind !== "record") {
    throw new Error(`bigcomplex payload must be record, got kind=${payload.kind}`);
  }
  const reField = payload.fields!["re"];
  const imField = payload.fields!["im"];
  if (!reField || !imField) {
    throw new Error("bigcomplex payload missing 're' or 'im' field");
  }
  return {
    re: decodeBigFloatAsString(reField, digits),
    im: decodeBigFloatAsString(imField, digits),
  };
}

function decodeValueField(v: V, digits: number): string | { re: string; im: string } {
  // value is bigfloat (real) or bigcomplex (complex) — both tagged.
  if (v.kind === "tagged" && v.tag === "bigfloat") {
    return decodeBigFloatAsString(v, digits);
  }
  if (v.kind === "tagged" && v.tag === "bigcomplex") {
    return decodeBigComplexAsObject(v, digits);
  }
  throw new Error(`value field has unexpected encoding: kind=${v.kind} tag=${v.tag}`);
}

function decodeOutput(v: unknown): Record<string, unknown> {
  const vTyped = v as V;

  // Tagged refusal envelope (special-eval/{unknown-head, non-finite-input,
  // degenerate-shape, no-known-representation}).
  if (vTyped.kind === "tagged" && (vTyped.tag ?? "").startsWith("special-eval/")) {
    return {
      kind: "tagged",
      tag: vTyped.tag,
      payload: decodePayloadAny(vTyped.payload!),
    };
  }

  if (vTyped.kind !== "record") {
    throw new Error(`expected record or tagged-refusal output, got kind=${vTyped.kind}`);
  }

  const f = vTyped.fields!;
  // Success envelope: realSuccess() / complexSuccess() in tool.ts.
  const valueField = f["value"];
  if (!valueField) throw new Error("output record missing 'value' field");
  return {
    value: decodeValueField(valueField, 60),
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

  // Arb-prec lane: precision=200 decimal digits ⇒ dispatchReal /
  // dispatchComplex routes to the arb-prec arm (precision > 15 ⇒ arb-prec
  // per tool.ts threshold).  achieved_precision reported by the tool
  // varies by head/regime; the verifier checks 0 ≤ ap ≤ 200.
  const flags: Record<string, unknown> = {
    precision: 200n,
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
