// =============================================================================
// benchmarks/linalg-svd/run-candidate.ts — corpus-resident bench adapter
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
// Input wire format:
//   { "A": [[float, ...], ...], "mode"?: "reduced" | "complete" }
//
// Output wire format (success path):
//   { "U": [[float, ...], ...], "S": [float, ...],
//     "Vt": [[float, ...], ...], "mode": str,
//     "reconstruction_error": float, "orthogonality_error_U": float,
//     "orthogonality_error_Vt": float, "condition_number": float,
//     "rank_estimate": int, "method": str, "warnings": [str, ...] }
//
// Tagged-boundary outputs are surfaced to the bench as-is (the verifier
// inspects them via `kind: "tagged"` checks); non-tagged outputs are decoded
// into the success-shape JSON the verifier expects.
//
// Mirrors bench/linalg-svd/run-candidate.ts in the workbench.
// If the tool's wire protocol changes, update this file too (two-repo change).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// ─── Resolve workbench root ──────────────────────────────────────────────────
//
// The corpus grader inherits WORKBENCH_ROOT from the environment (set by the
// caller or defaulting to the side-by-side sibling in grade.ts).  We read it
// here so that dynamic imports below resolve through the workbench package
// tree, not the corpus tree.
//
const WORKBENCH_ROOT: string =
  process.env["WORKBENCH_ROOT"] ??
  resolve(import.meta.dir, "..", "..", "..", "..", "scientist-workbench");

// ─── Dynamic imports (file paths, resolved through workbench) ────────────────

const composePath  = resolve(WORKBENCH_ROOT, "packages/compose/src/index.ts");
const protocolPath = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");

const { loadWorkbench } = await import(composePath);
const proto = await import(protocolPath);

const float64FromNumber: (n: number) => unknown = proto.float64FromNumber;
const float64ToNumber: (v: unknown) => number   = proto.float64ToNumber;
const list: (items: unknown[]) => unknown        = proto.list;
const record: (fields: Record<string, unknown>) => unknown = proto.record;
const str: (s: string) => unknown               = proto.str;

// ─── Local structural type (covers all shapes decoded below) ─────────────────
//
// We cannot import Value from @workbench/protocol as a workspace alias from
// the corpus.  This local structural type covers every variant we touch.

type V = {
  kind: string;
  items?: V[];
  fields?: Record<string, V>;
  value?: unknown;
  tag?: string;
  payload?: V;
};

// ─── raw JSON → canonical Value (input encoding) ─────────────────────────────

function encodeRow(row: readonly number[]): unknown {
  return list(row.map((x) => float64FromNumber(x)));
}

function encodeInput(raw: { A: readonly (readonly number[])[]; mode?: string }): unknown {
  const rows = list(raw.A.map(encodeRow));
  if (raw.mode === undefined) {
    return record({ A: rows });
  }
  return record({ A: rows, mode: str(raw.mode) });
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

function decodeString(v: V): string {
  if (v.kind !== "string") throw new Error(`expected string, got kind=${v.kind}`);
  return v.value as string;
}

function decodeInt(v: V): number {
  if (v.kind !== "integer") throw new Error(`expected integer, got kind=${v.kind}`);
  return Number(v.value);
}

function decodeAny(v: V): unknown {
  // Best-effort decode for tagged-boundary payloads.
  switch (v.kind) {
    case "string":  return v.value;
    case "integer": return Number(v.value);
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
    // Surface the tagged boundary as-is; bench verifier handles the
    // category-vs-input check.
    return { kind: "tagged", tag: v.tag, payload: decodeAny(v.payload!) };
  }
  if (v.kind !== "record") {
    throw new Error(`expected record, got kind=${v.kind}`);
  }
  const f = v.fields!;
  return {
    U:                      decodeFloatMatrix(f["U"]!),
    S:                      decodeFloatList(f["S"]!),
    Vt:                     decodeFloatMatrix(f["Vt"]!),
    mode:                   decodeString(f["mode"]!),
    reconstruction_error:   decodeFloat(f["reconstruction_error"]!),
    orthogonality_error_U:  decodeFloat(f["orthogonality_error_U"]!),
    orthogonality_error_Vt: decodeFloat(f["orthogonality_error_Vt"]!),
    condition_number:       decodeFloat(f["condition_number"]!),
    rank_estimate:          decodeInt(f["rank_estimate"]!),
    method:                 decodeString(f["method"]!),
    warnings:               decodeStringList(f["warnings"]!),
  };
}

// ─── main ────────────────────────────────────────────────────────────────────

const raw = JSON.parse(readFileSync(0, "utf8")) as {
  A: readonly (readonly number[])[];
  mode?: string;
};
const input = encodeInput(raw);

const wb = await loadWorkbench();
const out = await wb.run("linalg-svd", input) as V;

const decoded = decodeOutput(out);
process.stdout.write(JSON.stringify(decoded) + "\n");
