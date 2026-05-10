// =============================================================================
// benchmarks/meijer-g/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges the bench's language-neutral JSON wire format
//
//   in:  { "an": [{re, im}, ...], "ap": [...], "bm": [...], "bq": [...],
//          "z": {re, im}, "precision": <int>,
//          "request_mode"?: "auto" | "symbolic-required" | "numerical-required",
//          "force_method"?: "symbolic" | "slater" | "contour" | "asymptotic" }
//
//   out: symbolic success ⇒
//            { "kind": "symbolic", "rule", "source", "note", "method",
//              "expr": <opaque AST blob> }
//        numerical success ⇒
//            { "kind": "numerical",
//              "value": {re, im},
//              "achieved_precision", "method",
//              "working_precision", "warnings", "diagnostics" }
//        tagged refusal ⇒
//            { "kind": "tagged", "tag": "meijer-g/<class>",
//              "payload": { reason, ruled_out_methods } }
//        tool-err ⇒
//            { "kind": "tool_error", "name": "...", "message": "..." }
//
// to the canonical Value protocol that `tools/meijer-g/` speaks.
//
// This file lives in scientist-workbench-corpus (ADR-0028).  The @workbench/*
// workspace packages live in the workbench repo, not the corpus.  Bun resolves
// workspace aliases by walking upward from the *file's directory*, so a
// corpus-resident script cannot use @workbench/* as bare specifiers.  Instead
// we import directly from the workbench package source files using the
// WORKBENCH_ROOT environment variable, which the corpus grader makes available
// (and which defaults to the conventional side-by-side sibling path).
//
// Invocation surface: in-process via `executeToolDef` directly
// (NOT `@workbench/compose`'s `runWorkbench`).  Reason: the lc1/rn2 gap —
// `runWorkbench` validates partial flags against `def.flags` directly; for an
// arbprec tool the `--precision` flag is merged in by the *subprocess* runner
// only, and passing `{ precision: 50n }` to `runWorkbench` fails as an unknown
// flag.  `executeToolDef` is the contract surface both fan out to (ADR-0012)
// — schema validation in/out, provenance write — so the bench's results are
// admissible byte-identically.  Same workaround as the workbench-side
// `bench/meijer-g/run-candidate.ts` and
// `benchmarks/hypergeometric-pfq/run-candidate.ts`.
//
// meijer-g is an arbprec-tier tool (ADR-0020): arbprec: true, with the
// standard --precision flag.  The adapter is platform_pinned = false (arbprec
// tools are bit-identical cross-platform forever — ADR-0020 §Consequences).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Resolve workbench root ───────────────────────────────────────────────────
//
// The corpus grader sets WORKBENCH_ROOT when it spawns run-candidate.ts.
// The default fallback assumes the conventional side-by-side layout:
//   ~/Projects/scientist-workbench/         ← WORKBENCH_ROOT
//   ~/Projects/scientist-workbench-corpus/  ← this repo

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "scientist-workbench");

// ─── Dynamic imports (file paths, resolved through workbench) ────────────────

const contractPath  = resolve(WORKBENCH_ROOT, "packages/contract/src/index.ts");
const bigfloatPath  = resolve(WORKBENCH_ROOT, "packages/bigfloat/src/index.ts");
const protocolPath  = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");
const meijerGPath   = resolve(WORKBENCH_ROOT, "tools/meijer-g/tool.ts");

const contract = await import(contractPath) as {
  executeToolDef: (
    def: unknown,
    input: unknown,
    flags: Record<string, unknown>,
  ) => Promise<{ output: unknown }>;
};

const bf = await import(bigfloatPath) as {
  decimalToBinaryPrecision: (dps: number) => number;
  cfromStrings: (re: string, im: string, prec: number) => unknown;
  bigcomplexToValue: (bc: unknown) => unknown;
  valueToBigComplex: (v: unknown) => { re: unknown; im: unknown };
  toString: (a: unknown, digits: number) => string;
};

const proto = await import(protocolPath) as {
  list:   (items: unknown[]) => unknown;
  record: (fields: Record<string, unknown>) => unknown;
  str:    (s: string) => unknown;
};

const { def: meijerGDef } = await import(meijerGPath) as { def: unknown };

// ─── Types ────────────────────────────────────────────────────────────────────

interface RawComplex {
  re: string;
  im: string;
}

interface RawInput {
  an: readonly RawComplex[];
  ap: readonly RawComplex[];
  bm: readonly RawComplex[];
  bq: readonly RawComplex[];
  z: RawComplex;
  precision: number;
  request_mode?: string;
  force_method?: string;
}

type V = {
  kind: string;
  items?: V[];
  fields?: Record<string, V>;
  value?: string | boolean;
  tag?: string;
  payload?: V;
  head?: string;
  args?: V[];
  name?: string;
  num?: string;
  den?: string;
};

// ─── Rational string expansion ───────────────────────────────────────────────
//
// The bench corpus uses rational parameter strings like "1/2".  The bigfloat
// substrate expects decimal strings; we expand p/q at sufficient working
// precision before passing to cfromStrings.  Same implementation as the
// workbench-side bench adapter (bench/meijer-g/run-candidate.ts).

