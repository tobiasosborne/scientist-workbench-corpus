// =============================================================================
// benchmarks/hypergeometric-pfq/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges the bench's language-neutral JSON wire format
//
//   in:  { "a": [{"re": "<dec-or-rational>", "im": "<dec-or-rational>"}, ...],
//          "b": [{"re": "<dec-or-rational>", "im": "<dec-or-rational>"}, ...],
//          "z":  {"re": "<dec-or-rational>", "im": "<dec-or-rational>"},
//          "precision": <int> }
//   out: success ⇒ { "value": {"re": "<dec>", "im": "<dec>"},
//                    "achieved_precision": <int>,
//                    "method": "<string>",
//                    "n_terms": <int>,
//                    "working_precision": <int>,
//                    "warnings": [<string>, ...] }
//        refusal ⇒ { "kind": "tagged",
//                    "tag": "hypergeometric-pfq/<class>",
//                    "payload": {...} }
//        tool-err ⇒ { "kind": "tool_error", "name": "...", "message": "..." }
//
// to the canonical Value protocol that `tools/hypergeometric-pfq/` speaks.
//
// This file lives in scientist-workbench-corpus (ADR-0028).  The @workbench/*
// workspace packages live in the workbench repo, not the corpus.  Bun resolves
// workspace aliases by walking upward from the *file's directory*, so a
// corpus-resident script cannot use @workbench/* as bare specifiers.  Instead
// we import directly from the workbench package source files using the
// WORKBENCH_ROOT environment variable, which the corpus grader makes available
// (and which defaults to the conventional side-by-side sibling path).
//
// In-process invocation matches the precedent set by the workbench bench.
// The contract (schema validation, output validation, provenance write) holds
// byte-identically across in-process and subprocess surfaces (ADR-0012).
//
// hypergeometric-pfq is an arbprec-tier tool (ADR-0020): arbprec: true, with
// the standard --precision flag.  After the rn2 fix, runWorkbench validates
// flags against the merged tool-facing schema which includes the precision slot
// for arbprec tools; so wb.hypergeometricPfq(input, { precision: BigInt(p) })
// works through the typed barrel.
//
// The adapter: platform_pinned = false (arbprec tools are bit-identical
// cross-platform forever — ADR-0020 §Consequences).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

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

const composePath   = resolve(WORKBENCH_ROOT, "packages/compose/src/index.ts");
const protocolPath  = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");
const bigfloatPath  = resolve(WORKBENCH_ROOT, "packages/bigfloat/src/index.ts");

const { loadWorkbench, typed } = await import(composePath) as {
  loadWorkbench: () => Promise<unknown>;
  typed: (wb: unknown) => {
    hypergeometricPfq: (input: unknown, flags: { precision: bigint }) => Promise<unknown>;
  };
};
const proto = await import(protocolPath) as {
  list:   (items: unknown[]) => unknown;
  record: (fields: Record<string, unknown>) => unknown;
};
const bf = await import(bigfloatPath) as {
  decimalToBinaryPrecision: (dps: number) => number;
  cfromStrings: (re: string, im: string, prec: number) => unknown;
  bigcomplexToValue: (bc: unknown) => unknown;
  valueToBigComplex: (v: unknown) => { re: unknown; im: unknown };
  toString: (a: unknown, digits: number) => string;
};

// Helpers
const list   = proto.list;
const record = proto.record;

// ─── Types ────────────────────────────────────────────────────────────────────

interface RawComplex {
  re: string;
  im: string;
}

interface RawInput {
  a: readonly RawComplex[];
  b: readonly RawComplex[];
  z: RawComplex;
  precision: number;
}

type V = {
  kind: string;
  items?: V[];
  fields?: Record<string, V>;
  value?: string;
  tag?: string;
  payload?: V;
};

// ─── Rational string expansion ───────────────────────────────────────────────
//
// The bench corpus uses rational parameter strings like "1/3" (exact-input
// convention matching Wolfram's Hypergeometric2F1[1, 1, 2, 1/3]).  The bigfloat
// substrate expects decimal strings; we expand p/q at sufficient working
// precision and pass the resulting decimal string.  The expansion precision is
// set 16 bits beyond the request to absorb the dec→bin round-off.

