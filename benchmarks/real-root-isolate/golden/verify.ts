// =============================================================================
// real-root-isolate verifier — 4-check invariant verifier (TypeScript port).
// =============================================================================
//
// stdin:
//   { input:     { f: string, var: string },
//     candidate: { kind:"ok", intervals:[{lo,hi},...], ... }
//               | { kind:"tagged", tag, payload }
//               | { kind:"tool_error", ... },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// This is the TypeScript port of verify.py per ADR-0028 §4.
// Tolerances (none — all checks are exact rational/Sturm) are preserved
// byte-for-byte with verify.py.  Do NOT tighten or loosen during migration.
//
// The verifier implements BigInt rational Sturm sequences inline since:
//   (a) The checks are purely algebraic — no floating-point at any step.
//   (b) SymPy's Sturm sequence is reproduced here via standard polynomial GCD.
//   (c) The workbench's packages/real-roots BigInt polynomial kernel is the
//       reference implementation; this verifier is an independent re-derivation
//       that agrees with it on all 37 golden cases.
//
// Two top-level lanes (matching verify.py):
//   expected.kind == "ok"     → 4 happy-path checks
//   expected.kind == "tagged" → 2 refusal checks
//
// ─── Checks ──────────────────────────────────────────────────────────────────
//   shape                           — structural
//   each_interval_contains_one_root — exact (Sturm count + f(lo)/f(hi) = 0)
//   intervals_disjoint_and_ordered  — exact (rational comparison)
//   count_matches_total_real_roots  — exact (Sturm ground truth)
//   refusal_class_matches           — exact (tag string + payload.detail)
//
// No floating-point appears anywhere.  Default determinism tier
// (ADR-0015): bit-identical cross-platform forever.

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

// ─── Check type ───────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

// ─── Expected-output index ───────────────────────────────────────────────────

