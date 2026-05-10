// =============================================================================
// benchmarks/real-root-isolate/run-candidate.ts — corpus-resident bench adapter
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
// Input wire format (matches bench/real-root-isolate/golden/inputs.json):
//   {
//     "f":   string,  -- polynomial in `var` over ℚ (e.g. "x**3 - 3*x + 1")
//     "var": string   -- variable name (e.g. "x")
//   }
//
// Output wire format (success path):
//   {
//     "kind":      "ok",
//     "intervals": [ {"lo": "<rat-string>", "hi": "<rat-string>"}, ... ],
//     "method":    "vas-lmq",
//     "warnings":  []
//   }
//
// Tagged-boundary outputs are surfaced as { kind:"tagged", tag, payload }.
// Thrown ToolErrors are wrapped into { kind:"tool_error", name, message }.
//
// The adapter calls the tool in-process via @workbench/compose's loadWorkbench()
// which invokes the tool's fn() directly (no subprocess).  This is the
// standard corpus adapter pattern (ADR-0028 §3).
//
// Note: real-root-isolate is a symbolic-tier tool (default determinism class —
// no `numerical: true`, no `arbprec: true`).  Output is bit-identical cross-
// platform forever.  Adapter: platform_pinned = false (see adapter TOML).

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

const sym:  (name: string)  => unknown = proto.sym;
const expr: (head: string, args: unknown[]) => unknown = proto.expr;
const int:  (v: bigint)     => unknown = proto.int;

// ─── Local structural type for decoded Value nodes ───────────────────────────

type V = {
  kind:    string;
  items?:  V[];
  fields?: Record<string, V>;
  value?:  unknown;
  name?:   string;
  tag?:    string;
  payload?: V;
  head?:   string;
  args?:   V[];
};

// ─── Tiny expression parser ──────────────────────────────────────────────────
//
// Parses the bench's input vocabulary into canonical Values.
// Handles: integer literals, the variable symbol, parens, +, -, *, /, **, ^
// and unary -.  Division "/" is always tokenized as an operator (never as a
// rational-literal denominator) to avoid the `x**3/2 = x^(3/2)` mis-parse
// that the original workbench run-candidate.ts suffered.  The parser handles
// rational coefficients like `1/100` as `expr("/", [int(1), int(100)])`, which
// the tool treats correctly as a rational.
//
// Sufficient for bench/real-root-isolate/golden/inputs.json.

type Tok =
  | { t: "num"; v: bigint; d: bigint }
  | { t: "ident"; v: string }
  | { t: "op"; v: string }
  | { t: "lp" } | { t: "rp" }
  | { t: "eof" };

function tokenize(s: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < s.length) {
    const ch = s[i]!;
    if (/\s/.test(ch)) { i++; continue; }
    if (/[0-9]/.test(ch)) {
      let j = i;
      while (j < s.length && /[0-9]/.test(s[j]!)) j++;
      out.push({ t: "num", v: BigInt(s.slice(i, j)), d: 1n });
      i = j;
      continue;
    }
    if (/[a-zA-Z_]/.test(ch)) {
      let j = i;
      while (j < s.length && /[a-zA-Z0-9_]/.test(s[j]!)) j++;
      out.push({ t: "ident", v: s.slice(i, j) });
      i = j;
      continue;
    }
    if (ch === "*" && s[i + 1] === "*") { out.push({ t: "op", v: "**" }); i += 2; continue; }
    if (ch === "(") { out.push({ t: "lp" }); i++; continue; }
    if (ch === ")") { out.push({ t: "rp" }); i++; continue; }
    if ("+-*/^".includes(ch)) { out.push({ t: "op", v: ch }); i++; continue; }
    throw new Error(`tokenize: unexpected char ${JSON.stringify(ch)} at ${i}`);
  }
  out.push({ t: "eof" });
  return out;
}

interface PState { toks: Tok[]; i: number; varName: string }

function parsePolyString(src: string, varName: string): unknown {
  const toks = tokenize(src);
  const st: PState = { toks, i: 0, varName };
  const v = parseAddSub(st);
  if (st.toks[st.i]!.t !== "eof") throw new Error("parser: trailing input");
  return v;
}

function peek(st: PState): Tok { return st.toks[st.i]!; }
function eat(st: PState): Tok { return st.toks[st.i++]!; }

function parseAddSub(st: PState): unknown {
  let lhs = parseMulDiv(st);
  while (
    peek(st).t === "op" &&
    ((peek(st) as { t: "op"; v: string }).v === "+" ||
     (peek(st) as { t: "op"; v: string }).v === "-")
  ) {
    const op = (eat(st) as { t: "op"; v: string }).v as "+" | "-";
    const rhs = parseMulDiv(st);
    lhs = expr(op, [lhs, rhs]);
  }
  return lhs;
}

function parseMulDiv(st: PState): unknown {
  let lhs = parseUnary(st);
  while (
    peek(st).t === "op" &&
    ((peek(st) as { t: "op"; v: string }).v === "*" ||
     (peek(st) as { t: "op"; v: string }).v === "/")
  ) {
    const op = (eat(st) as { t: "op"; v: string }).v as "*" | "/";
    const rhs = parseUnary(st);
    lhs = expr(op, [lhs, rhs]);
  }
  return lhs;
}

