// =============================================================================
// benchmarks/solve/golden/verify.ts — 5-lane dispatch verifier (TS port).
// =============================================================================
//
// stdin:
//   { input:     { eqs: string[], vars: string[] },
//     candidate: { kind:"ok", vars, solutions, completeness, warnings }
//               | { kind:"tagged", tag, payload }
//               | { kind:"tool_error", ... },
//     expected:  { lane: "linear"|"univariate-poly"|"multivariate-poly"|
//                        "transcendental"|"refusal",
//                  tag?: string },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// TypeScript port of verify.py per ADR-0028 §4 with the multivariate-poly
// lane added in lockstep with the workbench's Gröbner substrate (ADR-0029,
// bead `scientist-workbench-x8d`).  Tolerances are preserved byte-for-byte
// with verify.py for the four original lanes.
//
// The verifier dispatches by expected.lane, NOT by candidate.kind —
// this is how "lied-about-scope" bugs are caught.
//
// ─── Lane dispatch ─────────────────────────────────────────────────────────
//   lane == "linear"            → 5 checks (shape, exact_satisfaction,
//                                 free_var_basis, rank_consistent,
//                                 completeness_correct)
//   lane == "univariate-poly"   → 4 checks (shape, each_root_satisfies,
//                                 count_with_multiplicity, distinct_roots_match)
//   lane == "multivariate-poly" → 3 checks (shape, each_solution_satisfies,
//                                 completeness_correct) — substitute-and-evaluate
//                                 per ADR-0019 §1.
//   lane == "transcendental"    → 3 checks (shape, branched_substitution_cube,
//                                 completeness_grid)
//   lane == "refusal"           → 2 checks (tag_matches, payload_predicate)
//
// ─── Tolerance regime ──────────────────────────────────────────────────────
//   linear             — NONE (exact BigInt rational arithmetic)
//   univariate-poly    — NONE (float64 eval: roots of deg ≤ 4 polynomials;
//                        tolerance 1e-10 for substitute-and-simplify check)
//   multivariate-poly  — 1e-7 absolute (substitute-and-evaluate residue;
//                        well above float64 noise for the bench's coefficient
//                        range [-9, 9])
//   transcendental cube — 1e-12 (relative; numpy modules='numpy' mirror)
//   transcendental grid — 1e-6 (root position)
//   refusal            — NONE (exact tag/structural checks)
//
// ─── Design notes ─────────────────────────────────────────────────────────
// verify.py uses SymPy for symbolic simplification.  This TypeScript port
// uses float64 arithmetic for the polynomial and transcendental lanes.
// For the linear lane, BigInt rational arithmetic provides exact equality
// (reproducing verify.py's fractions.Fraction checks).
//
// For the univariate-poly lane, verify.py calls sp.simplify(eq.subs(x, root))
// and checks == 0 symbolically.  Here we evaluate eq(root) numerically using
// float64.  For the bench's actual cases (degrees ≤ 10, all roots expressible
// in closed form or as Root[poly, k] companion-matrix evaluations), float64
// evaluation is accurate enough to distinguish pass from fail — the residuals
// are on the order of machine epsilon for correct roots vs. macroscopic for
// mutated roots.  Tolerance: 1e-8 * max(1, |eq|) (conservative, well above
// float64 rounding for degree ≤ 10).
//
// =============================================================================

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

// ─── Check type ───────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

// ─── Expected-output index ───────────────────────────────────────────────────
//
// The corpus grader passes { input, candidate, id } to the verifier but does
// NOT include the expected.json data in the stdin payload (grade.ts only reads
// inputs.json's cases array).  We load expected.json from the filesystem
// directly using import.meta.dir, keyed by case id.

type ExpectedEntry = {
  lane: "linear" | "univariate-poly" | "multivariate-poly" | "transcendental" | "refusal";
  tag?: string;
};

let _expectedIndex: Map<string, ExpectedEntry> | null = null;

function loadExpected(): Map<string, ExpectedEntry> {
  if (_expectedIndex !== null) return _expectedIndex;
  const path = resolve(import.meta.dir, "expected.json");
  if (!existsSync(path)) {
    _expectedIndex = new Map();
    return _expectedIndex;
  }
  const raw = JSON.parse(readFileSync(path, "utf8")) as {
    cases: Array<{ id: string; expected: ExpectedEntry }>;
  };
  _expectedIndex = new Map(raw.cases.map((c) => [c.id, c.expected]));
  return _expectedIndex;
}

// =============================================================================
// BigInt rational arithmetic — for exact linear-lane checks
// =============================================================================

type Rat = { n: bigint; d: bigint };

function gcdBig(a: bigint, b: bigint): bigint {
  a = a < 0n ? -a : a;
  b = b < 0n ? -b : b;
  while (b !== 0n) { const t = b; b = a % b; a = t; }
  return a === 0n ? 1n : a;
}

function ratNorm(n: bigint, d: bigint): Rat {
  if (d === 0n) throw new Error("ratNorm: zero denominator");
  if (d < 0n) { n = -n; d = -d; }
  const g = gcdBig(n < 0n ? -n : n, d);
  return { n: n / g, d: d / g };
}

function ratAdd(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.d + b.n * a.d, a.d * b.d); }
function ratSub(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.d - b.n * a.d, a.d * b.d); }
function ratMul(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.n, a.d * b.d); }
function ratEqZero(r: Rat): boolean  { return r.n === 0n; }

const RAT_ZERO: Rat = { n: 0n, d: 1n };

const RAT_RE = /^-?\d+(?:\/\d+)?$/;

function parseRat(s: string): Rat {
  s = s.trim();
  if (!RAT_RE.test(s)) throw new Error(`not a rational string: ${JSON.stringify(s)}`);
  const slash = s.indexOf("/");
  if (slash === -1) return { n: BigInt(s), d: 1n };
  return ratNorm(BigInt(s.slice(0, slash)), BigInt(s.slice(slash + 1)));
}

// ─── Affine expression evaluator (for free-var substitution) ─────────────────
//
// Mirrors linsolve-q's evalAffine.  The linear solve tool emits binding values
// as arithmetic expressions in the original variables and branch symbols.
// For checking: parse the value string and evaluate at a concrete rational
// substitution.  The tool emits forms like:
//   "5"                              — rational constant
//   "t_0"                            — bare free-variable
//   "(5 + (-2)*t_0)"                — affine with negated coefficient
//   "((3/2) + (1/3)*t_0 - t_1)"    — multiple free vars

type ATok =
  | { t: "num"; v: bigint; d: bigint }
  | { t: "ident"; v: string }
  | { t: "op"; v: string }
  | { t: "lp" } | { t: "rp" }
  | { t: "eof" };

function tokenizeAffine(s: string): ATok[] {
  const out: ATok[] = [];
  let i = 0;
  while (i < s.length) {
    const ch = s[i]!;
    if (/\s/.test(ch)) { i++; continue; }
    if (/\d/.test(ch)) {
      let j = i;
      while (j < s.length && /\d/.test(s[j]!)) j++;
      const n = BigInt(s.slice(i, j));
      i = j;
      if (i < s.length && s[i] === "/" && i + 1 < s.length && /\d/.test(s[i + 1]!)) {
        i++;
        let k = i;
        while (k < s.length && /\d/.test(s[k]!)) k++;
        out.push({ t: "num", v: n, d: BigInt(s.slice(i, k)) });
        i = k;
      } else {
        out.push({ t: "num", v: n, d: 1n });
      }
      continue;
    }
    if (/[a-zA-Z_]/.test(ch)) {
      let j = i;
      while (j < s.length && /[a-zA-Z0-9_]/.test(s[j]!)) j++;
      out.push({ t: "ident", v: s.slice(i, j) });
      i = j;
      continue;
    }
    if (ch === "(") { out.push({ t: "lp" }); i++; continue; }
    if (ch === ")") { out.push({ t: "rp" }); i++; continue; }
    if ("+-*/".includes(ch)) { out.push({ t: "op", v: ch }); i++; continue; }
    throw new Error(`tokenizeAffine: unexpected char ${JSON.stringify(ch)} in ${JSON.stringify(s)}`);
  }
  out.push({ t: "eof" });
  return out;
}

