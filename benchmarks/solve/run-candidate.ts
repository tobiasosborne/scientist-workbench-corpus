// =============================================================================
// benchmarks/solve/run-candidate.ts — corpus-resident bench adapter
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
// Input wire format (matches bench/solve/golden/inputs.json):
//   {
//     "eqs":  ["x + y - 3", "x - y - 1"],  -- equation strings (implicit = 0)
//     "vars": ["x", "y"]                    -- variable name strings
//   }
//
// Output wire format (success path):
//   {
//     "kind":         "ok",
//     "vars":         ["x", "y"],
//     "solutions":    [{"bindings": [{"var": "x", "value": "2"}, ...], "branches": []}],
//     "completeness": "complete" | "finite-rep-of-infinite",
//     "warnings":     []
//   }
//
// Tagged-boundary outputs are surfaced as:
//   { "kind": "tagged", "tag": "solve/<class>", "payload": {"detail": "..."} }
//
// Thrown ToolErrors are wrapped into { kind:"tool_error", name, message }.
//
// Tag namespace: the tool emits `solve/<class>` tags matching the bench
// verifier's expected namespace — no remapping needed.
//
// The adapter calls the tool in-process via @workbench/compose's
// loadWorkbench() which invokes the tool's fn() directly (no subprocess).
// This is the standard corpus adapter pattern (ADR-0028 §3).
//
// Note: solve is a symbolic-tier tool (default determinism class —
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

const sym:    (name: string) => unknown                              = proto.sym;
const expr:   (head: string, args: unknown[]) => unknown             = proto.expr;
const int_:   (v: bigint)    => unknown                              = proto.int;
const rat_:   (n: bigint, d: bigint) => unknown                      = proto.rat;
const list_:  (items: unknown[]) => unknown                          = proto.list;
const record_:(fields: Record<string, unknown>) => unknown           = proto.record;

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

// =============================================================================
// Equation string → Value encoder
// =============================================================================
//
// The bench stores equations as SymPy-parseable expression strings such as
// "x + y - 3", "sin(x) - 1/2", "cos(x) + cos(3*x) + cos(5*x)".  The tool
// expects `Value` protocol objects.  We parse these strings into Values
// compatible with the tool's `valueToRatFn` and `tryTranscendentalInvert`
// entry points.
//
// The expression grammar covers:
//   - Integer literals: "42", "-3"
//   - Rational literals: "1/2", "-7/4" (parsed as expr("/", [int, int]))
//   - Variable symbols: "x", "y", "z", "w" (and any identifier)
//   - Arithmetic operators: +, -, *, /, ** (^ is also accepted)
//   - Unary minus: "-x", "-(x + 1)"
//   - Function calls: sin(x), cos(x), tan(x), exp(x), log(x),
//                     sinh(x), cosh(x), tanh(x), abs(x), Abs(x),
//                     sqrt(x), asin(x), acos(x), atan(x)
//   - Parentheses
//
// Note: "/" is always an operator in this grammar.  "1/2" tokenises as
// int(1) op(/) int(2) and produces expr("/", [int(1n), int(2n)]).  The tool's
// `valueToRatFn` handles this form correctly.  (Python-style "1/2" in equation
// strings is common in the bench — e.g. "cos(x) - 1/2".)
//
// pi is a symbol: sym("pi").

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
    if (ch === "(" ) { out.push({ t: "lp"    }); i++; continue; }
    if (ch === ")" ) { out.push({ t: "rp"    }); i++; continue; }
    if (ch === "," ) { out.push({ t: "comma" }); i++; continue; }
    if ("+-*/^".includes(ch)) { out.push({ t: "op", v: ch }); i++; continue; }
    throw new Error(`tokenize: unexpected char ${JSON.stringify(ch)} in ${JSON.stringify(s)}`);
  }
  out.push({ t: "eof" });
  return out;
}

interface PS { toks: Tok[]; i: number }

function peek(st: PS): Tok { return st.toks[st.i]!; }
function eat(st: PS): Tok  { return st.toks[st.i++]!; }

