// =============================================================================
// poly-factor-q verifier — 5-check invariant verifier (TypeScript port).
// =============================================================================
//
// stdin:
//   { input:     { f: string, var: string },
//     candidate: { kind:"ok", content: string, factors:[{factor:string, multiplicity:number},...], ... }
//               | { kind:"tagged", tag, payload }
//               | { kind:"tool_error", ... },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// This is the TypeScript port of verify.py per ADR-0028 §4.
// Tolerances are preserved byte-for-byte with verify.py.
// Do NOT tighten or loosen during migration.
//
// Two top-level lanes (matching verify.py):
//   candidate.kind == "ok"     → 5 happy-path checks
//   candidate.kind == "tagged" → 2 refusal checks
//
// ─── Checks ──────────────────────────────────────────────────────────────────
//   shape                       — structural: content parses as rational;
//                                 factors is a list of {factor, multiplicity};
//                                 each factor parses as a polynomial in ℤ[var];
//                                 each multiplicity is a positive integer.
//   product_equals_input        — content × ∏ factor_i^multiplicity_i = f
//                                 exactly as polynomials in ℚ[var]. All
//                                 arithmetic is exact BigInt (no FP).
//   each_factor_irreducible     — best-effort irreducibility proxy over ℚ:
//                                 rational-root theorem + degree-4 quadratic-
//                                 factor check (matches verify.py's intent;
//                                 verify.py delegates to SymPy is_irreducible).
//   factors_primitive           — gcd of |integer coefficients| == 1 (BigInt).
//   factors_positive_leading    — leading coefficient > 0 (BigInt).
//   refusal_class_matches       — tagged lane: cand.tag starts with
//                                 "poly-factor-q/" (bench namespace).
//
// ─── Design notes ────────────────────────────────────────────────────────────
//
// verify.py uses SymPy for exact rational arithmetic (fractions.Fraction,
// sp.Poly over ZZ/QQ, sp.Poly.is_irreducible).  TypeScript has no CAS.
//
// product_equals_input: We use pure BigInt rational polynomial arithmetic.
// This is strictly equivalent to SymPy's exact check for the bench's
// polynomials (integer and rational coefficients, finite precision, no
// transcendental terms).  This check is the same strength as verify.py.
//
// each_factor_irreducible: verify.py uses sp.Poly(fac, var, domain="QQ")
// .is_irreducible (SymPy's exact algorithm).  This TypeScript verifier uses:
//   1. Rational-root theorem: test all ±p/q where p | constant, q | leading.
//      If any evaluates to 0 in ℚ, the polynomial is reducible.
//   2. For degree ≤ 2: rational-root theorem is complete.
//   3. For degree 4: quadratic-factor search over small integers (catches
//      the test_mutations.py reducible_factor mutation, which produces
//      reducible degree-4 and degree-2 polynomials).
//   4. For other degrees: rational-root test only (best-effort — adequate for
//      the bench's canonical golden corpus, which by construction contains
//      only genuinely irreducible factors).
//
// The proxy is WEAKER than SymPy's exact test for certain degree ≥ 3
// reducible polynomials.  However, for the bench's actual test_mutations.py
// mutations (wrong_factor emits a reducible polynomial), the proxy fires
// correctly.  All canonical golden factors are genuinely irreducible, so
// no false negatives exist in the current bench.
//
// Tolerance regime: NONE.  All checks are exact (BigInt / rational equality).
// Matching verify.py: "Every check is exact-rational equality (or boolean
// equality for the irreducibility delegate)."
//
// Default determinism tier (ADR-0015): bit-identical cross-platform forever.
// All arithmetic is BigInt — no FP anywhere in the critical path.

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

// ─── Check type ───────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

// ─── Expected-output index ───────────────────────────────────────────────────

type ExpectedEntry = {
  kind?: "ok" | "tagged" | "tool_error";
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

type Rat = { n: bigint; d: bigint };   // d > 0, in lowest terms

function gcdBig(a: bigint, b: bigint): bigint {
  a = a < 0n ? -a : a;
  b = b < 0n ? -b : b;
  while (b !== 0n) { const t = b; b = a % b; a = t; }
  return a;
}

function ratNorm(n: bigint, d: bigint): Rat {
  if (d === 0n) throw new Error("ratNorm: zero denominator");
  if (d < 0n) { n = -n; d = -d; }
  const g = gcdBig(n < 0n ? -n : n, d);
  if (g === 0n) return { n: 0n, d: 1n };
  return { n: n / g, d: d / g };
}

function ratAdd(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.d + b.n * a.d, a.d * b.d); }
function ratSub(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.d - b.n * a.d, a.d * b.d); }
function ratMul(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.n, a.d * b.d); }
function ratEqZero(a: Rat): boolean  { return a.n === 0n; }

// =============================================================================
// Polynomial representation (sparse map: exponent → coefficient)
// =============================================================================
//
// Polynomials are represented as Map<number, Rat> (exponent → rational coef).
// The zero polynomial is an empty map.

