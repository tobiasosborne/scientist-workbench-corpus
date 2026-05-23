// =============================================================================
// benchmarks/gamma-anchor/run-candidate.ts -- corpus-resident bench adapter
// =============================================================================
//
// Bridges the gamma-anchor bench's raw JSON wire format to the
// tools/special-eval canonical Value protocol.  Mirrors besselj-anchor's
// run-candidate.ts (B19) at the arb-prec lane.
//
// Wire-format deltas from besselj-anchor:
//   - 19 corpus heads vs 6.  4 corpus heads (GammaPDerivative,
//     IncompleteBeta, InverseIncompleteGammaP, InverseIncompleteGammaQ)
//     are NOT in tools/special-eval ADMITTED_HEADS; this adapter emits
//     special-eval/unknown-head for them (matches what the dispatcher
//     would emit if called -- but we short-circuit to avoid the route
//     through `executeToolDef`).
//   - Discriminated args: a/b/m/n carry {kind: "integer" | "half-integer"
//     | "decimal", value: "<60-char|fraction>"}.  Parsed via parseArg().
//   - Multiple per-head arg shapes:
//       Unary z: {z} -> args=[Number(z)]
//       Binary (a, z):    Polygamma(m, z), IncompleteGamma*(a, z),
//                         Pochhammer(a, n), GammaPDerivative(a, z),
//                         InverseIncompleteGamma{P,Q}(a, q-via-z)
//       Binary (a, b):    Beta, LogBeta, GammaRatio, GammaDeltaRatio
//       Ternary (z, a, b): IncompleteBeta
//
// in:  { "head": "<corpus head>",
//        "z?":   "<60-char>" | {re,im},
//        "a?":   {kind, value},
//        "b?":   {kind, value},
//        "m?":   {kind, value},
//        "n?":   {kind, value},
//        "id":   "<case id>" }
//
// out (success):
//        { "value": "<decimal>" | {re, im}, "method": "...",
//          "achieved_precision": N, "warnings": [...] }
//
// out (refusal):
//        { "kind": "tagged", "tag": "special-eval/<class>",
//          "payload": {...} }
//
// out (tool-err):
//        { "kind": "tool_error", "name": "...", "message": "..." }
//
// Lane: precision = 200 (decimal digits) routes the arb-prec lane.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// -- Resolve workbench root ---------------------------------------------------

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "scientist-workbench");

// -- Dynamic imports through the workbench package tree ----------------------

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

// -- Raw-JSON input shape -----------------------------------------------------

type ZScalar = string;
type ZComplex = { re: string; im: string };
type Z = ZScalar | ZComplex;
type ArgKV = { kind: "integer" | "half-integer" | "decimal"; value: string };

interface RawInput {
  head: string;
  z?: Z;
  a?: ArgKV;
  b?: ArgKV;
  m?: ArgKV;
  n?: ArgKV;
  id?: string;
}

function isComplex(z: Z): z is ZComplex {
  return typeof z === "object" && z !== null && "re" in z && "im" in z;
}

function parseArg(a: ArgKV): number {
  const s = a.value.trim();
  const fracMatch = /^(-?\d+)\/(\d+)$/.exec(s);
  if (fracMatch) {
    return Number(fracMatch[1]) / Number(fracMatch[2]);
  }
  return Number(s);
}

// -- Corpus heads NOT in tools/special-eval ADMITTED_HEADS -------------------

const UNADMITTED_HEADS = new Set<string>([
  "GammaPDerivative",
  "IncompleteBeta",
  "InverseIncompleteGammaP",
  "InverseIncompleteGammaQ",
]);

// -- Output structural type (for decoding) -----------------------------------

type V = {
  kind: string;
  items?: V[];
  fields?: Record<string, V>;
  value?: unknown;
  tag?: string;
  payload?: V;
};

// -- Per-head arg encoding ----------------------------------------------------
//
// Returns the args field (list or record) shape per head, given the raw input
// discriminated args.  Handles both real-args and complex-z dispatch.