function parseEq(src: string): unknown {
  const st: PS = { toks: tokenize(src), i: 0 };
  const v = parseAddSub(st);
  if (peek(st).t !== "eof") throw new Error(`parseEq: trailing input in ${JSON.stringify(src)}`);
  return v;
}

function parseAddSub(st: PS): unknown {
  let lhs = parseMulDiv(st);
  while (peek(st).t === "op") {
    const op = (peek(st) as { t: "op"; v: string }).v;
    if (op !== "+" && op !== "-") break;
    eat(st);
    const rhs = parseMulDiv(st);
    lhs = expr(op, [lhs, rhs]);
  }
  return lhs;
}

function parseMulDiv(st: PS): unknown {
  let lhs = parseUnary(st);
  while (peek(st).t === "op") {
    const op = (peek(st) as { t: "op"; v: string }).v;
    if (op !== "*" && op !== "/") break;
    eat(st);
    const rhs = parseUnary(st);
    if (op === "/") {
      // Detect integer/integer patterns and fold them into a `rational` Value
      // so that the tool's `isNumericConstant` predicate (which recognises
      // `integer | rational | float64` but NOT `expression`) accepts them.
      // This matters for the transcendental fast path: `cos(x) - 1/2` must
      // reach `tryTranscendentalInvert` with the constant encoded as `rat(1, 2)`,
      // not as `expr("/", [int(1), int(2)])`.
      //
      // Wrappers produced by int_() are plain {kind:"integer", value:"..."} objects.
      // We detect the pattern by inspecting the decoded JSON structure.
      const lhsV = lhs as { kind?: string; value?: unknown };
      const rhsV = rhs as { kind?: string; value?: unknown };
      if (lhsV.kind === "integer" && rhsV.kind === "integer") {
        const n = BigInt(String(lhsV.value));
        const d = BigInt(String(rhsV.value));
        if (d !== 0n) { lhs = rat_(n, d); continue; }
      }
      lhs = expr(op, [lhs, rhs]);
    } else {
      lhs = expr(op, [lhs, rhs]);
    }
  }
  return lhs;
}

function parseUnary(st: PS): unknown {
  if (peek(st).t === "op" && (peek(st) as { t: "op"; v: string }).v === "-") {
    eat(st);
    const inner = parseUnary(st);
    // Fold unary minus directly into integer/rational literals so that the
    // constant "-1" in expressions like "cos(-4*x + 1) - (-1)" becomes an
    // integer Value (kind=integer, value="-1") rather than expr("*", [-1, 1]).
    // The tool's `isNumericConstant` predicate only accepts integer/rational/
    // float64; expr("*", ...) would cause the transcendental fast-path to miss.
    const v = inner as { kind?: string; value?: unknown; num?: unknown; den?: unknown };
    if (v.kind === "integer") {
      const n = BigInt(String(v.value));
      return int_(-n);
    }
    if (v.kind === "rational") {
      const n = BigInt(String(v.num ?? v.value));
      const d = BigInt(String(v.den ?? "1"));
      return rat_(-n, d);
    }
    return expr("*", [int_(-1n), inner]);
  }
  if (peek(st).t === "op" && (peek(st) as { t: "op"; v: string }).v === "+") {
    eat(st);
    return parseUnary(st);
  }
  return parsePower(st);
}

function parsePower(st: PS): unknown {
  const base = parseAtom(st);
  if (peek(st).t === "op") {
    const op = (peek(st) as { t: "op"; v: string }).v;
    if (op === "**" || op === "^") {
      eat(st);
      const exp_ = parseUnary(st);
      return expr("^", [base, exp_]);
    }
  }
  return base;
}

