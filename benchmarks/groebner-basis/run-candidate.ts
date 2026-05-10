// =============================================================================
// benchmarks/groebner-basis/run-candidate.ts — corpus-resident bench adapter
// =============================================================================
//
// Bridges the bench's raw JSON wire format to the Phase 3 tool's canonical
// Value protocol.  The tool `tools/groebner-basis/tool.ts` does NOT yet
// exist — Phase 3 implements it against this adapter's expected contract.
// (The bench IS the spec; this file declares the contract Phase 3 must
// match.)
//
// Input wire format (matches benchmarks/groebner-basis/golden/inputs.json):
//   {
//     "polys": ["x**2 + y", "x*y + 1"],   // expression strings in vars over ℚ
//     "vars":  ["x", "y"],                 // variable order
//     "order": "lex" | "degrevlex"         // monomial order
//   }
//
// Output wire format (success path):
//   {
//     "kind":     "ok",
//     "basis":    ["y**3 + 1", "x - y**2"],
//     "order":    "lex",
//     "vars":     ["x", "y"],
//     "n_pairs":  <integer>,           // metric: pair-pruning effectiveness
//     "warnings": []
//   }
//
// Tagged-boundary outputs are surfaced as:
//   { "kind": "tagged", "tag": "groebner-basis/<class>", "payload": {"detail": "..."} }
//
// Thrown ToolErrors are wrapped into { kind:"tool_error", name, message }.
//
// Tag namespace: the tool emits `groebner-basis/<class>` tags matching the
// bench verifier's expected namespace — no remapping needed (cf.
// poly-factor-q which remaps poly-factor/ → poly-factor-q/).
//
// The adapter calls the tool in-process via @workbench/compose's
// loadWorkbench() which invokes the tool's fn() directly (no subprocess).
// This is the standard corpus adapter pattern (ADR-0028 §3).
//
// Note: groebner-basis is a symbolic-tier tool (default determinism class —
// no `numerical: true`, no `arbprec: true`).  Output is bit-identical
// cross-platform forever.  Adapter: platform_pinned = false (see adapter TOML).

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

const sym:    (name: string)  => unknown                              = proto.sym;
const expr:   (head: string, args: unknown[]) => unknown              = proto.expr;
const int_:   (v: bigint)     => unknown                              = proto.int;
const str_:   (v: string)     => unknown                              = proto.str;
const list_:  (items: unknown[]) => unknown                           = proto.list;
const record_:(fields: Record<string, unknown>) => unknown            = proto.record;

// ─── Local structural type for decoded Value nodes ───────────────────────────

type V = {
  kind:    string;
  items?:  V[];
  fields?: Record<string, V>;
  value?:  unknown;
  name?:   string;
  num?:    string;
  den?:    string;
  tag?:    string;
  payload?: V;
  head?:   string;
  args?:   V[];
};

// ─── Tiny expression parser ──────────────────────────────────────────────────
//
// Parses the bench's input vocabulary into canonical Values.  The grammar
// matches the poly-factor-q adapter exactly — integer / rational literals,
// declared symbols, parens, +, -, *, /, **, ^, unary -.  Function-call
// syntax `head(args...)` and undeclared identifiers pass through as
// expressions / foreign symbols so the tool can refuse them.
//
// This is a senior-grade port of poly-factor-q/run-candidate.ts's parser,
// generalised to multiple variables.

type Tok =
  | { t: "num"; v: bigint }
  | { t: "ident"; v: string }
  | { t: "op"; v: string }
  | { t: "lp" } | { t: "rp" } | { t: "comma" }
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
      out.push({ t: "num", v: BigInt(s.slice(i, j)) });
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
    if (ch === ",") { out.push({ t: "comma" }); i++; continue; }
    if ("+-*/^".includes(ch)) { out.push({ t: "op", v: ch }); i++; continue; }
    throw new Error(`tokenize: unexpected char ${JSON.stringify(ch)} at position ${i}`);
  }
  out.push({ t: "eof" });
  return out;
}

interface PState { toks: Tok[]; i: number; vars: Set<string> }

function parsePolyString(src: string, vars: string[]): unknown {
  const toks = tokenize(src);
  const st: PState = { toks, i: 0, vars: new Set(vars) };
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
    return expr("*", [int_(-1n), inner]);
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
    return int_((t as { t: "num"; v: bigint }).v);
  }
  if (t.t === "ident") {
    eat(st);
    const name = (t as { t: "ident"; v: string }).v;
    // Function call?
    if (peek(st).t === "lp") {
      eat(st);
      const args: unknown[] = [parseAddSub(st)];
      while (peek(st).t === "comma") {
        eat(st);
        args.push(parseAddSub(st));
      }
      if (peek(st).t !== "rp") throw new Error("parser: expected )");
      eat(st);
      return expr(name, args);
    }
    // Bare symbol — declared or foreign.  Foreign symbols pass through
    // as Sym values so the tool can refuse them with the parametric tag.
    return sym(name);
  }
  if (t.t === "lp") {
    eat(st);
    const v = parseAddSub(st);
    if (peek(st).t !== "rp") throw new Error("parser: expected )");
    eat(st);
    return v;
  }
  throw new Error(`parser: unexpected token ${JSON.stringify(t)}`);
}

