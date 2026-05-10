// =============================================================================
// benchmarks/linsolve-q/run-candidate.ts — corpus-resident bench adapter
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
// Input wire format (matches bench/linsolve-q/golden/inputs.json):
//   {
//     "A": [["3/2", "-1", "0"], ...],  -- m×n rational matrix, entries as strings
//     "b": ["5", "-1", "2/7"]          -- length-m rational vector, entries as strings
//   }
//
// Output wire format (success path):
//   {
//     "kind":      "unique" | "under-determined",
//     "x":         ["<rat-or-affine>", ...],  -- length n
//     "free_vars": ["t_0", ...],              -- empty for unique
//     "rank":      <int>,
//     "method":    "bareiss-one-step",
//     "warnings":  [...]
//   }
//
// For inconsistent systems the tool emits tagged "linsolve-q/inconsistent"
// with payload { rank, augmented_rank, warnings }; this is surfaced as:
//   {
//     "kind":           "inconsistent",
//     "rank":           <int>,
//     "augmented_rank": <int>,
//     "method":         "bareiss-one-step",
//     "warnings":       [...]
//   }
//
// Thrown ToolErrors are wrapped into { kind:"tool_error", name, message }.
//
// Tag namespace: the tool uses "linsolve-q/inconsistent" directly, matching
// the bench verifier's expected tag — no remapping is needed here.
//
// The adapter calls the tool in-process via @workbench/compose's
// loadWorkbench() which invokes the tool's fn() directly (no subprocess).
// This is the standard corpus adapter pattern (ADR-0028 §3).
//
// Note: linsolve-q is a symbolic-tier tool (default determinism class —
// no `numerical: true`, no `arbprec: true`).  Pure bigint arithmetic;
// output is bit-identical cross-platform forever.
// Adapter: platform_pinned = false (see adapter TOML).

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

const composePath  = resolve(WORKBENCH_ROOT, "packages/compose/src/index.ts");
const protocolPath = resolve(WORKBENCH_ROOT, "packages/protocol/src/index.ts");

const { loadWorkbench } = await import(composePath);
const proto = await import(protocolPath);

// Helpers used to build the input Value.
const int: (v: bigint)           => unknown = proto.int;
const rat: (n: bigint, d: bigint) => unknown = proto.rat;
const list:   (items: unknown[])  => unknown = proto.list;
const record: (fields: Record<string, unknown>) => unknown = proto.record;

// ─── Local structural type for decoded Value nodes ───────────────────────────

type V = {
  kind:    string;
  items?:  V[];
  fields?: Record<string, V>;
  value?:  string;   // integer value as decimal string
  num?:    string;   // rational numerator string
  den?:    string;   // rational denominator string
  name?:   string;   // symbol name
  tag?:    string;
  payload?: V;
  head?:   string;
  args?:   V[];
};

// ─── Wire-format rational parsing ────────────────────────────────────────────
//
// Input rational strings are in canonical form: "-?\d+(/\d+)?".
// We emit either an integer Value (denominator 1) or a rational Value.

function parseRationalString(s: string): unknown {
  const m = /^(-?\d+)(?:\/(\d+))?$/.exec(s.trim());
  if (!m) throw new Error(`invalid rational on wire: ${JSON.stringify(s)}`);
  const num = BigInt(m[1]!);
  const den = m[2] !== undefined ? BigInt(m[2]) : 1n;
  if (den === 1n) return int(num);
  return rat(num, den);
}

function encodeInput(raw: {
  A: readonly (readonly string[])[];
  b: readonly string[];
}): unknown {
  return record({
    A: list(raw.A.map((row) => list(row.map(parseRationalString)))),
    b: list(raw.b.map(parseRationalString)),
  });
}

// ─── Output decoding — integer/rational Value → string ───────────────────────

function valueToRationalString(v: V): string {
  if (v.kind === "integer") return String(v.value);
  if (v.kind === "rational") {
    const n = v.num ?? v.value;
    const d = v.den;
    if (!d || d === "1") return String(n);
    return `${n}/${d}`;
  }
  throw new Error(`expected integer/rational value, got ${v.kind}`);
}

// ─── Output decoding — affine expression → bench string ──────────────────────
//
// The tool encodes under-determined bindings as workbench arithmetic
// expressions.  For example, "5 − 2·t_0" becomes:
//   expr("+", [int(5), expr("*", [int(-2), sym("t_0")])])
//
// This function inverts that encoding to produce the bench's affine string
// (e.g., "5 + (-2)*t_0") that verify.py's SymPy parse can handle.
//
// The expression structure has these cases at each node:
//   integer / rational         → rational string literal
//   symbol(name)               → name (e.g. "t_0")
//   expr("+", [...])           → terms joined with "+" / "-"
//   expr("*", [coeff, symbol]) → "coeff*symbol" (the tool always uses 2-arg
//                                multiplication for scalar × free-var terms)

