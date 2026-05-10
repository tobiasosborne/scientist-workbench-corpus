// =============================================================================
// groebner-basis verifier — TypeScript lockstep port of verify.py
// =============================================================================
//
// stdin:
//   { input:     { polys: string[], vars: string[], order: "lex"|"degrevlex" },
//     candidate: { kind:"ok", basis: string[], order, vars, n_pairs, warnings }
//               | { kind:"tagged", tag, payload },
//     id?:       string,
//     expected?: { kind?: "ok"|"tagged", tag?: string } }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } },
//     wall_clock_seconds: number }
//
// This is the TypeScript LOCKSTEP verifier — it duplicates the four
// invariants from verify.py with senior-grade BigInt rational arithmetic
// for multivariate polynomials.  It is NOT the canonical verifier; the
// manifest's `verifier.cmd` stays python3.  Purpose: catch future
// SymPy/TS divergence on committed cases.  generate.py runs this on
// every committed case during golden refresh, halting on disagreement.
//
// The four invariants (mirroring verify.py line-by-line):
//
//   1. shape — kind/basis/order/vars structural check; each basis
//      string parses as a polynomial in the declared vars.
//   2. ideal_containment_input_to_candidate — every input polynomial
//      reduces to 0 mod the candidate basis under the requested order.
//   3. ideal_containment_candidate_to_input — every candidate polynomial
//      reduces to 0 mod a Gröbner basis of the input.  We can't compute
//      a GB ourselves in pure TS without re-implementing Buchberger;
//      instead we use the semantic-equivalence: if (2) holds AND every
//      S-pair of the candidate reduces to 0, then by Buchberger's
//      theorem the candidate IS a GB of <candidate>; if also <input> ⊆
//      <candidate>, then <input> = <candidate> iff every input poly
//      generates an element of <candidate>.  This is what (2) checks.
//      So in TS we approximate (3) as follows: confirm <input> ⊆
//      <candidate>; if the candidate basis is a Gröbner basis of itself
//      (via S-pair check (4)), then to verify <candidate> ⊆ <input> it
//      suffices to check that every candidate polynomial is a polynomial
//      combination of the inputs — but we DON'T have a way to test that
//      directly without computing a GB of the inputs.  So we DELEGATE
//      this invariant check: TS verify.ts ALWAYS marks (3) as
//      "delegated to verify.py" — `pass: true, detail: "delegated to
//      python lockstep"` — and the disagreement protocol relies on
//      (1), (2), and (4) catching divergences.  This is a documented
//      asymmetry: (3) is a Python-only check, with the lockstep
//      protocol restricted to (1), (2), (4).
//
//   4. s_pair_reduces_to_zero — for every pair (g_i, g_j) in the
//      candidate basis, S(g_i, g_j) reduces to 0 mod candidate under
//      the requested order.  CLO Ch.2 §6 Definition 4 p.84.
//
// Determinism: pure BigInt rational arithmetic everywhere.  Bit-identical
// cross-platform forever (default symbolic tier).
//
// Algorithm citation references in comments cite verify.py line numbers
// (after revision) for cross-reference.

import { readFileSync } from "node:fs";

// =============================================================================
// BigInt rational arithmetic
// =============================================================================
//
// Standard reduced-form rational, denominator strictly positive, in lowest
// terms.

type Rat = { n: bigint; d: bigint };

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

const ZERO_R: Rat = { n: 0n, d: 1n };
const ONE_R:  Rat = { n: 1n, d: 1n };

function ratFromInt(n: bigint): Rat { return { n, d: 1n }; }
function ratAdd(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.d + b.n * a.d, a.d * b.d); }
function ratSub(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.d - b.n * a.d, a.d * b.d); }
function ratMul(a: Rat, b: Rat): Rat { return ratNorm(a.n * b.n, a.d * b.d); }
function ratNeg(a: Rat): Rat { return { n: -a.n, d: a.d }; }
function ratInv(a: Rat): Rat {
  if (a.n === 0n) throw new Error("ratInv: zero");
  return a.n < 0n ? { n: -a.d, d: -a.n } : { n: a.d, d: a.n };
}
function ratIsZero(a: Rat): boolean { return a.n === 0n; }
function ratEq(a: Rat, b: Rat): boolean { return a.n === b.n && a.d === b.d; }
function ratIsOne(a: Rat): boolean { return a.n === 1n && a.d === 1n; }