function encodeArgs(raw: RawInput): unknown {
  const f64 = (n: number) => proto.float64FromNumber(n);
  const head = raw.head;

  // Arity-1 spine: head = {Gamma, LogGamma, Digamma, Trigamma, BarnesG, Hyperfactorial}
  // -> args = [Number(z)]
  const arity1 = new Set([
    "Gamma", "LogGamma", "Digamma", "Trigamma", "BarnesG", "Hyperfactorial",
  ]);
  if (arity1.has(head)) {
    const z = raw.z!;
    if (isComplex(z)) {
      return proto.record({
        re: proto.list([f64(Number(z.re))]),
        im: proto.list([f64(Number(z.im))]),
      });
    }
    return proto.list([f64(Number(z))]);
  }

  // Polygamma(m, z): m is integer, z scalar or complex
  if (head === "Polygamma") {
    const m = parseArg(raw.m!);
    const z = raw.z!;
    if (isComplex(z)) {
      return proto.record({
        re: proto.list([f64(m), f64(Number(z.re))]),
        im: proto.list([f64(0), f64(Number(z.im))]),
      });
    }
    return proto.list([f64(m), f64(Number(z))]);
  }

  // Pochhammer(a, n): a kv, n integer kv
  if (head === "Pochhammer") {
    const a = parseArg(raw.a!);
    const n = parseArg(raw.n!);
    return proto.list([f64(a), f64(n)]);
  }

  // IncompleteGamma{Upper,Lower,P,Q}(a, z)
  if (head === "IncompleteGammaUpper" || head === "IncompleteGammaLower"
      || head === "IncompleteGammaP" || head === "IncompleteGammaQ") {
    const a = parseArg(raw.a!);
    const z = raw.z!;
    if (isComplex(z)) {
      return proto.record({
        re: proto.list([f64(a), f64(Number(z.re))]),
        im: proto.list([f64(0), f64(Number(z.im))]),
      });
    }
    return proto.list([f64(a), f64(Number(z))]);
  }

  // Beta(a, b) / LogBeta(a, b) / GammaRatio(a, b) / GammaDeltaRatio(a, delta)
  if (head === "Beta" || head === "LogBeta" || head === "GammaRatio"
      || head === "GammaDeltaRatio") {
    const a = parseArg(raw.a!);
    const b = parseArg(raw.b!);
    return proto.list([f64(a), f64(b)]);
  }

  // Unadmitted heads: candidate emits special-eval/unknown-head before
  // dispatch.  But for safety we still build a placeholder list; the
  // top-level main() short-circuits before calling encodeArgs for
  // these heads.
  return proto.list([]);
}

function encodeInput(raw: RawInput): unknown {
  return proto.record({
    head: proto.str(raw.head),
    args: encodeArgs(raw),
  });
}

// -- Decode candidate output -> raw JSON -------------------------------------

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
  const bigfloat = bf.valueToBigFloat(v);
  return bf.toString(bigfloat, digits);
}

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
  const valueField = f["value"];
  if (!valueField) throw new Error("output record missing 'value' field");
  return {
    value: decodeValueField(valueField, 60),
    method: decodeString(f["method"]!),
    achieved_precision: decodeInteger(f["achieved_precision"]!),
    warnings: decodeStringList(f["warnings"]!),
  };
}

// -- Main ---------------------------------------------------------------------

async function main(): Promise<void> {
  const raw = JSON.parse(readFileSync(0, "utf8")) as RawInput;

  // Short-circuit for the 4 corpus heads not in tools/special-eval ADMITTED_HEADS.
  if (UNADMITTED_HEADS.has(raw.head)) {
    process.stdout.write(JSON.stringify({
      kind: "tagged",
      tag: "special-eval/unknown-head",
      payload: {
        head: raw.head,
        reason: `head '${raw.head}' is not in tools/special-eval ADMITTED_HEADS (corpus head outside scientist-workbench v0.2 capability)`,
      },
    }) + "\n");
    return;
  }

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

  // Hardcoded precision = 200 decimal digits => arb-prec lane.
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