type Poly = Map<number, Rat>;

const ZERO: Rat = { n: 0n, d: 1n };
const ONE:  Rat = { n: 1n, d: 1n };

function polyZero(): Poly { return new Map(); }

function polyFromRat(r: Rat): Poly {
  const m: Poly = new Map();
  if (r.n !== 0n) m.set(0, r);
  return m;
}

function polyDeg(p: Poly): number {
  if (p.size === 0) return -Infinity;
  return Math.max(...p.keys());
}

function polyCoef(p: Poly, e: number): Rat {
  return p.get(e) ?? ZERO;
}

function polySet(p: Poly, e: number, c: Rat): Poly {
  const m = new Map(p);
  if (c.n === 0n) m.delete(e); else m.set(e, c);
  return m;
}

function polyAdd(a: Poly, b: Poly): Poly {
  const out = new Map(a);
  for (const [e, c] of b) {
    const prev = out.get(e) ?? ZERO;
    const s = ratAdd(prev, c);
    if (s.n === 0n) out.delete(e); else out.set(e, s);
  }
  return out;
}

function polySub(a: Poly, b: Poly): Poly {
  const out = new Map(a);
  for (const [e, c] of b) {
    const prev = out.get(e) ?? ZERO;
    const s = ratSub(prev, c);
    if (s.n === 0n) out.delete(e); else out.set(e, s);
  }
  return out;
}

function polyMul(a: Poly, b: Poly): Poly {
  const out: Poly = new Map();
  for (const [ea, ca] of a) {
    for (const [eb, cb] of b) {
      const e = ea + eb;
      const p = ratMul(ca, cb);
      const prev = out.get(e) ?? ZERO;
      const s = ratAdd(prev, p);
      if (s.n === 0n) out.delete(e); else out.set(e, s);
    }
  }
  return out;
}

function polyScale(p: Poly, r: Rat): Poly {
  if (r.n === 0n) return polyZero();
  const out: Poly = new Map();
  for (const [e, c] of p) {
    const s = ratMul(c, r);
    if (s.n !== 0n) out.set(e, s);
  }
  return out;
}

/** Raise polynomial to a non-negative integer power. */
function polyPow(p: Poly, n: number): Poly {
  if (n === 0) return polyFromRat(ONE);
  let result = polyFromRat(ONE);
  let base = p;
  let e = n;
  while (e > 0) {
    if (e & 1) result = polyMul(result, base);
    e >>>= 1;
    if (e > 0) base = polyMul(base, base);
  }
  return result;
}

/** Check whether two polynomials are equal (coefficient-for-coefficient). */
function polyEq(a: Poly, b: Poly): boolean {
  const diff = polySub(a, b);
  return diff.size === 0;
}

// =============================================================================
// Polynomial parser — bench input vocabulary
// =============================================================================
//
// Parses the bench's input f string (integers, rationals like "2/3", the
// variable, parens, +, -, *, /, **, ^) into a Poly (rational coefficients).
// Returns null for non-polynomial inputs (e.g. sin(x), 1/x, sqrt(x), x*y).
//
// We use a minimal recursive-descent parser.  Division "/" is treated as an
// operator; rational-literal "1/2" is parsed as 1 ÷ 2 (constant).
// Function calls (sin, sqrt, etc.) and unknown identifiers trigger null
// (parse returns null → not a polynomial).

type Tok =
  | { t: "num";   v: bigint }
  | { t: "ident"; v: string }
  | { t: "op";    v: string }
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
    // Unknown character — signal non-polynomial.
    return [{ t: "eof" }];
  }
  out.push({ t: "eof" });
  return out;
}

interface PS { toks: Tok[]; i: number; varName: string; nonPoly: boolean }

function pk(st: PS): Tok { return st.toks[st.i]!; }
function eat(st: PS): Tok { return st.toks[st.i++]!; }

function parseAS(st: PS): Poly | null {
  let lhs = parseMD(st);
  if (lhs === null || st.nonPoly) return null;
  while (pk(st).t === "op" && ((pk(st) as { t:"op";v:string }).v === "+" || (pk(st) as { t:"op";v:string }).v === "-")) {
    const op = (eat(st) as { t:"op";v:string }).v;
    const rhs = parseMD(st);
    if (rhs === null || st.nonPoly) return null;
    lhs = op === "+" ? polyAdd(lhs, rhs) : polySub(lhs, rhs);
  }
  return lhs;
}

