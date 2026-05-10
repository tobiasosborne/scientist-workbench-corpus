// =============================================================================
// benchmarks/poly-roots-radical/run-candidate.ts — corpus-resident bench adapter
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
// Input wire format (matches bench/poly-roots-radical/golden/inputs.json):
//   {
//     "f":   string,  -- polynomial in `var` over ℚ (e.g. "x**3 - 3*x + 1")
//     "var": string   -- variable name (e.g. "x")
//   }
//
// Output wire format (success path):
//   {
//     "kind":     "ok",
//     "content":  "1",       -- leading rational coef (informational)
//     "roots":    [ {"root": "<expr-string>", "multiplicity": <int>}, ... ],
//     "method":   "factor-then-radicals",
//     "warnings": []
//   }
//
// Tagged-boundary outputs are surfaced as { kind:"tagged", tag, payload }.
// Thrown ToolErrors are wrapped into { kind:"tool_error", name, message }.
//
// The adapter calls the tool in-process via @workbench/compose's loadWorkbench()
// which invokes the tool's fn() directly (no subprocess).  This is the
// standard corpus adapter pattern (ADR-0028 §3).
//
// Note: poly-roots is a symbolic-tier tool (default determinism class —
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
// so that the G-tier refusal cases (non-polynomial, multivariate) can be
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

// ─── Value → SymPy-string rendering ──────────────────────────────────────────
//
// Converts the tool's output Value nodes to a string that SymPy can parse
// via `sympy.sympify(s, locals={var: x})`.  The closed vocabulary in
// poly-roots' output is:
//
//   integer                            →  "123" or "-456"
//   rational { num, den }              →  "num/den"
//   symbol   { name }                  →  name
//   expression("+",  [a, b])           →  "(a + b)"
//   expression("-",  [a, b])           →  "(a - b)"
//   expression("*",  [a, b])           →  "(a * b)"
//   expression("/",  [a, b])           →  "(a / b)"
//   expression("^",  [base, exp])      →  "(base ** exp)"
//   expression("neg", [a])             →  "(-a)"
//   expression("sqrt", [a])            →  "sqrt(a)"
//   expression("Root", [poly_expr, k]) →  "CRootOf(poly, k)"
//     where poly_expr has head "Polynomial" with integer coefficient args
//     — this gives CRootOf(c_n*x**n + ... + c_0, k) using a fresh symbol.
//
// All non-trivial nodes are wrapped in parentheses to avoid precedence
// ambiguities.  SymPy accepts this safely; extra parentheses are harmless.

function valueToSympyString(v: V, varName: string): string {
  switch (v.kind) {
    case "integer":
      return v.value as string;
    case "rational": {
      const n = v.num as string;
      const d = v.den as string;
      if (d === "1") return n;
      return `(${n}/${d})`;
    }
    case "symbol":
      return v.name as string;
    case "expression": {
      const head = v.head!;
      const args = v.args ?? [];
      switch (head) {
        case "+": {
          const [a, b] = args;
          return `(${valueToSympyString(a!, varName)} + ${valueToSympyString(b!, varName)})`;
        }
        case "-": {
          const [a, b] = args;
          return `(${valueToSympyString(a!, varName)} - ${valueToSympyString(b!, varName)})`;
        }
        case "*": {
          const [a, b] = args;
          return `(${valueToSympyString(a!, varName)} * ${valueToSympyString(b!, varName)})`;
        }
        case "/": {
          const [a, b] = args;
          return `(${valueToSympyString(a!, varName)} / ${valueToSympyString(b!, varName)})`;
        }
        case "^": {
          const [base, exp] = args;
          return `(${valueToSympyString(base!, varName)} ** ${valueToSympyString(exp!, varName)})`;
        }
        case "neg": {
          const [a] = args;
          return `(-(${valueToSympyString(a!, varName)}))`;
        }
        case "sqrt": {
          const [a] = args;
          return `sqrt(${valueToSympyString(a!, varName)})`;
        }
        case "Root": {
          // Root[Polynomial([c_n, c_{n-1}, ..., c_0]), k]
          // → CRootOf(c_n * _x**n + ..., k) where _x is a fresh symbol
          // The Polynomial node carries coefficients in high-to-low order.
          const [polyNode, kNode] = args;
          if (!polyNode || polyNode.kind !== "expression" || polyNode.head !== "Polynomial") {
            throw new Error(`valueToSympyString: Root first arg is not Polynomial: ${JSON.stringify(polyNode)}`);
          }
          const coeffs = polyNode.args ?? [];
          const k = kNode!.kind === "integer" ? parseInt(kNode!.value as string, 10) : 0;
          const deg = coeffs.length - 1;
          const terms: string[] = [];
          for (let i = 0; i <= deg; i++) {
            const coef = coeffs[i]!;
            const coefStr = valueToSympyString(coef, varName);
            const power = deg - i;
            if (power === 0) {
              terms.push(`(${coefStr})`);
            } else if (power === 1) {
              terms.push(`(${coefStr})*_x`);
            } else {
              terms.push(`(${coefStr})*_x**${power}`);
            }
          }
          return `CRootOf(${terms.join(" + ")}, ${k})`;
        }
        default:
          // Unknown head — pass through as a function call.
          // This covers any future extension and won't silently swallow data.
          return `${head}(${args.map((a) => valueToSympyString(a, varName)).join(", ")})`;
      }
    }
    default:
      throw new Error(`valueToSympyString: unhandled kind ${v.kind}`);
  }
}

// ─── Output decoding (payload for tagged) ────────────────────────────────────

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

function toBenchShape(out: V, varName: string): unknown {
  if (out.kind === "tagged") {
    return {
      kind:    "tagged",
      tag:     out.tag,
      payload: decodeAny(out.payload!),
    };
  }
  if (out.kind === "record") {
    const rootsField = out.fields!["roots"];
    if (!rootsField || rootsField.kind !== "list") {
      throw new Error("toBenchShape: roots field missing or not a list");
    }
    const roots = (rootsField.items ?? []).map((entry) => {
      if (entry.kind !== "record") {
        throw new Error("toBenchShape: roots entry not a record");
      }
      const rootValue = entry.fields!["root"]!;
      const multValue = entry.fields!["multiplicity"]!;
      return {
        root:         valueToSympyString(rootValue, varName),
        multiplicity: parseInt(multValue.value as string, 10),
      };
    });
    const methodValue = out.fields!["method"];
    const method = methodValue?.value as string ?? "factor-then-radicals";
    return {
      kind:     "ok",
      roots,
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
  out = await wb.run("poly-roots", inputValue) as V;
} catch (err) {
  const e = err as Error & { name?: string };
  process.stdout.write(
    JSON.stringify({ kind: "tool_error", name: e.name ?? "Error", message: e.message ?? String(e) }) + "\n",
  );
  process.exit(0);
}

process.stdout.write(JSON.stringify(toBenchShape(out, raw.var)) + "\n");