function valueToBenchAffineString(v: V): string {
  if (v.kind === "integer" || v.kind === "rational") {
    return valueToRationalString(v);
  }
  if (v.kind === "symbol") return v.name as string;
  if (v.kind === "expression") {
    if (v.head === "+") {
      // n-ary sum: emit first term, then subsequent terms with sign prefix.
      const parts = (v.args ?? []).map((a, i) => {
        const s = valueToBenchAffineString(a);
        if (i === 0) return s;
        if (s.startsWith("-")) return `- ${s.slice(1)}`;
        return `+ ${s}`;
      });
      return parts.join(" ");
    }
    if (v.head === "*") {
      // Binary: [coefficient, symbol].
      const args = v.args ?? [];
      if (args.length !== 2) {
        throw new Error(`unexpected '*' arity in affine: ${args.length}`);
      }
      const coeffStr = valueToRationalString(args[0]!);
      const symArg = args[1]!;
      if (symArg.kind !== "symbol") {
        throw new Error(`expected symbol in second arg of * (got ${symArg.kind})`);
      }
      // Canonical: strip "1*" and "-1*" to simplify.
      if (coeffStr === "-1") return `-${symArg.name}`;
      if (coeffStr === "1")  return symArg.name as string;
      return `${coeffStr}*${symArg.name}`;
    }
    throw new Error(`unexpected expression head in affine: ${v.head}`);
  }
  throw new Error(`unexpected value kind in affine: ${v.kind}`);
}

// ─── Output decoding — warnings ──────────────────────────────────────────────

function decodeWarnings(v: V): string[] {
  if (v.kind !== "list") throw new Error("warnings must be a list");
  return (v.items ?? []).map((it) => {
    if (it.kind !== "string") throw new Error("warnings must be list-of-string");
    return it.value as string;
  });
}

// ─── Output decoding — full tool output → bench wire shape ───────────────────

interface BenchOutput {
  kind: "unique" | "under-determined" | "inconsistent";
  x?: string[];
  free_vars?: string[];
  rank: number;
  augmented_rank?: number;
  method: string;
  warnings: string[];
}

function decodeOutput(out: V): BenchOutput {
  // Tagged refusal path: "linsolve-q/inconsistent".
  if (out.kind === "tagged") {
    if (out.tag !== "linsolve-q/inconsistent") {
      throw new Error(`unexpected tag: ${out.tag}`);
    }
    if (!out.payload || out.payload.kind !== "record") {
      throw new Error("tagged payload must be a record");
    }
    const payload = out.payload.fields!;
    return {
      kind:           "inconsistent",
      rank:           Number(BigInt((payload["rank"] as V).value!)),
      augmented_rank: Number(BigInt((payload["augmented_rank"] as V).value!)),
      method:         "bareiss-one-step",
      warnings:       decodeWarnings(payload["warnings"] as V),
    };
  }

  // Happy-path record: { vars, solutions, completeness, warnings }.
  if (out.kind !== "record") {
    throw new Error(`expected record output, got ${out.kind}`);
  }
  const fields = out.fields!;
  const completeness = (fields["completeness"] as V).value as string;
  const warnings = decodeWarnings(fields["warnings"] as V);

  const solutions = fields["solutions"] as V;
  if (solutions.kind !== "list") throw new Error("solutions must be a list");
  if ((solutions.items ?? []).length !== 1) {
    throw new Error(
      `linsolve-q: expected exactly 1 solution, got ${(solutions.items ?? []).length}`,
    );
  }
  const sol = solutions.items![0]!;
  if (sol.kind !== "record") throw new Error("solution must be a record");

  const bindings = sol.fields!["bindings"] as V;
  const branches = sol.fields!["branches"] as V;
  if (bindings.kind !== "list") throw new Error("bindings must be a list");
  if (branches.kind !== "list") throw new Error("branches must be a list");

  // Each binding is a record { var: symbol, value: <integer|rational|expression> }.
  const x = (bindings.items ?? []).map((b) => {
    if (b.kind !== "record") throw new Error("binding must be a record");
    return valueToBenchAffineString(b.fields!["value"] as V);
  });

  // Branches are the free-variable symbols.
  const free_vars = (branches.items ?? []).map((b) => {
    if (b.kind !== "symbol") throw new Error("branch must be a symbol");
    return b.name as string;
  });

  // Rank: for unique, rank = n (full column rank); for under-determined,
  // rank = n − |free_vars|.
  const n = x.length;
  const rank = completeness === "complete" ? n : n - free_vars.length;

  return {
    kind:      completeness === "complete" ? "unique" : "under-determined",
    x,
    free_vars,
    rank,
    method:   "bareiss-one-step",
    warnings,
  };
}

// ─── main ─────────────────────────────────────────────────────────────────────

interface BenchInput {
  A: readonly (readonly string[])[];
  b: readonly string[];
}

const raw = JSON.parse(readFileSync(0, "utf8")) as BenchInput;
const wb  = await loadWorkbench();

let inputValue: unknown;
try {
  inputValue = encodeInput(raw);
} catch (err) {
  const e = err as Error;
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: "EncodeError", message: e.message }) + "\n",
  );
  process.exit(0);
}

let out: V;
try {
  out = await (wb as { run: (name: string, input: unknown) => Promise<V> })
    .run("linsolve-q", inputValue) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "Error", message: e.message ?? String(e) }) + "\n",
  );
  process.exit(0);
}

try {
  process.stdout.write(JSON.stringify(decodeOutput(out)) + "\n");
} catch (err) {
  const e = err as Error;
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: "DecodeError", message: e.message }) + "\n",
  );
  process.exit(0);
}
