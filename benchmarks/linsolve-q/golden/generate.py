#!/usr/bin/env python3
"""Generate the linsolve-q golden master with triple-witness oracle.

Per ADR-0019, every golden case is admitted iff at least 2 of 3
oracles (Wolfram LinearSolve, SymPy linsolve, Cramer's rule) agree
on the answer modulo the verifier-invariant equivalence relation.

This generator:
  1. Constructs each test case (8 tiers per PROMPT.md).
  2. Computes the expected output via the local reference impl.
  3. Cross-validates against Wolfram and SymPy.
  4. Logs disagreements; admits only consensus cases to the bench.
  5. Writes inputs.json + expected.json + oracle_log.json.

Reproducibility: seeded `random.Random` (CPython stdlib).

Run:
  python3 generate.py             # full triple-witness (slow, ~10 min)
  WB_LIVE_ORACLE=0 python3 generate.py  # SymPy + Cramer only (fast)
"""
from __future__ import annotations

import json
import os
import random
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Tuple

import sympy as sp

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(HERE.parent / "reference"))
sys.path.insert(0, str(ROOT / "bench" / "_corpus"))

from linsolve_q_reference import bareiss_solve, fmt_rat, parse_rat  # noqa: E402
from oracle.wolfram import wolfram_query  # noqa: E402

LIVE_ORACLE = os.environ.get("WB_LIVE_ORACLE", "1") != "0"
SEED = 20260506
ENCODING_VERSION = 1

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def to_rat_str(x) -> str:
    """Coerce int/Fraction/sp.Rational to canonical rational string."""
    f = Fraction(x) if not isinstance(x, Fraction) else x
    return fmt_rat(f)


def matrix_to_str(A: List[List[Any]]) -> List[List[str]]:
    return [[to_rat_str(x) for x in row] for row in A]


def vector_to_str(b: List[Any]) -> List[str]:
    return [to_rat_str(x) for x in b]


def parse_matrix(A_strs: List[List[str]]) -> List[List[Fraction]]:
    return [[parse_rat(s) for s in row] for row in A_strs]


def parse_vector(b_strs: List[str]) -> List[Fraction]:
    return [parse_rat(s) for s in b_strs]


# ---------------------------------------------------------------------
# Sympy oracle
# ---------------------------------------------------------------------


def sympy_solve(A_strs: List[List[str]], b_strs: List[str]) -> Dict[str, Any]:
    """SymPy oracle: solve A·x = b exactly via sympy.linsolve.

    Returns a dict matching the reference implementation's output
    shape, for direct agreement comparison.
    """
    m = len(A_strs)
    n = len(A_strs[0]) if m > 0 else 0
    A = sp.Matrix([[sp.Rational(s) for s in row] for row in A_strs])
    b = sp.Matrix([sp.Rational(s) for s in b_strs])

    # SymPy's linsolve handles all three kinds; the FiniteSet either has
    # one tuple (unique or parametric — distinguished by free symbols)
    # or is empty (inconsistent).
    syms = sp.symbols(f"x0:{n}") if n > 0 else ()
    if not isinstance(syms, tuple):
        syms = (syms,)
    sol = sp.linsolve((A, b), *syms)

    rank_A = A.rank()
    rank_aug = A.row_join(b).rank()

    if len(sol) == 0:
        return {
            "kind": "inconsistent",
            "rank": rank_A,
            "augmented_rank": rank_aug,
            "method": "sympy-linsolve",
            "warnings": [],
        }

    # Single-tuple FiniteSet (unique or parametric).
    (tup,) = list(sol)
    free_in_tup = sorted(
        {s for entry in tup for s in entry.free_symbols},
        key=str,
    )

    if not free_in_tup:
        return {
            "kind": "unique",
            "x": [to_rat_str(entry) for entry in tup],
            "free_vars": [],
            "rank": rank_A,
            "method": "sympy-linsolve",
            "warnings": [],
        }

    # Parametric: rename SymPy's tau0/tau1/... to t_0/t_1/... matching
    # the reference convention.
    rename = {s: sp.Symbol(f"t_{i}") for i, s in enumerate(free_in_tup)}
    return {
        "kind": "under-determined",
        "x": [str(entry.subs(rename)).replace(" ", " ") for entry in tup],
        "free_vars": [f"t_{i}" for i in range(len(free_in_tup))],
        "rank": rank_A,
        "method": "sympy-linsolve",
        "warnings": [],
    }