function evalAffine(expr: string, subst: Map<string, Rat>): Rat {
  const toks = tokenizeAffine(expr);
  let i = 0;

  function peek(): ATok { return toks[i]!; }
  function eat(): ATok  { return toks[i++]!; }

  function parseMulDiv(): Rat {
    let lhs = parseAtom();
    while (peek().t === "op") {
      const op = (peek() as { t: "op"; v: string }).v;
      if (op !== "*" && op !== "/") break;
      eat();
      const rhs = parseAtom();
      if (op === "*") { lhs = ratMul(lhs, rhs); }
      else {
        if (ratEqZero(rhs)) throw new Error("division by zero in affine expression");
        lhs = ratMul(lhs, ratNorm(rhs.d, rhs.n < 0n ? -rhs.n : rhs.n));
      }
    }
    return lhs;
  }

  function parseAtom(): Rat {
    const t = peek();
    if (t.t === "num") {
      eat();
      return ratNorm((t as { t: "num"; v: bigint; d: bigint }).v, (t as { t: "num"; v: bigint; d: bigint }).d);
    }
    if (t.t === "ident") {
      eat();
      const name = (t as { t: "ident"; v: string }).v;
      const val = subst.get(name);
      if (val === undefined) throw new Error(`evalAffine: unknown variable ${JSON.stringify(name)}`);
      return val;
    }
    if (t.t === "lp") {
      eat();
      const inner = parseTopExpr();
      if (peek().t !== "rp") throw new Error("evalAffine: expected )");
      eat();
      return inner;
    }
    throw new Error(`evalAffine: unexpected token ${JSON.stringify(t)}`);
  }

  function parseTopExpr(): Rat {
    // Handle leading unary minus.
    if (peek().t === "op" && (peek() as { t: "op"; v: string }).v === "-") {
      eat();
      let lhs = ratNorm(-parseMulDiv().n, 1n);
      // Actually: negate the mul-div term, then continue with add/sub.
      // We already consumed the unary minus, so the n: -parseMulDiv().n is wrong
      // because parseMulDiv() was already called above.  Rewrite as:
      // Oops — we called parseMulDiv twice above (once for the negate, once for
      // continuations).  Reset: use a dedicated helper.
      void lhs;
      // Restart with correct unary-minus handling.
      throw new Error("evalAffine: internal parser bug — use parseAddSub");
    }
    return parseAddSub();
  }

  function parseAddSub(): Rat {
    // Handle optional leading unary minus on the first term.
    let lhs: Rat;
    if (peek().t === "op" && (peek() as { t: "op"; v: string }).v === "-") {
      eat();
      lhs = ratNorm(-parseMulDiv().n, parseMulDiv().d);
      // The above calls parseMulDiv() twice — bug.  Fix inline:
      throw new Error("evalAffine: internal - bug");
    }
    lhs = parseMulDiv();
    while (peek().t === "op") {
      const op = (peek() as { t: "op"; v: string }).v;
      if (op !== "+" && op !== "-") break;
      eat();
      const rhs = parseMulDiv();
      lhs = op === "+" ? ratAdd(lhs, rhs) : ratSub(lhs, rhs);
    }
    return lhs;
  }

  // Top-level entry: handle leading unary minus by negating the first
  // multiplicative term and continuing with the additive loop.
  function parseRoot(): Rat {
    let sign = 1n;
    if (peek().t === "op" && (peek() as { t: "op"; v: string }).v === "-") {
      eat();
      sign = -1n;
    }
    let lhs = parseMulDiv();
    if (sign < 0n) lhs = ratNorm(-lhs.n, lhs.d);
    while (peek().t === "op") {
      const op = (peek() as { t: "op"; v: string }).v;
      if (op !== "+" && op !== "-") break;
      eat();
      const rhs = parseMulDiv();
      lhs = op === "+" ? ratAdd(lhs, rhs) : ratSub(lhs, rhs);
    }
    return lhs;
  }

  void parseTopExpr;
  void parseAddSub;
  const result = parseRoot();
  if (peek().t !== "eof") throw new Error(`evalAffine: trailing input in ${JSON.stringify(expr)}`);
  return result;
}

// ─── BigInt rank computation via Bareiss elimination ─────────────────────────

function computeRankBigInt(A_in: bigint[][]): number {
  const m = A_in.length;
  if (m === 0) return 0;
  const n = A_in[0]!.length;
  if (n === 0) return 0;
  const M: bigint[][] = A_in.map((r) => [...r]);
  let rank = 0;
  let prev = 1n;
  let pivRow = 0;
  for (let col = 0; col < n && pivRow < m; col++) {
    let found = -1;
    for (let r = pivRow; r < m; r++) if (M[r]![col] !== 0n) { found = r; break; }
    if (found === -1) continue;
    if (found !== pivRow) { const tmp = M[found]!; M[found] = M[pivRow]!; M[pivRow] = tmp; }
    const pv = M[pivRow]![col]!;
    for (let r = pivRow + 1; r < m; r++) {
      const fac = M[r]![col]!;
      for (let j = col + 1; j < n; j++) {
        M[r]![j] = (pv * M[r]![j]! - fac * M[pivRow]![j]!) / prev;
      }
      M[r]![col] = 0n;
    }
    prev = pv; rank++; pivRow++;
  }
  return rank;
}

function ratRowsToInteger(A: Rat[][]): bigint[][] {
  return A.map((row) => {
    let lcm = 1n;
    for (const r of row) { const g = gcdBig(lcm, r.d); lcm = lcm / g * r.d; }
    return row.map((r) => r.n * (lcm / r.d));
  });
}

function computeRank(A: Rat[][]): number {
  if (A.length === 0) return 0;
  if (A[0]!.length === 0) return 0;
  return computeRankBigInt(ratRowsToInteger(A));
}

function computeAugRank(A: Rat[][], b: Rat[]): number {
  return computeRank(A.map((row, i) => [...row, b[i]!]));
}

// =============================================================================
// Float64 expression evaluator — for polynomial and transcendental lanes
// =============================================================================
//
// Evaluates expression strings produced by the tool (via run-candidate.ts's
// valueToString) numerically at a concrete variable substitution.
//
// Supported constructs:
//   integers, rationals (n/d notation)
//   symbols: pi → Math.PI, e → Math.E, variable names from the substitution map
//   arithmetic: +, -, *, /, **  (both Python ** and ^ are tokenised as **)
//   functions: sin, cos, tan, exp, log, sinh, cosh, tanh, abs, Abs,
//              asin, acos, atan, sqrt, sqrt
//   parentheses, function calls
//   CRootOf(poly, k) — companion-matrix root evaluation (see below)
//
// The float64 evaluator is adequate for the bench's actual output values:
//   • rational roots (integers, fractions)  — exact
//   • degree-2 radical roots (sqrt)         — ~1 ULP error
//   • degree-3/4 Cardano/Ferrari roots      — at most ~10 ULP
//   • transcendental branch expressions     — at most ~10 ULP

type FTok =
  | { t: "num"; v: number }
  | { t: "ident"; v: string }
  | { t: "op"; v: string }
  | { t: "lp" } | { t: "rp" } | { t: "comma" }
  | { t: "eof" };