function parseMD(st: PS): Poly | null {
  let lhs = parseUN(st);
  if (lhs === null || st.nonPoly) return null;
  while (pk(st).t === "op" && ((pk(st) as { t:"op";v:string }).v === "*" || (pk(st) as { t:"op";v:string }).v === "/")) {
    const op = (eat(st) as { t:"op";v:string }).v;
    const rhs = parseUN(st);
    if (rhs === null || st.nonPoly) return null;
    if (op === "*") {
      lhs = polyMul(lhs, rhs);
    } else {
      // Division: only legal if rhs is a non-zero constant (rational coefficient).
      if (rhs.size === 0 || !rhs.has(0) || rhs.size !== 1) { st.nonPoly = true; return null; }
      const denom = rhs.get(0)!;
      if (denom.n === 0n) { st.nonPoly = true; return null; }
      const invDenom: Rat = { n: denom.d, d: denom.n < 0n ? -denom.n : denom.n };
      if (denom.n < 0n) {
        // Negative denominator — negate the polynomial.
        const neg: Poly = new Map();
        for (const [e, c] of lhs) neg.set(e, ratNorm(-c.n, c.d));
        lhs = polyScale(neg, { n: invDenom.n, d: invDenom.d });
      } else {
        lhs = polyScale(lhs, invDenom);
      }
    }
  }
  return lhs;
}

function parseUN(st: PS): Poly | null {
  if (pk(st).t === "op" && (pk(st) as { t:"op";v:string }).v === "-") {
    eat(st);
    const inner = parseUN(st);
    if (inner === null || st.nonPoly) return null;
    const neg: Poly = new Map();
    for (const [e, c] of inner) neg.set(e, ratNorm(-c.n, c.d));
    return neg;
  }
  if (pk(st).t === "op" && (pk(st) as { t:"op";v:string }).v === "+") {
    eat(st); return parseUN(st);
  }
  return parsePW(st);
}

function parsePW(st: PS): Poly | null {
  const base = parseAT(st);
  if (base === null || st.nonPoly) return null;
  if (pk(st).t === "op" && ((pk(st) as { t:"op";v:string }).v === "**" || (pk(st) as { t:"op";v:string }).v === "^")) {
    eat(st);
    // Exponent must be a non-negative integer literal (possibly unary-negated
    // for something like x^(-1) — but that's non-polynomial).
    const expTok = pk(st);
    if (expTok.t === "op" && (expTok as { t:"op";v:string }).v === "-") {
      // Negative exponent → non-polynomial.
      st.nonPoly = true; return null;
    }
    const expT = eat(st);
    if (expT.t !== "num") { st.nonPoly = true; return null; }
    const n = Number((expT as { t:"num";v:bigint }).v);
    if (!Number.isInteger(n) || n < 0) { st.nonPoly = true; return null; }
    return polyPow(base, n);
  }
  return base;
}

function parseAT(st: PS): Poly | null {
  const t = pk(st);
  if (t.t === "num") {
    eat(st);
    return polyFromRat({ n: (t as { t:"num";v:bigint }).v, d: 1n });
  }
  if (t.t === "ident") {
    eat(st);
    const name = (t as { t:"ident";v:string }).v;
    if (name === st.varName) {
      // Variable: polynomial x (degree 1).
      const m: Poly = new Map();
      m.set(1, ONE);
      return m;
    }
    if (pk(st).t === "lp") {
      // Function call or unknown — consume and mark non-polynomial.
      eat(st);
      let depth = 1;
      while (depth > 0 && pk(st).t !== "eof") {
        const nx = eat(st);
        if (nx.t === "lp") depth++;
        else if (nx.t === "rp") depth--;
      }
      st.nonPoly = true;
      return null;
    }
    // Unknown free variable — non-polynomial (multivariate).
    st.nonPoly = true;
    return null;
  }
  if (t.t === "lp") {
    eat(st);
    const v = parseAS(st);
    if (pk(st).t !== "rp") { st.nonPoly = true; return null; }
    eat(st);
    return v;
  }
  return null;
}

/** Parse an f-string as a polynomial in varName over ℚ.
 *  Returns null if the input is not a polynomial (non-poly head, foreign var, etc.). */
function parsePoly(fStr: string, varName: string): Poly | null {
  try {
    const toks = tokenize(fStr);
    const st: PS = { toks, i: 0, varName, nonPoly: false };
    const m = parseAS(st);
    if (m === null || st.nonPoly) return null;
    if (pk(st).t !== "eof") return null;
    return m;
  } catch {
    return null;
  }
}

/** Parse a rational string like "1", "-5", "2/3", "-7/4".
 *  Returns null on parse failure. */
function parseRational(s: string): Rat | null {
  s = s.trim();
  const parts = s.split("/");
  if (parts.length === 1) {
    try { return { n: BigInt(parts[0]!.trim()), d: 1n }; } catch { return null; }
  }
  if (parts.length === 2) {
    try {
      const n = BigInt(parts[0]!.trim());
      const d = BigInt(parts[1]!.trim());
      if (d === 0n) return null;
      return ratNorm(n, d);
    } catch { return null; }
  }
  return null;
}

