// =============================================================================
// linsolve-q verifier — 8-check invariant verifier (TypeScript port).
// =============================================================================
//
// stdin:
//   { input:     { A: string[][], b: string[] },
//     candidate: { kind:"unique"|"under-determined"|"inconsistent", x?, free_vars?,
//                  rank, augmented_rank?, method, warnings },
//     id?:       string }
//
// stdout:
//   { pass: bool, reason: str, checks: { <name>: { pass, detail } } }
//
// This is the TypeScript port of verify.py per ADR-0028 §4.
// Tolerances: NONE — all checks are exact-rational equality or rank-equality.
// Preserved byte-for-byte with verify.py.  Do NOT tighten or loosen during
// migration.
//
// The verifier implements exact-rational arithmetic inline using BigInt (the
// fractions.Fraction equivalent).  The SymPy rank oracle is reproduced via
// Gaussian elimination over ℚ (exact).
//
// ─── Checks ──────────────────────────────────────────────────────────────────
//
//   shape                            — structural: types, field presence,
//                                      rational parse, kind, rank bounds.
//   kind_rank_consistent             — kind ↔ rank structural invariant.
//   exact_satisfaction_unique        — A·x ≡ b in ℚ (unique lane only).
//   free_var_basis_underdetermined   — 10 random rational substitutions,
//                                      each satisfying A·x ≡ b (under-det only).
//   rank_consistent                  — candidate.rank == rank(A) by BigInt GE.
//   inconsistency_witness            — augmented_rank > rank(A) (inconsistent only).
//   free_var_count_correct           — |free_vars| == n − rank (under-det only).
//   bit_budget                       — max intermediate bit length self-report
//                                      against expected_max_bit_length (Tier H only).
//
// Default determinism tier (ADR-0015): bit-identical cross-platform forever.
// All arithmetic is BigInt — no FP anywhere.
//
// =============================================================================

import { readFileSync } from "node:fs";

// ─── Check type ───────────────────────────────────────────────────────────────

type Check = { pass: boolean; detail: string };

// =============================================================================
// BigInt rational arithmetic
// =============================================================================
//
// A rational is { n: bigint, d: bigint } with d > 0, in lowest terms.

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
function ratEqZero(a: Rat): boolean  { return a.n === 0n; }
function ratEq(a: Rat, b: Rat): boolean { return a.n === b.n && a.d === b.d; }

const RAT_ZERO: Rat = { n: 0n, d: 1n };

// ─── Parse a rational string ──────────────────────────────────────────────────
//
// Accepts "-?\d+(/\d+)?" (the canonical form emitted by verify.py's generator
// and the workbench tool).

const RAT_RE = /^-?\d+(?:\/\d+)?$/;

function parseRat(s: string): Rat {
  s = s.trim();
  if (!RAT_RE.test(s)) throw new Error(`not a canonical rational: ${JSON.stringify(s)}`);
  const slash = s.indexOf("/");
  if (slash === -1) {
    return { n: BigInt(s), d: 1n };
  }
  const n = BigInt(s.slice(0, slash));
  const d = BigInt(s.slice(slash + 1));
  return ratNorm(n, d);
}