// ─── Value → polynomial-string rendering ─────────────────────────────────────
//
// The tool's output basis elements are Values in the closed +/-/*/^/integer/
// rational/symbol vocabulary.  We render them to expression strings the
// bench's verify.py can sympify directly.

function valueToPolyString(v: V): string {
  switch (v.kind) {
    case "integer":
      return String(v.value);
    case "rational": {
      const n = v.num ?? v.value;
      const d = v.den;
      if (d === undefined || d === "1") return String(n);
      return `(${n}/${d})`;
    }
    case "symbol":
      return v.name as string;
    case "expression": {
      const head = v.head!;
      const args = v.args ?? [];
      switch (head) {
        case "+": {
          if (args.length === 0) return "0";
          if (args.length === 1) return valueToPolyString(args[0]!);
          return "(" + args.map((a) => valueToPolyString(a)).join(" + ") + ")";
        }
        case "-": {
          const [a, b] = args;
          return `(${valueToPolyString(a!)} - ${valueToPolyString(b!)})`;
        }
        case "*": {
          if (args.length === 0) return "1";
          if (args.length === 1) return valueToPolyString(args[0]!);
          return "(" + args.map((a) => valueToPolyString(a)).join(" * ") + ")";
        }
        case "/": {
          const [a, b] = args;
          return `(${valueToPolyString(a!)} / ${valueToPolyString(b!)})`;
        }
        case "^": {
          const [base, exp_] = args;
          return `(${valueToPolyString(base!)}**${valueToPolyString(exp_!)})`;
        }
        case "neg": {
          const [a] = args;
          return `(-(${valueToPolyString(a!)}))`;
        }
        default:
          return `${head}(${args.map((a) => valueToPolyString(a)).join(", ")})`;
      }
    }
    default:
      throw new Error(`valueToPolyString: unhandled kind ${v.kind}`);
  }
}

// ─── Output decoding ──────────────────────────────────────────────────────────

function decodeAny(v: V): unknown {
  switch (v.kind) {
    case "string":     return v.value;
    case "integer":    return v.value;
    case "float64":    return v.value;
    case "boolean":    return v.value;
    case "symbol":     return v.name;
    case "list":       return (v.items ?? []).map(decodeAny);
    case "record": {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(v.fields ?? {})) out[k] = decodeAny(val);
      return out;
    }
    case "tagged":
      return { kind: "tagged", tag: v.tag, payload: decodeAny(v.payload!) };
    default:
      return null;
  }
}

// ─── Convert tool Value output to bench wire format ───────────────────────────

function toBenchShape(out: V): unknown {
  if (out.kind === "tagged") {
    const payload = decodeAny(out.payload!);
    return {
      kind:    "tagged",
      tag:     out.tag,
      payload,
    };
  }
  if (out.kind === "record") {
    // Extract basis (list of expressions).
    const basisField = out.fields!["basis"];
    if (!basisField || basisField.kind !== "list") {
      throw new Error("toBenchShape: basis field missing or not a list");
    }
    const basis = (basisField.items ?? []).map((entry) => valueToPolyString(entry));

    // Extract order, vars, n_pairs, warnings.
    const orderField = out.fields!["order"];
    const order = (orderField?.value as string) ?? "lex";

    const varsField = out.fields!["vars"];
    const vars: string[] =
      varsField && varsField.kind === "list"
        ? (varsField.items ?? []).map((e) => (e.name as string) ?? String(e.value))
        : [];

    const nPairsField = out.fields!["n_pairs"];
    const nPairs =
      nPairsField !== undefined ? Number((nPairsField.value as string | number) ?? 0) : 0;

    const warningsField = out.fields!["warnings"];
    const warnings: string[] =
      warningsField && warningsField.kind === "list"
        ? (warningsField.items ?? []).map((e) => String(e.value))
        : [];

    return {
      kind:     "ok",
      basis,
      order,
      vars,
      n_pairs:  nPairs,
      warnings,
    };
  }
  throw new Error(`toBenchShape: unexpected output kind ${out.kind}`);
}

// ─── main ─────────────────────────────────────────────────────────────────────

interface BenchInput {
  polys: string[];
  vars:  string[];
  order: "lex" | "degrevlex";
}

const raw  = JSON.parse(readFileSync(0, "utf8")) as BenchInput;
const wb   = await loadWorkbench();

// Parse each poly string into a Value expression.
const polyValues: unknown[] = [];
try {
  for (const p of raw.polys) {
    polyValues.push(parsePolyString(p, raw.vars));
  }
} catch (err) {
  const e = err as Error;
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: "ParseError", message: e.message }) + "\n",
  );
  process.exit(0);
}

const inputValue = record_({
  polys: list_(polyValues),
  vars:  list_(raw.vars.map((v) => sym(v))),
  order: str_(raw.order),
});

let out: V;
try {
  out = (await wb.run("groebner-basis", inputValue)) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({
      kind:    "tool_error",
      name:    e.name ?? "Error",
      message: e.message ?? String(e),
    }) + "\n",
  );
  process.exit(0);
}

process.stdout.write(JSON.stringify(toBenchShape(out)) + "\n");