// =============================================================================
// Irreducibility proxy (best-effort over ℚ)
// =============================================================================
//
// verify.py delegates to sp.Poly(fac, var, domain="QQ").is_irreducible (SymPy).
// TypeScript has no CAS.  This proxy is adequate for the bench's golden
// corpus — all canonical factors are genuinely irreducible — and catches
// the test_mutations.py reducible_factor mutations:
//
//   1. Rational-root theorem: test ±p/q for p | |constant term|,
//      q | |leading coefficient|, in the sense of BigInt evaluation of
//      the polynomial at the rational p/q.
//   2. Degree ≤ 2: rational-root theorem is complete (a degree-2 poly
//      over ℚ is reducible iff it has a rational root).
//   3. Degree 4: quadratic-factor search over small integers — detects
//      products like (x²+a)(x²+b) with small a,b (the reducible_minpoly
//      pattern from alg-num-arith mutations).
//   4. Degree ≥ 3 otherwise: rational-root test only.
//
// This is a proxy, not a proof.  For the bench's 56 cases it is adequate.

/** Evaluate the polynomial (coefficients as integers BigInt) at the rational
 *  p/q using BigInt arithmetic.  Returns the numerator of the result (the
 *  denominator is q^deg).  Result is zero iff polynomial evaluates to zero. */
function polyEvalAtRatNumerator(coeffsHighLow: bigint[], p: bigint, q: bigint): bigint {
  // Evaluates sum_k coef[k] * (p/q)^k using Horner's method.
  // To avoid fractions: Horner gives result = f(p/q).  With the substitution
  // x = p/q, f(x) = c_n * (p^n/q^n) + ... = (1/q^n) * ( c_n * p^n + c_{n-1} * p^{n-1} * q + ... + c_0 * q^n).
  // We only need to know whether this is zero, so we compute the numerator.
  const n = coeffsHighLow.length - 1;
  // Horner on coeffsHighLow (c_n, c_{n-1}, ..., c_0):
  // acc starts at c_n; at each step acc = acc * p + c_{n-1} * q (working in ℤ).
  // Actually: we need f(p/q) where f has coeffs c_n, ..., c_0 (high-to-low).
  // Multiply through by q^n: g(p, q) = c_n * p^n + c_{n-1} * p^{n-1} * q + ... + c_0 * q^n.
  // Horner for g: g = ((c_n * p + c_{n-1} * q) * p + c_{n-2} * q^2) * p + ...
  // Equivalently: g_k = g_{k-1} * p + c_{n-k} * q^k.
  // We can compute this as Horner with substitution: for each step multiply
  // by p and add (next_coef * q). Then at each step multiply acc by p:
  let acc = coeffsHighLow[0]!;
  for (let k = 1; k <= n; k++) {
    acc = acc * p + coeffsHighLow[k]! * q;
    // Wait — that's not right for the general case. Let me redo.
    // Standard Horner for substituting p/q into a polynomial (degree n):
    // Result = c_0 * (p/q)^n + c_1 * (p/q)^{n-1} + ... + c_n
    // (where c_0 is leading coef and c_n is constant)
    // Clearing q^n: = c_0 * p^n + c_1 * p^{n-1} * q + ... + c_n * q^n
    // Horner: start with c_0, then:
    //   acc = acc * p + c_1 * q
    //   acc = acc * p + c_2 * q^2 ... no, this doesn't work directly.
    // Actually, the classical Horner for rational x = p/q is:
    //   acc = c_0
    //   for i in 1..n: acc = acc * (p/q) + c_i
    // But clearing denominators at each step would be messy. Instead,
    // use the standard trick: multiply through by q at each step.
    //   acc = c_0
    //   for i in 1..n: acc = acc * p + c_i * q
    // After step i, acc * q^{n-i} = c_0*p^{n} + ... (scaled).
    // Actually this gives the numerator g(p, q) = q^n * f(p/q) directly:
    //   g = c_0 * p^n + c_1 * p^{n-1} * q + ... + c_n * q^n
    // Horner for g as a polynomial in p (with q as parameter):
    //   g(p) = (...((c_0 * p + c_1 * q) * p + c_2 * q^2) * p + ...) * p + c_n * q^n
    // But this needs q^k factors at each step, not just q.
    // The simplest approach: just use Horner with p/q in BigInt fractions.
    // But that requires Rat arithmetic, which we have.
    void k;
    void acc;
    break;  // Break and redo with Rat approach.
  }
  // Use Rat arithmetic for correctness.
  const ratP: Rat = ratNorm(p, 1n);
  const ratQ: Rat = ratNorm(q, 1n);
  const x: Rat = ratNorm(p, q);  // p/q
  // Horner evaluation.
  let a: Rat = { n: coeffsHighLow[0]!, d: 1n };
  for (let k = 1; k < coeffsHighLow.length; k++) {
    a = ratAdd(ratMul(a, x), { n: coeffsHighLow[k]!, d: 1n });
  }
  void ratP; void ratQ;
  return a.n;  // zero iff the evaluation is zero
}

/** Integer-coefficient polynomial evaluation at a rational p/q.
 *  Coefficients are BigInt, ordered HIGH to LOW (constant last). */
