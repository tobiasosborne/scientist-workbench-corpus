// =============================================================================
// benchmarks/poly-factor-q/run-candidate.ts — corpus-resident bench adapter
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
// Input wire format (matches bench/poly-factor-q/golden/inputs.json):
//   {
//     "f":   string,  -- polynomial in `var` over ℚ (e.g. "x**2 - 1")
//     "var": string   -- variable name (e.g. "x")
//   }
//
// Output wire format (success path, bench convention):
//   {
//     "kind":     "ok",
//     "content":  "<rational-string>",   -- e.g. "1", "2/3", "-5"
//     "factors":  [ { "factor": "<poly-string>", "multiplicity": <int> }, ... ],
//     "method":   "berlekamp-zassenhaus",
//     "warnings": []
//   }
//
// Tagged-boundary outputs are surfaced as:
//   { "kind": "tagged", "tag": "poly-factor-q/non-polynomial", "payload": {...} }
//
// Note the tag remapping: the tool (named "poly-factor") uses the tag
// "poly-factor/non-polynomial", but the bench verifier (verify.py) checks
// for tags starting with "poly-factor-q/".  The PROMPT.md specifies
// `tagged "poly-factor-q/non-polynomial"` as the bench-convention tag.
// This adapter renames the tag from the tool namespace to the bench namespace
// so the verifier's `startswith("poly-factor-q/")` check passes.
//
// Thrown ToolErrors are wrapped into { kind:"tool_error", name, message }.
//
// The adapter calls the tool in-process via @workbench/compose's loadWorkbench()
// which invokes the tool's fn() directly (no subprocess).  This is the
// standard corpus adapter pattern (ADR-0028 §3).
//
// Note: poly-factor is a symbolic-tier tool (default determinism class —
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
  num?:    string;   // rational numerator
  den?:    string;   // rational denominator
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
// that would occur from greedy rational-literal detection.  The parser handles
// rational coefficients like `1/100` as `expr("/", [int(1), int(100)])`, which
// the tool treats correctly as a rational.
//
// Also handles function calls like `sin(x)` and foreign symbols like `y`
// so that the H-tier refusal cases (non-polynomial, multivariate) can be
// passed to the tool for honest boundary-tag emission.

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
    return int((t as { t: "num"; v: bigint }).v);
  }
  if (t.t === "ident") {
    eat(st);
    const name = (t as { t: "ident"; v: string }).v;
    if (name === st.varName) return sym(name);
    // Foreign symbol or function call — pass through so the tool can refuse correctly.
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

// ─── Value → polynomial string rendering ─────────────────────────────────────
//
// Converts the tool's output Value nodes (expressions representing polynomials
// in ℤ[var]) to a string that SymPy and the bench's verify.py can parse.
// The poly-factor tool emits factors as expression Values in the closed
// vocabulary (+, -, *, /, ^, integer, rational, symbol).
//
// We render them using ** for power (SymPy-compatible) and put the variable
// name inline.  This matches the expected.json format (e.g. "x - 1",
// "x**2 + 1", "9*x + 1").

function valueToPolyString(v: V, varName: string): string {
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
          // The tool emits n-ary "+": args may have 2, 3, 4, or more elements.
          // Fold them with " + " as a flat n-ary sum.
          if (args.length === 0) return "0";
          if (args.length === 1) return valueToPolyString(args[0]!, varName);
          return "(" + args.map((a) => valueToPolyString(a, varName)).join(" + ") + ")";
        }
        case "-": {
          const [a, b] = args;
          return `(${valueToPolyString(a!, varName)} - ${valueToPolyString(b!, varName)})`;
        }
        case "*": {
          // Likewise, "*" may be n-ary.
          if (args.length === 0) return "1";
          if (args.length === 1) return valueToPolyString(args[0]!, varName);
          return "(" + args.map((a) => valueToPolyString(a, varName)).join(" * ") + ")";
        }
        case "/": {
          const [a, b] = args;
          return `(${valueToPolyString(a!, varName)} / ${valueToPolyString(b!, varName)})`;
        }
        case "^": {
          const [base, exp_] = args;
          return `(${valueToPolyString(base!, varName)}**${valueToPolyString(exp_!, varName)})`;
        }
        case "neg": {
          const [a] = args;
          return `(-(${valueToPolyString(a!, varName)}))`;
        }
        default:
          // Unknown head — pass through as a function call.
          return `${head}(${args.map((a) => valueToPolyString(a, varName)).join(", ")})`;
      }
    }
    default:
      throw new Error(`valueToPolyString: unhandled kind ${v.kind}`);
  }
}