// ─── Parse an affine expression string ───────────────────────────────────────
//
// Mirrors verify.py's parse_affine: parses strings like
//   "5 - 2*t_0 + 1/3*t_1"
// and evaluates at a substitution { t_0: Rat, t_1: Rat, ... }.
//
// The bench tool emits these strings through the run-candidate adapter, which
// produces patterns the verify.py SymPy parser accepts:
//
//   constant term only:             e.g. "3/2"
//   single free-var term:           e.g. "t_0"
//   sum of terms:                   e.g. "5 + (-2)*t_0 + 1/3*t_1"
//   "- coeff*var" form:             e.g. "5 - 2*t_0"
//
// Strategy: lightweight tokenise-and-evaluate so the parser mirrors what
// SymPy's sympify accepts.  We do NOT need to invert the expression — we only
// need to evaluate it at a concrete rational substitution.
//
// Tokens: number literals, identifiers, operators (+, -, *, /), parens.
// We parse a sum of signed terms, where each term is an optional coefficient
// (rational) times an optional free-variable symbol.
//
// This is strictly less general than verify.py's SymPy parse but is adequate
// for the exact expression forms the run-candidate emits.  Linearity is
// guaranteed structurally by the tool.

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
      // Check for /denominator immediately following (rational literal).
      if (i < s.length && s[i] === "/" && i + 1 < s.length && /\d/.test(s[i + 1]!)) {
        i++;
        let k = i;
        while (k < s.length && /\d/.test(s[k]!)) k++;
        const d = BigInt(s.slice(i, k));
        i = k;
        out.push({ t: "num", v: n, d });
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

/** Evaluate an affine expression string at a variable substitution.
 *  substitution maps free-variable names (e.g. "t_0") to Rat values.
 *  Throws if the expression contains unexpected structure.
 */
function evalAffine(exprStr: string, subst: Map<string, Rat>): Rat {
  const toks = tokenizeAffine(exprStr);
  let i = 0;

  function peek(): ATok { return toks[i]!; }
  function eat(): ATok  { return toks[i++]!; }

  function parseMulDiv(): Rat {
    let lhs = parseAtom();
    while (peek().t === "op" && ((peek() as {t:"op";v:string}).v === "*" || (peek() as {t:"op";v:string}).v === "/")) {
      const op = (eat() as {t:"op";v:string}).v;
      const rhs = parseAtom();
      if (op === "*") lhs = ratMul(lhs, rhs);
      else {
        if (ratEqZero(rhs)) throw new Error("division by zero in affine expression");
        lhs = ratMul(lhs, { n: rhs.d, d: rhs.n < 0n ? -rhs.n : rhs.n });
      }
    }
    return lhs;
  }

  function parseAtom(): Rat {
    const t = peek();
    if (t.t === "num") {
      eat();
      return ratNorm((t as {t:"num";v:bigint;d:bigint}).v, (t as {t:"num";v:bigint;d:bigint}).d);
    }
    if (t.t === "ident") {
      eat();
      const name = (t as {t:"ident";v:string}).v;
      const val = subst.get(name);
      if (val === undefined) {
        throw new Error(`evalAffine: unknown free variable ${JSON.stringify(name)} in ${JSON.stringify(exprStr)}`);
      }
      return val;
    }
    if (t.t === "lp") {
      eat();
      const inner = parseAddSub();
      if (peek().t !== "rp") throw new Error("evalAffine: expected )");
      eat();
      return inner;
    }
    throw new Error(`evalAffine: unexpected token ${JSON.stringify(t)} in ${JSON.stringify(exprStr)}`);
  }

  function parseAddSub(): Rat {
    let lhs = parseMulDiv();
    while (peek().t === "op" && ((peek() as {t:"op";v:string}).v === "+" || (peek() as {t:"op";v:string}).v === "-")) {
      const op = (eat() as {t:"op";v:string}).v;
      const rhs = parseMulDiv();
      lhs = op === "+" ? ratAdd(lhs, rhs) : ratSub(lhs, rhs);
    }
    return lhs;
  }

  // Some expressions start with unary minus: "-t_0", "- 2*t_0", etc.
  function parseTop(): Rat {
    if (peek().t === "op" && (peek() as {t:"op";v:string}).v === "-") {
      eat();
      return ratNorm(-parseMulDiv().n, parseMulDiv.length > 0 ? 1n : 1n);
    }
    return parseAddSub();
  }

  // Re-implement to handle leading unary minus correctly.
  function parseTopExpr(): Rat {
    if (peek().t === "op" && (peek() as {t:"op";v:string}).v === "-") {
      eat();
      const val = parseMulDiv();
      // Continue with additive terms.
      let lhs: Rat = ratNorm(-val.n, val.d);
      while (peek().t === "op" && ((peek() as {t:"op";v:string}).v === "+" || (peek() as {t:"op";v:string}).v === "-")) {
        const op = (eat() as {t:"op";v:string}).v;
        const rhs = parseMulDiv();
        lhs = op === "+" ? ratAdd(lhs, rhs) : ratSub(lhs, rhs);
      }
      return lhs;
    }
    return parseAddSub();
  }

  void parseTop;
  const result = parseTopExpr();
  if (peek().t !== "eof") {
    throw new Error(`evalAffine: trailing input in ${JSON.stringify(exprStr)}`);
  }
  return result;
}

// =============================================================================
// Integer-matrix rank via Bareiss fraction-free elimination
// =============================================================================
//
// Mirrors sympy.Matrix.rank() for our bench inputs.  We work with a BigInt
// integer matrix (clearing rational denominators per-row first) and apply the
// Bareiss one-step recurrence:
//
//   M[r][j] ← (M[pivotRow][pivotCol] · M[r][j]
//               − M[r][pivotCol]     · M[pivotRow][j])
//              / prev_pivot
//
// The division is exact in ℤ by Sylvester's identity, so intermediate values
// stay bounded by Hadamard's bound on submatrix determinants — no BigInt
// blow-up, even for dense 20×20 matrices with large integer entries.
//
// Rank is a function of the zero-pattern only, so clearing denominators
// (multiplying each row by its LCM of denominators) preserves rank.

function computeRankInteger(M_in: bigint[][]): number {
  const m = M_in.length;
  if (m === 0) return 0;
  const n = M_in[0]!.length;
  if (n === 0) return 0;

  // Deep copy.
  const M: bigint[][] = M_in.map((row) => [...row]);

  let rank = 0;
  let prevPivot = 1n;  // Bareiss sentinel: M^(-1)_{-1,-1} = 1
  let pivotRow = 0;

  for (let col = 0; col < n && pivotRow < m; col++) {
    // Find a non-zero pivot in this column.
    let found = -1;
    for (let r = pivotRow; r < m; r++) {
      if (M[r]![col] !== 0n) { found = r; break; }
    }
    if (found === -1) continue;

    // Swap pivot row into position.
    if (found !== pivotRow) {
      const tmp = M[found]!;
      M[found] = M[pivotRow]!;
      M[pivotRow] = tmp;
    }

    const pivotVal = M[pivotRow]![col]!;

    // Bareiss elimination below pivot.
    for (let r = pivotRow + 1; r < m; r++) {
      const factor = M[r]![col]!;
      for (let j = col + 1; j < n; j++) {
        // Exact integer division by prevPivot (guaranteed by Sylvester's identity).
        const num = pivotVal * M[r]![j]! - factor * M[pivotRow]![j]!;
        M[r]![j] = num / prevPivot;
      }
      M[r]![col] = 0n;
    }

    prevPivot = pivotVal;
    rank++;
    pivotRow++;
  }
  return rank;
}

/** Convert a rational matrix to a BigInt integer matrix by clearing per-row
 *  denominators (multiply each row by the LCM of its entry denominators).
 *  Rank-preserving: row scaling does not change the rank. */
function ratMatrixToInteger(A: Rat[][]): bigint[][] {
  return A.map((row) => {
    // Compute LCM of all denominators in this row.
    let lcm = 1n;
    for (const r of row) {
      const g = gcdBig(lcm, r.d);
      lcm = lcm / g * r.d;
    }
    // Multiply each entry by the row LCM: (n/d) * lcm = n * (lcm/d).
    return row.map((r) => r.n * (lcm / r.d));
  });
}

function computeRank(A: Rat[][]): number {
  const m = A.length;
  if (m === 0) return 0;
  if (A[0]!.length === 0) return 0;
  return computeRankInteger(ratMatrixToInteger(A));
}

/** Compute rank of the augmented matrix [A | b]. */
function computeAugmentedRank(A: Rat[][], b: Rat[]): number {
  const aug = A.map((row, i) => [...row, b[i]!]);
  return computeRank(aug);
}

// =============================================================================
// Check 1 — shape
// =============================================================================

function checkShape(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  const A = input_obj["A"];
  const b = input_obj["b"];
  if (!Array.isArray(A) || !Array.isArray(b)) {
    return { pass: false, detail: "A or b not a list" };
  }
  if (A.length > 0 && !A.every((row) => Array.isArray(row))) {
    return { pass: false, detail: "A is not list-of-lists" };
  }
  const m = A.length;
  const n = m > 0 ? (A[0] as unknown[]).length : 0;
  if (A.some((row) => (row as unknown[]).length !== n)) {
    return { pass: false, detail: "ragged A: row lengths differ" };
  }
  if ((b as unknown[]).length !== m) {
    return { pass: false, detail: `|b|=${(b as unknown[]).length} but |A|=${m}` };
  }
  // Parse all rational strings.
  try {
    for (const row of A as string[][]) for (const s of row) parseRat(s);
    for (const s of b as string[]) parseRat(s);
  } catch (e) {
    return { pass: false, detail: `unparseable rational: ${(e as Error).message}` };
  }

  const kind = candidate["kind"];
  if (!["unique", "under-determined", "inconsistent"].includes(kind as string)) {
    return { pass: false, detail: `invalid kind: ${JSON.stringify(kind)}` };
  }

  const rank = candidate["rank"];
  const minMN = m === 0 || n === 0 ? 0 : Math.min(m, n);
  if (typeof rank !== "number" || !Number.isInteger(rank) || rank < 0 || rank > minMN) {
    return { pass: false, detail: `invalid rank: ${JSON.stringify(rank)}` };
  }

  if (kind === "unique" || kind === "under-determined") {
    const x = candidate["x"];
    const free = candidate["free_vars"];
    if (!Array.isArray(x) || (x as unknown[]).length !== n) {
      return { pass: false, detail: `x must be length-${n} list; got ${JSON.stringify(x)?.slice(0, 60)}` };
    }
    if (!Array.isArray(free)) {
      return { pass: false, detail: `free_vars must be a list; got ${JSON.stringify(free)}` };
    }
    for (const fv of free as unknown[]) {
      if (typeof fv !== "string" || !/^t_\d+$/.test(fv)) {
        return { pass: false, detail: `free var must match 't_<int>': ${JSON.stringify(fv)}` };
      }
    }
  }

  if (kind === "inconsistent") {
    const ar = candidate["augmented_rank"];
    if (typeof ar !== "number" || !Number.isInteger(ar)) {
      return { pass: false, detail: "augmented_rank required for inconsistent" };
    }
  }

  return { pass: true, detail: "all structural fields valid" };
}

// =============================================================================
// Check 2 — kind_rank_consistent
// =============================================================================

function checkKindRankConsistent(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  const A = input_obj["A"] as string[][];
  const n = A.length > 0 ? (A[0] ?? []).length : 0;
  const kind = candidate["kind"] as string;
  const rank = candidate["rank"] as number;

  if (kind === "unique") {
    if (rank !== n) {
      return { pass: false, detail: `kind=unique requires rank=n; got rank=${rank}, n=${n}` };
    }
    return { pass: true, detail: `unique ⇒ rank=n=(${rank})` };
  }
  if (kind === "under-determined") {
    if (rank >= n) {
      return { pass: false, detail: `kind=under-determined requires rank<n; got rank=${rank}, n=${n}` };
    }
    return { pass: true, detail: `under-determined ⇒ rank<n (${rank}<${n})` };
  }
  if (kind === "inconsistent") {
    const ar = candidate["augmented_rank"] as number;
    if (ar <= rank) {
      return { pass: false, detail: `kind=inconsistent requires augmented_rank>rank; got augmented_rank=${ar}, rank=${rank}` };
    }
    return { pass: true, detail: `inconsistent ⇒ augmented_rank>rank (${ar}>${rank})` };
  }
  return { pass: false, detail: `unknown kind: ${kind}` };
}

// =============================================================================
// Check 3 — exact_satisfaction_unique
// =============================================================================

function checkExactSatisfactionUnique(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  if (candidate["kind"] !== "unique") {
    return { pass: true, detail: `n/a for kind=${candidate["kind"]}` };
  }
  const A = (input_obj["A"] as string[][]).map((row) => row.map(parseRat));
  const b = (input_obj["b"] as string[]).map(parseRat);
  const x = (candidate["x"] as string[]).map(parseRat);
  const m = A.length;
  const n = m > 0 ? A[0]!.length : 0;
  if (x.length !== n) {
    return { pass: false, detail: `|x|=${x.length} but |A_cols|=${n}` };
  }
  for (let i = 0; i < m; i++) {
    let s = RAT_ZERO;
    for (let j = 0; j < n; j++) {
      s = ratAdd(s, ratMul(A[i]![j]!, x[j]!));
    }
    const res = ratSub(s, b[i]!);
    if (!ratEqZero(res)) {
      return { pass: false, detail: `row ${i}: A[i]·x ≠ b[i] (residual ${res.n}/${res.d})` };
    }
  }
  return { pass: true, detail: `A·x ≡ b exactly across ${m} rows` };
}

// =============================================================================
// Check 4 — free_var_basis_underdetermined
// =============================================================================
//
// Mirrors verify.py's 10 deterministic substitutions from the pool:
//   {-3, -1, 0, 1, 2, 5/3, -7/4, 11, -19/4, 6/7}
// Trial t uses subst[i] = pool[(t + i) % pool.length] for free var i.

const SUBST_POOL: Rat[] = [
  { n: -3n,  d: 1n },
  { n: -1n,  d: 1n },
  { n:  0n,  d: 1n },
  { n:  1n,  d: 1n },
  { n:  2n,  d: 1n },
  ratNorm( 5n, 3n),
  ratNorm(-7n, 4n),
  { n:  11n, d: 1n },
  ratNorm(-19n, 4n),
  ratNorm(  6n, 7n),
];
const N_TRIALS = 10;

function checkFreeVarBasis(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  if (candidate["kind"] !== "under-determined") {
    return { pass: true, detail: `n/a for kind=${candidate["kind"]}` };
  }
  const A = (input_obj["A"] as string[][]).map((row) => row.map(parseRat));
  const b = (input_obj["b"] as string[]).map(parseRat);
  const freeVars = candidate["free_vars"] as string[];
  const xExprs   = candidate["x"] as string[];
  const m = A.length;
  const n = m > 0 ? A[0]!.length : 0;

  // Parse each x[j] as an affine expression (evaluated at substitution).
  for (let trial = 0; trial < N_TRIALS; trial++) {
    const subst = new Map<string, Rat>();
    freeVars.forEach((name, idx) => {
      subst.set(name, SUBST_POOL[(trial + idx) % SUBST_POOL.length]!);
    });

    // Evaluate each x[j] at this substitution.
    const x_concrete: Rat[] = [];
    for (let j = 0; j < n; j++) {
      let val: Rat;
      try {
        val = evalAffine(xExprs[j]!, subst);
      } catch (e) {
        return { pass: false, detail: `x[${j}] affine eval failed: ${(e as Error).message}` };
      }
      x_concrete.push(val);
    }

    // Check A·x_concrete == b exactly.
    for (let i = 0; i < m; i++) {
      let s = RAT_ZERO;
      for (let j = 0; j < n; j++) {
        s = ratAdd(s, ratMul(A[i]![j]!, x_concrete[j]!));
      }
      const res = ratSub(s, b[i]!);
      if (!ratEqZero(res)) {
        const substStr = JSON.stringify(Object.fromEntries([...subst.entries()].map(([k, v]) => [k, `${v.n}/${v.d}`])));
        return {
          pass: false,
          detail: `trial ${trial} (subst=${substStr}): row ${i} A·x=${s.n}/${s.d} ≠ b[i]=${b[i]!.n}/${b[i]!.d}`,
        };
      }
    }
  }
  return { pass: true, detail: `${N_TRIALS} random rational substitutions × ${freeVars.length} free vars all satisfy A·x ≡ b` };
}

// =============================================================================
// Check 5 — rank_consistent
// =============================================================================

function checkRankConsistent(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  const A = (input_obj["A"] as string[][]).map((row) => row.map(parseRat));
  const m = A.length;
  const sympyRank = m === 0 ? 0 : computeRank(A);
  if (candidate["rank"] !== sympyRank) {
    return { pass: false, detail: `candidate.rank=${candidate["rank"]} but computed rank(A)=${sympyRank}` };
  }
  return { pass: true, detail: `rank=${sympyRank} matches BigInt-GE oracle` };
}

// =============================================================================
// Check 6 — inconsistency_witness
// =============================================================================

function checkInconsistencyWitness(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  if (candidate["kind"] !== "inconsistent") {
    return { pass: true, detail: `n/a for kind=${candidate["kind"]}` };
  }
  const A = (input_obj["A"] as string[][]).map((row) => row.map(parseRat));
  const b = (input_obj["b"] as string[]).map(parseRat);
  if (A.length === 0) {
    return { pass: false, detail: "m=0 cannot be inconsistent" };
  }
  const rankA   = computeRank(A);
  const rankAug = computeAugmentedRank(A, b);
  if (rankAug <= rankA) {
    return { pass: false, detail: `Rouché-Capelli says CONSISTENT: rank(A)=${rankA}, rank([A|b])=${rankAug}` };
  }
  const ar = candidate["augmented_rank"] as number;
  if (ar !== rankAug) {
    return { pass: false, detail: `candidate.augmented_rank=${ar} but actual rank([A|b])=${rankAug}` };
  }
  return { pass: true, detail: `Rouché-Capelli witness: rank(A)=${rankA} < rank([A|b])=${rankAug}` };
}

// =============================================================================
// Check 7 — free_var_count_correct
// =============================================================================

function checkFreeVarCount(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  if (candidate["kind"] !== "under-determined") {
    return { pass: true, detail: `n/a for kind=${candidate["kind"]}` };
  }
  const A = input_obj["A"] as string[][];
  const n = A.length > 0 ? (A[0] ?? []).length : 0;
  const rank = candidate["rank"] as number;
  const expected = n - rank;
  const actual = ((candidate["free_vars"] as unknown[]) ?? []).length;
  if (actual !== expected) {
    return { pass: false, detail: `|free_vars|=${actual} but n-rank=${expected} (n=${n}, rank=${rank})` };
  }
  return { pass: true, detail: `|free_vars|=n-rank=${expected}` };
}

// =============================================================================
// Check 8 — bit_budget (Tier H only)
// =============================================================================

const BL_RE = /max intermediate bit length:\s*(\d+)/;

function checkBitBudget(
  input_obj: Record<string, unknown>,
  candidate: Record<string, unknown>,
): Check {
  const budget = input_obj["expected_max_bit_length"];
  if (budget === undefined || budget === null) {
    return { pass: true, detail: "n/a (no bit-budget on this tier)" };
  }
  const warnings = (candidate["warnings"] as string[]) ?? [];
  let matched: number | null = null;
  for (const w of warnings) {
    const m = BL_RE.exec(w);
    if (m) { matched = parseInt(m[1]!, 10); break; }
  }
  if (matched === null) {
    return {
      pass: false,
      detail: "warnings missing 'max intermediate bit length: <int>' self-report (required on Tier H)",
    };
  }
  if (matched > (budget as number)) {
    return {
      pass: false,
      detail: `max intermediate bit length ${matched} exceeds budget ${budget} — naive ℚ-Gaussian suspected`,
    };
  }
  return { pass: true, detail: `max intermediate bit length ${matched} ≤ budget ${budget}` };
}

// =============================================================================
// Aggregate
// =============================================================================

function wrap(
  checks: Record<string, Check>,
): { pass: boolean; reason: string; checks: Record<string, Check> } {
  const failed = Object.entries(checks).filter(([, c]) => !c.pass);
  if (failed.length === 0) return { pass: true, reason: "all invariants hold", checks };
  const reason = failed.map(([k, c]) => `${k}: ${c.detail}`).join("; ");
  return { pass: false, reason, checks };
}

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
  const inp  = payload.input;
  const cand = payload.candidate;

  if (typeof cand !== "object" || cand === null) {
    return { pass: false, reason: "candidate must be a JSON object", checks: {} };
  }

  const candR = cand as Record<string, unknown>;

  // Tool-error path: the bench does not expect tool_error for any golden case,
  // so if the tool emits one it is always a failure.
  if (candR["kind"] === "tool_error") {
    return {
      pass:   false,
      reason: `tool_error: ${JSON.stringify(candR["message"] ?? candR["name"] ?? "unknown")}`,
      checks: { shape: { pass: false, detail: "candidate emitted tool_error" } },
    };
  }

  const fns: Array<[string, (i: Record<string,unknown>, c: Record<string,unknown>) => Check]> = [
    ["shape",                          checkShape],
    ["kind_rank_consistent",           checkKindRankConsistent],
    ["exact_satisfaction_unique",      checkExactSatisfactionUnique],
    ["free_var_basis_underdetermined", checkFreeVarBasis],
    ["rank_consistent",                checkRankConsistent],
    ["inconsistency_witness",          checkInconsistencyWitness],
    ["free_var_count_correct",         checkFreeVarCount],
    ["bit_budget",                     checkBitBudget],
  ];

  const checks: Record<string, Check> = {};
  for (const [name, fn] of fns) {
    try {
      checks[name] = fn(inp, candR);
    } catch (e) {
      const err = e as Error;
      checks[name] = { pass: false, detail: `${err.constructor.name}: ${err.message}` };
    }
  }

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
      pass:   false,
      reason: `verifier crashed: ${err.constructor.name}: ${err.message}`,
      checks: {},
    };
  }
  process.stdout.write(JSON.stringify(result) + "\n");
}

await main();