function parseAtom(st: PS): unknown {
  const t = peek(st);
  if (t.t === "num") {
    eat(st);
    return int_((t as { t: "num"; v: bigint }).v);
  }
  if (t.t === "ident") {
    const name = (t as { t: "ident"; v: string }).v;
    eat(st);
    // Function call?
    if (peek(st).t === "lp") {
      eat(st); // consume "("
      const args: unknown[] = [];
      if (peek(st).t !== "rp") {
        args.push(parseAddSub(st));
        while (peek(st).t === "comma") {
          eat(st);
          args.push(parseAddSub(st));
        }
      }
      if (peek(st).t !== "rp") throw new Error(`parseAtom: expected ) in function call ${name}`);
      eat(st);
      // Normalise Python/SymPy capitalisations.
      const normName = name === "Abs" ? "abs" : name;
      return expr(normName, args);
    }
    // Plain symbol (including pi, e).
    return sym(name);
  }
  if (t.t === "lp") {
    eat(st);
    const inner = parseAddSub(st);
    if (peek(st).t !== "rp") throw new Error("parseAtom: expected )");
    eat(st);
    return inner;
  }
  throw new Error(`parseAtom: unexpected token ${JSON.stringify(t)}`);
}

function encodeInput(raw: { eqs: string[]; vars: string[] }): unknown {
  const eqValues = raw.eqs.map((eq) => parseEq(eq));
  const varValues = raw.vars.map((v) => sym(v));
  return record_({
    eqs:  list_(eqValues),
    vars: list_(varValues),
  });
}

// =============================================================================
// Value → bench output decoder
// =============================================================================
//
// The tool emits Value-protocol JSON.  We decode it to the bench's flat
// JSON shape that verify.py (and verify.ts) expects:
//
//   { kind: "ok", vars, solutions, completeness, warnings }
//   { kind: "tagged", tag, payload: { detail } }
//
// Binding values are decoded to SymPy-parseable expression strings.  The
// conversion is recursive over the Value tree.

function valueToString(v: V): string {
  if (v.kind === "integer") {
    return String(v.value);
  }
  if (v.kind === "rational") {
    const n = v.num ?? String(v.value);
    const d = v.den;
    if (!d || d === "1") return String(n);
    return `${n}/${d}`;
  }
  if (v.kind === "symbol") {
    return v.name as string;
  }
  if (v.kind === "string") {
    return v.value as string;
  }
  if (v.kind === "expression") {
    const h = v.head!;
    const args = v.args ?? [];
    // Unary functions.
    if (["sin","cos","tan","exp","log","sinh","cosh","tanh","abs",
         "asin","acos","atan","sqrt","sqrt"].includes(h) && args.length === 1) {
      return `${h}(${valueToString(args[0]!)})`;
    }
    // Binary/n-ary arithmetic operators.
    if (h === "+" || h === "-" || h === "*" || h === "/") {
      if (args.length === 1) {
        // Unary minus encoded as expr("-", [x]) or n-ary sum of 1 element.
        return h === "-" ? `(-${valueToString(args[0]!)})` : valueToString(args[0]!);
      }
      if (args.length === 2) {
        const lStr = valueToString(args[0]!);
        const rStr = valueToString(args[1]!);
        // Add parentheses around sums/differences in denominator positions to
        // avoid precedence ambiguity in the output strings.
        return `(${lStr} ${h} ${rStr})`;
      }
      // n-ary: fold left.
      let acc = valueToString(args[0]!);
      for (let i = 1; i < args.length; i++) {
        acc = `(${acc} ${h} ${valueToString(args[i]!)})`;
      }
      return acc;
    }
    if (h === "^" || h === "**") {
      return `(${valueToString(args[0]!)})**${valueToString(args[1]!)}`;
    }
    // Root[poly, k] — algebraic number representation (ADR-0018).
    if (h === "Root") {
      // args[0] is the polynomial expression (in an anonymous variable),
      // args[1] is the 1-based index.
      // Emit as a string that SymPy's RootOf can parse.
      // verify.py's _univ_each_root uses sp.sympify; SymPy supports
      // RootOf(p, k) for zero-indexed k.  The workbench uses 1-based k;
      // the verifier's SymPy call uses all_roots() via bipartite match,
      // so the exact index format in the string doesn't matter for
      // each_root_satisfies — the root value is what SymPy evaluates.
      // We emit "CRootOf(poly, k-1)" which SymPy sympifies to the
      // corresponding algebraic number.
      const polyStr = valueToString(args[0]!);
      const idxV = args[1]!;
      let idx = 0;
      if (idxV.kind === "integer") idx = Number(BigInt(String(idxV.value))) - 1;
      return `CRootOf(${polyStr}, ${idx})`;
    }
    // pi constant — emit as the symbol name, works as-is in SymPy.
    // (pi appears as sym("pi"), not as an expr head.)
    // Generic fallback: functional notation.
    return `${h}(${args.map(valueToString).join(", ")})`;
  }
  throw new Error(`valueToString: unsupported kind ${v.kind}`);
}