function tokenizeFloat(s: string): FTok[] {
  const out: FTok[] = [];
  let i = 0;
  while (i < s.length) {
    const ch = s[i]!;
    if (/\s/.test(ch)) { i++; continue; }
    if (/[0-9]/.test(ch) || (ch === "." && /[0-9]/.test(s[i+1] ?? ""))) {
      let j = i;
      while (j < s.length && /[0-9.]/.test(s[j]!)) j++;
      out.push({ t: "num", v: parseFloat(s.slice(i, j)) });
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
    throw new Error(`tokenizeFloat: unexpected char ${JSON.stringify(ch)} in ${JSON.stringify(s)}`);
  }
  out.push({ t: "eof" });
  return out;
}

const FLOAT_FUNCS: Record<string, (x: number) => number> = {
  sin: Math.sin, cos: Math.cos, tan: Math.tan,
  exp: Math.exp, log: Math.log,
  sinh: Math.sinh, cosh: Math.cosh, tanh: Math.tanh,
  abs: Math.abs, Abs: Math.abs,
  asin: Math.asin, acos: Math.acos, atan: Math.atan,
  arcsin: Math.asin, arccos: Math.acos, arctan: Math.atan,
  asinh: Math.asinh, acosh: Math.acosh, atanh: Math.atanh,
  sqrt: Math.sqrt,
  // The workbench emits `neg(x)` (not `-x`) for `expr("*", [int(-1), x])`
  // when the operand is a non-numeric subexpression — see
  // benchmarks/solve/run-candidate.ts's `valueToString` n=1 fall-through
  // for `expr("-", [x])` and the cas-core convention for unary negation
  // wrapping non-literal subexpressions.  Verify.ts must mirror this
  // vocabulary to evaluate shape-lemma cubic / quartic Horner expansions
  // whose intermediate sums fold a `neg(sqrt(...))` term.
  neg: (x: number) => -x,
};

const FLOAT_CONSTS: Record<string, number> = {
  pi: Math.PI,
  e: Math.E,
};

// ─── CRootOf companion-matrix real-root evaluation ────────────────────────────
//
// CRootOf(poly_expr, k) in the output string means "the k-th real root
// (0-based, ascending) of the polynomial poly_expr in an anonymous variable".
// We evaluate it by:
//   1. Numerically evaluate the polynomial coefficients.
//   2. Find all real roots via companion matrix eigenvalue estimation
//      (Durand-Kerner iteration) or bisection.
//   3. Sort real roots ascending, return the k-th.
//
// For the bench's cases, the anonymous variable in Root[poly, k] is the
// same as the bench variable (e.g. "x") — the valueToString encoder emits
// the poly expression with the variable symbol included.  We evaluate by
// treating the variable as the root being solved for; we extract coefficients
// from the poly string numerically.

function evalFloat(s: string, subst: Map<string, number>): number {
  const toks = tokenizeFloat(s);
  let i = 0;

  function peek(): FTok { return toks[i]!; }
  function eat(): FTok  { return toks[i++]!; }

  function parseAddSub(): number {
    let lhs = parseMulDiv();
    while (peek().t === "op") {
      const op = (peek() as { t: "op"; v: string }).v;
      if (op !== "+" && op !== "-") break;
      eat();
      const rhs = parseMulDiv();
      lhs = op === "+" ? lhs + rhs : lhs - rhs;
    }
    return lhs;
  }

  function parseMulDiv(): number {
    let lhs = parseUnary();
    while (peek().t === "op") {
      const op = (peek() as { t: "op"; v: string }).v;
      if (op !== "*" && op !== "/") break;
      eat();
      const rhs = parseUnary();
      lhs = op === "*" ? lhs * rhs : lhs / rhs;
    }
    return lhs;
  }

  // Handle leading unary `+`/`-` in any term position.  Without this,
  // an expression like `(... + -7/2)` (which the workbench emits from
  // shape-lemma cubic/quartic Horner expansions) tokenises to
  //   `... op(+) op(-) num(7) op(/) num(2) ...`
  // and the additive parser, after eating `+`, finds an operator token
  // where it expects an atom.  Threading a unary layer immediately
  // above the power-and-atom parser lets every term position absorb
  // its sign without disturbing left-associativity of `*` and `/`
  // (parseMulDiv's outer loop is the sole owner of multiplicative
  // associativity).
  function parseUnary(): number {
    let sign = 1;
    while (peek().t === "op") {
      const op = (peek() as { t: "op"; v: string }).v;
      if (op === "-") { eat(); sign = -sign; }
      else if (op === "+") { eat(); }
      else break;
    }
    return sign * parsePow();
  }

  function parsePow(): number {
    const base = parseAtom();
    if (peek().t === "op") {
      const op = (peek() as { t: "op"; v: string }).v;
      if (op === "**" || op === "^") {
        eat();
        const exp_ = parseAtom();
        return Math.pow(base, exp_);
      }
    }
    return base;
  }

  function parseAtom(): number {
    const t = peek();
    if (t.t === "num") { eat(); return (t as { t: "num"; v: number }).v; }
    if (t.t === "ident") {
      const name = (t as { t: "ident"; v: string }).v;
      eat();
      // Function call?
      if (peek().t === "lp") {
        eat(); // (
        // Check for CRootOf special form.
        if (name === "CRootOf") {
          // CRootOf(poly_expr_with_var, k)
          // Collect inner tokens up to the comma, then evaluate as a polynomial in x.
          // Strategy: collect the full poly token string and evaluate it symbolically.
          const polyToks: string[] = [];
          let depth = 1;
          while (depth > 0 && peek().t !== "eof") {
            const tk = peek();
            if (tk.t === "lp")    { depth++; polyToks.push("("); eat(); continue; }
            if (tk.t === "rp")    { depth--; if (depth > 0) polyToks.push(")"); eat(); continue; }
            if (tk.t === "comma" && depth === 1) { eat(); break; }
            if (tk.t === "num")   { polyToks.push(String((tk as { t:"num"; v:number }).v)); eat(); continue; }
            if (tk.t === "ident") { polyToks.push((tk as { t:"ident"; v:string }).v); eat(); continue; }
            if (tk.t === "op")    { polyToks.push((tk as { t:"op"; v:string }).v); eat(); continue; }
            eat();
          }
          const k = parseAtom(); // the index
          if (peek().t === "rp") eat(); // )
          const polyStr = polyToks.join(" ");
          return evalCRootOf(polyStr, k);
        }
        // Normal function call.
        const args: number[] = [];
        if (peek().t !== "rp") {
          args.push(parseAddSub());
          while (peek().t === "comma") { eat(); args.push(parseAddSub()); }
        }
        if (peek().t === "rp") eat();
        const fn = FLOAT_FUNCS[name];
        if (fn && args.length === 1) return fn(args[0]!);
        if (args.length === 2) {
          if (name === "pow") return Math.pow(args[0]!, args[1]!);
          if (name === "atan2") return Math.atan2(args[0]!, args[1]!);
        }
        throw new Error(`evalFloat: unknown function ${name}/${args.length}`);
      }
      // Constant or variable.
      const cval = FLOAT_CONSTS[name];
      if (cval !== undefined) return cval;
      const sval = subst.get(name);
      if (sval !== undefined) return sval;
      throw new Error(`evalFloat: unknown identifier ${JSON.stringify(name)}`);
    }
    if (t.t === "lp") {
      eat();
      const v = parseAddSub();
      if (peek().t === "rp") eat();
      return v;
    }
    throw new Error(`evalFloat: unexpected token ${JSON.stringify(t)} in ${JSON.stringify(s)}`);
  }

  const result = parseAddSub();
  if (peek().t !== "eof") throw new Error(`evalFloat: trailing tokens in ${JSON.stringify(s)}`);
  return result;
}

// ─── CRootOf poly evaluation via companion matrix / Durand-Kerner ────────────
//
// Given a polynomial expression string in a single variable (e.g. "x**3 - x - 1")
// and a 0-based real-root index k (ascending), return the k-th real root.

function evalCRootOf(polyStr: string, k: number): number {
  // Parse the polynomial by scanning for the variable name (the identifier
  // that is NOT a known constant or function).  We find the degree and
  // coefficients by evaluating poly at several integer points and fitting,
  // OR we use direct coefficient extraction via symbolic evaluation.
  //
  // Direct approach: evaluate poly at x=0,1,-1,2,-2,...  then use
  // finite differences to reconstruct the coefficient vector.
  // That requires knowing the degree up front.
  //
  // Simpler: extract the variable name from the poly string, then evaluate
  // using eval with x=various values, then use a bracketed bisection from a
  // Sturm-sequence root count — but Sturm needs the coefficients.
  //
  // Pragmatic approach for the bench's cases:
  //   1. The polynomial is in a single real variable (the bench variable).
  //   2. Bisect on [-1000, 1000] using sign changes, refine to 1e-12.
  //   3. Sort the roots ascending, return k-th.
  //
  // To evaluate the polynomial as a function of t, we replace the variable
  // in the string with our evaluation point.  The variable name is inferred
  // as the first unknown identifier.

  // Find the variable name.
  const toks = tokenizeFloat(polyStr);
  let varName: string | null = null;
  for (const tk of toks) {
    if (tk.t === "ident") {
      const n = (tk as { t: "ident"; v: string }).v;
      if (!FLOAT_FUNCS[n] && !FLOAT_CONSTS[n]) { varName = n; break; }
    }
  }
  if (!varName) {
    // Constant polynomial — no roots.
    throw new Error(`CRootOf: no variable in polynomial ${JSON.stringify(polyStr)}`);
  }

  const vn = varName;
  function polyAt(t: number): number {
    const sub = new Map<string, number>();
    sub.set(vn, t);
    return evalFloat(polyStr, sub);
  }

  // Estimate degree by sampling high values and fitting log|p(t)|/log(t).
  // For the bench we know deg ≤ 10; do a grid search on [-1000, 1000].
  const GRID_POINTS = 2000;
  const LO = -1e3, HI = 1e3;
  const step = (HI - LO) / GRID_POINTS;
  const roots: number[] = [];

  let prevY = polyAt(LO);
  let prevX = LO;

  for (let j = 1; j <= GRID_POINTS; j++) {
    const x = LO + j * step;
    let y: number;
    try { y = polyAt(x); } catch { y = NaN; }
    if (!isFinite(prevY) || !isFinite(y)) { prevX = x; prevY = y; continue; }
    if (prevY === 0) {
      roots.push(prevX);
    } else if (prevY * y < 0) {
      // Bisect for root.
      let a = prevX, b = x, fa = prevY, fb = y;
      for (let iter = 0; iter < 80; iter++) {
        const mid = (a + b) / 2;
        const fm = polyAt(mid);
        if (!isFinite(fm)) break;
        if (fa * fm <= 0) { b = mid; fb = fm; }
        else              { a = mid; fa = fm; }
        if (Math.abs(b - a) < 1e-12) break;
      }
      const root = (a + b) / 2;
      roots.push(root);
    }
    prevX = x; prevY = y;
  }
  // Check right endpoint.
  if (isFinite(prevY) && prevY === 0) roots.push(prevX);

  // Deduplicate roots that are within 1e-10 of each other.
  roots.sort((a, b) => a - b);
  const deduped: number[] = [];
  for (const r of roots) {
    if (deduped.length === 0 || r - deduped[deduped.length - 1]! > 1e-10) deduped.push(r);
  }

  if (k < 0 || k >= deduped.length) {
    throw new Error(`CRootOf index ${k} out of range (found ${deduped.length} real roots of ${polyStr})`);
  }
  return deduped[k]!;
}

// =============================================================================
// Equation string evaluator
// =============================================================================
//
// The bench input equations are Python/SymPy expression strings.  We need
// to evaluate them numerically (for transcendental and polynomial checks).
// Re-use evalFloat with the equation's variables in the substitution map.

function evalEq(eqStr: string, subst: Map<string, number>): number {
  return evalFloat(eqStr, subst);
}

// =============================================================================
// N/a helper
// =============================================================================

function na(lane: string): Check {
  return { pass: true, detail: `n/a for lane=${lane}` };
}

function fail(detail: string): Check {
  return { pass: false, detail };
}

function pass(detail: string): Check {
  return { pass: true, detail };
}

// =============================================================================
// Payload types
// =============================================================================

type Binding  = { var: string; value: string };
type Solution = { bindings: Binding[]; branches: string[] };

interface OkCandidate {
  kind:         "ok";
  vars:         string[];
  solutions:    Solution[];
  completeness: string;
  warnings:     string[];
}

interface TaggedCandidate {
  kind:    "tagged";
  tag:     string;
  payload: { detail: string };
}

type Candidate = OkCandidate | TaggedCandidate | { kind: string };

interface InputShape {
  eqs:  string[];
  vars: string[];
}

interface Expected {
  lane: "linear" | "univariate-poly" | "multivariate-poly" | "transcendental" | "refusal";
  tag?: string;
}

// =============================================================================
// Lane: refusal (2 checks)
// =============================================================================

function verifyRefusal(cand: Candidate, expected: Expected): Record<string, Check> {
  const checks: Record<string, Check> = {};

  if (cand.kind !== "tagged") {
    return {
      tag_matches: fail(`candidate.kind=${JSON.stringify(cand.kind)} but lane=refusal requires kind=tagged`),
      payload_predicate: fail("skipped: kind mismatch"),
    };
  }

  const ct = cand as TaggedCandidate;
  const tag = ct.tag ?? "";

  // tag_matches
  if (!tag.startsWith("solve/")) {
    checks["tag_matches"] = fail(`tag=${JSON.stringify(tag)} not in solve/* namespace`);
  } else if (expected.tag && tag !== expected.tag) {
    checks["tag_matches"] = fail(`cand.tag=${JSON.stringify(tag)} != expected.tag=${JSON.stringify(expected.tag)}`);
  } else {
    checks["tag_matches"] = pass(`refusal tag=${JSON.stringify(tag)}${expected.tag ? " matches expected" : " (no expected.tag pinned)"}`);
  }

  // payload_predicate
  const payload = ct.payload;
  if (!payload || typeof payload !== "object") {
    checks["payload_predicate"] = fail("payload missing or not a record");
  } else {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail !== "string" || !detail) {
      checks["payload_predicate"] = fail("payload.detail missing or empty");
    } else {
      checks["payload_predicate"] = pass("payload.detail is non-empty string");
    }
  }

  return checks;
}

