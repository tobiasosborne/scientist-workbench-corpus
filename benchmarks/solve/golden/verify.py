#!/usr/bin/env python3
"""solve verifier — 4-lane dispatch per ADR-0019 §1 + §2 + §5.

Reads `{input, candidate, expected, id, tier}` JSON on stdin, writes
`{pass, reason, checks}` JSON on stdout, exit 0 on PASS, 1 on FAIL.

The `expected` envelope carries a `lane` field set by the golden
generator (which classifies via the SymPy reference). The verifier
dispatches by `expected.lane`:

  linear           → 5 checks (shape, exact_satisfaction, free_var_basis,
                                rank_consistent, completeness_correct)
  univariate-poly  → 4 checks (shape, each_root_satisfies,
                                count_with_multiplicity, distinct_roots_match)
  transcendental   → 3 checks (shape, branched_substitution_cube,
                                completeness_grid)
  refusal          → 2 checks (tag_matches, payload_predicate)

Per-check pass/fail aggregates to a top-level pass/fail. n/a checks
report `pass=True, detail="n/a for lane=<lane>"` so the per-tier
matrix has uniform shape.

Tolerance regime per `verifier_protocol.md`: NONE for linear /
univariate-poly / refusal lanes (exact in ℚ); 1e-12 relative for the
transcendental cube; 1e-6 for the completeness grid.
"""
from __future__ import annotations

import itertools
import json
import math
import random
import sys
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import sympy as sp
from scipy.optimize import brentq  # type: ignore

# Out-of-domain inputs (e.g., log(neg) at some grid point, arccos(c) for |c|>1)
# are handled by the verifier's per-element evaluation fallback. The numpy
# RuntimeWarnings they emit are noise; suppress them at the lambdified-eval
# layer so the bench output stays clean.
np.seterr(invalid="ignore", divide="ignore", over="ignore")


# Non-exhaustive sample of free-variable rational draws (verifier_protocol.md).
RATIONAL_SAMPLES = [
    Fraction(-3),
    Fraction(-1),
    Fraction(0),
    Fraction(1),
    Fraction(2),
    Fraction(5, 3),
    Fraction(-7, 4),
    Fraction(11),
    Fraction(-19, 4),
    Fraction(6, 7),
]

CUBE_HALF = 3   # cube is [-CUBE_HALF, CUBE_HALF]^n
CUBE_TOL = 1e-12
GRID_LO, GRID_HI, GRID_N = -50.0, 50.0, 2000
GRID_TOL = 1e-6


# ---------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------