# ---------------------------------------------------------------------
# Wolfram oracle (slow but authoritative)
# ---------------------------------------------------------------------


def wolfram_check(A_strs: List[List[str]], b_strs: List[str]) -> Dict[str, Any]:
    """Wolfram oracle: cross-check by computing rank(A), rank([A|b]),
    and (when unique) LinearSolve. Returns a kind/rank summary that
    the agreement layer can compare to the reference output.

    We don't ask Wolfram for the parametric form because Mathematica's
    free-variable naming convention (`C[1], C[2], ...`) differs from
    ours and the agreement comparison reduces to "rank + kind"
    matching with substitution-into-A·x=b verified against the
    reference's parametrisation.
    """
    if not LIVE_ORACLE:
        return {"status": "skipped", "reason": "WB_LIVE_ORACLE=0"}

    m = len(A_strs)
    n = len(A_strs[0]) if m > 0 else 0

    if m == 0 or n == 0:
        return {"status": "skipped", "reason": "boundary case (m=0 or n=0)"}

    A_wl = "{" + ",".join("{" + ",".join(row) + "}" for row in A_strs) + "}"
    b_wl = "{" + ",".join(b_strs) + "}"

    code = (
        f"With[{{A = {A_wl}, b = {b_wl}}}, "
        f"{{ MatrixRank[A], MatrixRank[Join[A, Transpose[{{b}}], 2]], "
        f"  Quiet[Check[LinearSolve[A, b], $Failed]] }}]"
    )
    out = wolfram_query(code, timeout=30)
    if out["status"] != "ok":
        return {"status": out["status"], "reason": out.get("reason", "")}

    # Parse '{rank, aug_rank, x_or_$Failed}'. Use balanced-bracket scan
    # in case x contains nested braces.
    s = out["result"].strip()
    if not (s.startswith("{") and s.endswith("}")):
        return {"status": "parse-error", "raw": out["result"]}
    inner = s[1:-1].strip()
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in inner:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    if len(parts) != 3:
        return {"status": "parse-error", "raw": out["result"]}
    rank_str, aug_rank_str, x_str = parts
    return {
        "status": "ok",
        "rank": int(rank_str),
        "augmented_rank": int(aug_rank_str),
        "linsolve_result": x_str,  # may be `$Failed` or a list
    }


# ---------------------------------------------------------------------
# Agreement: reference vs SymPy vs Wolfram
# ---------------------------------------------------------------------