// =============================================================================
// Multivariate polynomial — sparse map from monomial-key (string) to Rat
// =============================================================================
//
// A monomial is an exponent vector (number[] of length n_vars).  We key
// the polynomial by a stable-sortable string form `e0,e1,...,en-1`.  The
// numeric exponent vector is recovered by parsing the key.  This is the
// SymPy-rings-style sparse representation.

type Mono = number[];
type Poly = Map<string, Rat>;     // mono-key → coef

function monoKey(m: Mono): string { return m.join(","); }
function monoFromKey(k: string): Mono {
  return k.split(",").map((s) => parseInt(s, 10));
}

function polyZero(): Poly { return new Map(); }

function polyAdd(a: Poly, b: Poly): Poly {
  const out = new Map(a);
  for (const [k, c] of b) {
    const prev = out.get(k) ?? ZERO_R;
    const s = ratAdd(prev, c);
    if (ratIsZero(s)) out.delete(k); else out.set(k, s);
  }
  return out;
}

function polySub(a: Poly, b: Poly): Poly {
  const out = new Map(a);
  for (const [k, c] of b) {
    const prev = out.get(k) ?? ZERO_R;
    const s = ratSub(prev, c);
    if (ratIsZero(s)) out.delete(k); else out.set(k, s);
  }
  return out;
}

function polyScale(p: Poly, c: Rat): Poly {
  if (ratIsZero(c)) return polyZero();
  const out: Poly = new Map();
  for (const [k, v] of p) {
    const s = ratMul(v, c);
    if (!ratIsZero(s)) out.set(k, s);
  }
  return out;
}

/** Multiply polynomial by a single (monomial, coef) term. */
function polyMulTerm(p: Poly, mono: Mono, coef: Rat): Poly {
  if (ratIsZero(coef)) return polyZero();
  const out: Poly = new Map();
  for (const [k, c] of p) {
    const e = monoFromKey(k);
    const ne = e.map((v, i) => v + mono[i]!);
    const nc = ratMul(c, coef);
    const nk = monoKey(ne);
    out.set(nk, nc);
  }
  return out;
}

function polyEq(a: Poly, b: Poly): boolean {
  if (a.size !== b.size) return false;
  for (const [k, v] of a) {
    const w = b.get(k);
    if (w === undefined) return false;
    if (!ratEq(v, w)) return false;
  }
  return true;
}

function polyIsZero(p: Poly): boolean { return p.size === 0; }

// =============================================================================
// Monomial orders: lex and degrevlex
// =============================================================================
//
// Comparator returns positive iff a > b under the order.
//
// lex: compare exponent vectors lexicographically (first differing index).
//   x > y > z gives x^a · y^b · z^c.  Variables ordered as in the `vars`
//   list — first var is "biggest".
//
// degrevlex (DRL): compare by total degree first; on tie, the LAST
// differing index wins, with the SMALLER exponent meaning bigger
// monomial.  CLO Ch.2 §2 Definition 6 p.58.

type Order = "lex" | "degrevlex";

function monoCmp(a: Mono, b: Mono, order: Order): number {
  if (order === "lex") {
    for (let i = 0; i < a.length; i++) {
      if (a[i]! !== b[i]!) return a[i]! - b[i]!;
    }
    return 0;
  }
  // degrevlex
  const da = a.reduce((s, v) => s + v, 0);
  const db = b.reduce((s, v) => s + v, 0);
  if (da !== db) return da - db;
  // Walk from the last index backwards; smaller exponent at the last
  // differing index ⇒ greater monomial.  CLO Ch.2 §2 p.58.
  for (let i = a.length - 1; i >= 0; i--) {
    if (a[i]! !== b[i]!) return b[i]! - a[i]!;
  }
  return 0;
}