// ─── Rational Value → string ──────────────────────────────────────────────────
//
// The tool emits `content` as either an integer Value or a rational Value.
// We convert it to the canonical string form that the bench verifier expects:
// "1", "-5", "2/3", "-7/4", etc.

function rationalValueToString(v: V): string {
  if (v.kind === "integer") {
    return String(v.value);
  }
  if (v.kind === "rational") {
    // Rational values have `num` (numerator) and `den` (denominator) as strings,
    // or may use `value` for legacy formats.
    const n = v.num ?? (v as unknown as Record<string,unknown>)["numerator"] ?? v.value;
    const d = v.den ?? (v as unknown as Record<string,unknown>)["denominator"];
    if (d === undefined || d === "1" || d === 1) return String(n);
    return `${n}/${d}`;
  }
  // Fallback for other numeric kinds.
  return String(v.value);
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

// ─── Tag remapping ────────────────────────────────────────────────────────────
//
// The tool (poly-factor) emits tags in the "poly-factor/*" namespace.
// The bench verifier (verify.py) checks `tag.startswith("poly-factor-q/")`.
// The PROMPT.md specifies the bench tag as "poly-factor-q/non-polynomial".
// This function remaps the tool's tag to the bench's expected namespace:
//   "poly-factor/non-polynomial" → "poly-factor-q/non-polynomial"
//
// Any other poly-factor/* tags are also remapped (prefix substitution).

function remapTag(toolTag: string): string {
  if (toolTag.startsWith("poly-factor/")) {
    return "poly-factor-q/" + toolTag.slice("poly-factor/".length);
  }
  return toolTag;
}

// ─── Convert tool Value output to bench wire format ───────────────────────────

function toBenchShape(out: V, varName: string): unknown {
  if (out.kind === "tagged") {
    const remapped = remapTag(out.tag ?? "");
    const payload = decodeAny(out.payload!);
    return {
      kind:    "tagged",
      tag:     remapped,
      payload: payload,
    };
  }
  if (out.kind === "record") {
    // Extract content.
    const contentField = out.fields!["content"];
    if (!contentField) throw new Error("toBenchShape: content field missing");
    const contentStr = rationalValueToString(contentField);

    // Extract factors list.
    const factorsField = out.fields!["factors"];
    if (!factorsField || factorsField.kind !== "list") {
      throw new Error("toBenchShape: factors field missing or not a list");
    }
    const factors = (factorsField.items ?? []).map((entry) => {
      if (entry.kind !== "record") {
        throw new Error("toBenchShape: factors entry not a record");
      }
      const factorValue = entry.fields!["factor"]!;
      const multValue   = entry.fields!["multiplicity"]!;
      return {
        factor:       valueToPolyString(factorValue, varName),
        multiplicity: parseInt(String(multValue.value), 10),
      };
    });

    const methodValue = out.fields!["method"];
    const method = methodValue?.value as string ?? "berlekamp-zassenhaus";

    return {
      kind:     "ok",
      content:  contentStr,
      factors,
      method,
      warnings: [],
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
  out = await wb.run("poly-factor", inputValue) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "Error", message: e.message ?? String(e) }) + "\n",
  );
  process.exit(0);
}

process.stdout.write(JSON.stringify(toBenchShape(out, raw.var)) + "\n");