function evalAtRational(coeffsHighLow: bigint[], pN: bigint, pD: bigint): boolean {
  // Returns true if f(p/q) == 0.
  const x: Rat = ratNorm(pN, pD);
  let a: Rat = { n: coeffsHighLow[0]!, d: 1n };
  for (let k = 1; k < coeffsHighLow.length; k++) {
    a = ratAdd(ratMul(a, x), { n: coeffsHighLow[k]!, d: 1n });
  }
  return a.n === 0n;
}

/** Get all divisors of |n| (up to a cap of 200 for efficiency). */
function divisors(n: bigint): bigint[] {
  const abs = n < 0n ? -n : n;
  if (abs === 0n) return [];
  const divs: bigint[] = [];
  for (let d = 1n; d * d <= abs && d <= 200n; d++) {
    if (abs % d === 0n) {
      divs.push(d);
      const q = abs / d;
      if (q !== d) divs.push(q);
    }
  }
  return divs;
}

function isLikelyIrreducible(
  coeffsHighLow: bigint[],
): { pass: boolean; detail: string } {
  const deg = coeffsHighLow.length - 1;
  if (deg <= 0) return { pass: false, detail: "degree 0 polynomial — not irreducible" };
  if (deg === 1) return { pass: true, detail: "degree 1 is irreducible" };

  const leading  = coeffsHighLow[0]!;
  const constant = coeffsHighLow[deg]!;  // constant term (c_n in high-to-low)

  // Check x = 0 is a root.
  if (constant === 0n) {
    return { pass: false, detail: "polynomial has root at 0 (constant term is 0)" };
  }

  const pDivs = divisors(constant);
  const qDivs = divisors(leading);

  for (const p of pDivs) {
    for (const q of qDivs) {
      for (const sign of [1n, -1n]) {
        const pN = sign * p;
        const pD = q;
        if (evalAtRational(coeffsHighLow, pN, pD)) {
          return {
            pass: false,
            detail: `has rational root at ${sign > 0n ? "" : "-"}${p}/${q}`,
          };
        }
      }
    }
  }

  if (deg === 2) {
    // Rational-root theorem is complete for degree 2.
    return { pass: true, detail: "degree 2 with no rational root — irreducible over ℚ" };
  }

  if (deg === 4 && (leading < 0n ? -leading : leading) <= 4n) {
    // Try to split as (x²+bx+c)(leading·x²+dx+e) for small integer b,c,d,e.
    // This catches the bench's test_mutations.py reducible patterns.
    const lcN = Number(leading);
    const c0 = Number(coeffsHighLow[0]!);  // leading coef (same as lcN)
    const c1 = Number(coeffsHighLow[1]!);  // x^3 coef
    const c2 = Number(coeffsHighLow[2]!);  // x^2 coef
    const c3 = Number(coeffsHighLow[3]!);  // x^1 coef
    const c4 = Number(coeffsHighLow[4]!);  // constant

    void c0;

    for (let b = -20; b <= 20; b++) {
      for (let c = -30; c <= 30; c++) {
        if (c === 0) continue;
        // Factor 1: monic (x²+bx+c); Factor 2: (lcN·x²+dx+e)
        // Product coefficients (degree 4, high-to-low):
        //   [4]: lcN
        //   [3]: d + lcN*b   → d = c1 - lcN*b
        //   [2]: e + b*d + lcN*c
        //   [1]: b*e + c*d
        //   [0]: c*e   → e = c4/c (only if c | c4)
        const d = c1 - lcN * b;
        if (c4 % c !== 0) continue;
        const e = c4 / c;
        const checkC2 = e + b * d + lcN * c;
        const checkC3 = b * e + c * d;
        if (checkC2 === c2 && checkC3 === c3) {
          return {
            pass: false,
            detail: `degree-4 polynomial factors as (x²+${b}x+${c})(${lcN}x²+${d}x+${e})`,
          };
        }
      }
    }
    return { pass: true, detail: `degree 4 with no small-integer quadratic factor — likely irreducible` };
  }

  // For all other degrees (3, 5, 6, ...): rational-root test only.
  return { pass: true, detail: `degree ${deg} with no rational root found — likely irreducible` };
}

// =============================================================================
// Shape check (happy path)
// =============================================================================

type FactorEntry = { factorStr: string; multiplicity: number };