type ExpectedEntry = {
  kind?: "tagged" | "tool_error";
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
// BigInt rational arithmetic
// =============================================================================
//
// A rational number is { n: bigint, d: bigint } with d > 0, in lowest terms.
// All operations below maintain that invariant.

type Rat = { n: bigint; d: bigint };

function gcd(a: bigint, b: bigint): bigint {
  a = a < 0n ? -a : a;
  b = b < 0n ? -b : b;
  while (b !== 0n) {
    const t = b;
    b = a % b;
    a = t;
  }
  return a;
}

function rat(n: bigint, d: bigint = 1n): Rat {
  if (d === 0n) throw new Error("rat: zero denominator");
  if (d < 0n) { n = -n; d = -d; }
  const g = gcd(n < 0n ? -n : n, d);
  return { n: n / g, d: d / g };
}

function ratFromString(s: string): Rat {
  // Parse rational strings like "1", "-2", "3/4", "-1/2".
  const slash = s.indexOf("/");
  if (slash === -1) return rat(BigInt(s));
  return rat(BigInt(s.slice(0, slash)), BigInt(s.slice(slash + 1)));
}

function ratCmp(a: Rat, b: Rat): number {
  // Returns -1, 0, or 1.
  const lhs = a.n * b.d;
  const rhs = b.n * a.d;
  if (lhs < rhs) return -1;
  if (lhs > rhs) return 1;
  return 0;
}

function ratAdd(a: Rat, b: Rat): Rat {
  return rat(a.n * b.d + b.n * a.d, a.d * b.d);
}

function ratSub(a: Rat, b: Rat): Rat {
  return rat(a.n * b.d - b.n * a.d, a.d * b.d);
}

function ratMul(a: Rat, b: Rat): Rat {
  return rat(a.n * b.n, a.d * b.d);
}

function ratNeg(a: Rat): Rat {
  return { n: -a.n, d: a.d };
}

function ratSign(a: Rat): number {
  if (a.n > 0n) return 1;
  if (a.n < 0n) return -1;
  return 0;
}

// =============================================================================
// BigInt polynomial arithmetic over ℚ
// =============================================================================
//
// Polynomial: Rat[] in high-to-low order (SymPy / workbench convention):
//   [c_n, c_{n-1}, ..., c_0] represents c_n·x^n + ... + c_0.
// The empty array is the zero polynomial.
// All functions maintain the invariant that the leading coefficient is non-zero
// (no leading zeros in canonical form).

const ZERO_RAT: Rat = { n: 0n, d: 1n };

function polyDegree(f: Rat[]): number {
  return f.length - 1;
}

function polyIsZero(f: Rat[]): boolean {
  return f.length === 0;
}

function polyStrip(f: Rat[]): Rat[] {
  let i = 0;
  while (i < f.length && f[i]!.n === 0n) i++;
  return f.slice(i);
}

/** Evaluate polynomial f at rational point x. Horner's method. */
function polyEval(f: Rat[], x: Rat): Rat {
  if (f.length === 0) return ZERO_RAT;
  let acc = f[0]!;
  for (let i = 1; i < f.length; i++) {
    acc = ratAdd(ratMul(acc, x), f[i]!);
  }
  return acc;
}

/** Polynomial subtraction. */
function polySub(f: Rat[], g: Rat[]): Rat[] {
  // Pad to same length.
  const n = Math.max(f.length, g.length);
  const result: Rat[] = [];
  for (let i = 0; i < n; i++) {
    const fi = f[f.length - n + i] ?? ZERO_RAT;
    const gi = g[g.length - n + i] ?? ZERO_RAT;
    result.push(ratSub(fi, gi));
  }
  return polyStrip(result);
}

/** Polynomial pseudo-remainder (over ℚ, this is just the standard remainder
 *  since we can divide rationals exactly). */
function polyRem(f: Rat[], g: Rat[]): Rat[] {
  if (polyIsZero(g)) throw new Error("polyRem: divisor is zero");
  let r = [...f];
  const dg = polyDegree(g);
  const lc = g[0]!;

  while (!polyIsZero(r) && polyDegree(r) >= dg) {
    // scalar = leading(r) / leading(g); subtract scalar·x^(deg(r)-deg(g))·g from r.
    // In the high-to-low representation: scalar·x^shift·g[i] aligns to r[i]
    // (the leading-coefficient cancellation zeros r[0]).
    const scalar = rat(r[0]!.n * lc.d, r[0]!.d * lc.n);
    for (let i = 0; i < g.length; i++) {
      r[i] = ratSub(r[i]!, ratMul(scalar, g[i]!));
    }
    r = polyStrip(r);
  }
  return r;
}

/** Polynomial derivative. */
function polyDeriv(f: Rat[]): Rat[] {
  if (f.length <= 1) return [];
  const out: Rat[] = [];
  const deg = f.length - 1;
  for (let i = 0; i < deg; i++) {
    out.push(ratMul(f[i]!, rat(BigInt(deg - i))));
  }
  return polyStrip(out);
}

/** Scale all coefficients by a rational scalar. */
function polyScale(f: Rat[], s: Rat): Rat[] {
  if (s.n === 0n) return [];
  return polyStrip(f.map((c) => ratMul(c, s)));
}

// =============================================================================
// Sturm sequence and root counting
// =============================================================================
//
// The classical Sturm sequence for a squarefree polynomial f:
//   p_0 = f
//   p_1 = f'
//   p_{i+1} = -rem(p_{i-1}, p_i)   (using exact polynomial remainder)
//
// The number of distinct real roots of f in the interval (a, b) is:
//   σ(f, a) − σ(f, b)
// where σ(f, x) is the number of sign changes in the sequence
//   p_0(x), p_1(x), ..., p_k(x)
// (ignoring zeros).
//
// For an open interval (lo, hi) with lo < hi:
//   count_open = σ(f, lo) − σ(f, hi)
//              − [f(lo)=0 ? 1 : 0]   ← exclude lo if it's a root
//              − [f(hi)=0 ? 1 : 0]   ← exclude hi if it's a root
// This matches verify.py's _check_each_interval logic exactly.
//
// For the full real line:
//   count_all = σ(f, −∞) − σ(f, +∞)
// where ±∞ are evaluated by inspecting the leading-coefficient signs at
// even/odd degree.

function sturmSequence(f: Rat[]): Rat[][] {
  if (polyIsZero(f)) return [];
  const seq: Rat[][] = [f, polyDeriv(f)];
  if (polyIsZero(seq[1]!)) return [f];   // constant → no roots
  while (true) {
    const prev2 = seq[seq.length - 2]!;
    const prev1 = seq[seq.length - 1]!;
    const rem = polyRem(prev2, prev1);
    if (polyIsZero(rem)) break;
    seq.push(polyScale(rem, rat(-1n)));
  }
  return seq;
}

function signChanges(values: number[]): number {
  let changes = 0;
  let lastSign = 0;
  for (const v of values) {
    if (v === 0) continue;
    if (lastSign !== 0 && v !== lastSign) changes++;
    lastSign = v;
  }
  return changes;
}

function sturmSignsAtPoint(seq: Rat[][], x: Rat): number[] {
  return seq.map((p) => ratSign(polyEval(p, x)));
}

function sturmSignsAtPosInf(seq: Rat[][]): number[] {
  // At +∞, sign of polynomial = sign of leading coefficient.
  return seq.map((p) => (polyIsZero(p) ? 0 : ratSign(p[0]!)));
}

function sturmSignsAtNegInf(seq: Rat[][]): number[] {
  // At −∞, sign of polynomial = leading coefficient × (−1)^degree.
  return seq.map((p) => {
    if (polyIsZero(p)) return 0;
    const deg = polyDegree(p);
    const s = ratSign(p[0]!);
    return deg % 2 === 0 ? s : -s;
  });
}

/** Count real roots of f in the CLOSED interval [lo, hi].
 *
 *  The classical Sturm theorem: V(lo) - V(hi) counts roots in the open
 *  interval (lo, hi) when f(lo) ≠ 0 and f(hi) ≠ 0.  When endpoints are
 *  roots of f, the formula needs correction:
 *
 *    - When f(lo) = 0: V(lo) excludes the root at lo (counts roots in
 *      (lo, hi) only), so the closed-interval count is V(lo) - V(hi) + 1.
 *    - When f(lo) ≠ 0: V(lo) - V(hi) = roots in (lo, hi); closed count is
 *      the same (no root at lo).
 *
 *  Equivalently: count_closed = V(lo) - V(hi) + [f(lo) = 0].
 *
 *  This matches SymPy's Poly.count_roots(lo, hi) semantics exactly (SymPy
 *  counts in the closed [lo, hi]).  Verified against verify.py's
 *  p.count_roots(lo, hi) calls.
 */
function countRootsClosedInterval(
  seq: Rat[][], f: Rat[], lo: Rat, hi: Rat,
): number {
  const signsLo = sturmSignsAtPoint(seq, lo);
  const signsHi = sturmSignsAtPoint(seq, hi);
  const sturm = signChanges(signsLo) - signChanges(signsHi);
  // Add 1 if lo is a root (lo-correction brings V(lo-ε) = V(lo)+1 into the count).
  const fLoIsZero = ratSign(polyEval(f, lo)) === 0 ? 1 : 0;
  return sturm + fLoIsZero;
}

/** Count all real roots of f over (−∞, +∞). */
function countAllRealRoots(seq: Rat[][]): number {
  const signsNeg = sturmSignsAtNegInf(seq);
  const signsPos = sturmSignsAtPosInf(seq);
  return signChanges(signsNeg) - signChanges(signsPos);
}

// =============================================================================
// Polynomial parser
// =============================================================================
//
// Parses expressions in the bench's input vocabulary — integers,
// rational literals (e.g. "1/2"), the variable symbol, parens, +, -, *, /, **,
// and function calls like sin(x).  Produces a Rat[] coefficient array
// in high-to-low order.  Returns null for non-polynomial inputs
// (triggering the shape check's "skip arithmetic" path, which is correct:
// refusal cases have kind="tagged" and the arithmetic checks are skipped).
//
// The parser produces a univariate polynomial over ℚ.  Inputs that
// involve a second symbol (e.g. "x*y - 1") or non-polynomial functions
// (e.g. "sin(x)") return null — the happy-path checks will be skipped
// since those are refusal cases.

type ParseResult = { kind: "poly"; coeffs: Rat[] } | { kind: "non-poly" };

// Polynomial represented as Map<exponent, coefficient> during building.
type PolyMap = Map<number, Rat>;

function polyMapFromRat(r: Rat): PolyMap {
  const m: PolyMap = new Map();
  if (r.n !== 0n) m.set(0, r);
  return m;
}

function polyMapFromVar(): PolyMap {
  const m: PolyMap = new Map();
  m.set(1, rat(1n));
  return m;
}

function addPolyMaps(a: PolyMap, b: PolyMap): PolyMap {
  const out = new Map(a);
  for (const [exp, c] of b) {
    const existing = out.get(exp) ?? ZERO_RAT;
    const s = ratAdd(existing, c);
    if (s.n === 0n) out.delete(exp);
    else out.set(exp, s);
  }
  return out;
}

function subPolyMaps(a: PolyMap, b: PolyMap): PolyMap {
  const out = new Map(a);
  for (const [exp, c] of b) {
    const existing = out.get(exp) ?? ZERO_RAT;
    const s = ratSub(existing, c);
    if (s.n === 0n) out.delete(exp);
    else out.set(exp, s);
  }
  return out;
}

function mulPolyMaps(a: PolyMap, b: PolyMap): PolyMap {
  const out: PolyMap = new Map();
  for (const [ea, ca] of a) {
    for (const [eb, cb] of b) {
      const exp = ea + eb;
      const prod = ratMul(ca, cb);
      const existing = out.get(exp) ?? ZERO_RAT;
      const s = ratAdd(existing, prod);
      if (s.n === 0n) out.delete(exp);
      else out.set(exp, s);
    }
  }
  return out;
}

function polyMapToCoeffs(m: PolyMap): Rat[] {
  if (m.size === 0) return [];
  const maxDeg = Math.max(...m.keys());
  const coeffs: Rat[] = new Array(maxDeg + 1).fill(ZERO_RAT);
  for (const [exp, c] of m) {
    coeffs[maxDeg - exp] = c;
  }
  return polyStrip(coeffs);
}

type Tok2 =
  | { t: "num"; v: bigint; d: bigint }
  | { t: "ident"; v: string }
  | { t: "op"; v: string }
  | { t: "lp" } | { t: "rp" } | { t: "comma" }
  | { t: "eof" };

function tokenize2(s: string): Tok2[] {
  const out: Tok2[] = [];
  let i = 0;
  while (i < s.length) {
    const ch = s[i]!;
    if (/\s/.test(ch)) { i++; continue; }
    if (/[0-9]/.test(ch)) {
      let j = i;
      while (j < s.length && /[0-9]/.test(s[j]!)) j++;
      // Always tokenize as an integer; "/" is always a division operator.
      // This avoids the `x**3/2 = x^(3/2)` mis-parse from greedy
      // rational-literal detection.  The parser handles `1/2` as division.
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
    if (ch === ",") { out.push({ t: "comma" }); i++; continue; }
    if ("+-*/^".includes(ch)) { out.push({ t: "op", v: ch }); i++; continue; }
    throw new Error(`tokenize2: unexpected char ${JSON.stringify(ch)} at ${i}`);
  }
  out.push({ t: "eof" });
  return out;
}

type ParseState2 = { toks: Tok2[]; i: number; varName: string; nonPoly: boolean };

function peek2(st: ParseState2): Tok2 { return st.toks[st.i]!; }
function eat2(st: ParseState2): Tok2 { return st.toks[st.i++]!; }

// Each parser function returns a PolyMap or null on non-polynomial.

function parseAddSub2(st: ParseState2): PolyMap | null {
  let lhs = parseMulDiv2(st);
  if (lhs === null) return null;
  while (
    peek2(st).t === "op" &&
    ((peek2(st) as { t: "op"; v: string }).v === "+" ||
     (peek2(st) as { t: "op"; v: string }).v === "-")
  ) {
    const op = (eat2(st) as { t: "op"; v: string }).v;
    const rhs = parseMulDiv2(st);
    if (rhs === null) return null;
    lhs = op === "+" ? addPolyMaps(lhs, rhs) : subPolyMaps(lhs, rhs);
  }
  return lhs;
}

function parseMulDiv2(st: ParseState2): PolyMap | null {
  let lhs = parseUnary2(st);
  if (lhs === null) return null;
  while (
    peek2(st).t === "op" &&
    ((peek2(st) as { t: "op"; v: string }).v === "*" ||
     (peek2(st) as { t: "op"; v: string }).v === "/")
  ) {
    const op = (eat2(st) as { t: "op"; v: string }).v;
    const rhs = parseUnary2(st);
    if (rhs === null) return null;
    if (op === "*") {
      lhs = mulPolyMaps(lhs, rhs);
    } else {
      // Division: only legal if rhs is a constant (non-zero rational).
      if (rhs.size !== 1 || !rhs.has(0)) return null;  // non-constant divisor → non-poly
      const denom = rhs.get(0)!;
      // Scale lhs by 1/denom.
      const inv = rat(denom.d, denom.n);
      const scaled: PolyMap = new Map();
      for (const [exp, c] of lhs) scaled.set(exp, ratMul(c, inv));
      lhs = scaled;
    }
  }
  return lhs;
}

function parseUnary2(st: ParseState2): PolyMap | null {
  if (peek2(st).t === "op" && (peek2(st) as { t: "op"; v: string }).v === "-") {
    eat2(st);
    const inner = parseUnary2(st);
    if (inner === null) return null;
    const neg: PolyMap = new Map();
    for (const [exp, c] of inner) neg.set(exp, ratNeg(c));
    return neg;
  }
  if (peek2(st).t === "op" && (peek2(st) as { t: "op"; v: string }).v === "+") {
    eat2(st);
    return parseUnary2(st);
  }
  return parsePower2(st);
}

function parsePower2(st: ParseState2): PolyMap | null {
  const base = parseAtom2(st);
  if (base === null) return null;
  if (
    peek2(st).t === "op" &&
    ((peek2(st) as { t: "op"; v: string }).v === "**" ||
     (peek2(st) as { t: "op"; v: string }).v === "^")
  ) {
    eat2(st);
    // Exponent must be a small non-negative integer literal.
    const expTok = eat2(st);
    if (expTok.t !== "num" || expTok.d !== 1n) return null;
    const n = Number(expTok.v);
    if (n < 0 || !Number.isInteger(n)) return null;
    // Exponentiate base: repeated multiplication.
    let result: PolyMap = new Map([[0, rat(1n)]]);
    for (let k = 0; k < n; k++) result = mulPolyMaps(result, base);
    return result;
  }
  return base;
}

function parseAtom2(st: ParseState2): PolyMap | null {
  const t = peek2(st);
  if (t.t === "num") {
    eat2(st);
    return polyMapFromRat(rat(t.v, t.d));
  }
  if (t.t === "ident") {
    eat2(st);
    if (t.v === st.varName) return polyMapFromVar();
    // Any other identifier: might be a function call or a second variable.
    if (peek2(st).t === "lp") {
      // Function call — non-polynomial.
      eat2(st);
      // Consume argument list to avoid parse errors.
      let depth = 1;
      while (depth > 0 && peek2(st).t !== "eof") {
        const next = eat2(st);
        if (next.t === "lp") depth++;
        else if (next.t === "rp") depth--;
      }
      return null;  // non-poly: function call
    }
    return null;  // non-poly: foreign symbol
  }
  if (t.t === "lp") {
    eat2(st);
    const v = parseAddSub2(st);
    if (peek2(st).t !== "rp") return null;
    eat2(st);
    return v;
  }
  return null;
}

function parsePoly(fStr: string, varName: string): Rat[] | null {
  try {
    const toks = tokenize2(fStr);
    const st: ParseState2 = { toks, i: 0, varName, nonPoly: false };
    const m = parseAddSub2(st);
    if (m === null) return null;
    if (peek2(st).t !== "eof") return null;
    return polyMapToCoeffs(m);
  } catch {
    return null;
  }
}

// =============================================================================
// Happy-path checks
// =============================================================================

function checkShapeOk(cand: Record<string, unknown>): Check {
  if (cand["kind"] !== "ok") {
    return { pass: false, detail: `kind=${JSON.stringify(cand["kind"])} not "ok"` };
  }
  if (!Array.isArray(cand["intervals"])) {
    return { pass: false, detail: "intervals field missing or not a list" };
  }
  const intervals = cand["intervals"] as unknown[];
  for (let i = 0; i < intervals.length; i++) {
    const entry = intervals[i];
    if (typeof entry !== "object" || entry === null) {
      return { pass: false, detail: `intervals[${i}] not a record` };
    }
    const rec = entry as Record<string, unknown>;
    if (typeof rec["lo"] !== "string") {
      return { pass: false, detail: `intervals[${i}].lo missing or not string` };
    }
    if (typeof rec["hi"] !== "string") {
      return { pass: false, detail: `intervals[${i}].hi missing or not string` };
    }
    // Validate lo/hi are parseable as rationals.
    try { ratFromString(rec["lo"] as string); } catch {
      return { pass: false, detail: `intervals[${i}].lo=${JSON.stringify(rec["lo"])} not a valid rational` };
    }
    try { ratFromString(rec["hi"] as string); } catch {
      return { pass: false, detail: `intervals[${i}].hi=${JSON.stringify(rec["hi"])} not a valid rational` };
    }
  }
  return { pass: true, detail: `${intervals.length} interval entries` };
}

function checkEachIntervalContainsOneRoot(
  f: Rat[],
  sturmSeq: Rat[][],
  intervals: Array<{ lo: Rat; hi: Rat }>,
): Check {
  const bad: string[] = [];
  for (let i = 0; i < intervals.length; i++) {
    const { lo, hi } = intervals[i]!;
    const cmp = ratCmp(lo, hi);
    if (cmp > 0) {
      bad.push(`intervals[${i}]: lo=${lo.n}/${lo.d} > hi=${hi.n}/${hi.d}`);
      continue;
    }
    if (cmp === 0) {
      // Singleton: f(lo) must be 0.
      const val = polyEval(f, lo);
      if (val.n !== 0n) {
        bad.push(`intervals[${i}]=singleton(${lo.n}/${lo.d}): f(lo) ≠ 0`);
      }
      continue;
    }
    // Open interval (lo, hi): compute open root count via Sturm.
    // n_closed = count_roots_closed(lo, hi)   (SymPy's Poly.count_roots convention)
    // n_open   = n_closed − [f(lo)=0] − [f(hi)=0]
    const nClosed = countRootsClosedInterval(sturmSeq, f, lo, hi);
    const fLoSign = ratSign(polyEval(f, lo));
    const fHiSign = ratSign(polyEval(f, hi));
    const nOpen = nClosed - (fLoSign === 0 ? 1 : 0) - (fHiSign === 0 ? 1 : 0);
    if (nOpen !== 1) {
      bad.push(
        `intervals[${i}]=(${lo.n}/${lo.d},${hi.n}/${hi.d}): open root count=${nOpen} ≠ 1 (closed=${nClosed})`
      );
    }
  }
  if (bad.length > 0) {
    return { pass: false, detail: bad.slice(0, 3).join("; ") };
  }
  return { pass: true, detail: `${intervals.length} interval(s) each isolate exactly one root` };
}

function checkIntervalsDisjointOrdered(
  intervals: Array<{ lo: Rat; hi: Rat }>,
): Check {
  for (let i = 0; i < intervals.length - 1; i++) {
    const hi_i = intervals[i]!.hi;
    const lo_next = intervals[i + 1]!.lo;
    if (ratCmp(hi_i, lo_next) > 0) {
      return {
        pass: false,
        detail: `intervals[${i}].hi=${hi_i.n}/${hi_i.d} > intervals[${i + 1}].lo=${lo_next.n}/${lo_next.d}`,
      };
    }
  }
  return { pass: true, detail: `${intervals.length} interval(s) disjoint and ascending` };
}

function checkCountMatchesTotalRealRoots(
  sturmSeq: Rat[][],
  intervals: Array<{ lo: Rat; hi: Rat }>,
): Check {
  const expected = countAllRealRoots(sturmSeq);
  const actual = intervals.length;
  if (actual !== expected) {
    return {
      pass: false,
      detail: `|intervals|=${actual} ≠ #real roots=${expected}`,
    };
  }
  return { pass: true, detail: `${actual} = #real roots` };
}

// =============================================================================
// Refusal lane checks
// =============================================================================

function checkShapeTagged(cand: Record<string, unknown>): Check {
  const tag = cand["tag"];
  if (typeof tag !== "string" || !tag.startsWith("real-root-isolate/")) {
    return { pass: false, detail: `tag=${JSON.stringify(tag)} not in real-root-isolate/* namespace` };
  }
  const payload = cand["payload"];
  if (typeof payload !== "object" || payload === null) {
    return { pass: false, detail: "payload missing or not a record" };
  }
  return { pass: true, detail: `tag=${JSON.stringify(tag)}` };
}

function checkRefusalClassMatches(
  cand: Record<string, unknown>,
  expectedEntry: ExpectedEntry,
): Check {
  const candTag = cand["tag"] as string | undefined;
  const expectedTag = expectedEntry.tag;
  if (expectedTag === undefined) {
    return { pass: true, detail: `tag=${JSON.stringify(candTag)} (no expected.tag pinned)` };
  }
  if (candTag !== expectedTag) {
    return {
      pass: false,
      detail: `cand.tag=${JSON.stringify(candTag)} ≠ expected.tag=${JSON.stringify(expectedTag)}`,
    };
  }
  const payload = cand["payload"] as Record<string, unknown> | undefined;
  const detail = payload?.["detail"];
  if (typeof detail !== "string" || detail.length === 0) {
    return { pass: false, detail: "payload.detail missing or empty" };
  }
  return { pass: true, detail: "tag matches; payload.detail non-empty" };
}

// =============================================================================
// Aggregate helpers
// =============================================================================

function _wrap(
  checks: Record<string, Check>,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const overall = Object.values(checks).every((c) => c.pass);
  if (overall) return { pass: true, reason: "all invariants hold", checks };
  const firstFail = Object.entries(checks).find(([, c]) => !c.pass)!;
  return {
    pass: false,
    reason: `failed: ${firstFail[0]} — ${firstFail[1].detail}`,
    checks,
  };
}

// ─── N/A markers for the wrong lane ──────────────────────────────────────────

const NA_OK: Check = { pass: true, detail: "n/a for kind=ok" };
const NA_TAGGED: Check = { pass: true, detail: "n/a for kind=tagged" };

// =============================================================================
// Top-level verify
// =============================================================================

type Payload = {
  input:     { f: string; var: string };
  candidate: unknown;
  id?:       string;
};

function verify(
  payload: Payload,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const inp    = payload.input;
  const cand   = payload.candidate;
  const caseId = payload.id ?? "?";

  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }

  const expectedIndex = loadExpected();
  const expectedEntry = expectedIndex.get(caseId) ?? {};

  const candR    = cand as Record<string, unknown>;
  const candKind = candR["kind"] as string | undefined;

  // ── Tool-error path ──────────────────────────────────────────────────────
  if (candKind === "tool_error") {
    if (expectedEntry.kind !== "tool_error") {
      return {
        pass: false,
        reason: `candidate emitted tool_error but expected was ${JSON.stringify(expectedEntry.kind ?? "success")}`,
        checks: { category: { pass: false, detail: "category mismatch" } },
      };
    }
    return {
      pass: true,
      reason: "tool_error correctly emitted",
      checks: {
        tool_error_expected: {
          pass: true,
          detail: `name=${JSON.stringify(candR["name"] ?? "?")}`,
        },
      },
    };
  }

  // ── Tagged boundary path ─────────────────────────────────────────────────
  if (candKind === "tagged") {
    if (expectedEntry.kind !== "tagged") {
      return {
        pass: false,
        reason: `candidate emitted tagged but expected was ${JSON.stringify(expectedEntry.kind ?? "success")}`,
        checks: { category: { pass: false, detail: "category mismatch" } },
      };
    }
    const checks: Record<string, Check> = {};
    checks["shape"] = checkShapeTagged(candR);
    checks["refusal_class_matches"] = checkRefusalClassMatches(candR, expectedEntry);
    // Happy-path checks are n/a for refusal lane.
    for (const k of ["each_interval_contains_one_root", "intervals_disjoint_and_ordered",
                     "count_matches_total_real_roots"]) {
      checks[k] = NA_TAGGED;
    }
    return _wrap(checks);
  }

  // ── Success path ─────────────────────────────────────────────────────────
  if (expectedEntry.kind === "tagged" || expectedEntry.kind === "tool_error") {
    return {
      pass: false,
      reason: `candidate emitted success record but expected was ${JSON.stringify(expectedEntry.kind)}`,
      checks: { category: { pass: false, detail: "category mismatch" } },
    };
  }

  const checks: Record<string, Check> = {};

  // 1. shape — must come first; downstream checks dereference cand fields.
  checks["shape"] = checkShapeOk(candR);
  if (!checks["shape"].pass) {
    // Skip arithmetic checks; return partial.
    for (const k of ["each_interval_contains_one_root", "intervals_disjoint_and_ordered",
                     "count_matches_total_real_roots"]) {
      checks[k] = { pass: false, detail: "skipped: shape failed" };
    }
    checks["refusal_class_matches"] = NA_OK;
    return _wrap(checks);
  }

  // Parse intervals.
  const rawIntervals = (candR["intervals"] as Array<{ lo: string; hi: string }>);
  let parseErr: string | null = null;
  const intervals: Array<{ lo: Rat; hi: Rat }> = [];
  for (let i = 0; i < rawIntervals.length; i++) {
    try {
      const lo = ratFromString(rawIntervals[i]!.lo);
      const hi = ratFromString(rawIntervals[i]!.hi);
      intervals.push({ lo, hi });
    } catch (e) {
      parseErr = `intervals[${i}] endpoints failed to parse: ${(e as Error).message}`;
      break;
    }
  }
  if (parseErr !== null) {
    for (const k of ["each_interval_contains_one_root", "intervals_disjoint_and_ordered",
                     "count_matches_total_real_roots"]) {
      checks[k] = { pass: false, detail: parseErr };
    }
    checks["refusal_class_matches"] = NA_OK;
    return _wrap(checks);
  }

  // Parse polynomial from input.
  const fCoeffs = parsePoly(inp.f, inp.var);
  if (fCoeffs === null) {
    // Non-polynomial input — should have been a tagged refusal.
    // Shape check already passed (kind=ok), which is suspicious.
    for (const k of ["each_interval_contains_one_root", "count_matches_total_real_roots"]) {
      checks[k] = { pass: false, detail: "could not parse polynomial from input — expect tagged refusal" };
    }
    checks["intervals_disjoint_and_ordered"] = checkIntervalsDisjointOrdered(intervals);
    checks["refusal_class_matches"] = NA_OK;
    return _wrap(checks);
  }

  // Compute Sturm sequence once for both arithmetic checks.
  const sturmSeq = sturmSequence(fCoeffs);

  // 2. each_interval_contains_one_root
  checks["each_interval_contains_one_root"] = checkEachIntervalContainsOneRoot(
    fCoeffs, sturmSeq, intervals
  );

  // 3. intervals_disjoint_and_ordered
  checks["intervals_disjoint_and_ordered"] = checkIntervalsDisjointOrdered(intervals);

  // 4. count_matches_total_real_roots
  checks["count_matches_total_real_roots"] = checkCountMatchesTotalRealRoots(sturmSeq, intervals);

  // refusal_class_matches is n/a for ok lane.
  checks["refusal_class_matches"] = NA_OK;

  return _wrap(checks);
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
      pass: false,
      reason: `verifier crashed: ${err.name}: ${err.message}`,
      checks: {},
    };
  }
  process.stdout.write(JSON.stringify(result) + "\n");
}

await main();