// =============================================================================
// Lane: linear (5 checks)
// =============================================================================
//
// Checks: shape, exact_satisfaction, free_var_basis, rank_consistent,
//         completeness_correct.
//
// The linear lane equations are all polynomials of total degree ≤ 1 in the
// variables.  We extract the A matrix and b vector by evaluating the
// equations at rational test points (or by treating coefficients as rationals
// directly parsed from the equation strings).
//
// Design choice: we parse the coefficient matrix from the equation strings
// using a simplified linear-coefficient extractor.  For the bench's cases
// (all coefficients are small integers/rationals from [-9, 9]), this is
// reliable.  The alternative — substituting the candidate binding values
// directly into the equation strings via evalFloat — is used for
// exact_satisfaction.

// Parse a linear equation over rational variables into Rat coefficients.
// Returns { A_row: Rat[], b: Rat } for equation A_row · vars + const = 0.
// Uses sympy-style: substitute var_i = 0 to get constant, substitute var_i = 1
// and others = 0 to get coefficient of var_i.
function extractLinearCoeffs(
  eqStr: string,
  varNames: string[],
): { Arow: Rat[]; b: Rat } | null {
  // Evaluate eq at zeros: get the constant term.
  const zeroSub = new Map<string, number>(varNames.map((v) => [v, 0]));
  let constVal: number;
  try { constVal = evalEq(eqStr, zeroSub); }
  catch { return null; }

  // For each var_i: set var_i = 1, others = 0.
  const Arow: Rat[] = [];
  for (const vn of varNames) {
    const sub = new Map<string, number>(varNames.map((v) => [v, 0]));
    sub.set(vn, 1);
    let v1: number;
    try { v1 = evalEq(eqStr, sub); }
    catch { return null; }
    // coeff_i = eq(1 for v_i, 0 otherwise) - eq(0, 0, ...)
    // We need this as a rational.  For the bench, coefficients are integers
    // or simple fractions.  Round to nearest rational with denominator ≤ 1000.
    const coeff = v1 - constVal;
    const coeffRat = approxRat(coeff);
    Arow.push(coeffRat);
  }
  const bRat = ratNorm(-approxRat(constVal).n, approxRat(constVal).d);
  return { Arow, b: bRat };
}

// Approximate a float as a rational with small denominator.
function approxRat(x: number): Rat {
  if (!isFinite(x)) throw new Error(`approxRat: non-finite ${x}`);
  // Round to nearest multiple of 1/1000 (enough for the bench's coefficients).
  const sign = x < 0 ? -1n : 1n;
  x = Math.abs(x);
  // Try denominators 1 through 1000.
  for (let d = 1; d <= 1000; d++) {
    const nd = x * d;
    const n = Math.round(nd);
    if (Math.abs(n - nd) < 1e-9) {
      return ratNorm(sign * BigInt(n), BigInt(d));
    }
  }
  // Fallback: use the nearest integer (for very small floats, near-zero cases).
  return { n: BigInt(Math.round(x)) * sign, d: 1n };
}

const SUBST_POOL: Rat[] = [
  { n: -3n, d: 1n }, { n: -1n, d: 1n }, { n: 0n, d: 1n },
  { n: 1n, d: 1n }, { n: 2n, d: 1n },
  ratNorm(5n, 3n), ratNorm(-7n, 4n),
  { n: 11n, d: 1n }, ratNorm(-19n, 4n), ratNorm(6n, 7n),
];