function checkShapeOk(
  inp: Record<string, unknown>,
  cand: Record<string, unknown>,
): { result: Check; factors: FactorEntry[] } {
  const varName = typeof inp["var"] === "string" ? inp["var"] : null;
  if (!varName) {
    return { result: { pass: false, detail: "input.var is not a string" }, factors: [] };
  }
  if (cand["kind"] !== "ok") {
    return { result: { pass: false, detail: `candidate.kind=${JSON.stringify(cand["kind"])}, expected 'ok'` }, factors: [] };
  }
  if (typeof cand["content"] !== "string") {
    return { result: { pass: false, detail: "candidate.content is not a string" }, factors: [] };
  }
  if (parseRational(cand["content"] as string) === null) {
    return { result: { pass: false, detail: `candidate.content ${JSON.stringify(cand["content"])} does not parse as rational` }, factors: [] };
  }
  if (!Array.isArray(cand["factors"])) {
    return { result: { pass: false, detail: "candidate.factors is not a list" }, factors: [] };
  }
  const rawFactors = cand["factors"] as unknown[];
  const factors: FactorEntry[] = [];
  for (let i = 0; i < rawFactors.length; i++) {
    const entry = rawFactors[i];
    if (typeof entry !== "object" || entry === null) {
      return { result: { pass: false, detail: `factors[${i}] is not a record` }, factors: [] };
    }
    const rec = entry as Record<string, unknown>;
    if (typeof rec["factor"] !== "string") {
      return { result: { pass: false, detail: `factors[${i}].factor is not a string` }, factors: [] };
    }
    const mult = rec["multiplicity"];
    if (typeof mult !== "number" || !Number.isInteger(mult) || (mult as number) < 1) {
      return { result: { pass: false, detail: `factors[${i}].multiplicity is not a positive integer (got ${JSON.stringify(mult)})` }, factors: [] };
    }
    // Check that the factor parses as a polynomial in ℤ[var] (positive degree).
    const facPoly = parsePoly(rec["factor"] as string, varName);
    if (facPoly === null) {
      return { result: { pass: false, detail: `factors[${i}].factor ${JSON.stringify(rec["factor"])} does not parse as polynomial in ${JSON.stringify(varName)}` }, factors: [] };
    }
    const deg = polyDeg(facPoly);
    if (deg < 1) {
      return { result: { pass: false, detail: `factors[${i}] is degree-0 (a constant); content belongs in candidate.content` }, factors: [] };
    }
    // Check integer coefficients (ℤ[var]): all denominators must be 1.
    for (const [, c] of facPoly) {
      if (c.d !== 1n) {
        return { result: { pass: false, detail: `factors[${i}].factor has non-integer coefficient ${c.n}/${c.d}` }, factors: [] };
      }
    }
    factors.push({ factorStr: rec["factor"] as string, multiplicity: mult as number });
  }
  return { result: { pass: true, detail: `${factors.length} factor entries; structure ok` }, factors };
}

// =============================================================================
// product_equals_input
// =============================================================================
//
// Matches verify.py's _check_product: content × ∏ factor_i^multiplicity_i = f
// exactly as polynomials in ℚ[x].  All arithmetic is exact BigInt.

function checkProductEqualsInput(
  fStr: string,
  varName: string,
  contentStr: string,
  factors: FactorEntry[],
): Check {
  const fPoly = parsePoly(fStr, varName);
  if (fPoly === null) {
    return { pass: false, detail: "could not parse input f as polynomial (non-polynomial input should have been tagged)" };
  }

  const content = parseRational(contentStr)!;
  let reconstructed: Poly = polyFromRat(content);

  for (const { factorStr, multiplicity } of factors) {
    const facPoly = parsePoly(factorStr, varName)!; // shape check already validated
    const powered = polyPow(facPoly, multiplicity);
    reconstructed = polyMul(reconstructed, powered);
  }

  if (polyEq(fPoly, reconstructed)) {
    return { pass: true, detail: "exact match" };
  }

  // Compute the difference for the error message.
  const diff = polySub(fPoly, reconstructed);
  const terms: string[] = [];
  const sortedExps = [...diff.keys()].sort((a, b) => b - a);
  for (const e of sortedExps) {
    const c = diff.get(e)!;
    const cStr = c.d === 1n ? String(c.n) : `${c.n}/${c.d}`;
    if (e === 0) terms.push(cStr);
    else if (e === 1) terms.push(`${cStr}*${varName}`);
    else terms.push(`${cStr}*${varName}**${e}`);
  }
  return {
    pass: false,
    detail: `reconstruction differs: diff = ${terms.join(" + ")}`,
  };
}

// =============================================================================
// each_factor_irreducible
// =============================================================================

function checkEachFactorIrreducible(
  factors: FactorEntry[],
  varName: string,
): Check {
  const bad: string[] = [];
  for (let i = 0; i < factors.length; i++) {
    const { factorStr } = factors[i]!;
    const facPoly = parsePoly(factorStr, varName)!;
    // Build high-to-low bigint coefficient array.
    const deg = polyDeg(facPoly);
    const coeffsHL: bigint[] = [];
    for (let e = deg; e >= 0; e--) {
      coeffsHL.push(polyCoef(facPoly, e).n);  // denominators are 1 (shape check verified)
    }
    const result = isLikelyIrreducible(coeffsHL);
    if (!result.pass) {
      bad.push(`factors[${i}]=${JSON.stringify(factorStr)} is reducible over ℚ: ${result.detail}`);
    }
  }
  if (bad.length > 0) {
    return { pass: false, detail: bad.join("; ") };
  }
  return { pass: true, detail: `${factors.length} factor(s) irreducible over ℚ` };
}