/** Leading monomial (key + exponent vector) and leading coefficient
 *  under the supplied order.  Returns null for the zero polynomial. */
function leadTerm(p: Poly, order: Order): { mono: Mono; coef: Rat; key: string } | null {
  if (p.size === 0) return null;
  let bestKey: string | null = null;
  let bestMono: Mono | null = null;
  for (const k of p.keys()) {
    const m = monoFromKey(k);
    if (bestMono === null || monoCmp(m, bestMono, order) > 0) {
      bestMono = m;
      bestKey = k;
    }
  }
  return { mono: bestMono!, coef: p.get(bestKey!)!, key: bestKey! };
}

/** Element-wise max of two exponent vectors — the LCM of the
 *  corresponding monomials. */
function monoLcm(a: Mono, b: Mono): Mono {
  return a.map((v, i) => Math.max(v, b[i]!));
}

/** True iff every entry of `b` is ≥ the corresponding entry of `a`
 *  (i.e., monomial `a` divides monomial `b`). */
function monoDivides(a: Mono, b: Mono): boolean {
  for (let i = 0; i < a.length; i++) {
    if (a[i]! > b[i]!) return false;
  }
  return true;
}

/** Subtract exponent vectors: result[i] = a[i] - b[i].  Caller must
 *  ensure b divides a (no negative exponents). */
function monoSub(a: Mono, b: Mono): Mono {
  return a.map((v, i) => v - b[i]!);
}

// =============================================================================
// Multivariate polynomial division — CLO Ch.2 §3 Theorem 3 p.64-66
// =============================================================================
//
// Given polynomial f and divisor list G = [g_1, ..., g_s], compute the
// remainder r such that f = q_1 g_1 + ... + q_s g_s + r, where every
// term of r is NOT divisible by any LM(g_i).  This is the multivariate
// generalisation of polynomial long division.
//
// Algorithm (verbatim from CLO §3 with the standard tweak that we
// re-find the leading term of `p` after every step rather than tracking
// a "current term" pointer):
//
//   r ← 0
//   p ← f
//   while p ≠ 0:
//     find some i such that LM(g_i) divides LM(p)
//     if found:
//       p ← p − (LT(p) / LT(g_i)) · g_i
//     else:
//       move LT(p) to r (subtract from p, add to r)
//
// Returns r.  We discard the quotients (they are not needed for
// invariant checks).

function polyDivRem(f: Poly, G: Poly[], order: Order): Poly {
  let p: Poly = new Map(f);
  let r: Poly = polyZero();
  while (p.size > 0) {
    const lt = leadTerm(p, order)!;
    let reduced = false;
    for (const g of G) {
      if (g.size === 0) continue;
      const ltG = leadTerm(g, order)!;
      if (monoDivides(ltG.mono, lt.mono)) {
        // Subtract (LT(p) / LT(g)) · g from p.
        const quotMono = monoSub(lt.mono, ltG.mono);
        const quotCoef = ratMul(lt.coef, ratInv(ltG.coef));
        const sub = polyMulTerm(g, quotMono, quotCoef);
        p = polySub(p, sub);
        reduced = true;
        break;
      }
    }
    if (!reduced) {
      // Move LT(p) into r and remove from p.
      const ltm = leadTerm(p, order)!;
      r.set(ltm.key, ltm.coef);
      p.delete(ltm.key);
    }
  }
  return r;
}

// =============================================================================
// S-polynomial — CLO Ch.2 §6 Definition 4 p.84
// =============================================================================
//
// S(f, g) = (lcm/LM(f))/LC(f) · f − (lcm/LM(g))/LC(g) · g
//
// where lcm is the monomial LCM of the leading monomials.  The
// resulting polynomial has the leading-coefficient cancellation
// property — its leading monomial is strictly smaller than lcm(LM(f),
// LM(g)).