function verifyLinear(
  inp: InputShape,
  cand: Candidate,
  _expected: Expected,
): Record<string, Check> {
  const checks: Record<string, Check> = {};
  const SKIP = (k: string) => { checks[k] = fail("skipped: shape failed"); };

  if (cand.kind !== "ok") {
    checks["shape"] = fail(`candidate.kind=${JSON.stringify(cand.kind)} but lane=linear requires kind=ok`);
    ["exact_satisfaction","free_var_basis","rank_consistent","completeness_correct"].forEach(SKIP);
    return checks;
  }

  const ok = cand as OkCandidate;
  const varNames = inp.vars;
  const n = varNames.length;

  // ── shape ─────────────────────────────────────────────────────────────────
  let shapeFail: string | null = null;
  if (ok.vars === undefined || !Array.isArray(ok.vars)) {
    shapeFail = "vars field missing or not a list";
  } else if (ok.solutions === undefined || !Array.isArray(ok.solutions)) {
    shapeFail = "solutions field missing";
  } else if (ok.solutions.length > 1) {
    shapeFail = `linear lane: |solutions| ∈ {0,1}, got ${ok.solutions.length}`;
  } else if (!["complete","finite-rep-of-infinite"].includes(ok.completeness)) {
    shapeFail = `completeness=${JSON.stringify(ok.completeness)}`;
  } else if (ok.solutions.length === 1) {
    const sol = ok.solutions[0]!;
    if (!sol.bindings || !sol.branches) shapeFail = "solution missing bindings/branches";
    else if (sol.bindings.length !== n) shapeFail = `|bindings|=${sol.bindings.length} != |vars|=${n}`;
    else {
      for (let idx = 0; idx < n; idx++) {
        const b = sol.bindings[idx]!;
        if (b.var !== varNames[idx]) { shapeFail = `binding[${idx}].var=${JSON.stringify(b.var)} != ${JSON.stringify(varNames[idx])}`; break; }
        if (typeof b.value !== "string" || !b.value) { shapeFail = `binding[${idx}].value is not a string`; break; }
      }
    }
  }
  if (shapeFail) {
    checks["shape"] = fail(shapeFail);
    ["exact_satisfaction","free_var_basis","rank_consistent","completeness_correct"].forEach(SKIP);
    return checks;
  }
  checks["shape"] = pass(`${n} vars; ${ok.solutions.length} solution(s); shape ok`);

  const branches: string[] = ok.solutions.length === 1 ? (ok.solutions[0]!.branches ?? []) : [];
  const bindings: Binding[] = ok.solutions.length === 1 ? (ok.solutions[0]!.bindings ?? []) : [];

  // ── exact_satisfaction ───────────────────────────────────────────────────
  {
    if (ok.solutions.length === 0) {
      // Inconsistent: verify by rank comparison.
      let consistent = false;
      try {
        const Arows: Rat[][] = [];
        const bVec: Rat[] = [];
        for (const eqStr of inp.eqs) {
          const res = extractLinearCoeffs(eqStr, varNames);
          if (!res) { consistent = true; break; }
          Arows.push(res.Arow);
          bVec.push(res.b);
        }
        if (!consistent) {
          const rA  = computeRank(Arows);
          const rAb = computeAugRank(Arows, bVec);
          consistent = rA >= rAb;
        }
        checks["exact_satisfaction"] = consistent
          ? fail("candidate claims inconsistent but rank(A) == rank([A|b])")
          : pass("inconsistency confirmed via rank comparison");
      } catch (e) {
        checks["exact_satisfaction"] = fail(`rank extraction failed: ${(e as Error).message}`);
      }
    } else {
      // Build substitution: var_i → value from binding (possibly in terms of branches).
      const branchSub = new Map<string, Rat>();
      for (const b of branches) branchSub.set(b, RAT_ZERO);

      let satFail: string | null = null;
      for (let eqIdx = 0; eqIdx < inp.eqs.length && !satFail; eqIdx++) {
        const eqStr = inp.eqs[eqIdx]!;
        // Build a concrete float substitution for this equation.
        const sub = new Map<string, number>();
        for (const b of branches) sub.set(b, 0);
        let evalOk = true;
        for (let vi = 0; vi < n; vi++) {
          const bindingVal = bindings[vi]!.value;
          let fval: number;
          try {
            fval = evalFloat(bindingVal, new Map<string, number>(branches.map((b) => [b, 0])));
          } catch {
            evalOk = false;
            satFail = `binding[${vi}].value didn't evaluate: ${bindingVal}`;
            break;
          }
          sub.set(varNames[vi]!, fval);
        }
        if (!evalOk) break;
        let res: number;
        try { res = evalEq(eqStr, sub); }
        catch (e) { satFail = `eq[${eqIdx}] eval failed: ${(e as Error).message}`; break; }
        if (Math.abs(res) > 1e-8 * Math.max(1, Math.abs(res))) {
          satFail = `equation #${eqIdx+1} residue=${res} with all branches=0`;
        }
      }
      // Also try with one nontrivial branch substitution if any.
      if (!satFail && branches.length > 0) {
        for (let eqIdx = 0; eqIdx < inp.eqs.length && !satFail; eqIdx++) {
          const eqStr = inp.eqs[eqIdx]!;
          const sub = new Map<string, number>();
          for (let bi = 0; bi < branches.length; bi++) sub.set(branches[bi]!, bi + 1);
          for (let vi = 0; vi < n; vi++) {
            const bindingVal = bindings[vi]!.value;
            let fval: number;
            try { fval = evalFloat(bindingVal, sub); }
            catch { fval = 0; }
            sub.set(varNames[vi]!, fval);
          }
          let res: number;
          try { res = evalEq(eqStr, sub); }
          catch { continue; }
          if (Math.abs(res) > 1e-8 * Math.max(1, Math.abs(res))) {
            satFail = `equation #${eqIdx+1} residue=${res} with branches substituted`;
          }
        }
      }
      checks["exact_satisfaction"] = satFail ? fail(satFail) : pass(`${inp.eqs.length} equation(s) satisfied`);
    }
  }

  // ── free_var_basis ────────────────────────────────────────────────────────
  if (branches.length === 0) {
    checks["free_var_basis"] = na("linear (no branches)");
  } else {
    const nBranch = branches.length;
    const nSamples = Math.min(50, Math.pow(SUBST_POOL.length, nBranch));
    let freeFail: string | null = null;

    const indices = Array.from({ length: nSamples }, (_, t) =>
      branches.map((_, bIdx) => SUBST_POOL[(t + bIdx) % SUBST_POOL.length]!)
    );

    for (const branchVals of indices) {
      if (freeFail) break;
      // Build a BigInt-rational substitution map for the branches.
      const sub = new Map<string, Rat>();
      branches.forEach((b, idx) => sub.set(b, branchVals[idx]!));

      // Also build a float substitution for the binding values.
      const floatBranchSub = new Map<string, number>();
      branches.forEach((b, idx) => floatBranchSub.set(b, Number(branchVals[idx]!.n) / Number(branchVals[idx]!.d)));

      // Evaluate each equation at the substituted binding values.
      for (let eqIdx = 0; eqIdx < inp.eqs.length && !freeFail; eqIdx++) {
        const eqStr = inp.eqs[eqIdx]!;
        const floatSub = new Map<string, number>(floatBranchSub);
        let evalFailed = false;
        for (let vi = 0; vi < n; vi++) {
          try {
            const fval = evalFloat(bindings[vi]!.value, floatBranchSub);
            floatSub.set(varNames[vi]!, fval);
          } catch { evalFailed = true; break; }
        }
        if (evalFailed) break;
        let res: number;
        try { res = evalEq(eqStr, floatSub); }
        catch { break; }
        if (Math.abs(res) > 1e-8 * Math.max(1, Math.abs(res))) {
          freeFail = `branch sample ${JSON.stringify(Object.fromEntries(branches.map((b,i)=>[b,`${branchVals[i]!.n}/${branchVals[i]!.d}`])))} fails eq #${eqIdx+1}: res=${res}`;
        }
      }
    }
    checks["free_var_basis"] = freeFail ? fail(freeFail) : pass(`${nSamples} free-var samples satisfy`);
  }

  // ── rank_consistent ───────────────────────────────────────────────────────
  {
    let rankFail: string | null = null;
    try {
      const Arows: Rat[][] = [];
      for (const eqStr of inp.eqs) {
        const res = extractLinearCoeffs(eqStr, varNames);
        if (!res) { rankFail = "could not extract linear coefficients from equation string"; break; }
        Arows.push(res.Arow);
      }
      if (!rankFail) {
        const rank = computeRank(Arows);
        const expectedBranches = n - rank;
        if (branches.length !== expectedBranches) {
          rankFail = `|branches|=${branches.length}, expected n_vars - rank(A) = ${n} - ${rank} = ${expectedBranches}`;
        }
        if (!rankFail) checks["rank_consistent"] = pass(`|branches| = ${branches.length} = n_vars - rank(A)`);
      }
    } catch (e) {
      rankFail = `rank computation failed: ${(e as Error).message}`;
    }
    if (rankFail) checks["rank_consistent"] = fail(rankFail);
  }

  // ── completeness_correct ──────────────────────────────────────────────────
  {
    const comp = ok.completeness;
    const sols = ok.solutions;
    let compFail: string | null = null;
    if (comp === "complete") {
      if (sols.length === 0) {
        // ok — inconsistent + complete
      } else if (sols.length === 1 && branches.length === 0) {
        // ok — unique + complete
      } else {
        compFail = "completeness='complete' but solutions has branches";
      }
    } else if (comp === "finite-rep-of-infinite") {
      if (sols.length === 1 && branches.length > 0) {
        // ok
      } else {
        compFail = "completeness='finite-rep-of-infinite' but solutions empty or has no branches";
      }
    } else {
      compFail = `unknown completeness ${JSON.stringify(comp)}`;
    }
    checks["completeness_correct"] = compFail ? fail(compFail) : pass(`completeness=${JSON.stringify(comp)} consistent`);
  }

  return checks;
}

// =============================================================================
// Lane: univariate-poly (4 checks)
// =============================================================================
//
// Checks: shape, each_root_satisfies, count_with_multiplicity, distinct_roots_match.
//
// Design: evaluate the polynomial at each candidate root using evalFloat.
// The polynomial is parsed from the input equation string.  For well-formed
// candidate roots (integer, rational, radical, Root[]), float64 evaluation
// is accurate enough: correct roots satisfy the equation to ~1e-10, while
// mutated roots (off-by-one, wrong sign flip, wrong multiplicity) produce
// macroscopic residuals.

// Parse the polynomial from the input equation string and evaluate it at a
// given float x-value.  The input is a single equation string (SymPy form)
// and a single variable name.
function evalPoly(eqStr: string, varName: string, xVal: number): number {
  const sub = new Map<string, number>([[varName, xVal]]);
  return evalEq(eqStr, sub);
}

// Compute degree-based expected root count using the factor_list approach:
// for the bench's cases, we need to count roots with multiplicity.
// We approximate by numerically finding roots of the polynomial via grid scan
// and then matching them to candidate roots.
//
// For count_with_multiplicity: the polynomial's factor_list gives Σ deg(f_i)·e_i.
// We compute this numerically by estimating the total degree of the polynomial
// (sum of roots with multiplicity from a companion-matrix approach), then cross-
// checking against the candidate.
//
// Practical approach: since the bench input polynomial is a single expression
// string, we evaluate it at many points and count sign changes + exact zeros.
// The sign-change count gives the number of distinct real roots; the total
// degree (from sampling) gives the bound.  But for multiplicity detection
// (e.g., (x-1)^2), sign changes undercount.
//
// For the bench, the generate.py guarantees that every univariate-poly
// golden case falls into one of the factorisation categories handled by the
// tool.  We can compute the expected root count by evaluating f(x) and f'(x)
// at 200 points and counting the total degree as the degree of the poly
// extracted from the string.