function _expandRational(s: string, workBits: number): string {
  const trimmed = s.trim();
  if (!trimmed.includes("/")) return trimmed;
  const [pStr, qStr] = trimmed.split("/");
  const p = BigInt(pStr!);
  const q = BigInt(qStr!);
  if (q === 0n) throw new Error(`rational ${s} has zero denominator`);
  const dps = Math.ceil(workBits * 0.30103) + 16;
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
  // Feed parameters at decimalToBinaryPrecision(precision) + 64 working bits
  // so the dispatcher's internal cancellation-bump retries aren't capped by
  // the input's bit budget.
  const workBits = bf.decimalToBinaryPrecision(raw.precision) + 64;
  const anValues = raw.an.map((c) => encodeComplex(c, workBits));
  const apValues = raw.ap.map((c) => encodeComplex(c, workBits));
  const bmValues = raw.bm.map((c) => encodeComplex(c, workBits));
  const bqValues = raw.bq.map((c) => encodeComplex(c, workBits));
  const zValue = encodeComplex(raw.z, workBits);

  const fields: Record<string, unknown> = {
    an: proto.list(anValues),
    ap: proto.list(apValues),
    bm: proto.list(bmValues),
    bq: proto.list(bqValues),
    z: zValue,
  };
  if (raw.request_mode !== undefined) {
    fields["request_mode"] = proto.str(raw.request_mode);
  }
  return {
    input: proto.record(fields),
    precision: raw.precision,
  };
}

// ─── Output decoding ─────────────────────────────────────────────────────────

/** Decode any Value back to a JSON-y blob (used for the symbolic `expr` field
 *  and for tagged-refusal payloads). */
function decodePayloadAny(v: V): unknown {
  switch (v.kind) {
    case "string":
      return v.value;
    case "integer":
      return (v.value as string).toString();
    case "rational":
      return { num: v.num, den: v.den };
    case "boolean":
      return v.value;
    case "float64":
      return v.value;
    case "list":
      return (v.items ?? []).map(decodePayloadAny);
    case "record": {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v.fields ?? {})) {
        out[k] = decodePayloadAny(val);
      }
      return out;
    }
    case "tagged":
      return {
        kind: "tagged",
        tag: v.tag,
        payload: decodePayloadAny(v.payload!),
      };
    case "expression":
      return {
        kind: "expression",
        head: v.head,
        args: (v.args ?? []).map(decodePayloadAny),
      };
    case "symbol":
      return { kind: "symbol", name: v.name };
    default:
      return null;
  }
}

function decodeBigComplexAsStrings(v: unknown, dps: number): { re: string; im: string } {
  const bc = bf.valueToBigComplex(v);
  return {
    re: bf.toString(bc.re, dps + 5),
    im: bf.toString(bc.im, dps + 5),
  };
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

function decodeOutput(v: unknown, requestedPrecision: number): Record<string, unknown> {
  const vTyped = v as V;

  // Tagged refusal envelope.
  if (vTyped.kind === "tagged") {
    return {
      kind: "tagged",
      tag: vTyped.tag,
      payload: decodePayloadAny(vTyped.payload!),
    };
  }

  if (vTyped.kind !== "record") {
    throw new Error(`expected record or tagged, got kind=${vTyped.kind}`);
  }

  const f = vTyped.fields!;
  const kindField = f["kind"];
  if (!kindField || kindField.kind !== "string") {
    throw new Error("output record missing 'kind' string field");
  }

  if (kindField.value === "symbolic") {
    return {
      kind: "symbolic",
      rule:   decodeString(f["rule"]!),
      source: decodeString(f["source"]!),
      note:   decodeString(f["note"]!),
      method: decodeString(f["method"]!),
      expr:   decodePayloadAny(f["expr"]!),
    };
  }

  if (kindField.value === "numerical") {
    return {
      kind:                "numerical",
      value:               decodeBigComplexAsStrings(f["value"]!, requestedPrecision),
      achieved_precision:  decodeInteger(f["achieved_precision"]!),
      method:              decodeString(f["method"]!),
      working_precision:   decodeInteger(f["working_precision"]!),
      warnings:            decodeStringList(f["warnings"]!),
      diagnostics:         decodePayloadAny(f["diagnostics"]!),
    };
  }

  throw new Error(
    `unexpected record output kind=${JSON.stringify(kindField.value)}; ` +
    `expected 'symbolic' | 'numerical'`,
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

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

  // Build flags: precision is always set; force-method is optional.
  const flags: Record<string, unknown> = {
    precision: BigInt(prepared.precision),
  };
  if (raw.force_method !== undefined) {
    flags["force-method"] = raw.force_method;
  }

  const t0 = performance.now();
  let out: unknown;
  try {
    const result = await contract.executeToolDef(
      meijerGDef,
      prepared.input,
      flags,
    );
    out = result.output;
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
  const elapsedMs = performance.now() - t0;

  const decoded = decodeOutput(out, prepared.precision);
  decoded["elapsed_ms"] = elapsedMs;
  process.stdout.write(JSON.stringify(decoded) + "\n");
}

await main();