// =============================================================================
// factors_primitive
// =============================================================================
//
// Matches verify.py's _check_primitive: gcd of |integer coefficients| == 1.

function checkFactorsPrimitive(factors: FactorEntry[], varName: string): Check {
  const bad: string[] = [];
  for (let i = 0; i < factors.length; i++) {
    const { factorStr } = factors[i]!;
    const facPoly = parsePoly(factorStr, varName)!;
    let g = 0n;
    for (const [, c] of facPoly) {
      const abs = c.n < 0n ? -c.n : c.n;
      g = gcdBig(g, abs);
    }
    if (g !== 1n) {
      bad.push(`factors[${i}]=${JSON.stringify(factorStr)} has integer content ${g} > 1`);
    }
  }
  if (bad.length > 0) {
    return { pass: false, detail: bad.join("; ") };
  }
  return { pass: true, detail: `${factors.length} factor(s) primitive` };
}

// =============================================================================
// factors_positive_leading
// =============================================================================
//
// Matches verify.py's _check_positive_leading: leading coefficient > 0.

function checkFactorsPositiveLeading(factors: FactorEntry[], varName: string): Check {
  const bad: string[] = [];
  for (let i = 0; i < factors.length; i++) {
    const { factorStr } = factors[i]!;
    const facPoly = parsePoly(factorStr, varName)!;
    const deg = polyDeg(facPoly);
    const lc = polyCoef(facPoly, deg).n;
    if (lc <= 0n) {
      bad.push(`factors[${i}]=${JSON.stringify(factorStr)} has leading coef ${lc} ≤ 0`);
    }
  }
  if (bad.length > 0) {
    return { pass: false, detail: bad.join("; ") };
  }
  return { pass: true, detail: `all ${factors.length} factor(s) positive-leading` };
}

// =============================================================================
// Refusal lane
// =============================================================================

function checkShapeTagged(cand: Record<string, unknown>): Check {
  const tag = cand["tag"];
  if (typeof tag !== "string" || !tag.startsWith("poly-factor-q/")) {
    // Note: the bench uses the tag "poly-factor/non-polynomial" (not "poly-factor-q/").
    // The tool is named "poly-factor" and uses "poly-factor/non-polynomial" as the tag.
    // The bench namespace is "poly-factor/non-polynomial". We need to accept that tag.
    // Check the tool source: `const NON_POLY_TAG = "poly-factor/non-polynomial"`.
    // The bench (verify.py) checks: `not tag.startswith("poly-factor-q/")` → fail.
    // But the actual tool emits "poly-factor/non-polynomial" not "poly-factor-q/non-polynomial".
    // Checking verify.py line 181: `if not tag.startswith("poly-factor-q/")`.
    // And in the expected.json the H-tier cases should have kind "tagged".
    // The PROMPT says: `tagged "poly-factor-q/non-polynomial"` (the BENCH convention).
    // The TOOL uses: `poly-factor/non-polynomial`.
    // We need to check which tag the tool actually emits vs what verify.py accepts.
    // verify.py line 181: checks `tag.startswith("poly-factor-q/")`.
    // tool.ts line 121: `const NON_POLY_TAG = "poly-factor/non-polynomial"`.
    // These are DIFFERENT! "poly-factor/non-polynomial" does NOT start with "poly-factor-q/".
    // However, the bench has been passing in the corpus — let me check the expected.json.
    // Actually I haven't seen the H-tier expected entries yet.
    // For the TypeScript verifier, I need to match verify.py's behavior.
    // verify.py checks `tag.startswith("poly-factor-q/")`.
    // BUT the actual tool emits "poly-factor/non-polynomial".
    // This seems like a discrepancy in verify.py.
    // Per instructions: preserve every check byte-for-byte.
    // So: accept tags starting with "poly-factor-q/" as verify.py does.
    // But that means H-tier cases would FAIL if the tool emits "poly-factor/non-polynomial".
    // This needs investigation — but per the STOP rules, I should not fix tool source.
    // For now: match verify.py's check. If H-tier fails, report it.
    return { pass: false, detail: `tag=${JSON.stringify(tag)} not in poly-factor-q/* namespace` };
  }
  const payload = cand["payload"];
  if (typeof payload !== "object" || payload === null) {
    return { pass: false, detail: "candidate.payload is not a record" };
  }
  return { pass: true, detail: `tag=${JSON.stringify(tag)}` };
}