function sPolynomial(f: Poly, g: Poly, order: Order): Poly {
  const ltF = leadTerm(f, order);
  const ltG = leadTerm(g, order);
  if (ltF === null || ltG === null) {
    return polyZero();
  }
  const lcm = monoLcm(ltF.mono, ltG.mono);
  const fQuot = monoSub(lcm, ltF.mono);
  const gQuot = monoSub(lcm, ltG.mono);
  const fCoef = ratInv(ltF.coef);
  const gCoef = ratInv(ltG.coef);
  const fTerm = polyMulTerm(f, fQuot, fCoef);
  const gTerm = polyMulTerm(g, gQuot, gCoef);
  return polySub(fTerm, gTerm);
}

// =============================================================================
// Polynomial parser — bench input vocabulary
// =============================================================================
//
// Parses an expression like `x**2 + (2/3)*x*y + 1` into a Poly under a
// given variable list.  Supports + - * / ^ ** and rational literals
// `n/d`.  Foreign symbols and non-polynomial heads (sin, log, etc.)
// trigger `null`.

type Tok =
  | { t: "num";   v: bigint }
  | { t: "ident"; v: string }
  | { t: "op";    v: string }
  | { t: "lp" } | { t: "rp" } | { t: "comma" }
  | { t: "eof" };

function tokenize(s: string): Tok[] | null {
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
    return null;
  }
  out.push({ t: "eof" });
  return out;
}

interface PS { toks: Tok[]; i: number; vars: string[]; nonPoly: boolean }

function pk(st: PS): Tok { return st.toks[st.i]!; }
function eat(st: PS): Tok { return st.toks[st.i++]!; }

function constPoly(v: Rat, nVars: number): Poly {
  const m = polyZero();
  if (!ratIsZero(v)) {
    const k = monoKey(new Array(nVars).fill(0));
    m.set(k, v);
  }
  return m;
}

function varPoly(name: string, vars: string[]): Poly | null {
  const idx = vars.indexOf(name);
  if (idx < 0) return null;
  const exp = new Array(vars.length).fill(0);
  exp[idx] = 1;
  const m = polyZero();
  m.set(monoKey(exp), ONE_R);
  return m;
}

function parsePolyMain(st: PS): Poly | null {
  let lhs = parsePolyTerm(st);
  if (lhs === null || st.nonPoly) return null;
  while (pk(st).t === "op" &&
        ((pk(st) as { t: "op"; v: string }).v === "+" || (pk(st) as { t: "op"; v: string }).v === "-")) {
    const op = (eat(st) as { t: "op"; v: string }).v;
    const rhs = parsePolyTerm(st);
    if (rhs === null || st.nonPoly) return null;
    lhs = op === "+" ? polyAdd(lhs, rhs) : polySub(lhs, rhs);
  }
  return lhs;
}

function parsePolyTerm(st: PS): Poly | null {
  let lhs = parsePolyUnary(st);
  if (lhs === null || st.nonPoly) return null;
  while (pk(st).t === "op" &&
        ((pk(st) as { t: "op"; v: string }).v === "*" || (pk(st) as { t: "op"; v: string }).v === "/")) {
    const op = (eat(st) as { t: "op"; v: string }).v;
    const rhs = parsePolyUnary(st);
    if (rhs === null || st.nonPoly) return null;
    if (op === "*") {
      lhs = polyMulPoly(lhs, rhs);
    } else {
      // Division: rhs must be a non-zero constant polynomial.
      if (!isConstPoly(rhs)) { st.nonPoly = true; return null; }
      const c = constValue(rhs);
      if (ratIsZero(c)) { st.nonPoly = true; return null; }
      lhs = polyScale(lhs, ratInv(c));
    }
  }
  return lhs;
}

function isConstPoly(p: Poly): boolean {
  if (p.size === 0) return true;
  if (p.size > 1) return false;
  const k = p.keys().next().value as string;
  return monoFromKey(k).every((e) => e === 0);
}

function constValue(p: Poly): Rat {
  if (p.size === 0) return ZERO_R;
  return p.values().next().value as Rat;
}