function _expandRational(s: string, workBits: number): string {
  const trimmed = s.trim();
  if (!trimmed.includes("/")) return trimmed;
  const [pStr, qStr] = trimmed.split("/");
  const p = BigInt(pStr!);
  const q = BigInt(qStr!);
  if (q === 0n) throw new Error(`rational ${s} has zero denominator`);
  // Decimal-digit count to safely round-trip workBits bits.
  const dps = Math.ceil(workBits * 0.30103) + 16;
  // Long-division: produce `dps` digits past the decimal point.
  const sign = (p < 0n) !== (q < 0n) ? "-" : "";
  let pa = p < 0n ? -p : p;
  const qa = q < 0n ? -q : q;
  const intPart = pa / qa;
  let rem = pa - intPart * qa;
  let frac = "";
  for (let i = 0; i < dps; i++) {
    rem *= 10n;
    const digit = rem / qa;
    rem = rem - digit * qa;
    frac += digit.toString();
    if (rem === 0n) break;
  }
  return sign + intPart.toString() + (frac ? "." + frac : "");
}

function encodeComplex(c: RawComplex, workBits: number): unknown {
  const re = _expandRational(c.re, workBits);
  const im = _expandRational(c.im, workBits);
  return bf.bigcomplexToValue(bf.cfromStrings(re, im, workBits));
}

function encodeInput(raw: RawInput): { input: unknown; precision: number } {
  // The tool stores BigComplex parameters at whatever precision their mantissas
  // already carry; we feed them at decimalToBinaryPrecision(precision) + 64
  // working bits so the tool's own working-precision bumps for cancellation
  // aren't capped by the input's bit budget.
  const workBits = bf.decimalToBinaryPrecision(raw.precision) + 64;
  const aValues = raw.a.map((c) => encodeComplex(c, workBits));
  const bValues = raw.b.map((c) => encodeComplex(c, workBits));
  const zValue  = encodeComplex(raw.z, workBits);
  return {
    input: record({
      a: list(aValues),
      b: list(bValues),
      z: zValue,
    }),
    precision: raw.precision,
  };
}

// ─── Output decoding ─────────────────────────────────────────────────────────

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

function decodeBigComplexAsStrings(v: unknown, dps: number): { re: string; im: string } {
  // Decode the tagged BigComplex back to a {re, im} decimal-string pair
  // formatted at dps + 5 decimal places.  We pad the formatting precision
  // by 5 so the verifier sees a value with ≥dps significant digits to
  // compare against the pinned truth.
  const bc = bf.valueToBigComplex(v);
  return {
    re: bf.toString(bc.re, dps + 5),
    im: bf.toString(bc.im, dps + 5),
  };
}

function decodePayloadAny(v: V): unknown {
  switch (v.kind) {
    case "string":  return v.value;
    case "integer": return Number(v.value);
    case "boolean": return (v as unknown as { value: boolean }).value;
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

function decodeOutput(v: unknown, requestedPrecision: number): Record<string, unknown> {
  const vTyped = v as V;
  if (vTyped.kind === "tagged") {
    return {
      kind:    "tagged",
      tag:     vTyped.tag,
      payload: decodePayloadAny(vTyped.payload!),
    };
  }
  if (vTyped.kind !== "record") {
    throw new Error(`expected record or tagged, got kind=${vTyped.kind}`);
  }
  const f = vTyped.fields!;
  return {
    value:               decodeBigComplexAsStrings(f["value"]!, requestedPrecision),
    achieved_precision:  decodeInteger(f["achieved_precision"]!),
    method:              decodeString(f["method"]!),
    n_terms:             decodeInteger(f["n_terms"]!),
    working_precision:   decodeInteger(f["working_precision"]!),
    warnings:            decodeStringList(f["warnings"]!),
  };
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const raw = JSON.parse(readFileSync(0, "utf8")) as RawInput;

  let prepared: { input: unknown; precision: number };
  try {
    prepared = encodeInput(raw);
  } catch (err) {
    const e = err as Error;
    process.stdout.write(
      JSON.stringify({
        kind:    "tool_error",
        name:    "InputEncodeFailed",
        message: e.message,
      }) + "\n",
    );
    return;
  }

  let out: unknown;
  try {
    const workbench = await loadWorkbench();
    const wb = typed(workbench);
    out = await wb.hypergeometricPfq(prepared.input, {
      precision: BigInt(prepared.precision),
    });
  } catch (err) {
    const e = err as Error & { name?: string };
    process.stdout.write(
      JSON.stringify({
        kind:    "tool_error",
        name:    e.name ?? "Error",
        message: e.message ?? String(e),
      }) + "\n",
    );
    return;
  }

  const decoded = decodeOutput(out, prepared.precision);
  process.stdout.write(JSON.stringify(decoded) + "\n");
}

await main();
