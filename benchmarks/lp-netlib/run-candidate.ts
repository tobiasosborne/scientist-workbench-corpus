// =============================================================================
// benchmarks/lp-netlib/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges raw JSON wire format ↔ scientist-workbench canonical Value
// protocol for the convex-cone solver tier (ADR-0030).  Shared by
// `lp-netlib` and `lp-small`; the lp-small bridge is a thin re-export.
//
// Pipeline per case:
//
//   stdin (raw JSON):
//       { minimize:  { c: number[] },
//         subjectTo: { Ax_eq_b?: { A: number[][], b: number[] },
//                      cones:   [{ head, indices?, size? }, ...] },
//         precision?: number,
//         max_iter?:  number }
//
//   ↓ encodeInput → canonical Value protocol
//
//   wb.run(<tool>, value, flags?)
//       — <tool> defaults to "lp-solve"; override via env CANDIDATE_TOOL
//
//   ↓ decodeOutput → raw JSON
//
//   stdout:
//       success record { status, x, dual, slack, objective?,
//                        achieved_precision?, iterations, method,
//                        condition_estimate, warnings }
//       OR tagged refusal { kind: "tagged", tag, payload }
//
// Tool-not-registered case: if wb.run throws because the candidate tool
// hasn't been built yet (Phase 1 / Phase 2 of epic eg9j), emit a clean
// tagged envelope `scientist-workbench/tool-not-registered` so the
// grader records the failure with a useful detail string rather than a
// JSON parse crash.  CLAUDE.md Rule 1 (fail loud) — the verifier will
// fail status_consistency for the case but the implementer gets a
// readable signal.
//
// ─── Wire format references ─────────────────────────────────────────────────
//
//   ADR-0030 §C        — canonical input schema (cone-solve)
//   ADR-0030 §D        — canonical output schema
//   benchmarks/lp-netlib/DESCRIPTION.md   — raw-JSON projection
//   benchmarks/lp-netlib/golden/verifier_protocol.md — verifier checks
//
// ─── Why field-absence semantics for `objective` ────────────────────────────
//
// JSON.parse rejects raw `Infinity` tokens (spec).  Status="infeasible"
// or status="unbounded" cases have nominally ±∞ objective.  Per
// shard 004 / 005 decision, candidates omit the `objective` field in
// these cases rather than carry NaN/Inf.  The bridge mirrors this:
// it reads back the canonical record, and emits `objective` to stdout
// JSON only when the candidate's canonical record has the field.
//
// Mirrors benchmarks/linalg-eigh/run-candidate.ts in pattern; the
// LP wire is materially different (records-of-records-of-lists with
// expression-encoded cones) so the encoders / decoders are LP-specific.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Resolve workbench root ──────────────────────────────────────────────────

const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "scientist-workbench");

const CANDIDATE_TOOL: string = process.env["CANDIDATE_TOOL"] ?? "lp-solve";

// ─── Dynamic imports through the workbench package tree ─────────────────────

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

// ─── Local structural type ──────────────────────────────────────────────────

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

// ─── Raw JSON → canonical Value (input encoding) ────────────────────────────
//
// Cone heads per ADR-0030 §C:
//
//   NonNegCone[indices: list<integer>]
//   SOCone[indices: list<integer>]
//   PSDCone[size: integer, indices: list<integer>]
//   ExpCone[i: integer, j: integer, k: integer]
//   PowCone[alpha: rational, i: integer, j: integer, k: integer]
//   ZeroCone[indices: list<integer>]
//
// LP exercises only NonNegCone today.  The other heads are wired here
// so any future SOCP/SDP/EXP/POW case in lp-small (or qp-maros-meszaros,
// sdp-sdplib) lands without touching this file.  PowCone is the one
// that requires `rat()` — alpha is a rational, not a float.

interface RawCone {
  head: string;
  indices?: number[];
  size?: number;
  i?: number;
  j?: number;
  k?: number;
  alpha?: { num: string; den: string } | string;   // rational
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
    case "NonNegCone":
    case "SOCone":
    case "ZeroCone":
      if (!c.indices) throw new Error(`${c.head} missing indices`);
      return expr(c.head, [encodeIntList(c.indices)]);
    case "PSDCone":
      if (c.size === undefined || !c.indices)
        throw new Error(`PSDCone missing size or indices`);
      return expr("PSDCone", [int(BigInt(c.size)), encodeIntList(c.indices)]);
    case "ExpCone":
      if (c.i === undefined || c.j === undefined || c.k === undefined)
        throw new Error(`ExpCone missing i/j/k`);
      return expr("ExpCone", [int(BigInt(c.i)), int(BigInt(c.j)), int(BigInt(c.k))]);
    case "PowCone":
      // PowCone alpha is a rational.  Raw JSON convention: either
      // {num,den} strings or a string "p/q".  We accept both; the
      // canonical Value form is rat(num, den) (via proto.rat).
      throw new Error(`PowCone encoding not yet wired (no test case requires it)`);
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

// ─── Canonical Value → raw JSON (output decoding) ───────────────────────────

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
    case "integer": return v.value as string;     // keep as string to preserve precision
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
  // Field-absence semantics: objective + achieved_precision are
  // present only when status === "optimal".  See shard 004 / 005.
  if ("objective"          in f) out["objective"]          = decodeFloat(f["objective"]!);
  if ("achieved_precision" in f) out["achieved_precision"] = decodeFloat(f["achieved_precision"]!);
  return out;
}

// ─── Main ───────────────────────────────────────────────────────────────────

const raw = JSON.parse(readFileSync(0, "utf8")) as RawInput;
const input = encodeInput(raw);

const wb = await loadWorkbench();

let outValue: V;
try {
  outValue = await wb.run(CANDIDATE_TOOL, input) as V;
} catch (e) {
  // Tool not registered, or tool threw a hard error before producing a
  // tagged refusal.  Surface as a clean refusal envelope so the verifier
  // records a useful detail string and the implementer sees the actual
  // error in `detail` rather than a JSON parse crash.
  const detail = (e as Error).message ?? String(e);
  const refusal = tagged("scientist-workbench/tool-not-registered", record({
    tool:   { kind: "string", value: CANDIDATE_TOOL } as unknown,
    detail: { kind: "string", value: detail.slice(0, 500) } as unknown,
  })) as V;
  outValue = refusal;
}

const decoded = decodeOutput(outValue);
process.stdout.write(JSON.stringify(decoded) + "\n");