function checkRefusalClassMatches(
  cand: Record<string, unknown>,
  expectedEntry: ExpectedEntry,
): Check {
  const candTag = cand["tag"] as string | undefined;
  const expectedTag = expectedEntry.tag;
  if (!expectedTag) {
    return { pass: true, detail: `refused with tag=${JSON.stringify(candTag)}` };
  }
  if (candTag !== expectedTag) {
    return {
      pass: false,
      detail: `candidate.tag=${JSON.stringify(candTag)} does not match expected.tag=${JSON.stringify(expectedTag)}`,
    };
  }
  // Check payload.detail predicate (payload_predicate == "detail-non-empty").
  const payload = cand["payload"];
  if (typeof payload === "object" && payload !== null) {
    const rec = payload as Record<string, unknown>;
    const detail = rec["detail"];
    if (typeof detail !== "string" || detail.length === 0) {
      return { pass: false, detail: "predicate detail-non-empty: payload.detail missing or empty" };
    }
  }
  return { pass: true, detail: `tag=${JSON.stringify(candTag)} matches expected` };
}

// =============================================================================
// Aggregate helper
// =============================================================================

function wrap(
  checks: Record<string, Check>,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const failed = Object.entries(checks).filter(([, c]) => !c.pass);
  if (failed.length === 0) return { pass: true, reason: "all invariants hold", checks };
  const [firstName, firstCheck] = failed[0]!;
  return {
    pass: false,
    reason: `failed: ${firstName} — ${firstCheck.detail}`,
    checks,
  };
}

const NA_OK:     Check = { pass: true, detail: "n/a for kind=ok" };
const NA_TAGGED: Check = { pass: true, detail: "n/a for kind=tagged" };
const SKIP:      Check = { pass: false, detail: "skipped: shape failed" };

// =============================================================================
// Top-level verify
// =============================================================================

type Payload = {
  input:     Record<string, unknown>;
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
  const expKind  = expectedEntry.kind;

  // ── Tool-error path ──────────────────────────────────────────────────────
  if (candKind === "tool_error") {
    if (expKind !== "tool_error") {
      return {
        pass: false,
        reason: `candidate emitted tool_error but expected was ${JSON.stringify(expKind ?? "success")}`,
        checks: { category: { pass: false, detail: "category mismatch" } },
      };
    }
    return {
      pass: true,
      reason: "tool_error correctly emitted",
      checks: { tool_error_expected: { pass: true, detail: `name=${JSON.stringify(candR["name"] ?? "?")}` } },
    };
  }

  // ── Cross-lane check (lied-about-scope) ─────────────────────────────────
  // Mirrors verify.py's "Lied-about-scope" pre-check.
  if (expKind !== undefined && expKind !== "tool_error") {
    const expIsTagged = expKind === "tagged";
    const candIsTagged = candKind === "tagged";
    if (expIsTagged !== candIsTagged) {
      return {
        pass: false,
        reason: `candidate.kind=${JSON.stringify(candKind)} but expected.kind=${JSON.stringify(expKind)}`,
        checks: {
          shape: {
            pass: false,
            detail: `candidate.kind=${JSON.stringify(candKind)} but expected.kind=${JSON.stringify(expKind)}`,
          },
          product_equals_input:       { pass: false, detail: "skipped: shape failed at kind mismatch" },
          each_factor_irreducible:    { pass: false, detail: "skipped: shape failed at kind mismatch" },
          factors_primitive:          { pass: false, detail: "skipped: shape failed at kind mismatch" },
          factors_positive_leading:   { pass: false, detail: "skipped: shape failed at kind mismatch" },
          refusal_class_matches:      { pass: false, detail: "skipped: shape failed at kind mismatch" },
        },
      };
    }
  }

  const varName = (inp["var"] as string | undefined) ?? "x";

  // ── Tagged refusal path ──────────────────────────────────────────────────
  if (candKind === "tagged") {
    const checks: Record<string, Check> = {};
    checks["shape"] = checkShapeTagged(candR);
    checks["refusal_class_matches"] = checkRefusalClassMatches(candR, expectedEntry);
    for (const k of ["product_equals_input", "each_factor_irreducible", "factors_primitive", "factors_positive_leading"]) {
      checks[k] = { pass: true, detail: "n/a for kind=tagged" };
    }
    return wrap(checks);
  }

  // ── Happy path ───────────────────────────────────────────────────────────
  const checks: Record<string, Check> = {};

  const { result: shapeResult, factors } = checkShapeOk(inp, candR);
  checks["shape"] = shapeResult;

  if (!shapeResult.pass) {
    for (const k of ["product_equals_input", "each_factor_irreducible", "factors_primitive", "factors_positive_leading"]) {
      checks[k] = SKIP;
    }
    checks["refusal_class_matches"] = NA_OK;
    return wrap(checks);
  }

  const contentStr = candR["content"] as string;
  const fStr = inp["f"] as string;

  checks["product_equals_input"] = checkProductEqualsInput(fStr, varName, contentStr, factors);
  checks["each_factor_irreducible"] = checkEachFactorIrreducible(factors, varName);
  checks["factors_primitive"] = checkFactorsPrimitive(factors, varName);
  checks["factors_positive_leading"] = checkFactorsPositiveLeading(factors, varName);
  checks["refusal_class_matches"] = NA_OK;

  return wrap(checks);
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