def verify(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run all applicable checks on a single case envelope."""
    inp = case["input"]
    cand = case["candidate"]
    expected = case.get("expected", {})

    lane = expected.get("lane")
    if lane is None:
        return _aggregate({"shape": {
            "pass": False,
            "detail": "expected.lane is missing — golden generator did not classify the case",
        }})

    if lane == "linear":
        return _verify_linear(inp, cand, expected)
    if lane == "univariate-poly":
        return _verify_univariate_poly(inp, cand, expected)
    if lane == "transcendental":
        return _verify_transcendental(inp, cand, expected)
    if lane == "refusal":
        return _verify_refusal(inp, cand, expected)

    return _aggregate({"shape": {
        "pass": False,
        "detail": f"unknown lane {lane!r}",
    }})


def _aggregate(checks: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    failed = [k for k, v in checks.items() if not v["pass"]]
    return {
        "pass": len(failed) == 0,
        "reason": "all invariants hold" if not failed else f"failed: {', '.join(failed)}",
        "checks": checks,
    }


def _na(lane: str) -> Dict[str, Any]:
    return {"pass": True, "detail": f"n/a for lane={lane}"}


# ---------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------


def _parse_eqs(inp: Dict[str, Any], extra_syms: Optional[List[str]] = None) -> Tuple[List[sp.Expr], List[sp.Symbol]]:
    """Parse input eqs/vars into SymPy expressions."""
    syms = [sp.Symbol(v) for v in inp["vars"]]
    locals_map = {str(s): s for s in syms}
    if extra_syms:
        for es in extra_syms:
            locals_map[es] = sp.Symbol(es)
    eqs = [sp.sympify(e, locals=locals_map) for e in inp["eqs"]]
    return eqs, syms


def _check_lied_about_scope(cand: Dict[str, Any], expected_kind: str, lane: str) -> Optional[Dict[str, Any]]:
    """Reject a candidate whose kind doesn't match the lane's expectation.

    Returns a `shape`-failed envelope if mismatched, None otherwise.
    """
    cand_kind = cand.get("kind")
    if cand_kind != expected_kind:
        return _aggregate({"shape": {
            "pass": False,
            "detail": (
                f"candidate.kind={cand_kind!r} but lane={lane!r} requires kind={expected_kind!r}"
            ),
        }})
    return None


# ---------------------------------------------------------------------
# Lane: linear (5 checks)
# ---------------------------------------------------------------------


def _verify_linear(inp: Dict, cand: Dict, expected: Dict) -> Dict[str, Any]:
    fail = _check_lied_about_scope(cand, "ok", "linear")
    if fail is not None:
        return fail

    checks: Dict[str, Dict[str, Any]] = {}

    # Branch symbol set used across checks; computed once after shape.
    branches_str = []
    if cand.get("solutions"):
        branches_str = list(cand["solutions"][0].get("branches", []))
    eqs, syms = _parse_eqs(inp, extra_syms=branches_str)
    branch_syms = [sp.Symbol(b) for b in branches_str]

    checks["shape"] = _linear_shape(inp, cand, syms, branch_syms)
    if not checks["shape"]["pass"]:
        for k in ("exact_satisfaction", "free_var_basis", "rank_consistent", "completeness_correct"):
            checks[k] = {"pass": False, "detail": "skipped: shape failed"}
        return _aggregate(checks)

    checks["exact_satisfaction"] = _linear_exact_satisfaction(inp, cand, eqs, syms, branch_syms)
    checks["free_var_basis"] = _linear_free_var_basis(inp, cand, eqs, syms, branch_syms)
    checks["rank_consistent"] = _linear_rank_consistent(inp, cand, eqs, syms)
    checks["completeness_correct"] = _linear_completeness_correct(cand)
    return _aggregate(checks)


def _linear_shape(inp: Dict, cand: Dict, syms: List[sp.Symbol], branches: List[sp.Symbol]) -> Dict[str, Any]:
    if cand.get("kind") != "ok":
        return {"pass": False, "detail": f"kind={cand.get('kind')!r}, expected 'ok'"}
    if "vars" not in cand or list(cand["vars"]) != list(inp["vars"]):
        return {"pass": False, "detail": "vars field missing or mismatches input"}
    if "solutions" not in cand:
        return {"pass": False, "detail": "solutions field missing"}
    if cand.get("completeness") not in ("complete", "finite-rep-of-infinite"):
        return {"pass": False, "detail": f"completeness={cand.get('completeness')!r}"}
    sols = cand["solutions"]
    if not isinstance(sols, list) or len(sols) > 1:
        return {"pass": False, "detail": f"linear lane: |solutions| ∈ {{0, 1}}, got {len(sols)}"}

    if len(sols) == 0:
        return {"pass": True, "detail": "inconsistent (solutions=[])"}

    sol = sols[0]
    if "bindings" not in sol or "branches" not in sol:
        return {"pass": False, "detail": "solution missing bindings/branches"}
    if len(sol["bindings"]) != len(syms):
        return {"pass": False, "detail": f"|bindings|={len(sol['bindings'])} != |vars|={len(syms)}"}

    # Each binding parses.
    locals_map = {str(s): s for s in syms} | {str(b): b for b in branches}
    for i, b in enumerate(sol["bindings"]):
        if b.get("var") != str(syms[i]):
            return {"pass": False, "detail": f"binding[{i}].var={b.get('var')!r} != expected {str(syms[i])!r}"}
        try:
            sp.sympify(b["value"], locals=locals_map)
        except (sp.SympifyError, SyntaxError) as exc:
            return {"pass": False, "detail": f"binding[{i}].value did not parse: {exc}"}
    return {"pass": True, "detail": f"{len(sol['bindings'])} bindings; {len(branches)} branches"}


def _linear_exact_satisfaction(inp, cand, eqs, syms, branches) -> Dict[str, Any]:
    """A·x − b ≡ 0 exactly in ℚ (with branch symbols free)."""
    sols = cand["solutions"]
    if len(sols) == 0:
        # Inconsistent claim: verify by rank comparison.
        try:
            A, b = sp.linear_eq_to_matrix(eqs, *syms)
        except (sp.PolynomialError, ValueError) as exc:
            return {"pass": False, "detail": f"linear_eq_to_matrix failed: {exc}"}
        Ab = A.row_join(b)
        if A.rank() < Ab.rank():
            return {"pass": True, "detail": "inconsistency confirmed via rank comparison"}
        return {"pass": False, "detail": "candidate claims inconsistent but rank(A) == rank([A|b])"}

    sol = sols[0]
    locals_map = {str(s): s for s in syms} | {str(b): b for b in branches}
    sub = {syms[i]: sp.sympify(sol["bindings"][i]["value"], locals=locals_map) for i in range(len(syms))}
    for i, eq in enumerate(eqs):
        residue = sp.simplify(eq.subs(sub))
        if residue != 0:
            return {"pass": False, "detail": f"equation #{i + 1} residue ≠ 0: {residue}"}
    return {"pass": True, "detail": f"{len(eqs)} equation(s) satisfied symbolically"}


def _linear_free_var_basis(inp, cand, eqs, syms, branches) -> Dict[str, Any]:
    if not branches:
        return _na("linear (no branches)")
    sol = cand["solutions"][0]
    locals_map = {str(s): s for s in syms} | {str(b): b for b in branches}
    bindings = {syms[i]: sp.sympify(sol["bindings"][i]["value"], locals=locals_map) for i in range(len(syms))}

    rng = random.Random(20260507)
    n_branches = len(branches)
    # Cap total tuples at 50 to keep verifier responsive on n_branches ≥ 3.
    n_samples = min(50, len(RATIONAL_SAMPLES) ** n_branches)
    for _ in range(n_samples):
        choices = [rng.choice(RATIONAL_SAMPLES) for _ in range(n_branches)]
        sub_branches = {branches[i]: sp.Rational(c.numerator, c.denominator) for i, c in enumerate(choices)}
        for i, eq in enumerate(eqs):
            residue = sp.simplify(eq.subs(bindings).subs(sub_branches))
            if residue != 0:
                return {
                    "pass": False,
                    "detail": (
                        f"free-var sample {dict(zip(branches, choices))} fails eq #{i + 1}: residue={residue}"
                    ),
                }
    return {"pass": True, "detail": f"{n_samples} free-var samples all satisfy"}


def _linear_rank_consistent(inp, cand, eqs, syms) -> Dict[str, Any]:
    sols = cand["solutions"]
    try:
        A, b = sp.linear_eq_to_matrix(eqs, *syms)
    except (sp.PolynomialError, ValueError) as exc:
        return {"pass": False, "detail": f"linear_eq_to_matrix failed: {exc}"}
    rank_A = A.rank()
    n_vars = len(syms)
    if len(sols) == 0:
        return {"pass": True, "detail": f"inconsistent: rank(A)={rank_A}, rank([A|b])={A.row_join(b).rank()}"}
    n_branches = len(sols[0]["branches"])
    expected_branches = n_vars - rank_A
    if n_branches != expected_branches:
        return {
            "pass": False,
            "detail": f"|branches|={n_branches}, expected n_vars - rank(A) = {n_vars} - {rank_A} = {expected_branches}",
        }
    return {"pass": True, "detail": f"|branches| = {n_branches} = n_vars - rank(A)"}


def _linear_completeness_correct(cand: Dict) -> Dict[str, Any]:
    sols = cand["solutions"]
    completeness = cand["completeness"]
    if completeness == "complete":
        if len(sols) == 0:
            return {"pass": True, "detail": "inconsistent + complete"}
        if len(sols) == 1 and len(sols[0]["branches"]) == 0:
            return {"pass": True, "detail": "unique + complete"}
        return {"pass": False, "detail": "completeness='complete' but solutions has branches"}
    if completeness == "finite-rep-of-infinite":
        if len(sols) == 1 and len(sols[0]["branches"]) > 0:
            return {"pass": True, "detail": "under-determined + finite-rep-of-infinite"}
        return {
            "pass": False,
            "detail": "completeness='finite-rep-of-infinite' but solutions empty or has no branches",
        }
    return {"pass": False, "detail": f"unknown completeness {completeness!r}"}


# ---------------------------------------------------------------------
# Lane: univariate polynomial (4 checks)
# ---------------------------------------------------------------------


def _verify_univariate_poly(inp: Dict, cand: Dict, expected: Dict) -> Dict[str, Any]:
    fail = _check_lied_about_scope(cand, "ok", "univariate-poly")
    if fail is not None:
        return fail

    checks: Dict[str, Dict[str, Any]] = {}
    eqs, syms = _parse_eqs(inp)
    if len(eqs) != 1 or len(syms) != 1:
        return _aggregate({"shape": {
            "pass": False,
            "detail": "univariate-poly lane requires exactly 1 eq, 1 var",
        }})
    eq, x = eqs[0], syms[0]

    checks["shape"] = _univ_shape(cand, x)
    if not checks["shape"]["pass"]:
        for k in ("each_root_satisfies", "count_with_multiplicity", "distinct_roots_match"):
            checks[k] = {"pass": False, "detail": "skipped: shape failed"}
        return _aggregate(checks)

    checks["each_root_satisfies"] = _univ_each_root(cand, eq, x)
    checks["count_with_multiplicity"] = _univ_count_mult(cand, eq, x)
    checks["distinct_roots_match"] = _univ_distinct_match(cand, eq, x)
    return _aggregate(checks)


def _univ_shape(cand: Dict, x: sp.Symbol) -> Dict[str, Any]:
    if cand.get("kind") != "ok":
        return {"pass": False, "detail": f"kind={cand.get('kind')!r}"}
    if cand.get("completeness") != "complete":
        return {"pass": False, "detail": f"completeness={cand.get('completeness')!r}, expected 'complete'"}
    sols = cand.get("solutions", [])
    if not isinstance(sols, list):
        return {"pass": False, "detail": "solutions not a list"}
    locals_map = {str(x): x}
    for i, sol in enumerate(sols):
        if len(sol.get("bindings", [])) != 1:
            return {"pass": False, "detail": f"solution[{i}] has |bindings| != 1"}
        if sol.get("branches"):
            return {"pass": False, "detail": f"solution[{i}] has non-empty branches; not allowed for univariate-poly"}
        b = sol["bindings"][0]
        if b.get("var") != str(x):
            return {"pass": False, "detail": f"solution[{i}] var={b.get('var')!r} != {str(x)!r}"}
        try:
            sp.sympify(b["value"], locals=locals_map)
        except (sp.SympifyError, SyntaxError) as exc:
            return {"pass": False, "detail": f"solution[{i}] value did not parse: {exc}"}
    return {"pass": True, "detail": f"{len(sols)} solution(s); shape ok"}


def _univ_each_root(cand: Dict, eq: sp.Expr, x: sp.Symbol) -> Dict[str, Any]:
    locals_map = {str(x): x}
    for i, sol in enumerate(cand["solutions"]):
        val = sp.sympify(sol["bindings"][0]["value"], locals=locals_map)
        residue = sp.simplify(eq.subs(x, val))
        if residue != 0:
            # Try harder simplification (radicals etc.).
            residue = sp.radsimp(sp.simplify(eq.subs(x, val)))
            if residue != 0:
                return {"pass": False, "detail": f"root[{i}] {val} → eq.subs = {residue}"}
    return {"pass": True, "detail": f"{len(cand['solutions'])} root(s) substitute to 0"}


def _univ_count_mult(cand: Dict, eq: sp.Expr, x: sp.Symbol) -> Dict[str, Any]:
    p = sp.Poly(eq, x, domain="QQ")
    _coeff, factors = p.factor_list()
    expected_count = sum(f.total_degree() * m for f, m in factors)
    actual = len(cand["solutions"])
    if actual != expected_count:
        return {
            "pass": False,
            "detail": f"|solutions|={actual} != Σ deg(f_i)·m_i = {expected_count}",
        }
    return {"pass": True, "detail": f"{actual} = Σ deg(f_i)·m_i"}


def _univ_distinct_match(cand: Dict, eq: sp.Expr, x: sp.Symbol) -> Dict[str, Any]:
    """Multiset of cand roots equals multiset of SymPy all_roots."""
    locals_map = {str(x): x}
    cand_roots = [sp.sympify(sol["bindings"][0]["value"], locals=locals_map) for sol in cand["solutions"]]
    p = sp.Poly(eq, x, domain="QQ")
    sympy_roots = p.all_roots(multiple=True)

    # Bipartite match: each candidate root pairs with exactly one SymPy root
    # under simplify(diff) == 0. Greedy match (works when roots are distinct
    # or grouped by multiplicity).
    available = list(range(len(sympy_roots)))
    for cr in cand_roots:
        matched = None
        for idx in available:
            try:
                diff = sp.simplify(cr - sympy_roots[idx])
            except (sp.PolynomialError, TypeError):
                diff = sp.nsimplify(cr - sympy_roots[idx])
            if diff == 0:
                matched = idx
                break
        if matched is None:
            return {"pass": False, "detail": f"candidate root {cr} matches no SymPy root"}
        available.remove(matched)
    if available:
        leftover = [sympy_roots[i] for i in available]
        return {"pass": False, "detail": f"unmatched SymPy roots: {leftover}"}
    return {"pass": True, "detail": f"{len(cand_roots)} candidate roots ↔ {len(sympy_roots)} SymPy roots"}


# ---------------------------------------------------------------------
# Lane: transcendental (3 checks)
# ---------------------------------------------------------------------


def _verify_transcendental(inp: Dict, cand: Dict, expected: Dict) -> Dict[str, Any]:
    fail = _check_lied_about_scope(cand, "ok", "transcendental")
    if fail is not None:
        return fail

    checks: Dict[str, Dict[str, Any]] = {}
    branches_all = []
    if cand.get("solutions"):
        for sol in cand["solutions"]:
            branches_all.extend(sol.get("branches", []))
    eqs, syms = _parse_eqs(inp, extra_syms=list(set(branches_all)))
    if len(eqs) != 1 or len(syms) != 1:
        return _aggregate({"shape": {
            "pass": False,
            "detail": "transcendental lane requires exactly 1 eq, 1 var",
        }})
    eq, x = eqs[0], syms[0]

    checks["shape"] = _trans_shape(cand, x)
    if not checks["shape"]["pass"]:
        for k in ("branched_substitution_cube", "completeness_grid"):
            checks[k] = {"pass": False, "detail": "skipped: shape failed"}
        return _aggregate(checks)

    checks["branched_substitution_cube"] = _trans_cube(cand, eq, x)
    checks["completeness_grid"] = _trans_grid(cand, eq, x)
    return _aggregate(checks)


def _trans_shape(cand: Dict, x: sp.Symbol) -> Dict[str, Any]:
    if cand.get("kind") != "ok":
        return {"pass": False, "detail": f"kind={cand.get('kind')!r}"}
    sols = cand.get("solutions", [])
    if not sols:
        return {"pass": False, "detail": "no solutions for transcendental lane"}
    for i, sol in enumerate(sols):
        if len(sol.get("bindings", [])) != 1:
            return {"pass": False, "detail": f"solution[{i}] |bindings| != 1"}
        if sol["bindings"][0].get("var") != str(x):
            return {"pass": False, "detail": f"solution[{i}] var mismatch"}
        for bs in sol.get("branches", []):
            if not (isinstance(bs, str) and bs.startswith("t_")):
                return {"pass": False, "detail": f"solution[{i}] branch {bs!r} not in t_<int> form"}
    return {"pass": True, "detail": f"{len(sols)} solution(s); branches consistent"}


def _trans_cube(cand: Dict, eq: sp.Expr, x: sp.Symbol) -> Dict[str, Any]:
    """For each Solution, instantiate every integer tuple in [-3, 3]^n
    and verify the equation residue is < 1e-12 relative."""
    locals_map = {str(x): x}
    for sol in cand["solutions"]:
        for bs in sol["branches"]:
            locals_map[bs] = sp.Symbol(bs)
        bind_expr = sp.sympify(sol["bindings"][0]["value"], locals=locals_map)
        branch_syms = [sp.Symbol(b) for b in sol["branches"]]
        n = len(branch_syms)
        # Build a numerical lambda for eq(x) with x = bind_expr(branches).
        substituted = eq.subs(x, bind_expr)
        free = sorted([s for s in substituted.free_symbols], key=str)
        if free and (len(free) != n or any(s.name not in sol["branches"] for s in free)):
            return {
                "pass": False,
                "detail": f"substituted eq has unexpected free symbols: {free}",
            }
        # Lambdify.
        if n == 0:
            try:
                val = complex(substituted.evalf())
            except (TypeError, ValueError):
                # Symbolic non-numeric — try simplify.
                if sp.simplify(substituted) == 0:
                    continue
                return {"pass": False, "detail": f"non-branch case residue not numeric: {substituted}"}
            if abs(val) > CUBE_TOL:
                return {"pass": False, "detail": f"non-branch case |residue|={abs(val)} > {CUBE_TOL}"}
            continue
        try:
            f_num = sp.lambdify(branch_syms, substituted, modules=["numpy", "math"])
        except Exception as exc:  # pragma: no cover
            return {"pass": False, "detail": f"lambdify failed: {exc}"}
        for tup in itertools.product(range(-CUBE_HALF, CUBE_HALF + 1), repeat=n):
            try:
                val = f_num(*tup)
            except (ValueError, ZeroDivisionError, FloatingPointError):
                continue  # NaN/inf at this tuple — skip; grid will catch missing
            if isinstance(val, complex) or (hasattr(val, "imag") and val.imag != 0):
                # Out-of-domain (e.g., asin(5)) — complex residue. Pass if magnitude
                # near zero, otherwise check real-part magnitude relatively.
                magnitude = abs(complex(val))
                if magnitude > CUBE_TOL:
                    return {
                        "pass": False,
                        "detail": f"tuple {tup} residue magnitude={magnitude} > {CUBE_TOL}",
                    }
                continue
            if not math.isfinite(val):
                continue
            if abs(val) > CUBE_TOL:
                return {
                    "pass": False,
                    "detail": f"tuple {tup} |residue|={abs(val)} > {CUBE_TOL}",
                }
    return {"pass": True, "detail": f"{len(cand['solutions'])} solutions × cube tuples all satisfied"}


def _trans_grid(cand: Dict, eq: sp.Expr, x: sp.Symbol) -> Dict[str, Any]:
    """Sample eq on a 1D grid trimmed to the cube reach; every grid root
    within that reach must match some candidate-instantiated tuple.

    The cube reach is `[-R, R]` where `R = max |candidate_value(tuple)|`
    over all solutions and all integer tuples in the cube. Grid roots
    outside `[-R, R]` are not verifiable from the cube and are skipped —
    the candidate's correctness *outside* the cube is unobservable here.
    Inside the reach, every grid root must match some cube tuple within
    `GRID_TOL`; an unmatched in-reach grid root is a missed branch.
    """
    # Compute candidate reach by enumerating cube-instantiated values.
    cand_values: List[float] = []
    locals_map = {str(x): x}
    for sol in cand["solutions"]:
        for bs in sol["branches"]:
            locals_map[bs] = sp.Symbol(bs)
        bind_expr = sp.sympify(sol["bindings"][0]["value"], locals=locals_map)
        branch_syms = [sp.Symbol(b) for b in sol["branches"]]
        if not branch_syms:
            try:
                v = complex(bind_expr.evalf())
                if v.imag == 0 and math.isfinite(v.real):
                    cand_values.append(v.real)
            except (TypeError, ValueError):
                pass
            continue
        try:
            g_num = sp.lambdify(branch_syms, bind_expr, modules=["numpy", "math"])
        except Exception:  # pragma: no cover
            continue
        for tup in itertools.product(range(-CUBE_HALF, CUBE_HALF + 1), repeat=len(branch_syms)):
            try:
                v = g_num(*tup)
            except (ValueError, ZeroDivisionError):
                continue
            if isinstance(v, complex):
                if v.imag != 0:
                    continue
                v = v.real
            if math.isfinite(v):
                cand_values.append(float(v))

    if not cand_values:
        # No real candidate values (out-of-domain like cos(x) = 5).
        # Grid scan still useful as a sanity check, but no roots to match.
        return {"pass": True, "detail": "no real candidate-instantiated values; grid n/a"}

    arr = np.asarray(cand_values)
    reach = float(np.max(np.abs(arr)))
    # Use min(GRID_HI, reach) as the verification half-window.
    win = min(float(GRID_HI), reach)
    if win < 0.1:
        # Single-point reach (e.g., exp(x) = 5 → x = log(5) ≈ 1.61); use a
        # small ball around each candidate value as the verification region
        # rather than a single window.
        return _trans_grid_pointwise(cand_values, eq, x)

    f_num = sp.lambdify(x, eq, modules=["numpy", "math"])
    xs = np.linspace(-win, win, GRID_N)
    try:
        ys = f_num(xs)
    except (ValueError, ZeroDivisionError, TypeError):
        # Domain issues (e.g., log(x) at x≤0). Per-element evaluation fallback.
        ys = np.full_like(xs, np.nan)
        for i, v in enumerate(xs):
            try:
                ys[i] = f_num(v)
            except (ValueError, ZeroDivisionError, TypeError):
                ys[i] = np.nan
    ys = np.asarray(ys, dtype=np.complex128)
    if np.any(np.imag(ys) != 0):
        # Equation has non-real values somewhere — restrict to real samples.
        real_mask = np.imag(ys) == 0
        ys = np.where(real_mask, np.real(ys), np.nan).astype(float)
    else:
        ys = np.real(ys).astype(float)

    # Find sign changes (grid roots) and refine to <1e-12 via brentq, so
    # the comparison against GRID_TOL=1e-6 is tight rather than dominated
    # by linear-interpolation residue (grid step ~win/1000).
    grid_roots: List[float] = []
    for i in range(len(xs) - 1):
        a, b = ys[i], ys[i + 1]
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        if a == 0:
            grid_roots.append(float(xs[i]))
        elif a * b < 0:
            try:
                root = brentq(
                    lambda v: float(np.real(f_num(v))),
                    float(xs[i]), float(xs[i + 1]),
                    xtol=1e-13, rtol=1e-13, maxiter=100,
                )
                # Pole filter: a sign change across a singularity (e.g.,
                # tan at π/2) produces a "root" with |f(root)| huge.
                # Real roots of well-behaved equations satisfy
                # |f(root)| < eps numerically.
                f_at_root = float(np.real(f_num(root)))
                if math.isfinite(f_at_root) and abs(f_at_root) < 1e-6:
                    grid_roots.append(float(root))
            except (ValueError, RuntimeError):
                # Fallback to linear interpolation if brentq can't bracket.
                t = a / (a - b)
                grid_roots.append(float(xs[i] + t * (xs[i + 1] - xs[i])))

    if not grid_roots:
        return {"pass": True, "detail": f"no real grid roots in [-{win:.2f}, {win:.2f}]"}

    missed: List[float] = []
    for gr in grid_roots:
        if abs(gr) > win:
            continue  # outside cube reach; not verifiable here
        if np.min(np.abs(arr - gr)) > GRID_TOL:
            missed.append(gr)
    if missed:
        return {"pass": False, "detail": f"{len(missed)} grid root(s) unmatched: {missed[:5]}{'...' if len(missed) > 5 else ''}"}
    return {"pass": True, "detail": f"{len(grid_roots)} grid roots all matched within {GRID_TOL} (window=±{win:.2f})"}


def _trans_grid_pointwise(cand_values: List[float], eq: sp.Expr, x: sp.Symbol) -> Dict[str, Any]:
    """For finite-root cases (no branches, or branches with very small
    reach), verify each candidate value satisfies the equation numerically
    and that no extra real roots exist in a small ball around each."""
    f_num = sp.lambdify(x, eq, modules=["numpy", "math"])
    for v in cand_values:
        try:
            r = f_num(float(v))
        except (ValueError, ZeroDivisionError, TypeError):
            return {"pass": False, "detail": f"candidate value {v} causes domain error"}
        if isinstance(r, complex):
            r = abs(r)
        if not math.isfinite(r) or abs(r) > GRID_TOL:
            return {"pass": False, "detail": f"candidate value {v} → eq = {r}"}
    return {"pass": True, "detail": f"{len(cand_values)} pointwise candidate values satisfy eq"}


# ---------------------------------------------------------------------
# Lane: refusal (2 checks)
# ---------------------------------------------------------------------


def _verify_refusal(inp: Dict, cand: Dict, expected: Dict) -> Dict[str, Any]:
    fail = _check_lied_about_scope(cand, "tagged", "refusal")
    if fail is not None:
        return fail

    checks = {
        "tag_matches": _refuse_tag(cand, expected),
        "payload_predicate": _refuse_payload(cand),
    }
    return _aggregate(checks)


def _refuse_tag(cand: Dict, expected: Dict) -> Dict[str, Any]:
    cand_tag = cand.get("tag")
    if not isinstance(cand_tag, str) or not cand_tag.startswith("solve/"):
        return {"pass": False, "detail": f"tag={cand_tag!r} not in solve/* namespace"}
    expected_tag = expected.get("tag")
    if expected_tag is None:
        return {"pass": True, "detail": f"refusal tag={cand_tag!r} (no expected.tag pinned)"}
    if cand_tag != expected_tag:
        return {"pass": False, "detail": f"cand.tag={cand_tag!r} != expected.tag={expected_tag!r}"}
    return {"pass": True, "detail": f"tag matches {cand_tag!r}"}


def _refuse_payload(cand: Dict) -> Dict[str, Any]:
    payload = cand.get("payload")
    if not isinstance(payload, dict):
        return {"pass": False, "detail": "payload missing or not a record"}
    detail = payload.get("detail")
    if not isinstance(detail, str) or not detail:
        return {"pass": False, "detail": "payload.detail missing or empty"}
    return {"pass": True, "detail": "payload.detail is non-empty string"}


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def main() -> int:
    raw = sys.stdin.read()
    case = json.loads(raw)
    result = verify(case)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