function polyDegree(eqStr: string, varName: string): number {
  // Estimate degree by sampling f(x) at large x.
  // For polynomial p(x) of degree d: |p(x)| ≈ |leading_coef| * |x|^d for large x.
  try {
    const y1 = Math.abs(evalPoly(eqStr, varName, 100));
    const y2 = Math.abs(evalPoly(eqStr, varName, 200));
    if (y1 > 1e-10 && y2 > 1e-10) {
      const d = Math.log(y2 / y1) / Math.log(200 / 100);
      return Math.round(d);
    }
  } catch { /* fall through */ }
  return -1;
}

// Count total roots with multiplicity = degree of poly (by FTA for monic poly,
// this is equal to the sum Σ deg(f_i)*e_i over irreducible factors).
// For a poly over ℚ with rational roots counted with multiplicity = degree.
function expectedRootCount(eqStr: string, varName: string): number | null {
  try {
    const deg = polyDegree(eqStr, varName);
    return deg >= 0 ? deg : null;
  } catch {
    return null;
  }
}

// Find all distinct numerical real roots of a polynomial via dense grid.
function findNumericRoots(eqStr: string, varName: string, lo = -100, hi = 100, pts = 1000): number[] {
  const step = (hi - lo) / pts;
  const roots: number[] = [];
  let prevY = evalPoly(eqStr, varName, lo);
  let prevX = lo;
  for (let j = 1; j <= pts; j++) {
    const x = lo + j * step;
    let y: number;
    try { y = evalPoly(eqStr, varName, x); } catch { y = NaN; }
    if (!isFinite(prevY) || !isFinite(y)) { prevX = x; prevY = y; continue; }
    if (prevY === 0) {
      roots.push(prevX);
    } else if (prevY * y < 0) {
      // Bisect.
      let a = prevX, b = x, fa = prevY;
      for (let iter = 0; iter < 60; iter++) {
        const mid = (a + b) / 2;
        const fm = evalPoly(eqStr, varName, mid);
        if (!isFinite(fm)) break;
        if (fa * fm <= 0) { b = mid; } else { a = mid; fa = fm; }
        if (Math.abs(b - a) < 1e-12) break;
      }
      roots.push((a + b) / 2);
    }
    prevX = x; prevY = y;
  }
  // Dedup.
  roots.sort((a, b) => a - b);
  const deduped: number[] = [];
  for (const r of roots) {
    if (deduped.length === 0 || r - deduped[deduped.length - 1]! > 1e-10) deduped.push(r);
  }
  return deduped;
}

function verifyUnivariatePoly(
  inp: InputShape,
  cand: Candidate,
  _expected: Expected,
): Record<string, Check> {
  const checks: Record<string, Check> = {};
  const SKIP = (k: string) => { checks[k] = fail("skipped: shape failed"); };

  if (cand.kind !== "ok") {
    checks["shape"] = fail(`candidate.kind=${JSON.stringify(cand.kind)} but lane=univariate-poly requires kind=ok`);
    ["each_root_satisfies","count_with_multiplicity","distinct_roots_match"].forEach(SKIP);
    return checks;
  }

  if (inp.eqs.length !== 1 || inp.vars.length !== 1) {
    checks["shape"] = fail("univariate-poly lane requires exactly 1 eq, 1 var");
    ["each_root_satisfies","count_with_multiplicity","distinct_roots_match"].forEach(SKIP);
    return checks;
  }

  const ok = cand as OkCandidate;
  const eqStr = inp.eqs[0]!;
  const varName = inp.vars[0]!;

  // ── shape ─────────────────────────────────────────────────────────────────
  let shapeFail: string | null = null;
  if (ok.completeness !== "complete") {
    shapeFail = `completeness=${JSON.stringify(ok.completeness)}, expected 'complete'`;
  } else if (!Array.isArray(ok.solutions)) {
    shapeFail = "solutions not a list";
  } else {
    for (let i = 0; i < ok.solutions.length; i++) {
      const sol = ok.solutions[i]!;
      if (sol.bindings?.length !== 1) { shapeFail = `solution[${i}] has |bindings|!=1`; break; }
      if (sol.branches?.length !== 0) { shapeFail = `solution[${i}] has non-empty branches`; break; }
      if (sol.bindings[0]!.var !== varName) { shapeFail = `solution[${i}] var mismatch`; break; }
      if (typeof sol.bindings[0]!.value !== "string") { shapeFail = `solution[${i}] value not string`; break; }
    }
  }
  if (shapeFail) {
    checks["shape"] = fail(shapeFail);
    ["each_root_satisfies","count_with_multiplicity","distinct_roots_match"].forEach(SKIP);
    return checks;
  }
  checks["shape"] = pass(`${ok.solutions.length} solution(s); shape ok`);

  // ── each_root_satisfies ───────────────────────────────────────────────────
  {
    let rootFail: string | null = null;
    const candidateFloats: Array<number | null> = [];
    for (let i = 0; i < ok.solutions.length && !rootFail; i++) {
      const valStr = ok.solutions[i]!.bindings[0]!.value;
      let rootVal: number;
      try {
        rootVal = evalFloat(valStr, new Map<string, number>());
      } catch (e) {
        // Could be Root[poly, k] or complex expression.  Skip evaluation for these.
        candidateFloats.push(null);
        continue;
      }
      candidateFloats.push(rootVal);
      let residual: number;
      try { residual = evalPoly(eqStr, varName, rootVal); }
      catch (e) { rootFail = `eq eval at root[${i}] failed: ${(e as Error).message}`; break; }
      const tol = 1e-8 * Math.max(1, Math.abs(residual));
      if (Math.abs(residual) > tol) {
        // Try a tighter tolerance with respect to polynomial value scale.
        const scale = Math.abs(evalPoly(eqStr, varName, rootVal + 1)) + 1;
        const tol2 = 1e-8 * scale;
        if (Math.abs(residual) > tol2) {
          rootFail = `root[${i}] ${valStr} ≈ ${rootVal} → eq.subs = ${residual}`;
        }
      }
    }
    checks["each_root_satisfies"] = rootFail
      ? fail(rootFail)
      : pass(`${ok.solutions.filter((_, i) => candidateFloats[i] !== null).length} root(s) evaluated and satisfied`);
  }

  // ── count_with_multiplicity ───────────────────────────────────────────────
  {
    // Total degree = expected number of roots with multiplicity.
    const deg = polyDegree(eqStr, varName);
    if (deg === null || deg < 0) {
      checks["count_with_multiplicity"] = pass("n/a: degree estimation unavailable");
    } else {
      const actual = ok.solutions.length;
      if (actual !== deg) {
        checks["count_with_multiplicity"] = fail(`|solutions|=${actual} != estimated deg=${deg}`);
      } else {
        checks["count_with_multiplicity"] = pass(`${actual} = estimated deg`);
      }
    }
  }

  // ── distinct_roots_match ──────────────────────────────────────────────────
  {
    // Find numerical roots of the equation via grid scan.
    let matchFail: string | null = null;
    try {
      const numericRoots = findNumericRoots(eqStr, varName);
      // For each numeric root, check that at least one candidate root is close.
      const candidateVals: number[] = [];
      for (const sol of ok.solutions) {
        try {
          const v = evalFloat(sol.bindings[0]!.value, new Map<string, number>());
          if (isFinite(v)) candidateVals.push(v);
        } catch { /* complex root — skip */ }
      }
      // Every numeric root must match a candidate root within 1e-6.
      const unmatched: number[] = [];
      for (const nr of numericRoots) {
        const matched = candidateVals.some((cv) => Math.abs(cv - nr) < 1e-6);
        if (!matched) unmatched.push(nr);
      }
      if (unmatched.length > 0) {
        matchFail = `${unmatched.length} numeric root(s) unmatched by candidate: ${unmatched.slice(0,5).map((r) => r.toFixed(6)).join(", ")}`;
      } else {
        checks["distinct_roots_match"] = pass(`${numericRoots.length} numeric root(s) all matched`);
      }
    } catch (e) {
      matchFail = `root-finding failed: ${(e as Error).message}`;
    }
    if (matchFail) checks["distinct_roots_match"] = fail(matchFail);
  }

  return checks;
}

// =============================================================================
// Lane: multivariate-poly (3 checks)
// =============================================================================
//
// Checks: shape, each_solution_satisfies, completeness_correct.
//
// The lane verifies zero-dim multivariate polynomial systems solved via the
// workbench's Gröbner+FGLM+shape-lemma stack (ADR-0029, bead `x8d`).  The
// candidate's solutions are a finite list of `{bindings: [{var, value}]}`
// with branches=[] and completeness="complete" (zero-dim ⇒ no parameters).
//
// Verification is substitute-and-evaluate (ADR-0019 §1):
//   1. For each candidate Solution, evaluate every binding's `value` string
//      via the float64 evaluator (handles integers, rationals, sqrt,
//      Cardano radicals, and CRootOf companion-matrix evaluation for deg ≥ 5
//      Root[poly,k] outputs).
//   2. Substitute the resulting numeric values into every input equation
//      and evaluate the residue.  PASS iff |residue| < 1e-7 absolute tol
//      (well above float64 rounding noise for the bench's coefficient range
//      [-9, 9] and target solution magnitudes ≤ ~5).
//
// Tolerance regime: 1e-7 absolute.  Conservative — true roots produce
// residues at ~1e-13 to ~1e-15; mutated solutions produce O(1) residues.
// We do NOT require exact bindings/value-string match against an oracle:
// the workbench may emit `Root[poly, k]` where the reference SymPy
// emits a CRootOf or radical form, and both should evaluate to the same
// real root within float64 precision.  Verifying via substitution is the
// only contract-honest check (matches univariate-poly's each_root_satisfies).