function polyMulPoly(a: Poly, b: Poly): Poly {
  const out: Poly = new Map();
  for (const [ka, ca] of a) {
    const ea = monoFromKey(ka);
    for (const [kb, cb] of b) {
      const eb = monoFromKey(kb);
      const ne = ea.map((v, i) => v + eb[i]!);
      const nc = ratMul(ca, cb);
      const nk = monoKey(ne);
      const prev = out.get(nk) ?? ZERO_R;
      const s = ratAdd(prev, nc);
      if (ratIsZero(s)) out.delete(nk); else out.set(nk, s);
    }
  }
  return out;
}

function parsePolyUnary(st: PS): Poly | null {
  if (pk(st).t === "op" && (pk(st) as { t: "op"; v: string }).v === "-") {
    eat(st);
    const inner = parsePolyUnary(st);
    if (inner === null || st.nonPoly) return null;
    return polyScale(inner, { n: -1n, d: 1n });
  }
  if (pk(st).t === "op" && (pk(st) as { t: "op"; v: string }).v === "+") {
    eat(st);
    return parsePolyUnary(st);
  }
  return parsePolyPower(st);
}

function parsePolyPower(st: PS): Poly | null {
  const base = parsePolyAtom(st);
  if (base === null || st.nonPoly) return null;
  if (pk(st).t === "op" &&
     ((pk(st) as { t: "op"; v: string }).v === "**" || (pk(st) as { t: "op"; v: string }).v === "^")) {
    eat(st);
    // Exponent must be a non-negative integer literal (possibly
    // unary-negated → non-polynomial).
    if (pk(st).t === "op" && (pk(st) as { t: "op"; v: string }).v === "-") {
      st.nonPoly = true; return null;
    }
    const expT = eat(st);
    if (expT.t !== "num") { st.nonPoly = true; return null; }
    const n = Number((expT as { t: "num"; v: bigint }).v);
    if (!Number.isInteger(n) || n < 0) { st.nonPoly = true; return null; }
    let r = constPoly(ONE_R, st.vars.length);
    for (let i = 0; i < n; i++) r = polyMulPoly(r, base);
    return r;
  }
  return base;
}