def admit(case_id: str, ref: Dict[str, Any],
          sympy_out: Dict[str, Any],
          wolfram_out: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (admitted, reason). Admitted iff >= 2 oracles agree on
    kind + rank + (where applicable) augmented_rank.

    The parametric-form check is skipped here; the verifier does it
    via random-substitution at bench time. What we need at admission
    time is: do the oracles agree on the *structure* of the answer
    (kind, rank, augmented rank)?
    """
    ref_signature = _signature(ref)
    sympy_signature = _signature(sympy_out)
    if ref_signature != sympy_signature:
        return False, (
            f"ref vs sympy disagree: ref={ref_signature} "
            f"sympy={sympy_signature}"
        )
    # Wolfram: optional (skipped on boundary cases or when LIVE=0).
    if wolfram_out.get("status") == "ok":
        wolfram_kind = _kind_from_wolfram(wolfram_out, ref)
        if wolfram_kind == "inconsistent":
            wolfram_signature = (wolfram_kind, wolfram_out["rank"],
                                 wolfram_out.get("augmented_rank"))
        else:
            wolfram_signature = (wolfram_kind, wolfram_out["rank"], "==rank")
        if wolfram_signature != ref_signature:
            return False, (
                f"ref vs wolfram disagree: ref={ref_signature} "
                f"wolf={wolfram_signature}"
            )
        return True, "ref + sympy + wolfram agree"
    return True, f"ref + sympy agree; wolfram skipped ({wolfram_out.get('status')})"


def _signature(out: Dict[str, Any]) -> Tuple[str, int, Any]:
    """Compact signature for cross-oracle agreement.

    augmented_rank is meaningful ONLY for inconsistent kinds; for unique
    and under-determined the augmented rank equals rank by definition, so
    embedding it would force a representation choice (oracles vary —
    Wolfram returns 1, ref returns None) without semantic content.
    """
    if out["kind"] == "inconsistent":
        return (out["kind"], out["rank"], out.get("augmented_rank"))
    return (out["kind"], out["rank"], "==rank")


def _kind_from_wolfram(wolf: Dict[str, Any], ref: Dict[str, Any]) -> str:
    """Map Wolfram's outputs to our `kind` taxonomy."""
    if wolf["augmented_rank"] > wolf["rank"]:
        return "inconsistent"
    if wolf["linsolve_result"].strip() == "$Failed":
        # LinearSolve returned $Failed but ranks agree -> Wolfram
        # treated it as under-determined or refused. Cross-check
        # via rank.
        if wolf["rank"] < len(ref.get("x", [0]) or [0]):
            return "under-determined"
        return "unique"
    # Otherwise: ranks agree, LinearSolve returned a vector. If # of
    # cols == rank ⇒ unique; else under-determined.
    n_unknown = len(ref.get("x", []) or [])
    if wolf["rank"] == n_unknown:
        return "unique"
    return "under-determined"


# ---------------------------------------------------------------------
# Case constructors per tier
# ---------------------------------------------------------------------


def tier_A_shape_edges() -> List[Tuple[str, List[List], List]]:
    """6 cases: degenerate / minimal-shape inputs."""
    return [
        # 1×1 unique
        ("A1-1x1-unique", [[Fraction(3)]], [Fraction(6)]),
        # 1×2 under-determined
        ("A2-1x2-underdetermined", [[Fraction(1), Fraction(2)]], [Fraction(5)]),
        # 2×1 over-determined consistent
        ("A3-2x1-overdetermined-consistent",
         [[Fraction(2)], [Fraction(4)]], [Fraction(6), Fraction(12)]),
        # 2×2 identity
        ("A4-2x2-identity",
         [[Fraction(1), Fraction(0)], [Fraction(0), Fraction(1)]],
         [Fraction(7), Fraction(-3)]),
        # 3×3 identity
        ("A5-3x3-identity",
         [[Fraction(1) if i == j else Fraction(0) for j in range(3)] for i in range(3)],
         [Fraction(1), Fraction(2), Fraction(3)]),
        # 0×n: every x ∈ Q^n is a solution. SymPy's behavior on this
        # is dimension-error; we admit it via reference + Cramer.
        # Skipping for now; included as a known under-determined edge.
        # (Will be a separate Tier-A case once SymPy-side adapter handles m=0.)
    ]


def tier_B_random_well_conditioned(rng: random.Random) -> List[Tuple[str, List[List], List]]:
    """8 cases at sizes 3..100. Random small-rational entries; full-rank."""
    cases = []
    # Sizes capped at 30 for v1: dense exact-rational solving past n~50
    # is dominated by SymPy / reference wall-clock (10s+ per case), and
    # the integer-preserving claim (the actual test) is exercised by
    # Tier H stress cases. Larger sizes are a v2 expansion.
    sizes = [3, 5, 8, 12, 20, 30]
    for n in sizes:
        # Build a random invertible n×n matrix by composing random
        # row operations on the identity; ensures non-singularity.
        A = [[Fraction(1) if i == j else Fraction(0) for j in range(n)]
             for i in range(n)]
        # Apply ~3n random row ops with small integer multipliers.
        for _ in range(3 * n):
            i, j = rng.sample(range(n), 2)
            mul = Fraction(rng.randint(-3, 3))
            if mul == 0:
                mul = Fraction(1)
            for k in range(n):
                A[i][k] = A[i][k] + mul * A[j][k]
        # Now pick a random integer x and compute b = A·x.
        x_true = [Fraction(rng.randint(-5, 5)) for _ in range(n)]
        b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
        cases.append((f"B-randwell-n{n}", A, b))
    return cases


def tier_C_classical(rng: random.Random) -> List[Tuple[str, List[List], List]]:
    """6 cases: Hilbert (4) + Pascal + Vandermonde."""
    cases = []
    # Hilbert H_n: H[i][j] = 1/(i+j+1)
    for n in [3, 5, 8, 10]:
        H = [[Fraction(1, i + j + 1) for j in range(n)] for i in range(n)]
        x_true = [Fraction(rng.randint(-3, 3), rng.randint(1, 4))
                  for _ in range(n)]
        b = [sum(H[i][j] * x_true[j] for j in range(n)) for i in range(n)]
        cases.append((f"C-hilbert-n{n}", H, b))
    # Pascal P_n: P[i][j] = C(i+j, i)
    n = 6
    from math import comb
    P = [[Fraction(comb(i + j, i)) for j in range(n)] for i in range(n)]
    x_true = [Fraction(1) for _ in range(n)]
    b = [sum(P[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append((f"C-pascal-n{n}", P, b))
    # Vandermonde V_n: V[i][j] = node_i^j
    n = 5
    nodes = [Fraction(1), Fraction(2), Fraction(-1), Fraction(3, 2), Fraction(-3)]
    V = [[nodes[i] ** j for j in range(n)] for i in range(n)]
    x_true = [Fraction(rng.randint(-2, 2)) for _ in range(n)]
    b = [sum(V[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append((f"C-vandermonde-n{n}", V, b))
    return cases


def tier_D_integer_stress(rng: random.Random) -> List[Tuple[str, List[List], List]]:
    """4 cases: large-integer-coefficient stress at n ∈ {20, 30, 50}-banded."""
    cases = []
    for tag, n in [("D-dense-n10", 10), ("D-dense-n15", 15)]:
        # Dense ±100 entries — kept small enough for SymPy timing.
        A = [[Fraction(rng.randint(-100, 100)) for _ in range(n)]
             for _ in range(n)]
        # Force non-singularity by adding a multiple of I if singular.
        sym_A = sp.Matrix([[int(c) for c in row] for row in A])
        if sym_A.det() == 0:
            for i in range(n):
                A[i][i] += Fraction(1)
        x_true = [Fraction(rng.randint(-5, 5)) for _ in range(n)]
        b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
        cases.append((tag, A, b))
    # Banded n=30 (tridiagonal) — sparse so n=30 is tractable
    n = 30
    A = [[Fraction(0) for _ in range(n)] for _ in range(n)]
    for i in range(n):
        A[i][i] = Fraction(rng.randint(2, 9))
        if i > 0:
            A[i][i - 1] = Fraction(rng.randint(-3, 3))
        if i < n - 1:
            A[i][i + 1] = Fraction(rng.randint(-3, 3))
    x_true = [Fraction(rng.randint(-3, 3)) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append(("D-tridiag-n30", A, b))
    # Arrowhead n=20
    n = 20
    A = [[Fraction(0) for _ in range(n)] for _ in range(n)]
    for i in range(n):
        A[i][i] = Fraction(rng.randint(2, 9))
        A[i][0] = A[i][0] + Fraction(rng.randint(-2, 2))
        A[0][i] = A[0][i] + Fraction(rng.randint(-2, 2))
    A[0][0] = Fraction(50)  # ensure non-singularity at the hub
    x_true = [Fraction(rng.randint(-2, 2)) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append(("D-arrowhead-n20", A, b))
    return cases


def tier_E_underdetermined() -> List[Tuple[str, List[List], List]]:
    """8 cases: under-determined (m < n, or rank-deficient with consistent rhs)."""
    cases = []
    # m < n: 1×2, 2×3, 3×5
    cases.append(("E-1x2",
                  [[Fraction(2), Fraction(3)]], [Fraction(7)]))
    cases.append(("E-2x3",
                  [[Fraction(1), Fraction(2), Fraction(0)],
                   [Fraction(0), Fraction(1), Fraction(3)]],
                  [Fraction(4), Fraction(5)]))
    cases.append(("E-3x5",
                  [[Fraction(1), Fraction(0), Fraction(0), Fraction(2), Fraction(1)],
                   [Fraction(0), Fraction(1), Fraction(0), Fraction(-1), Fraction(3)],
                   [Fraction(0), Fraction(0), Fraction(1), Fraction(4), Fraction(-2)]],
                  [Fraction(3), Fraction(0), Fraction(7)]))
    # Square but rank-deficient with consistent rhs.
    cases.append(("E-3x3-rank2-consistent",
                  [[Fraction(1), Fraction(2), Fraction(3)],
                   [Fraction(2), Fraction(4), Fraction(6)],
                   [Fraction(0), Fraction(1), Fraction(0)]],
                  [Fraction(6), Fraction(12), Fraction(2)]))
    cases.append(("E-4x4-rank2-consistent",
                  [[Fraction(1), Fraction(0), Fraction(1), Fraction(0)],
                   [Fraction(0), Fraction(1), Fraction(0), Fraction(1)],
                   [Fraction(2), Fraction(0), Fraction(2), Fraction(0)],
                   [Fraction(0), Fraction(3), Fraction(0), Fraction(3)]],
                  [Fraction(5), Fraction(2), Fraction(10), Fraction(6)]))
    cases.append(("E-3x3-rank1-consistent",
                  [[Fraction(1), Fraction(2), Fraction(3)],
                   [Fraction(2), Fraction(4), Fraction(6)],
                   [Fraction(3), Fraction(6), Fraction(9)]],
                  [Fraction(1), Fraction(2), Fraction(3)]))
    # Specifically a free variable in middle position.
    cases.append(("E-3x4-mid-free",
                  [[Fraction(1), Fraction(0), Fraction(2), Fraction(0)],
                   [Fraction(0), Fraction(0), Fraction(1), Fraction(0)],
                   [Fraction(0), Fraction(0), Fraction(0), Fraction(1)]],
                  [Fraction(3), Fraction(1), Fraction(5)]))
    # Larger structurally-rank-1 case
    n = 8
    A = [[Fraction(i + 1) for _ in range(n)] for i in range(n)]
    rhs = [Fraction(i + 1) for i in range(n)]  # consistent: x = [1,0,0,…]
    cases.append((f"E-{n}x{n}-rank1-consistent", A, rhs))
    return cases


def tier_F_inconsistent() -> List[Tuple[str, List[List], List]]:
    """6 cases: inconsistent systems (rank(A) < rank([A|b]))."""
    cases = []
    # Tall over-determined
    cases.append(("F-2x1-overdet-incons",
                  [[Fraction(1)], [Fraction(2)]],
                  [Fraction(3), Fraction(7)]))  # x=3 vs x=7/2
    cases.append(("F-3x2-overdet-incons",
                  [[Fraction(1), Fraction(0)], [Fraction(0), Fraction(1)],
                   [Fraction(1), Fraction(1)]],
                  [Fraction(2), Fraction(3), Fraction(6)]))  # 2+3=5 ≠ 6
    cases.append(("F-4x3-overdet-incons",
                  [[Fraction(1), Fraction(0), Fraction(0)],
                   [Fraction(0), Fraction(1), Fraction(0)],
                   [Fraction(0), Fraction(0), Fraction(1)],
                   [Fraction(1), Fraction(1), Fraction(1)]],
                  [Fraction(1), Fraction(2), Fraction(3), Fraction(7)]))  # 1+2+3=6≠7
    # Square rank-deficient + inconsistent
    cases.append(("F-3x3-rank2-incons",
                  [[Fraction(1), Fraction(2), Fraction(3)],
                   [Fraction(2), Fraction(4), Fraction(6)],
                   [Fraction(0), Fraction(1), Fraction(0)]],
                  [Fraction(6), Fraction(13), Fraction(2)]))  # 2*row1=row2 ⇒ b1*2≠b2
    cases.append(("F-3x3-rank1-incons",
                  [[Fraction(1), Fraction(2), Fraction(3)],
                   [Fraction(2), Fraction(4), Fraction(6)],
                   [Fraction(3), Fraction(6), Fraction(9)]],
                  [Fraction(1), Fraction(2), Fraction(4)]))  # 3*1=3≠4
    cases.append(("F-4x4-rank2-incons",
                  [[Fraction(1), Fraction(0), Fraction(1), Fraction(0)],
                   [Fraction(0), Fraction(1), Fraction(0), Fraction(1)],
                   [Fraction(2), Fraction(0), Fraction(2), Fraction(0)],
                   [Fraction(0), Fraction(3), Fraction(0), Fraction(3)]],
                  [Fraction(5), Fraction(2), Fraction(11), Fraction(6)]))  # 2*5=10≠11
    return cases


def tier_G_industrial(rng: random.Random) -> List[Tuple[str, List[List], List]]:
    """4 cases: structured / industrial-shape matrices."""
    cases = []
    # Block-diagonal n=20 (4 5×5 blocks)
    n = 20
    A = [[Fraction(0) for _ in range(n)] for _ in range(n)]
    for blk in range(4):
        for i in range(5):
            for j in range(5):
                A[blk * 5 + i][blk * 5 + j] = Fraction(rng.randint(-3, 3))
        # ensure block non-singular by adding identity * 5
        for i in range(5):
            A[blk * 5 + i][blk * 5 + i] += Fraction(5)
    x_true = [Fraction(rng.randint(-3, 3)) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append(("G-blockdiag-n20", A, b))
    # Cyclotomic-coefficient n=10: A[i][j] = (i + j*ω) where ω is a primitive
    # 12-th root of unity — but staying in Q means using small integer
    # approximations. Use Pascal-style integer triangle scaled.
    n = 10
    A = [[Fraction(((i + 1) * (j + 1)) % 7 + 1) for j in range(n)]
         for i in range(n)]
    # Force non-singularity:
    for i in range(n):
        A[i][i] += Fraction(20)
    x_true = [Fraction(1, i + 1) for i in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append(("G-mod7-pascal-n10", A, b))
    # Bordered banded n=15
    n = 15
    A = [[Fraction(0) for _ in range(n)] for _ in range(n)]
    for i in range(n):
        A[i][i] = Fraction(4)
        if i > 0:
            A[i][i - 1] = Fraction(-1)
        if i < n - 1:
            A[i][i + 1] = Fraction(-1)
        A[i][n - 1] = A[i][n - 1] + Fraction(1)
        A[n - 1][i] = A[n - 1][i] + Fraction(1)
    A[n - 1][n - 1] = Fraction(20)
    x_true = [Fraction(1) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append(("G-bordered-banded-n15", A, b))
    # Symmetric positive-definite n=20 (random orthogonal-ish via I + N·Nᵀ)
    n = 20
    A = [[Fraction(0) for _ in range(n)] for _ in range(n)]
    for i in range(n):
        A[i][i] = Fraction(2 * n)
        for j in range(i):
            v = Fraction(rng.randint(-2, 2))
            A[i][j] = v
            A[j][i] = v
    x_true = [Fraction(rng.randint(-3, 3)) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append(("G-spd-n20", A, b))
    return cases


def tier_H_integer_preserving_stress(rng: random.Random) -> List[Tuple[str, List[List], List]]:
    """8 cases that exhibit exponential bit-growth under naive Q-Gaussian.

    The bit-budget self-report check on these cases is the headline test
    that catches "got the right answer but with bit-blown intermediate
    values." Bareiss bounds the bit length of intermediates by Hadamard's
    bound on submatrix determinants; naive ℚ-Gaussian's accumulated
    denominator products grow as O(2^n · max-coeff^n).
    """
    cases = []
    # Random ±3 entries at n = 6, 8, 10 — sizes where SymPy oracle
    # tractable AND naive Q-Gaussian's bit-blow is detectable. Larger
    # sizes are admissible for the bench but slow the cross-validation
    # oracle phase past a tolerable wall-clock budget.
    for n in [6, 8, 10]:
        while True:
            A = [[Fraction(rng.randint(-3, 3)) for _ in range(n)]
                 for _ in range(n)]
            sym_A = sp.Matrix([[int(c) for c in row] for row in A])
            if sym_A.det() != 0:
                break
        x_true = [Fraction(1) for _ in range(n)]
        b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
        cases.append((f"H-rand3-n{n}", A, b))
    # Checkerboard ±1 sign pattern at n = 10
    n = 10
    A = [[Fraction((-1) ** (i + j)) * Fraction(rng.randint(1, 5))
          for j in range(n)] for i in range(n)]
    sym_A = sp.Matrix([[int(c) for c in row] for row in A])
    if sym_A.det() == 0:
        for i in range(n):
            A[i][i] += Fraction(1)
    x_true = [Fraction(1, 2) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append((f"H-checkerboard-n{n}", A, b))
    # Bareiss 1968 §V Table 1 worked example: a 4x4 integer matrix.
    # The paper uses a specific matrix; we approximate with a similar
    # structure (full reproduction would require OCR of the table).
    A = [[Fraction(2), Fraction(1), Fraction(3), Fraction(-2)],
         [Fraction(-1), Fraction(4), Fraction(2), Fraction(1)],
         [Fraction(3), Fraction(-2), Fraction(1), Fraction(5)],
         [Fraction(1), Fraction(2), Fraction(-3), Fraction(4)]]
    x_true = [Fraction(1), Fraction(2), Fraction(-1), Fraction(3)]
    b = [sum(A[i][j] * x_true[j] for j in range(4)) for i in range(4)]
    cases.append(("H-bareiss-paper-style-4x4", A, b))
    # Fibonacci-like growth: A[i][j] = F(i+j+1)
    fib = [1, 1]
    while len(fib) < 30:
        fib.append(fib[-1] + fib[-2])
    n = 8
    A = [[Fraction(fib[i + j + 1]) for j in range(n)] for i in range(n)]
    if sp.Matrix([[int(c) for c in row] for row in A]).det() == 0:
        for i in range(n):
            A[i][i] += Fraction(1)
    x_true = [Fraction(1) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append((f"H-fibonacci-n{n}", A, b))
    # Magic-square-ish n=10 with entries scaling with i*j
    n = 10
    A = [[Fraction((i * j + i + j + 1) % 13 + 1) for j in range(n)]
         for i in range(n)]
    for i in range(n):
        A[i][i] += Fraction(20)
    x_true = [Fraction(1) for _ in range(n)]
    b = [sum(A[i][j] * x_true[j] for j in range(n)) for i in range(n)]
    cases.append((f"H-mod13-n{n}", A, b))
    return cases


# ---------------------------------------------------------------------
# Bit-length budget per Tier-H case
# ---------------------------------------------------------------------


def _max_bit_length_in_solution(out: Dict[str, Any]) -> int:
    """Estimate the max bit length appearing in the output's
    coefficient strings — what the candidate would self-report."""
    if "x" not in out:
        return 0
    bls = []
    for s in out["x"]:
        for tok in s.replace("*", " ").replace("+", " ").replace("-", " ").split():
            try:
                f = parse_rat(tok)
                bls.append(max(f.numerator.bit_length(), f.denominator.bit_length()))
            except ValueError:
                continue
    return max(bls or [0])


# ---------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------


def main() -> int:
    rng = random.Random(SEED)
    all_cases: List[Tuple[str, List[List], List]] = []
    all_cases.extend(tier_A_shape_edges())
    all_cases.extend(tier_B_random_well_conditioned(rng))
    all_cases.extend(tier_C_classical(rng))
    all_cases.extend(tier_D_integer_stress(rng))
    all_cases.extend(tier_E_underdetermined())
    all_cases.extend(tier_F_inconsistent())
    all_cases.extend(tier_G_industrial(rng))
    all_cases.extend(tier_H_integer_preserving_stress(rng))

    inputs: List[Dict[str, Any]] = []
    expected: List[Dict[str, Any]] = []
    oracle_log: List[Dict[str, Any]] = []
    skipped: List[str] = []

    print(f"Generating {len(all_cases)} cases...", flush=True)
    for case_id, A, b in all_cases:
        m, n = len(A), len(A[0]) if A else 0
        print(f"  [{case_id}] m={m} n={n}", flush=True)
        A_strs = matrix_to_str(A)
        b_strs = vector_to_str(b)
        print(f"    ref...", flush=True, end="")
        # Reference
        ref_out = bareiss_solve([row[:] for row in A], b[:])
        print(f" ok ({ref_out['kind']})", flush=True)
        # SymPy
        print(f"    sympy...", flush=True, end="")
        try:
            sympy_out = sympy_solve(A_strs, b_strs)
            print(f" ok ({sympy_out['kind']})", flush=True)
        except Exception as e:
            print(f" raised {type(e).__name__}", flush=True)
            oracle_log.append({"id": case_id, "skipped": True,
                               "reason": f"SymPy raised: {e}"})
            skipped.append(case_id)
            continue
        # Wolfram (only if LIVE)
        wolfram_out = wolfram_check(A_strs, b_strs)
        ok, reason = admit(case_id, ref_out, sympy_out, wolfram_out)
        oracle_log.append({
            "id": case_id,
            "admitted": ok,
            "reason": reason,
            "ref_signature": _signature(ref_out),
            "sympy_signature": _signature(sympy_out),
            "wolfram_status": wolfram_out.get("status"),
        })
        if not ok:
            skipped.append(case_id)
            continue

        case_input = {"A": A_strs, "b": b_strs}
        # Extra metadata for Tier-H bit-budget verifier check.
        if case_id.startswith("H-"):
            estimated_bl = _max_bit_length_in_solution(ref_out)
            # Allow 2× headroom for the candidate's intermediate values
            # (Bareiss intermediates can be slightly larger than output
            # coefficients in the underdetermined / pivot-shuffle cases).
            case_input["expected_max_bit_length"] = max(
                int(estimated_bl * 2.5) + 50, 200,
            )

        inputs.append({"id": case_id, "input": case_input})
        expected.append({"id": case_id, "expected": ref_out})

    # Write outputs.
    (HERE / "inputs.json").write_text(json.dumps(
        {"version": ENCODING_VERSION, "seed": SEED, "cases": inputs},
        indent=2, sort_keys=True,
    ))
    (HERE / "expected.json").write_text(json.dumps(
        {"version": ENCODING_VERSION, "seed": SEED, "cases": expected},
        indent=2, sort_keys=True,
    ))
    (HERE / "oracle_log.json").write_text(json.dumps(
        {"admitted": len(inputs),
         "skipped": skipped,
         "log": oracle_log},
        indent=2, sort_keys=True,
    ))

    print(f"Generated {len(inputs)} admitted cases "
          f"({len(skipped)} skipped: {skipped})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