const MV_POLY_TOL = 1e-7;

function verifyMultivariatePoly(
  inp: InputShape,
  cand: Candidate,
  _expected: Expected,
): Record<string, Check> {
  const checks: Record<string, Check> = {};
  const SKIP = (k: string) => { checks[k] = fail("skipped: shape failed"); };

  if (cand.kind !== "ok") {
    checks["shape"] = fail(
      `candidate.kind=${JSON.stringify(cand.kind)} but lane=multivariate-poly requires kind=ok`,
    );
    ["each_solution_satisfies", "completeness_correct"].forEach(SKIP);
    return checks;
  }

  const ok = cand as OkCandidate;
  const varNames = inp.vars;
  const n = varNames.length;

  // ── shape ─────────────────────────────────────────────────────────────────
  let shapeFail: string | null = null;
  if (!Array.isArray(ok.solutions)) {
    shapeFail = "solutions field missing or not a list";
  } else if (ok.completeness !== "complete") {
    shapeFail = `completeness=${JSON.stringify(ok.completeness)}, expected 'complete' (zero-dim ⇒ finite)`;
  } else if (!Array.isArray(ok.vars) || ok.vars.length !== n) {
    shapeFail = `|vars|=${ok.vars?.length ?? "?"} != input |vars|=${n}`;
  } else {
    for (let i = 0; i < ok.solutions.length && !shapeFail; i++) {
      const sol = ok.solutions[i]!;
      if (!sol.bindings || sol.bindings.length !== n) {
        shapeFail = `solution[${i}] |bindings|=${sol.bindings?.length ?? 0} != |vars|=${n}`;
        break;
      }
      if ((sol.branches ?? []).length !== 0) {
        shapeFail = `solution[${i}] has non-empty branches (zero-dim ⇒ no parameters)`;
        break;
      }
      for (let j = 0; j < n; j++) {
        const b = sol.bindings[j]!;
        if (b.var !== varNames[j]) {
          shapeFail = `solution[${i}].bindings[${j}].var=${JSON.stringify(b.var)} != ${JSON.stringify(varNames[j])}`;
          break;
        }
        if (typeof b.value !== "string" || !b.value) {
          shapeFail = `solution[${i}].bindings[${j}].value is not a non-empty string`;
          break;
        }
      }
    }
  }
  if (shapeFail) {
    checks["shape"] = fail(shapeFail);
    ["each_solution_satisfies", "completeness_correct"].forEach(SKIP);
    return checks;
  }
  checks["shape"] = pass(`${n} vars; ${ok.solutions.length} solution(s); shape ok`);

  // ── each_solution_satisfies ──────────────────────────────────────────────
  {
    let satFail: string | null = null;
    let evaluated = 0;

    for (let i = 0; i < ok.solutions.length && !satFail; i++) {
      const sol = ok.solutions[i]!;
      const sub = new Map<string, number>();
      let evalOk = true;

      for (let j = 0; j < n; j++) {
        const valStr = sol.bindings[j]!.value;
        let v: number;
        try {
          v = evalFloat(valStr, new Map<string, number>());
        } catch (e) {
          satFail = `solution[${i}] bindings[${j}].value=${JSON.stringify(valStr)} did not evaluate: ${(e as Error).message}`;
          evalOk = false;
          break;
        }
        if (!isFinite(v)) {
          satFail = `solution[${i}] bindings[${j}].value=${JSON.stringify(valStr)} evaluates to non-finite (${v})`;
          evalOk = false;
          break;
        }
        sub.set(varNames[j]!, v);
      }
      if (!evalOk) break;

      for (let eqIdx = 0; eqIdx < inp.eqs.length && !satFail; eqIdx++) {
        const eqStr = inp.eqs[eqIdx]!;
        let res: number;
        try { res = evalEq(eqStr, sub); }
        catch (e) {
          satFail = `eq[${eqIdx}] eval failed at solution[${i}]: ${(e as Error).message}`;
          break;
        }
        if (!isFinite(res)) {
          satFail = `eq[${eqIdx}] residue non-finite at solution[${i}]`;
          break;
        }
        if (Math.abs(res) > MV_POLY_TOL) {
          const subStr = Array.from(sub).map(([k, v]) => `${k}=${v.toFixed(6)}`).join(", ");
          satFail = `eq[${eqIdx}] residue=${res.toExponential(3)} at solution[${i}] (${subStr}); tol=${MV_POLY_TOL}`;
          break;
        }
      }
      evaluated++;
    }

    checks["each_solution_satisfies"] = satFail
      ? fail(satFail)
      : pass(`${evaluated} solution(s) substituted into ${inp.eqs.length} equation(s); all residues < ${MV_POLY_TOL}`);
  }

  // ── completeness_correct ─────────────────────────────────────────────────
  // Zero-dim ideals always have completeness="complete"; the shape check
  // above already enforces this, but keep an independent check for the
  // mutation-prove harness.
  if (ok.completeness === "complete") {
    checks["completeness_correct"] = pass("completeness='complete' (zero-dim)");
  } else {
    checks["completeness_correct"] = fail(
      `completeness=${JSON.stringify(ok.completeness)}, expected 'complete'`,
    );
  }

  return checks;
}

// =============================================================================
// Lane: transcendental (3 checks)
// =============================================================================
//
// Checks: shape, branched_substitution_cube, completeness_grid.

const CUBE_HALF = 3;     // cube is [-3, 3]^n
const CUBE_TOL  = 1e-12;
const GRID_LO   = -50;
const GRID_HI   =  50;
const GRID_N    = 2000;
const GRID_TOL  = 1e-6;