function parsePolyAtom(st: PS): Poly | null {
  const t = pk(st);
  if (t.t === "num") {
    eat(st);
    return constPoly({ n: (t as { t: "num"; v: bigint }).v, d: 1n }, st.vars.length);
  }
  if (t.t === "ident") {
    eat(st);
    const name = (t as { t: "ident"; v: string }).v;
    const vp = varPoly(name, st.vars);
    if (vp !== null) return vp;
    if (pk(st).t === "lp") {
      // Function call — non-polynomial.
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
    // Foreign free variable.
    st.nonPoly = true;
    return null;
  }
  if (t.t === "lp") {
    eat(st);
    const v = parsePolyMain(st);
    if (v === null || st.nonPoly) return null;
    if (pk(st).t !== "rp") { st.nonPoly = true; return null; }
    eat(st);
    return v;
  }
  return null;
}

function parsePoly(src: string, vars: string[]): Poly | null {
  const toks = tokenize(src);
  if (toks === null) return null;
  const st: PS = { toks, i: 0, vars, nonPoly: false };
  const p = parsePolyMain(st);
  if (p === null || st.nonPoly) return null;
  if (pk(st).t !== "eof") return null;
  return p;
}

// =============================================================================
// Top-level verifier
// =============================================================================

type Check = { pass: boolean; detail: string };

interface CaseEnvelope {
  id?: string;
  input: { polys: string[]; vars: string[]; order: string };
  candidate: any;
  expected?: any;
}

const VALID_REFUSAL_CLASSES = new Set([
  "groebner-basis/empty-input",
  "groebner-basis/empty-vars",
  "groebner-basis/parametric",
  "groebner-basis/non-polynomial",
]);

const LARGE_BASIS_THRESHOLD = 20;
const SAMPLE_PAIR_COUNT = 50;

function verify(env: CaseEnvelope): {
  pass: boolean;
  reason: string;
  checks: Record<string, Check>;
  wall_clock_seconds: number;
} {
  const t0 = performance.now();
  const inp = env.input;
  const cand = env.candidate;
  const expected = env.expected ?? {};
  const caseId = env.id ?? "";

  const checks: Record<string, Check> = {};
  const candKind = cand?.kind;
  const expectedKind = expected?.kind;

  if (expectedKind && candKind !== expectedKind) {
    checks["shape"] = {
      pass: false,
      detail: `candidate.kind=${JSON.stringify(candKind)} but expected.kind=${JSON.stringify(expectedKind)}`,
    };
    for (const k of [
      "ideal_containment_input_to_candidate",
      "ideal_containment_candidate_to_input",
      "s_pair_reduces_to_zero",
      "tag_matches",
    ]) {
      checks[k] = { pass: false, detail: "skipped: shape failed at kind mismatch" };
    }
    return aggregate(checks, t0);
  }

  if (candKind === "tagged") {
    checks["shape"] = checkShapeTagged(cand);
    checks["tag_matches"] = checkTagMatches(cand, expected);
    for (const k of [
      "ideal_containment_input_to_candidate",
      "ideal_containment_candidate_to_input",
      "s_pair_reduces_to_zero",
    ]) {
      checks[k] = { pass: true, detail: "n/a for kind=tagged" };
    }
    return aggregate(checks, t0);
  }

  // Happy-path lane.
  const shape = checkShapeOk(inp, cand);
  checks["shape"] = shape;
  if (!shape.pass) {
    for (const k of [
      "ideal_containment_input_to_candidate",
      "ideal_containment_candidate_to_input",
      "s_pair_reduces_to_zero",
    ]) {
      checks[k] = { pass: false, detail: "skipped: shape failed" };
    }
    checks["tag_matches"] = { pass: true, detail: "n/a for kind=ok" };
    return aggregate(checks, t0);
  }

  const order = inp.order as Order;
  const vars = inp.vars;

  // Parse polynomials via the BigInt-rational parser.
  const inputPolys: Poly[] = [];
  const candPolys: Poly[] = [];
  for (let i = 0; i < inp.polys.length; i++) {
    const p = parsePoly(inp.polys[i]!, vars);
    if (p === null) {
      checks["shape"] = {
        pass: false,
        detail: `input[${i}]=${JSON.stringify(inp.polys[i])} fails parse in TS lockstep`,
      };
      for (const k of [
        "ideal_containment_input_to_candidate",
        "ideal_containment_candidate_to_input",
        "s_pair_reduces_to_zero",
      ]) {
        checks[k] = { pass: false, detail: "skipped: parse failed" };
      }
      checks["tag_matches"] = { pass: true, detail: "n/a for kind=ok" };
      return aggregate(checks, t0);
    }
    if (!polyIsZero(p)) inputPolys.push(p);
  }
  for (let i = 0; i < cand.basis.length; i++) {
    const p = parsePoly(cand.basis[i]!, vars);
    if (p === null) {
      checks["shape"] = {
        pass: false,
        detail: `basis[${i}]=${JSON.stringify(cand.basis[i])} fails parse in TS lockstep`,
      };
      for (const k of [
        "ideal_containment_input_to_candidate",
        "ideal_containment_candidate_to_input",
        "s_pair_reduces_to_zero",
      ]) {
        checks[k] = { pass: false, detail: "skipped: parse failed" };
      }
      checks["tag_matches"] = { pass: true, detail: "n/a for kind=ok" };
      return aggregate(checks, t0);
    }
    if (!polyIsZero(p)) candPolys.push(p);
  }

  // Empty-basis edge cases mirror verify.py.
  if (candPolys.length === 0) {
    if (inputPolys.length === 0) {
      checks["ideal_containment_input_to_candidate"] = { pass: true, detail: "vacuous: both ideals are (0)" };
      checks["ideal_containment_candidate_to_input"] = { pass: true, detail: "vacuous: both ideals are (0)" };
      checks["s_pair_reduces_to_zero"] = { pass: true, detail: "vacuous: candidate is empty" };
    } else {
      checks["ideal_containment_input_to_candidate"] = {
        pass: false,
        detail: "candidate basis is empty but input ideal is non-zero",
      };
      checks["ideal_containment_candidate_to_input"] = { pass: true, detail: "vacuous: candidate is empty" };
      checks["s_pair_reduces_to_zero"] = { pass: true, detail: "vacuous: candidate is empty" };
    }
    checks["tag_matches"] = { pass: true, detail: "n/a for kind=ok" };
    return aggregate(checks, t0);
  }

  // Invariant 2: every input polynomial reduces to 0 mod candidate.
  // verify.py:301-318 (PolyElement.div delegated; we re-implement
  // multivariate division via polyDivRem).
  checks["ideal_containment_input_to_candidate"] = checkInputInCand(inputPolys, candPolys, order);

  // Invariant 3: delegated to verify.py.  See the file header for
  // why TS doesn't replicate this — it requires computing a Gröbner
  // basis of the input, which is the non-trivial algorithm we'd be
  // verifying.  Lockstep agreement on (1), (2), and (4) is the
  // intended scope of TS verify.ts.
  checks["ideal_containment_candidate_to_input"] = {
    pass: true,
    detail: "delegated to verify.py (Buchberger needed to compute reference GB; out of TS scope)",
  };

  // Invariant 4: every S-pair reduces to 0 mod candidate.
  checks["s_pair_reduces_to_zero"] = checkSPairs(candPolys, order, caseId);

  checks["tag_matches"] = { pass: true, detail: "n/a for kind=ok" };
  return aggregate(checks, t0);
}

function aggregate(
  checks: Record<string, Check>,
  t0: number,
): { pass: boolean; reason: string; checks: Record<string, Check>; wall_clock_seconds: number } {
  const failed = Object.entries(checks).filter(([, v]) => !v.pass).map(([k]) => k);
  return {
    pass: failed.length === 0,
    reason: failed.length === 0 ? "all invariants hold" : `failed: ${failed.join(", ")}`,
    checks,
    wall_clock_seconds: Number(((performance.now() - t0) / 1000).toFixed(4)),
  };
}

function checkShapeOk(inp: any, cand: any): Check {
  if (cand.kind !== "ok") {
    return { pass: false, detail: `candidate.kind=${JSON.stringify(cand.kind)}, expected 'ok'` };
  }
  if (!Array.isArray(cand.basis)) return { pass: false, detail: "candidate.basis is not a list" };
  if (cand.order !== "lex" && cand.order !== "degrevlex") {
    return { pass: false, detail: `candidate.order=${JSON.stringify(cand.order)} not in {lex, degrevlex}` };
  }
  if (cand.order !== inp.order) {
    return { pass: false, detail: `candidate.order=${JSON.stringify(cand.order)} differs from input.order=${JSON.stringify(inp.order)}` };
  }
  if (JSON.stringify(cand.vars) !== JSON.stringify(inp.vars)) {
    return { pass: false, detail: "candidate.vars does not match input.vars" };
  }
  for (let i = 0; i < cand.basis.length; i++) {
    if (typeof cand.basis[i] !== "string") {
      return { pass: false, detail: `basis[${i}] is not a string` };
    }
  }
  return { pass: true, detail: `|basis|=${cand.basis.length}, order=${JSON.stringify(cand.order)}` };
}

function checkShapeTagged(cand: any): Check {
  const tag = cand.tag;
  if (typeof tag !== "string") return { pass: false, detail: "candidate.tag is not a string" };
  if (!tag.startsWith("groebner-basis/")) {
    return { pass: false, detail: `candidate.tag=${JSON.stringify(tag)} not in groebner-basis/ namespace` };
  }
  if (!VALID_REFUSAL_CLASSES.has(tag)) {
    return { pass: false, detail: `candidate.tag=${JSON.stringify(tag)} not a declared refusal class` };
  }
  if (typeof cand.payload !== "object" || cand.payload === null || Array.isArray(cand.payload)) {
    return { pass: false, detail: "candidate.payload is not a record" };
  }
  return { pass: true, detail: `tag=${JSON.stringify(tag)}` };
}

function checkTagMatches(cand: any, expected: any): Check {
  const candTag = cand.tag;
  const expTag = expected?.tag;
  if (!expTag) return { pass: true, detail: `refused with tag=${JSON.stringify(candTag)} (no expected pinned)` };
  if (candTag !== expTag) {
    return {
      pass: false,
      detail: `candidate.tag=${JSON.stringify(candTag)} != expected.tag=${JSON.stringify(expTag)}`,
    };
  }
  return { pass: true, detail: `tag=${JSON.stringify(candTag)} matches expected` };
}

function polyToShortStr(p: Poly): string {
  const parts: string[] = [];
  let count = 0;
  for (const [k, c] of p) {
    if (count++ > 4) { parts.push("..."); break; }
    const e = monoFromKey(k);
    const cs = c.d === 1n ? `${c.n}` : `${c.n}/${c.d}`;
    const ms = e.map((v, i) => v === 0 ? null : (v === 1 ? `v${i}` : `v${i}^${v}`)).filter(Boolean).join("·");
    parts.push(ms ? `${cs}·${ms}` : cs);
  }
  return parts.join(" + ") || "0";
}

function checkInputInCand(input: Poly[], cand: Poly[], order: Order): Check {
  const misses: string[] = [];
  for (let i = 0; i < input.length; i++) {
    const r = polyDivRem(input[i]!, cand, order);
    if (!polyIsZero(r)) {
      misses.push(`input[${i}] reduces to non-zero ${polyToShortStr(r)}`);
    }
  }
  if (misses.length > 0) {
    const trail = misses.length > 3 ? ` (+${misses.length - 3} more)` : "";
    return { pass: false, detail: misses.slice(0, 3).join("; ") + trail };
  }
  return { pass: true, detail: `every input polynomial (${input.length}) ∈ ⟨candidate⟩` };
}

// FNV-1a 32-bit hash for deterministic seeding.
function fnv1a(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

// Mulberry32 for deterministic sample selection.
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function checkSPairs(cand: Poly[], order: Order, caseId: string): Check {
  const n = cand.length;
  if (n < 2) return { pass: true, detail: `trivially: |basis|=${n} < 2 (no pairs to check)` };

  const allPairs: [number, number][] = [];
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) allPairs.push([i, j]);

  let pairs: [number, number][];
  let regime: string;
  if (n <= LARGE_BASIS_THRESHOLD) {
    pairs = allPairs;
    regime = `all ${allPairs.length} pairs`;
  } else {
    const seed = fnv1a(caseId || "no-id");
    const rng = mulberry32(seed);
    const sampleSize = Math.min(SAMPLE_PAIR_COUNT, allPairs.length);
    // Fisher-Yates partial shuffle.
    const arr = [...allPairs];
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [arr[i], arr[j]] = [arr[j]!, arr[i]!];
    }
    pairs = arr.slice(0, sampleSize);
    regime = `sample of ${sampleSize}/${allPairs.length} pairs (seeded RNG, case-id keyed)`;
  }

  const bad: string[] = [];
  for (const [i, j] of pairs) {
    const s = sPolynomial(cand[i]!, cand[j]!, order);
    const r = polyDivRem(s, cand, order);
    if (!polyIsZero(r)) {
      bad.push(`(${i},${j}): S-pair reduces to non-zero ${polyToShortStr(r)}`);
    }
  }
  if (bad.length > 0) {
    const trail = bad.length > 3 ? ` (+${bad.length - 3} more)` : "";
    return {
      pass: false,
      detail: `checked ${regime}; failures: ${bad.slice(0, 3).join("; ")}${trail}`,
    };
  }
  return { pass: true, detail: `checked ${regime}; all reduce to 0 mod candidate` };
}

// =============================================================================
// CLI
// =============================================================================

const raw = readFileSync(0, "utf8");
const env = JSON.parse(raw) as CaseEnvelope;
const result = verify(env);
process.stdout.write(JSON.stringify(result, null, 2) + "\n");
process.exit(result.pass ? 0 : 1);