// Decode the warnings list field to string[].
function decodeWarnings(v: V): string[] {
  if (v.kind !== "list") throw new Error("warnings must be a list");
  return (v.items ?? []).map((it) => {
    if (it.kind !== "string" && it.kind !== "symbol") throw new Error("warning items must be strings");
    return String(it.value ?? it.name);
  });
}

interface BenchOutput {
  kind:         "ok" | "tagged";
  // ok path
  vars?:        string[];
  solutions?:   Array<{ bindings: Array<{ var: string; value: string }>; branches: string[] }>;
  completeness?: string;
  warnings?:    string[];
  // tagged path
  tag?:         string;
  payload?:     { detail: string };
}

function decodeOutput(out: V): BenchOutput {
  // Tagged refusal path.
  if (out.kind === "tagged") {
    const tag = out.tag as string;
    let detail = "refusal";
    if (out.payload && out.payload.kind === "record" && out.payload.fields) {
      const df = out.payload.fields["detail"];
      if (df) {
        detail = df.kind === "string" ? (df.value as string) : valueToString(df);
      }
    }
    return { kind: "tagged", tag, payload: { detail } };
  }

  // Happy-path record: { vars, solutions, completeness, warnings }.
  if (out.kind !== "record") {
    throw new Error(`expected record output, got ${out.kind}`);
  }
  const fields = out.fields!;

  // vars: list<symbol>
  const varsV = fields["vars"] as V;
  if (varsV.kind !== "list") throw new Error("vars must be a list");
  const vars = (varsV.items ?? []).map((s) => {
    if (s.kind !== "symbol") throw new Error("var must be symbol");
    return s.name as string;
  });

  // completeness: string
  const compV = fields["completeness"] as V;
  const completeness = compV.kind === "string" ? (compV.value as string) : valueToString(compV);

  // warnings: list<string>
  const warnV = fields["warnings"] as V;
  const warnings = decodeWarnings(warnV);

  // solutions: list<record { bindings: list, branches: list }>
  const solsV = fields["solutions"] as V;
  if (solsV.kind !== "list") throw new Error("solutions must be a list");
  const solutions = (solsV.items ?? []).map((sol) => {
    if (sol.kind !== "record") throw new Error("solution must be a record");
    const solFields = sol.fields!;

    const bindingsV = solFields["bindings"] as V;
    const branchesV = solFields["branches"] as V;
    if (bindingsV.kind !== "list") throw new Error("bindings must be a list");
    if (branchesV.kind !== "list") throw new Error("branches must be a list");

    const bindings = (bindingsV.items ?? []).map((b) => {
      if (b.kind !== "record") throw new Error("binding must be a record");
      const bf = b.fields!;
      const varSym = bf["var"] as V;
      const valV   = bf["value"] as V;
      if (varSym.kind !== "symbol") throw new Error("binding.var must be symbol");
      return { var: varSym.name as string, value: valueToString(valV) };
    });

    const branches = (branchesV.items ?? []).map((b) => {
      if (b.kind !== "symbol") throw new Error("branch must be symbol");
      return b.name as string;
    });

    return { bindings, branches };
  });

  return { kind: "ok", vars, solutions, completeness, warnings };
}

// =============================================================================
// main
// =============================================================================

interface BenchInput {
  eqs:  string[];
  vars: string[];
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
    .run("solve", inputValue) as V;
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