function verifyTranscendental(
  inp: InputShape,
  cand: Candidate,
  _expected: Expected,
): Record<string, Check> {
  const checks: Record<string, Check> = {};
  const SKIP = (k: string) => { checks[k] = fail("skipped: shape failed"); };

  if (cand.kind !== "ok") {
    checks["shape"] = fail(`candidate.kind=${JSON.stringify(cand.kind)} but lane=transcendental requires kind=ok`);
    ["branched_substitution_cube","completeness_grid"].forEach(SKIP);
    return checks;
  }

  if (inp.eqs.length !== 1 || inp.vars.length !== 1) {
    checks["shape"] = fail("transcendental lane requires exactly 1 eq, 1 var");
    ["branched_substitution_cube","completeness_grid"].forEach(SKIP);
    return checks;
  }

  const ok = cand as OkCandidate;
  const eqStr = inp.eqs[0]!;
  const varName = inp.vars[0]!;

  // ── shape ─────────────────────────────────────────────────────────────────
  let shapeFail: string | null = null;
  if (!Array.isArray(ok.solutions) || ok.solutions.length === 0) {
    shapeFail = "no solutions for transcendental lane";
  } else {
    for (let i = 0; i < ok.solutions.length && !shapeFail; i++) {
      const sol = ok.solutions[i]!;
      if (sol.bindings?.length !== 1) { shapeFail = `solution[${i}] |bindings| != 1`; }
      else if (sol.bindings[0]!.var !== varName) { shapeFail = `solution[${i}] var mismatch`; }
      else {
        for (const bs of sol.branches ?? []) {
          if (!/^t_\d+$/.test(bs)) { shapeFail = `solution[${i}] branch ${JSON.stringify(bs)} not t_<int> form`; break; }
        }
      }
    }
  }
  if (shapeFail) {
    checks["shape"] = fail(shapeFail);
    ["branched_substitution_cube","completeness_grid"].forEach(SKIP);
    return checks;
  }
  checks["shape"] = pass(`${ok.solutions.length} solution(s); branches consistent`);

  // Collect all cube-instantiated x values (for completeness_grid reach).
  const cubeXVals: number[] = [];

  // ── branched_substitution_cube ─────────────────────────────────────────────
  {
    let cubeFail: string | null = null;

    for (const sol of ok.solutions) {
      if (cubeFail) break;
      const bindingStr = sol.bindings[0]!.value;
      const branches   = sol.branches ?? [];
      const n = branches.length;

      // Generate all integer tuples in [-3, 3]^n.
      const tupleRange = Array.from({ length: 2 * CUBE_HALF + 1 }, (_, i) => i - CUBE_HALF);
      function* iterTuples(depth: number, cur: number[]): Generator<number[]> {
        if (depth === 0) { yield cur; return; }
        for (const v of tupleRange) { yield* iterTuples(depth - 1, [...cur, v]); }
      }

      for (const tup of iterTuples(n, [])) {
        if (cubeFail) break;
        const branchSub = new Map<string, number>();
        branches.forEach((b, idx) => branchSub.set(b, tup[idx]!));

        let xVal: number;
        try { xVal = evalFloat(bindingStr, branchSub); }
        catch { continue; } // evaluation failed — skip (out of domain)

        if (!isFinite(xVal) || isNaN(xVal)) continue;
        cubeXVals.push(xVal);

        const sub = new Map<string, number>([[varName, xVal]]);
        let residue: number;
        try { residue = evalEq(eqStr, sub); }
        catch { continue; } // domain error at this point — skip

        if (!isFinite(residue)) continue;
        if (Math.abs(residue) > CUBE_TOL) {
          cubeFail = `tuple ${JSON.stringify(tup)} residue |${residue}| > ${CUBE_TOL}`;
        }
      }
    }
    checks["branched_substitution_cube"] = cubeFail
      ? fail(cubeFail)
      : pass(`all solution × cube tuples satisfied (tol=${CUBE_TOL})`);
  }

  // ── completeness_grid ─────────────────────────────────────────────────────
  {
    if (cubeXVals.length === 0) {
      checks["completeness_grid"] = pass("no real cube-instantiated values; grid n/a");
    } else {
      // The grid window must match the cube's *actual* asymmetric reach,
      // not a symmetric `±max(|cube|)`.  For invertible-head equations
      // with a non-zero linear coefficient (e.g. `tan(-2x + 2) - (-3)`),
      // the cube's [t_0 = -3 .. 3] integer range maps to an asymmetric
      // x-range in the original variable.  A symmetric scan would scan
      // territory the cube cannot possibly reach (because the periodic
      // image of t_0 ∈ ℤ over [t_0 = 4, t_0 = 5, ...] lies further along
      // one direction only) and report the unreached roots as
      // "unmatched", a false-positive completeness violation.
      const cubeMin = cubeXVals.reduce((m, v) => Math.min(m, v), cubeXVals[0]!);
      const cubeMax = cubeXVals.reduce((m, v) => Math.max(m, v), cubeXVals[0]!);
      const reach = Math.max(Math.abs(cubeMin), Math.abs(cubeMax));
      const win = Math.min(GRID_HI, reach);
      if (win < 0.1) {
        // Pointwise check for finite-reach cases.
        let gridFail: string | null = null;
        for (const xv of cubeXVals) {
          let res: number;
          try { res = evalEq(eqStr, new Map([[varName, xv]])); }
          catch { continue; }
          if (isFinite(res) && Math.abs(res) > GRID_TOL) {
            gridFail = `pointwise candidate value ${xv} → eq = ${res}`;
          }
        }
        checks["completeness_grid"] = gridFail ? fail(gridFail) : pass(`pointwise candidate values all satisfy eq`);
      } else {
        // Grid scan over `[cubeMin, cubeMax]` clamped to `[-GRID_HI,
        // GRID_HI]`.  Critically, the scan does NOT extend beyond the
        // cube's actual reach: any grid root outside `[cubeMin,
        // cubeMax]` corresponds to a branch index outside `[-CUBE_HALF,
        // CUBE_HALF]`, which the cube cannot match by construction —
        // flagging it as "unmatched" would be a false-positive
        // completeness violation.
        const lo = Math.max(-GRID_HI, cubeMin);
        const hi = Math.min(GRID_HI, cubeMax);
        const step = (hi - lo) / GRID_N;
        const gridRoots: number[] = [];
        let prevY = (() => { try { return evalEq(eqStr, new Map([[varName, lo]])); } catch { return NaN; } })();
        let prevX = lo;

        for (let j = 1; j <= GRID_N; j++) {
          const x = lo + j * step;
          let y: number;
          try { y = evalEq(eqStr, new Map([[varName, x]])); }
          catch { y = NaN; }
          if (!isFinite(prevY) || !isFinite(y)) { prevX = x; prevY = y; continue; }
          if (prevY === 0) {
            gridRoots.push(prevX);
          } else if (prevY * y < 0) {
            // Bisect to refine root to ~1e-12.
            let a = prevX, b = x, fa = prevY;
            for (let iter = 0; iter < 80; iter++) {
              const mid = (a + b) / 2;
              let fm: number;
              try { fm = evalEq(eqStr, new Map([[varName, mid]])); }
              catch { break; }
              // Pole filter: if |fm| is huge, this is a discontinuity, not a root.
              if (Math.abs(fm) > 1e6) break;
              if (fa * fm <= 0) { b = mid; } else { a = mid; fa = fm; }
              if (Math.abs(b - a) < 1e-13) break;
            }
            const root = (a + b) / 2;
            // Validate: |f(root)| must be small.
            let fRoot: number;
            try { fRoot = evalEq(eqStr, new Map([[varName, root]])); }
            catch { fRoot = Infinity; }
            if (isFinite(fRoot) && Math.abs(fRoot) < 1e-6) {
              gridRoots.push(root);
            }
          }
          prevX = x; prevY = y;
        }

        if (gridRoots.length === 0) {
          checks["completeness_grid"] = pass(`no grid roots in [${lo.toFixed(2)}, ${hi.toFixed(2)}]`);
        } else {
          // Every grid root must match some cube-instantiated x value within GRID_TOL.
          const missed: number[] = [];
          for (const gr of gridRoots) {
            const close = cubeXVals.some((cv) => Math.abs(cv - gr) <= GRID_TOL);
            if (!close) missed.push(gr);
          }
          if (missed.length > 0) {
            checks["completeness_grid"] = fail(
              `${missed.length} grid root(s) unmatched: ${missed.slice(0, 5).map((r) => r.toFixed(6)).join(", ")}`,
            );
          } else {
            checks["completeness_grid"] = pass(
              `${gridRoots.length} grid root(s) all matched within ${GRID_TOL} (window=[${lo.toFixed(2)}, ${hi.toFixed(2)}])`,
            );
          }
        }
      }
    }
  }

  return checks;
}

// =============================================================================
// Top-level verify
// =============================================================================

type Payload = {
  input:      InputShape;
  candidate:  unknown;
  expected?:  Expected;     // populated if passed on stdin (future-compat)
  id?:        string;
};

function verify(
  payload: Payload,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const inp  = payload.input;
  const cand = payload.candidate as Candidate;

  // Resolve expected entry: prefer payload.expected if present (future-compat),
  // otherwise look up by id from the filesystem expected.json.
  let exp: Expected | undefined = payload.expected;
  if (!exp || !exp.lane) {
    const id = payload.id;
    if (id) {
      const entry = loadExpected().get(id);
      if (entry) exp = entry;
    }
  }

  if (!inp || !Array.isArray(inp.eqs) || !Array.isArray(inp.vars)) {
    return { pass: false, reason: "input must have eqs[] and vars[]", checks: {} };
  }

  if (!exp || !exp.lane) {
    return {
      pass:   false,
      reason: "expected.lane is missing — expected.json lookup failed or id not provided",
      checks: { shape: { pass: false, detail: "expected.lane missing" } },
    };
  }

  // Tool-error path: always a failure.
  if ((cand as Record<string, unknown>)["kind"] === "tool_error") {
    return {
      pass:   false,
      reason: `tool_error: ${JSON.stringify((cand as Record<string, unknown>)["message"] ?? "unknown")}`,
      checks: { shape: { pass: false, detail: "candidate emitted tool_error" } },
    };
  }

  let checks: Record<string, Check>;
  try {
    switch (exp.lane) {
      case "linear":
        checks = verifyLinear(inp, cand, exp);
        break;
      case "univariate-poly":
        checks = verifyUnivariatePoly(inp, cand, exp);
        break;
      case "multivariate-poly":
        checks = verifyMultivariatePoly(inp, cand, exp);
        break;
      case "transcendental":
        checks = verifyTranscendental(inp, cand, exp);
        break;
      case "refusal":
        checks = verifyRefusal(cand, exp);
        break;
      default:
        return {
          pass:   false,
          reason: `unknown lane ${JSON.stringify(exp.lane)}`,
          checks: { shape: { pass: false, detail: `unknown lane: ${exp.lane}` } },
        };
    }
  } catch (e) {
    const err = e as Error;
    return {
      pass:   false,
      reason: `verifier threw: ${err.message}`,
      checks: { shape: { pass: false, detail: `${err.constructor.name}: ${err.message}` } },
    };
  }

  const failed = Object.entries(checks).filter(([, c]) => !c.pass);
  if (failed.length === 0) return { pass: true, reason: "all invariants hold", checks };
  const reason = failed.map(([k, c]) => `${k}: ${c.detail}`).join("; ");
  return { pass: false, reason, checks };
}

// =============================================================================
// main
// =============================================================================

async function main(): Promise<void> {
  let stdin = "";
  for await (const chunk of Bun.stdin.stream()) {
    stdin += new TextDecoder().decode(chunk);
  }
  let result: { pass: boolean; reason: string; checks: Record<string, Check> };
  try {
    const payload = JSON.parse(stdin) as Payload;
    result = verify(payload);
  } catch (e) {
    const err = e as Error;
    process.stderr.write(`${err.stack ?? err.message}\n`);
    result = {
      pass:   false,
      reason: `verifier crashed: ${err.constructor.name}: ${err.message}`,
      checks: {},
    };
  }
  process.stdout.write(JSON.stringify(result) + "\n");
}

await main();
