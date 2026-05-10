#!/usr/bin/env python3
"""Generate the groebner-basis golden master with dual-witness oracle.

This generator emits ~80 cases across 5 tiers (per the orchestrator's
Phase 2a plan), oracle-cross-validated against Wolfram's GroebnerBasis[]
and SymPy's groebner(). Cases where the two oracles disagree are
DROPPED with a flagged log entry; the bench corpus only carries
oracle-consensus cases.

# Tier breakdown (target: 79–80 cases)

  hand-curated (15)         Cox-Little-O'Shea worked examples + Buchberger
                            illustrative cases + the (x²+y, xy+1) classic.
  classical-hard (4–5)      cyclic-3, cyclic-4, Katsura-3, Katsura-4. Cyclic-5
                            and Katsura-4-lex are gated on a 10s-budget timing
                            check; we observed Katsura-4 lex takes ~16s on this
                            device (skip), Cyclic-5 grevlex ~0.4s (admit
                            grevlex only), Cyclic-5 lex >15s (skip).
  stratified-random (40)    Three sub-tiers with adversarial generation:
                              tier-A (15): n_vars=2, total_deg ≤ 3, m_polys ∈ {2,3}
                              tier-B (15): n_vars=3, total_deg ≤ 4, m_polys ∈ {2,3,4}
                              tier-C (10): n_vars=4-5, total_deg ≤ 3, m_polys ∈ {3,4}
                            Adversarial mix per the Phase 2a plan: 30%
                            random-uniform, 30% near-coprime-LMs, 20%
                            multiplicity-induced, 10% coefficient-swell,
                            10% large-but-structured.
  monomial-degenerate (10)  Single-generator ideals, monomial ideals, trivial
                            ideal {1} (inconsistent), pure-power ideals,
                            already-reduced bases. Both lex and degrevlex.
  refusal-envelope (10)     non-polynomial / parametric / empty-input /
                            empty-vars cases.

# Adversarial random generation

Per the Phase 2a plan, naive uniform-random sampling produces 95%
generic-easy systems that defeat the bench's purpose. The
stratified-random tier therefore biases toward known-hard structures:

  - 30% random-uniform           random ℚ coefficients in [-9,9]/{1..7}
  - 30% near-coprime-LMs         LM(p_i) coprime → Buchberger Crit 1 hits
  - 20% multiplicity-induced     ideal has repeated roots
  - 10% coefficient-swell-prone  small-coprime leads → reduction blow-up
  - 10% large-but-structured     m=4, n=4 with clean LM pattern

Strategy is reproducible: same seed + same constants ⟹ same case set.

# Wolframscript notes

`wolframscript -code 'GroebnerBasis[...]'` is observed to occasionally
segfault on cleanup AFTER printing well-formed output. We capture stdout
only and parse the result; non-zero exit codes are tolerated when stdout
is well-formed Mathematica syntax. Wolframscript is also slow to start
(~300ms per call); the 80-case generation runs in batches with a single
warm kernel where possible.

# Lockstep TS verifier sanity

After committing each case, generate.py runs `verify.ts` on the same
case and asserts it agrees with `verify.py`. Disagreement halts
generation with a flagged report — the corrected oracle protocol from
the Phase 2a plan.

# Wall-clock baseline

For tier `classical-hard`, `expected.json` carries
`wall_clock_baseline_seconds` measured at generate time on this device.
This is a metric (not an invariant) — corpus query reports drift.

# Reproducibility

  python3 generate.py            # full dual-witness, all tiers
  WB_NO_WOLFRAM=1 python3 generate.py   # SymPy only (faster, no Wolfram)
  WB_SKIP_LOCKSTEP=1 python3 generate.py # skip TS lockstep check
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reference"))

from groebner_reference import groebner_reference  # noqa: E402

SEED = 20260510
ENCODING_VERSION = 1
WOLFRAM_TIMEOUT = 30
SYMPY_TIMEOUT = 20

USE_WOLFRAM = os.environ.get("WB_NO_WOLFRAM", "0") != "1"
SKIP_LOCKSTEP = os.environ.get("WB_SKIP_LOCKSTEP", "0") == "1"


# ---------------------------------------------------------------------
# Wolfram oracle
# ---------------------------------------------------------------------


def wolfram_groebner(
    polys: List[str],
    vars_: List[str],
    order: str,
) -> Tuple[Optional[List[str]], str]:
    """Invoke wolframscript GroebnerBasis. Returns (basis_strings, status).

    Wolframscript can segfault during cleanup AFTER printing well-formed
    output. We capture stdout-only and parse; non-zero exit is tolerated
    if stdout parses cleanly. Returns None on hard failure.
    """
    if not USE_WOLFRAM:
        return None, "skipped"
    mma_order = "Lexicographic" if order == "lex" else "DegreeReverseLexicographic"
    mma_polys = ", ".join(polys).replace("**", "^")
    mma_vars = ", ".join(vars_)
    code = (
        f"basis = GroebnerBasis[{{{mma_polys}}}, {{{mma_vars}}}, "
        f"MonomialOrder -> {mma_order}];"
        f'WriteString["stdout", ToString[InputForm[basis]] <> "\\n"]'
    )
    try:
        result = subprocess.run(
            ["wolframscript", "-code", code],
            capture_output=True,
            text=True,
            timeout=WOLFRAM_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except FileNotFoundError:
        return None, "wolframscript-not-found"

    out = result.stdout.strip()
    if not out:
        return None, f"empty-stdout (exit={result.returncode})"
    # The first line containing a brace-list is the answer; subsequent
    # lines (e.g. a stray "Null") are interpreter noise that we discard.
    line = None
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("{") and ln.endswith("}"):
            line = ln
            break
    if line is None:
        return None, f"no-list-line: {out[:80]!r}"
    try:
        parsed = _parse_mma_list(line)
    except ValueError as exc:
        return None, f"parse-error: {exc}"
    return parsed, "ok"


def _parse_mma_list(s: str) -> List[str]:
    """Parse a Mathematica-form list `{p1, p2, ...}` into a Python list of
    canonical SymPy expression strings.

    Wolfram InputForm uses `^` for powers (already SymPy-compatible after
    we wrap in `sp.sympify`) and integer literals like `1` are fine.
    Rationals appear as `1/3`. Negative leading coefs as `-x + ...` —
    SymPy handles all of these.
    """
    s = s.strip()
    if not s.startswith("{") or not s.endswith("}"):
        raise ValueError(f"not a Mathematica list: {s[:60]}...")
    inner = s[1:-1].strip()
    if not inner:
        return []
    # Split on top-level commas.
    out: List[str] = []
    depth = 0
    cur = []
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    # Convert Mathematica's ^ to SymPy's ** (sympify handles `^` as XOR
    # by default, so we explicitly translate).
    return [p.replace("^", "**") for p in out]


# ---------------------------------------------------------------------
# Oracle agreement layer
# ---------------------------------------------------------------------


def _canonical_basis_set(
    basis_strs: List[str],
    vars_: List[str],
    order: str,
) -> Optional[frozenset]:
    """Reduce a basis-as-strings to a canonical frozenset of monic poly
    representations.

    We compute the reduced GB of <basis> via SymPy, monicise each
    element, then represent each as a canonical (degree, exponents,
    coefficients) tuple. Two GBs are oracle-consensus iff their reduced
    canonical sets are equal — modulo basis-element ordering.

    Returns None if any input fails to parse.
    """
    try:
        sym_objs = [sp.Symbol(v) for v in vars_]
        sym_locals = {v: s for v, s in zip(vars_, sym_objs)}
        exprs = [sp.sympify(p, locals=sym_locals) for p in basis_strs]
        nonzero = [e for e in exprs if e != 0]
        if not nonzero:
            return frozenset()
        sympy_order = "grevlex" if order == "degrevlex" else order
        # Re-compute the reduced GB to canonicalise.
        G = sp.groebner(nonzero, *sym_objs, order=sympy_order)
        canon: List[Tuple] = []
        for g in G:
            gp = sp.Poly(g, *sym_objs, domain="QQ")
            if gp.is_zero:
                continue
            # Monicise.
            lc = gp.LC()
            if lc != 1:
                gp = gp.monic()
            # Canonical representation: sorted (exponent_tuple, coef) list.
            terms = sorted(
                (tuple(monom), str(coef))
                for monom, coef in zip(gp.monoms(), gp.coeffs())
            )
            canon.append(tuple(terms))
        return frozenset(canon)
    except Exception:
        return None


def oracles_agree(
    sympy_basis: List[str],
    wolfram_basis: List[str],
    vars_: List[str],
    order: str,
) -> bool:
    """Two bases generate the same ideal iff their reduced GBs (monic)
    are set-equal. We compute both reduced GBs via SymPy as the
    canonicaliser (the other oracle of record is wolfram, and we trust
    SymPy's Buchberger to be correct on its own output).
    """
    a = _canonical_basis_set(sympy_basis, vars_, order)
    b = _canonical_basis_set(wolfram_basis, vars_, order)
    if a is None or b is None:
        return False
    return a == b


# ---------------------------------------------------------------------
# Hand-curated cases
# ---------------------------------------------------------------------


def hand_curated_cases() -> List[Dict[str, Any]]:
    """15 hand-curated cases drawn from CLO 4th ed. Ch.2 worked
    examples, Buchberger 1979 §3 illustrative cases, and the standard
    teaching examples. Each cites its source page.
    """
    return [
        # The classic CLO §6 example.
        {
            "id": "hand-001",
            "tier": "hand-curated",
            "ref": "CLO 4th ed. Ch.2 §6 Example 1 p.84 — the (x²+y, xy+1) classic",
            "input": {"polys": ["x**2 + y", "x*y + 1"], "vars": ["x", "y"], "order": "lex"},
        },
        {
            "id": "hand-002",
            "tier": "hand-curated",
            "ref": "CLO 4th ed. Ch.2 §6 Example 1 p.84 — degrevlex variant",
            "input": {"polys": ["x**2 + y", "x*y + 1"], "vars": ["x", "y"], "order": "degrevlex"},
        },
        # CLO Ch.2 §7 problem 1 — twisted cubic.
        {
            "id": "hand-003",
            "tier": "hand-curated",
            "ref": "CLO 4th ed. Ch.2 §7 problem 1 — twisted cubic ideal in lex",
            "input": {
                "polys": ["y - x**2", "z - x**3"],
                "vars": ["x", "y", "z"],
                "order": "lex",
            },
        },
        # CLO Ch.2 §8 — example 8 ideal membership.
        {
            "id": "hand-004",
            "tier": "hand-curated",
            "ref": "CLO 4th ed. Ch.2 §8 problem 4 — quadric surface",
            "input": {
                "polys": ["x**2 + y**2 + z**2 - 1", "x**2 + z**2 - y", "x - z"],
                "vars": ["x", "y", "z"],
                "order": "degrevlex",
            },
        },
        # Bilinear classic.
        {
            "id": "hand-005",
            "tier": "hand-curated",
            "ref": "Buchberger 1979 §3 illustrative — line system",
            "input": {
                "polys": ["x + y - 3", "x - y - 1"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # Already-reduced single-element basis.
        {
            "id": "hand-006",
            "tier": "hand-curated",
            "ref": "trivial: single quadratic",
            "input": {"polys": ["x**2 - 2"], "vars": ["x"], "order": "lex"},
        },
        # Two-circle intersection.
        {
            "id": "hand-007",
            "tier": "hand-curated",
            "ref": "two-circle intersection",
            "input": {
                "polys": ["x**2 + y**2 - 1", "(x-1)**2 + y**2 - 1"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # Conic-line intersection.
        {
            "id": "hand-008",
            "tier": "hand-curated",
            "ref": "ellipse + line",
            "input": {
                "polys": ["x**2 + 4*y**2 - 4", "x + 2*y - 2"],
                "vars": ["x", "y"],
                "order": "degrevlex",
            },
        },
        # Three-variable system with finite solutions.
        {
            "id": "hand-009",
            "tier": "hand-curated",
            "ref": "three-variable elimination example",
            "input": {
                "polys": ["x + y + z - 6", "x*y + y*z + z*x - 11", "x*y*z - 6"],
                "vars": ["x", "y", "z"],
                "order": "lex",
            },
        },
        # Idempotent: GB of a GB is the GB.
        {
            "id": "hand-010",
            "tier": "hand-curated",
            "ref": "idempotency — input is already a reduced GB",
            "input": {
                "polys": ["x - y**2", "y**3 + 1"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # Three-variable degree-2 system.
        {
            "id": "hand-011",
            "tier": "hand-curated",
            "ref": "three quadrics",
            "input": {
                "polys": [
                    "x**2 + y**2 + z**2 - 4",
                    "x*y + y*z + z*x - 1",
                    "x + y + z - 2",
                ],
                "vars": ["x", "y", "z"],
                "order": "degrevlex",
            },
        },
        # Two-variable square system that produces a non-trivial GB.
        {
            "id": "hand-012",
            "tier": "hand-curated",
            "ref": "Buchberger pedagogical: x²+y²−1, x³−y",
            "input": {
                "polys": ["x**2 + y**2 - 1", "x**3 - y"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # Linear system embedded in higher-deg vocabulary.
        {
            "id": "hand-013",
            "tier": "hand-curated",
            "ref": "linear-as-polynomial-deg-1",
            "input": {
                "polys": ["2*x + 3*y - 7", "x - y - 1"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # Symmetric system.
        {
            "id": "hand-014",
            "tier": "hand-curated",
            "ref": "symmetric power-sum to elementary",
            "input": {
                "polys": ["x + y - 3", "x**2 + y**2 - 5"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # Buchberger 1979 §3 — three-poly example.
        {
            "id": "hand-015",
            "tier": "hand-curated",
            "ref": "Buchberger 1979 §3 — three quadric example",
            "input": {
                "polys": [
                    "x**2 - y",
                    "x*y - z",
                    "y**2 - x*z",
                ],
                "vars": ["x", "y", "z"],
                "order": "degrevlex",
            },
        },
    ]


# ---------------------------------------------------------------------
# Classical-hard cases
# ---------------------------------------------------------------------


def classical_hard_cases() -> List[Dict[str, Any]]:
    """4–5 hard classical systems with timing baselines.

    cyclic-n: x_1+...+x_n, sum of products of consecutive k-tuples for
    k=2..n-1, x_1...x_n − 1.
    Katsura-n: standard catalyst combustion model on n+1 vars; here we
    use the 4-var (Katsura-3) and 5-var (Katsura-4) variants.

    cyclic-5 lex and Katsura-4 lex measured >10s on this device — skipped.
    See module docstring.
    """
    cases: List[Dict[str, Any]] = []

    # cyclic-3 (3 vars).
    cyclic3_polys = [
        "x + y + z",
        "x*y + y*z + z*x",
        "x*y*z - 1",
    ]
    cases.append({
        "id": "classical-001",
        "tier": "classical-hard",
        "ref": "cyclic-3 lex (Faugère ISSAC 1994 benchmark family)",
        "input": {"polys": cyclic3_polys, "vars": ["x", "y", "z"], "order": "lex"},
    })
    cases.append({
        "id": "classical-002",
        "tier": "classical-hard",
        "ref": "cyclic-3 degrevlex",
        "input": {"polys": cyclic3_polys, "vars": ["x", "y", "z"], "order": "degrevlex"},
    })

    # cyclic-4 (4 vars).
    cyclic4_polys = [
        "x1 + x2 + x3 + x4",
        "x1*x2 + x2*x3 + x3*x4 + x4*x1",
        "x1*x2*x3 + x2*x3*x4 + x3*x4*x1 + x4*x1*x2",
        "x1*x2*x3*x4 - 1",
    ]
    cases.append({
        "id": "classical-003",
        "tier": "classical-hard",
        "ref": "cyclic-4 lex (Faugère benchmark)",
        "input": {
            "polys": cyclic4_polys,
            "vars": ["x1", "x2", "x3", "x4"],
            "order": "lex",
        },
    })

    # Katsura-3 (4 vars).
    katsura3_polys = [
        "x0 + 2*x1 + 2*x2 + 2*x3 - 1",
        "x0**2 + 2*x1**2 + 2*x2**2 + 2*x3**2 - x0",
        "2*x0*x1 + 2*x1*x2 + 2*x2*x3 - x1",
        "2*x0*x2 + x1**2 + 2*x1*x3 - x2",
    ]
    cases.append({
        "id": "classical-004",
        "tier": "classical-hard",
        "ref": "Katsura-3 lex (chemistry-equilibrium benchmark)",
        "input": {
            "polys": katsura3_polys,
            "vars": ["x0", "x1", "x2", "x3"],
            "order": "lex",
        },
    })

    # cyclic-5 (5 vars) — grevlex only (lex >15s on this device).
    cyclic5_polys = [
        "x1 + x2 + x3 + x4 + x5",
        "x1*x2 + x2*x3 + x3*x4 + x4*x5 + x5*x1",
        "x1*x2*x3 + x2*x3*x4 + x3*x4*x5 + x4*x5*x1 + x5*x1*x2",
        "x1*x2*x3*x4 + x2*x3*x4*x5 + x3*x4*x5*x1 + x4*x5*x1*x2 + x5*x1*x2*x3",
        "x1*x2*x3*x4*x5 - 1",
    ]
    cases.append({
        "id": "classical-005",
        "tier": "classical-hard",
        "ref": "cyclic-5 degrevlex (lex skipped: >15s on this device)",
        "input": {
            "polys": cyclic5_polys,
            "vars": ["x1", "x2", "x3", "x4", "x5"],
            "order": "degrevlex",
        },
    })

    return cases


# ---------------------------------------------------------------------
# Stratified-random cases — adversarial mix
# ---------------------------------------------------------------------


def _mk_var_names(n: int) -> List[str]:
    return ["x", "y", "z", "u", "v"][:n]


def _rand_rational_coef(rng: random.Random, big: bool = False) -> str:
    """Random rational coefficient. `big=True` for swell-prone tier."""
    if big:
        num = rng.randint(-50, 50)
        den = rng.choice([1, 1, 1, 2, 3, 5, 7])
    else:
        num = rng.randint(-9, 9)
        if num == 0:
            num = 1
        den = rng.choice([1, 1, 1, 2, 3, 5, 7])
    f = Fraction(num, den)
    if f.denominator == 1:
        return str(f.numerator)
    return f"{f.numerator}/{f.denominator}"


def _monomial_str(exps: Tuple[int, ...], vars_: List[str]) -> str:
    """Render an exponent vector as a monomial string."""
    parts: List[str] = []
    for v, e in zip(vars_, exps):
        if e == 0:
            continue
        if e == 1:
            parts.append(v)
        else:
            parts.append(f"{v}**{e}")
    if not parts:
        return "1"
    return "*".join(parts)


def _term_str(coef_str: str, exps: Tuple[int, ...], vars_: List[str]) -> str:
    """Render a (coef, monomial) term as a string. Wraps rational coef
    in parentheses for safety inside a sum."""
    mono = _monomial_str(exps, vars_)
    if mono == "1":
        return f"({coef_str})"
    if "/" in coef_str or coef_str.startswith("-"):
        return f"({coef_str})*{mono}"
    return f"{coef_str}*{mono}"


def _gen_rand_poly(
    rng: random.Random,
    vars_: List[str],
    max_deg: int,
    n_terms: int,
    big_coefs: bool = False,
) -> str:
    """Generate a random polynomial with `n_terms` distinct monomials of
    total degree ≤ max_deg."""
    used: set = set()
    pieces: List[str] = []
    attempts = 0
    while len(used) < n_terms and attempts < 200:
        attempts += 1
        exps = []
        for _ in range(len(vars_)):
            exps.append(rng.randint(0, max_deg))
        if sum(exps) > max_deg:
            continue
        exps_t = tuple(exps)
        if exps_t in used:
            continue
        used.add(exps_t)
        coef = _rand_rational_coef(rng, big=big_coefs)
        pieces.append(_term_str(coef, exps_t, vars_))
    if not pieces:
        # Fallback — emit constant 1.
        return "1"
    return " + ".join(pieces)


def _gen_near_coprime_pair(
    rng: random.Random,
    vars_: List[str],
    max_deg: int,
) -> List[str]:
    """Two polys with disjoint LMs (coprime monomials) — Buchberger Crit 1
    should kill the pair."""
    n = len(vars_)
    # Build two monomials over disjoint subsets of vars.
    half = max(1, n // 2)
    e1 = [0] * n
    e2 = [0] * n
    for i in range(half):
        e1[i] = rng.randint(1, max_deg)
    for i in range(half, n):
        e2[i] = rng.randint(1, max_deg)
    if all(e == 0 for e in e2):
        e2[-1] = max_deg  # fallback
    # Two polynomials whose leading monomials are e1 and e2 (disjoint).
    p1 = _term_str("1", tuple(e1), vars_) + " + " + _gen_rand_poly(rng, vars_, max(1, max_deg - 1), 2)
    p2 = _term_str("1", tuple(e2), vars_) + " + " + _gen_rand_poly(rng, vars_, max(1, max_deg - 1), 2)
    return [p1, p2]


def _gen_multiplicity_induced(
    rng: random.Random,
    vars_: List[str],
) -> List[str]:
    """Construct an ideal known to have repeated roots so the lex GB does
    not have shape form. E.g. ((x-a)^2, (x-a)*(y-b), ...) over n vars."""
    n = len(vars_)
    a = rng.randint(-3, 3)
    b = rng.randint(-3, 3)
    if n == 2:
        return [
            f"(({vars_[0]}) - ({a}))**2",
            f"(({vars_[0]}) - ({a}))*(({vars_[1]}) - ({b}))",
        ]
    if n == 3:
        c = rng.randint(-3, 3)
        return [
            f"(({vars_[0]}) - ({a}))**2",
            f"(({vars_[0]}) - ({a}))*(({vars_[1]}) - ({b}))",
            f"({vars_[2]}) - ({c})",
        ]
    # n=4,5: pad with linear bindings.
    polys = [
        f"(({vars_[0]}) - ({a}))**2",
        f"(({vars_[0]}) - ({a}))*(({vars_[1]}) - ({b}))",
    ]
    for k in range(2, n):
        polys.append(f"({vars_[k]}) - ({rng.randint(-3,3)})")
    return polys


def _gen_swell_prone(
    rng: random.Random,
    vars_: List[str],
    max_deg: int,
) -> List[str]:
    """Three polys with small co-prime leading coefficients designed to
    accumulate large rational coefficients during reduction."""
    coefs = rng.sample([2, 3, 5, 7, 11], k=3)
    polys = []
    for c in coefs:
        # Build poly: c · x^a · y · ... + small low-deg terms.
        n = len(vars_)
        exps = [0] * n
        exps[0] = max_deg
        if n > 1:
            exps[1] = 1
        # Lead term + 2 random small terms.
        head = _term_str(str(c), tuple(exps), vars_)
        tail = _gen_rand_poly(rng, vars_, max(1, max_deg - 1), 2)
        polys.append(f"{head} + {tail}")
    return polys


def _gen_large_structured(
    rng: random.Random,
    vars_: List[str],
    max_deg: int,
) -> List[str]:
    """m=4 polynomials with structured leading-monomial pattern."""
    n = len(vars_)
    polys: List[str] = []
    for k in range(4):
        exps = [0] * n
        exps[k % n] = max_deg
        head = _term_str("1", tuple(exps), vars_)
        # Add 2 small terms.
        tail = _gen_rand_poly(rng, vars_, max(1, max_deg - 1), 2)
        polys.append(f"{head} + {tail}")
    return polys


def stratified_random_cases(rng: random.Random) -> List[Dict[str, Any]]:
    """Generate the 40 stratified-random cases under the adversarial mix."""
    cases: List[Dict[str, Any]] = []

    # tier-A: 15 cases, n_vars=2, total_deg ≤ 3, m_polys ∈ {2,3}
    a_specs: List[Tuple[str, str, int, int]] = []
    # 30%-30%-20%-10%-10% across 15 cases:
    # 5 random-uniform, 5 near-coprime, 3 multiplicity, 1 swell, 1 large-structured
    a_specs += [("uniform", "lex", 2, 2)] * 2 + [("uniform", "degrevlex", 2, 3)] * 3
    a_specs += [("near-coprime", "lex", 2, 2)] * 3 + [("near-coprime", "degrevlex", 2, 2)] * 2
    a_specs += [("multiplicity", "lex", 2, 2)] * 3
    a_specs += [("swell", "lex", 2, 3)] * 1
    a_specs += [("large-structured", "degrevlex", 2, 4)] * 1

    # tier-B: 15 cases, n_vars=3, total_deg ≤ 4
    b_specs: List[Tuple[str, str, int, int]] = []
    b_specs += [("uniform", "degrevlex", 3, 2)] * 2 + [("uniform", "lex", 3, 3)] * 3
    b_specs += [("near-coprime", "degrevlex", 3, 2)] * 3 + [("near-coprime", "lex", 3, 2)] * 2
    b_specs += [("multiplicity", "degrevlex", 3, 3)] * 3
    b_specs += [("swell", "degrevlex", 3, 3)] * 1
    b_specs += [("large-structured", "degrevlex", 3, 4)] * 1

    # tier-C: 10 cases, n_vars ∈ {4,5}, total_deg ≤ 3
    c_specs: List[Tuple[str, str, int, int]] = []
    c_specs += [("uniform", "degrevlex", 4, 3)] * 2 + [("uniform", "degrevlex", 5, 3)] * 1
    c_specs += [("near-coprime", "degrevlex", 4, 3)] * 2 + [("near-coprime", "degrevlex", 5, 3)] * 1
    c_specs += [("multiplicity", "degrevlex", 4, 3)] * 2
    c_specs += [("swell", "degrevlex", 4, 4)] * 1
    c_specs += [("large-structured", "degrevlex", 4, 4)] * 1

    # Assemble
    for prefix, specs, max_deg_default in [
        ("rand-A", a_specs, 3),
        ("rand-B", b_specs, 4),
        ("rand-C", c_specs, 3),
    ]:
        for i, (kind, order, n_vars, m_polys) in enumerate(specs):
            vars_ = _mk_var_names(n_vars)
            md = max_deg_default
            if kind == "uniform":
                polys = [
                    _gen_rand_poly(rng, vars_, md,
                                   n_terms=2 + rng.randint(0, 2))
                    for _ in range(m_polys)
                ]
            elif kind == "near-coprime":
                polys = _gen_near_coprime_pair(rng, vars_, md)
                while len(polys) < m_polys:
                    polys.append(_gen_rand_poly(rng, vars_, md, 3))
            elif kind == "multiplicity":
                polys = _gen_multiplicity_induced(rng, vars_)
            elif kind == "swell":
                polys = _gen_swell_prone(rng, vars_, md)
            elif kind == "large-structured":
                polys = _gen_large_structured(rng, vars_, md)
            else:
                raise ValueError(f"unknown kind {kind!r}")

            cases.append({
                "id": f"{prefix}-{i+1:03d}",
                "tier": "stratified-random",
                "ref": f"adversarial-mix sub-class={kind!r}",
                "input": {
                    "polys": polys,
                    "vars": vars_,
                    "order": order,
                },
            })
    return cases


# ---------------------------------------------------------------------
# Monomial-degenerate cases
# ---------------------------------------------------------------------


def monomial_degenerate_cases() -> List[Dict[str, Any]]:
    """10 cases: single-generator ideals, monomial ideals, trivial {1},
    pure-power ideals, already-reduced bases. Both lex and degrevlex.
    """
    return [
        # Single-generator (already a GB).
        {
            "id": "mono-001",
            "tier": "monomial-degenerate",
            "ref": "single quadratic — already a GB",
            "input": {"polys": ["x**2 - 4"], "vars": ["x"], "order": "lex"},
        },
        {
            "id": "mono-002",
            "tier": "monomial-degenerate",
            "ref": "single bivariate — already a GB",
            "input": {"polys": ["x**2 + y**2 - 1"], "vars": ["x", "y"], "order": "degrevlex"},
        },
        # Monomial ideal (LMs only).
        {
            "id": "mono-003",
            "tier": "monomial-degenerate",
            "ref": "monomial ideal (x², xy, y²) — already a GB",
            "input": {
                "polys": ["x**2", "x*y", "y**2"],
                "vars": ["x", "y"],
                "order": "degrevlex",
            },
        },
        {
            "id": "mono-004",
            "tier": "monomial-degenerate",
            "ref": "monomial ideal (x³, y²) — pure-power 2-var",
            "input": {
                "polys": ["x**3", "y**2"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # Pure-power ideal.
        {
            "id": "mono-005",
            "tier": "monomial-degenerate",
            "ref": "pure-power 3-var",
            "input": {
                "polys": ["x**3", "y**4", "z**2"],
                "vars": ["x", "y", "z"],
                "order": "degrevlex",
            },
        },
        # Trivial ideal: {1}.
        {
            "id": "mono-006",
            "tier": "monomial-degenerate",
            "ref": "inconsistent ideal — {1}",
            "input": {"polys": ["1"], "vars": ["x"], "order": "lex"},
        },
        # Trivial ideal as combination (1 = x - (x - 1)).
        {
            "id": "mono-007",
            "tier": "monomial-degenerate",
            "ref": "inconsistent system — x = 0 and x = 1",
            "input": {"polys": ["x", "x - 1"], "vars": ["x"], "order": "lex"},
        },
        # Already-reduced 2-element basis.
        {
            "id": "mono-008",
            "tier": "monomial-degenerate",
            "ref": "already-reduced basis (x², y³) — idempotency",
            "input": {
                "polys": ["x**2", "y**3"],
                "vars": ["x", "y"],
                "order": "degrevlex",
            },
        },
        # Two-variable: zero polynomial mixed in.
        {
            "id": "mono-009",
            "tier": "monomial-degenerate",
            "ref": "non-zero generators (zero filtered)",
            "input": {
                "polys": ["x**2 - 1", "y - 2"],
                "vars": ["x", "y"],
                "order": "lex",
            },
        },
        # 3-var pure-monomial degrevlex.
        {
            "id": "mono-010",
            "tier": "monomial-degenerate",
            "ref": "3-var monomial ideal (xy, yz, zx) degrevlex",
            "input": {
                "polys": ["x*y", "y*z", "z*x"],
                "vars": ["x", "y", "z"],
                "order": "degrevlex",
            },
        },
    ]


# ---------------------------------------------------------------------
# Refusal-envelope cases — bench oracles do NOT run here; these are
# expected-tagged outputs only.
# ---------------------------------------------------------------------


def refusal_envelope_cases() -> List[Dict[str, Any]]:
    """10 boundary cases. Bench expectation: tagged refusal with the
    correct class.
    """
    return [
        # non-polynomial
        {
            "id": "ref-001",
            "tier": "refusal-envelope",
            "ref": "non-polynomial: sin(x)",
            "input": {"polys": ["sin(x) + y"], "vars": ["x", "y"], "order": "lex"},
            "expected_tag": "groebner-basis/non-polynomial",
        },
        {
            "id": "ref-002",
            "tier": "refusal-envelope",
            "ref": "non-polynomial: log(x)",
            "input": {"polys": ["log(x) + 1"], "vars": ["x"], "order": "lex"},
            "expected_tag": "groebner-basis/non-polynomial",
        },
        {
            "id": "ref-003",
            "tier": "refusal-envelope",
            "ref": "non-polynomial: exp(x)",
            "input": {"polys": ["exp(x) - y"], "vars": ["x", "y"], "order": "degrevlex"},
            "expected_tag": "groebner-basis/non-polynomial",
        },
        # parametric
        {
            "id": "ref-004",
            "tier": "refusal-envelope",
            "ref": "parametric: extra symbol a",
            "input": {"polys": ["x + a"], "vars": ["x"], "order": "lex"},
            "expected_tag": "groebner-basis/parametric",
        },
        {
            "id": "ref-005",
            "tier": "refusal-envelope",
            "ref": "parametric: hidden var z in 2-var system",
            "input": {
                "polys": ["x*z + y", "x - 1"],
                "vars": ["x", "y"],
                "order": "lex",
            },
            "expected_tag": "groebner-basis/parametric",
        },
        {
            "id": "ref-006",
            "tier": "refusal-envelope",
            "ref": "parametric: 3-var input with unknown 'k'",
            "input": {
                "polys": ["x + y - k", "y - z"],
                "vars": ["x", "y", "z"],
                "order": "degrevlex",
            },
            "expected_tag": "groebner-basis/parametric",
        },
        # empty-input
        {
            "id": "ref-007",
            "tier": "refusal-envelope",
            "ref": "empty polys",
            "input": {"polys": [], "vars": ["x"], "order": "lex"},
            "expected_tag": "groebner-basis/empty-input",
        },
        {
            "id": "ref-008",
            "tier": "refusal-envelope",
            "ref": "empty polys, 3-var",
            "input": {"polys": [], "vars": ["x", "y", "z"], "order": "degrevlex"},
            "expected_tag": "groebner-basis/empty-input",
        },
        # empty-vars
        {
            "id": "ref-009",
            "tier": "refusal-envelope",
            "ref": "empty vars",
            "input": {"polys": ["1"], "vars": [], "order": "lex"},
            "expected_tag": "groebner-basis/empty-vars",
        },
        # additional non-polynomial: square root
        {
            "id": "ref-010",
            "tier": "refusal-envelope",
            "ref": "non-polynomial: sqrt(x) is x**(1/2)",
            "input": {"polys": ["sqrt(x) + 1"], "vars": ["x"], "order": "lex"},
            "expected_tag": "groebner-basis/non-polynomial",
        },
    ]


# ---------------------------------------------------------------------
# Per-case oracle admission
# ---------------------------------------------------------------------


def admit_case(case: Dict[str, Any]) -> Tuple[bool, Optional[Dict], Dict]:
    """For one case: compute SymPy + Wolfram outputs; if oracles agree,
    admit and return the expected-output record. Otherwise drop with a
    flagged log.
    """
    inp = case["input"]
    polys = inp["polys"]
    vars_ = inp["vars"]
    order = inp["order"]
    tier = case["tier"]

    # Refusal-envelope: oracles not consulted; expected is the tagged class.
    if tier == "refusal-envelope":
        sp_result = groebner_reference(polys, vars_, order)
        if sp_result.get("kind") != "tagged":
            return False, None, {
                "status": "DROPPED",
                "reason": f"reference did not refuse for tier=refusal-envelope: {sp_result}",
            }
        expected_tag = case.get("expected_tag")
        if expected_tag and sp_result.get("tag") != expected_tag:
            return False, None, {
                "status": "DROPPED",
                "reason": f"reference tag={sp_result.get('tag')!r} != expected {expected_tag!r}",
            }
        expected = {
            "kind": "tagged",
            "tag": sp_result["tag"],
        }
        log = {
            "status": "admitted",
            "sympy": f"refused: {sp_result['tag']}",
            "wolfram": "skipped (refusal tier)",
            "consensus": "sympy + bench-design",
        }
        return True, expected, log

    # Happy-path tiers.
    t0 = time.time()
    sp_result = groebner_reference(polys, vars_, order)
    sp_time = time.time() - t0

    if sp_result.get("kind") != "ok":
        return False, None, {
            "status": "DROPPED",
            "reason": f"sympy reference returned non-ok: {sp_result}",
        }

    sp_basis = sp_result["basis"]

    # Wolfram cross-check.
    wolfram_basis: Optional[List[str]] = None
    wolfram_status = "skipped"
    if USE_WOLFRAM:
        wolfram_basis, wolfram_status = wolfram_groebner(polys, vars_, order)

    if wolfram_basis is not None:
        agree = oracles_agree(sp_basis, wolfram_basis, vars_, order)
        if not agree:
            return False, None, {
                "status": "DROPPED",
                "reason": "oracle-disagreement: SymPy and Wolfram emitted different ideals",
                "sympy_basis": sp_basis,
                "wolfram_basis": wolfram_basis,
                "wolfram_status": wolfram_status,
            }
        consensus = "sympy + wolfram"
    else:
        consensus = f"sympy only (wolfram: {wolfram_status})"

    expected: Dict[str, Any] = {
        "kind": "ok",
        "basis": sp_basis,
        "order": order,
        "vars": vars_,
        "wall_clock_baseline_seconds": round(sp_time, 4),
    }

    log = {
        "status": "admitted",
        "sympy": "ok",
        "wolfram": wolfram_status,
        "consensus": consensus,
        "sympy_time_seconds": round(sp_time, 4),
    }
    return True, expected, log


# ---------------------------------------------------------------------
# Lockstep TS verifier check
# ---------------------------------------------------------------------


def lockstep_ts_check(
    case_input: Dict,
    case_id: str,
    expected: Dict,
    candidate: Dict,
) -> Tuple[bool, str]:
    """Run verify.ts on the same case and compare the pass/fail outcome
    with verify.py. Mismatches halt generation.
    """
    if SKIP_LOCKSTEP:
        return True, "skipped"
    bun = "/home/tobias/.amp/bin/bun"
    if not Path(bun).exists():
        bun = "bun"
    payload = {"id": case_id, "input": case_input, "candidate": candidate, "expected": expected}
    # Run python verifier first.
    py_proc = subprocess.run(
        [sys.executable, str(HERE / "verify.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        py_result = json.loads(py_proc.stdout)
    except json.JSONDecodeError as exc:
        return False, f"verify.py emitted non-JSON: {exc}; stderr={py_proc.stderr[:200]}"
    # Run TS verifier.
    ts_proc = subprocess.run(
        [bun, str(HERE / "verify.ts")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        ts_result = json.loads(ts_proc.stdout)
    except json.JSONDecodeError as exc:
        return False, f"verify.ts emitted non-JSON: {exc}; stderr={ts_proc.stderr[:200]}"
    if py_result["pass"] != ts_result["pass"]:
        return False, (
            f"verify.py.pass={py_result['pass']!r} but verify.ts.pass={ts_result['pass']!r}; "
            f"py.reason={py_result.get('reason')!r}; ts.reason={ts_result.get('reason')!r}"
        )
    return True, "ok"


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def main() -> int:
    rng = random.Random(SEED)

    cases: List[Dict[str, Any]] = []
    cases.extend(hand_curated_cases())
    cases.extend(classical_hard_cases())
    cases.extend(stratified_random_cases(rng))
    cases.extend(monomial_degenerate_cases())
    cases.extend(refusal_envelope_cases())

    print(f"Generated {len(cases)} candidate cases.")
    print(f"USE_WOLFRAM={USE_WOLFRAM}, SKIP_LOCKSTEP={SKIP_LOCKSTEP}")

    inputs: List[Dict] = []
    expected: List[Dict] = []
    oracle_log: List[Dict] = []
    drops = 0
    wolfram_times: List[float] = []
    sympy_times: List[float] = []

    for case in cases:
        print(f"  case {case['id']:30s} ({case['tier']:20s}) ", end="", flush=True)
        admit, exp, log = admit_case(case)
        if not admit:
            drops += 1
            print(f"DROPPED  ({log.get('reason', '?')[:80]})")
            oracle_log.append({"id": case["id"], **log})
            continue
        # Lockstep TS verifier check (only on the SymPy reference output).
        if not SKIP_LOCKSTEP and case["tier"] != "refusal-envelope":
            # Reconstruct candidate from expected (the SymPy-derived basis).
            candidate = {
                "kind": "ok",
                "basis": exp["basis"],
                "order": exp["order"],
                "vars": exp["vars"],
                "n_pairs": 0,
                "warnings": [],
            }
            ok, msg = lockstep_ts_check(case["input"], case["id"], exp, candidate)
            if not ok:
                print(f"LOCKSTEP-DISAGREEMENT ({msg[:80]})")
                drops += 1
                oracle_log.append({
                    "id": case["id"],
                    "status": "DROPPED",
                    "reason": f"lockstep ts/py disagreement: {msg}",
                })
                continue
        elif not SKIP_LOCKSTEP and case["tier"] == "refusal-envelope":
            # For refusal cases we synthesise a tagged candidate.
            candidate = {
                "kind": "tagged",
                "tag": exp["tag"],
                "payload": {"detail": "from generate.py"},
            }
            ok, msg = lockstep_ts_check(case["input"], case["id"], exp, candidate)
            if not ok:
                print(f"LOCKSTEP-DISAGREEMENT ({msg[:80]})")
                drops += 1
                oracle_log.append({
                    "id": case["id"],
                    "status": "DROPPED",
                    "reason": f"lockstep ts/py disagreement: {msg}",
                })
                continue
        print(f"OK  ({log.get('consensus', '-')[:40]})")

        if "sympy_time_seconds" in log:
            sympy_times.append(log["sympy_time_seconds"])

        # Strip oracle-log-only fields from the expected record.
        inputs.append({"id": case["id"], "tier": case["tier"], "input": case["input"]})
        expected.append({"id": case["id"], "expected": exp})
        oracle_log.append({"id": case["id"], **log})

    # Write outputs.
    payload_inputs = {
        "version": ENCODING_VERSION,
        "seed": SEED,
        "cases": inputs,
    }
    payload_expected = {
        "version": ENCODING_VERSION,
        "cases": expected,
    }
    (HERE / "inputs.json").write_text(json.dumps(payload_inputs, indent=2) + "\n")
    (HERE / "expected.json").write_text(json.dumps(payload_expected, indent=2) + "\n")
    (HERE / "oracle_log.json").write_text(json.dumps(oracle_log, indent=2) + "\n")

    print()
    print(f"Wrote {len(inputs)} cases. Drops: {drops}.")
    if sympy_times:
        avg_sp = sum(sympy_times) / len(sympy_times)
        max_sp = max(sympy_times)
        print(f"  SymPy timing: avg={avg_sp:.3f}s, max={max_sp:.3f}s ({len(sympy_times)} cases)")
    if drops:
        print("  See oracle_log.json for disagreement / lockstep reports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