function parseUnary(st: PState): unknown {
  if (peek(st).t === "op" && (peek(st) as { t: "op"; v: string }).v === "-") {
    eat(st);
    const inner = parseUnary(st);
    return expr("*", [int(-1n), inner]);
  }
  if (peek(st).t === "op" && (peek(st) as { t: "op"; v: string }).v === "+") {
    eat(st);
    return parseUnary(st);
  }
  return parsePower(st);
}

function parsePower(st: PState): unknown {
  const base = parseAtom(st);
  if (
    peek(st).t === "op" &&
    ((peek(st) as { t: "op"; v: string }).v === "**" ||
     (peek(st) as { t: "op"; v: string }).v === "^")
  ) {
    eat(st);
    const exp_ = parseUnary(st);
    return expr("^", [base, exp_]);
  }
  return base;
}

function parseAtom(st: PState): unknown {
  const t = peek(st);
  if (t.t === "num") {
    eat(st);
    return t.d === 1n ? int(t.v) : expr("/", [int(t.v), int(t.d)]);
  }
  if (t.t === "ident") {
    eat(st);
    if (t.v === st.varName) return sym(t.v);
    // Foreign symbol — bench's "x*y - 1" (multivariate refusal) and
    // "sin(x)" (non-polynomial) need to round-trip. Symbols pass through;
    // function calls are recognised by "(" follow.
    if (peek(st).t === "lp") {
      eat(st);
      const args: unknown[] = [parseAddSub(st)];
      while (
        peek(st).t === "op" &&
        (peek(st) as { t: "op"; v: string }).v === ","
      ) {
        eat(st);
        args.push(parseAddSub(st));
      }
      if (peek(st).t !== "rp") throw new Error("parser: expected )");
      eat(st);
      return expr(t.v, args);
    }
    return sym(t.v);
  }
  if (t.t === "lp") {
    eat(st);
    const v = parseAddSub(st);
    if (peek(st).t !== "rp") throw new Error("parser: expected )");
    eat(st);
    return v;
  }
  throw new Error(`parser: unexpected ${JSON.stringify(t)}`);
}

// ─── Output rendering ─────────────────────────────────────────────────────────
//
// Flatten the tool's expr("/", [int(n), int(d)]) or raw int(n) to the bench's
// rational-string form so verify.py's sympy.Rational(s) parse succeeds.

function ratValueToString(v: V): string {
  if (v.kind === "integer") return v.value as string;
  if (v.kind === "expression" && v.head === "/" && (v.args?.length ?? 0) === 2) {
    const n = v.args![0]!;
    const d = v.args![1]!;
    if (n.kind === "integer" && d.kind === "integer") {
      return (d.value as string) === "1"
        ? (n.value as string)
        : `${n.value}/${d.value}`;
    }
  }
  throw new Error(`ratValueToString: unexpected shape ${JSON.stringify(v).slice(0, 80)}`);
}

function decodeAny(v: V): unknown {
  switch (v.kind) {
    case "string":     return v.value;
    case "integer":    return v.value;   // keep as string for payload fidelity
    case "float64":    return v.value;
    case "boolean":    return v.value;
    case "symbol":     return v.name;
    case "list":       return v.items!.map(decodeAny);
    case "record": {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v.fields!)) out[k] = decodeAny(val);
      return out;
    }
    case "tagged": return { kind: "tagged", tag: v.tag, payload: decodeAny(v.payload!) };
    default:       return null;
  }
}

function toBenchShape(out: V): unknown {
  if (out.kind === "tagged") {
    return {
      kind:    "tagged",
      tag:     out.tag,
      payload: decodeAny(out.payload!),
    };
  }
  if (out.kind === "record") {
    const intervalsField = out.fields!["intervals"];
    if (!intervalsField || intervalsField.kind !== "list") {
      throw new Error("toBenchShape: intervals field missing");
    }
    const intervals = intervalsField.items!.map((iv) => {
      if (iv.kind !== "record") throw new Error("toBenchShape: interval entry not a record");
      return {
        lo: ratValueToString(iv.fields!["lo"]!),
        hi: ratValueToString(iv.fields!["hi"]!),
      };
    });
    return {
      kind:      "ok",
      intervals,
      method:    "vas-lmq",
      warnings:  [],
    };
  }
  throw new Error(`toBenchShape: unexpected output kind ${out.kind}`);
}

// ─── main ─────────────────────────────────────────────────────────────────────

interface BenchInput { f: string; var: string }

const raw  = JSON.parse(readFileSync(0, "utf8")) as BenchInput;
const wb   = await loadWorkbench();

let fValue: unknown;
try {
  fValue = parsePolyString(raw.f, raw.var);
} catch (err) {
  const e = err as Error;
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: "ParseError", message: e.message }) + "\n",
  );
  process.exit(0);
}

const inputValue = {
  kind:   "record",
  fields: {
    f:   fValue,
    var: sym(raw.var),
  },
};

let out: V;
try {
  out = await wb.run("real-root-isolate", inputValue) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "Error", message: e.message ?? String(e) }) + "\n",
  );
  process.exit(0);
}

process.stdout.write(JSON.stringify(toBenchShape(out)) + "\n");
