#!/usr/bin/env python3
"""alg-num-arith verifier — invariant checks per ADR-0019 §1.

Reads `{id, tier, input, expected, candidate}` JSON on stdin, writes
`{pass, reason, checks}` JSON on stdout, exit 0 PASS / 1 FAIL.

Three lanes:

  expected.kind == "ok"      — arithmetic happy-path: 4 checks
                                (shape, canonical_form,
                                 vanishes_at_op_value,
                                 index_matches_real_position)
  expected.kind == "boolean" — eq: 2 checks (shape, matches_oracle)
  expected.kind == "tagged"  — refusal: 2 checks (shape,
                                refusal_class_matches)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

import sympy as sp

# Resolve sibling reference module
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BENCH_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(BENCH_ROOT, "reference"))

from alg_num_arith_reference import (  # noqa: E402
    oracle_compute,
    root_value_to_sympy_minpoly,
    root_value_to_sympy_k,
)

x = sp.Symbol("x")


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def verify(case: Dict[str, Any]) -> Dict[str, Any]:
    inp = case["input"]
    cand = case["candidate"]
    expected = case.get("expected", {})
    expected_kind = expected.get("kind")
    cand_kind = cand.get("kind")

    # Lied-about-scope: candidate landed in a different lane than
    # expected (e.g., expected refusal but emitted a Root[]; expected
    # boolean for an eq case but emitted a Root[]; expected an
    # arithmetic answer but refused).
    expected_lane = _expected_lane(expected_kind)
    cand_lane = _candidate_lane(cand_kind)
    if expected_lane is not None and expected_lane != cand_lane:
        return _aggregate({"shape": {
            "pass": False,
            "detail": (
                f"candidate lane={cand_lane!r} (kind={cand_kind!r}); "
                f"expected lane={expected_lane!r} (kind={expected_kind!r})"
            ),
        }})

    if expected_kind == "tagged":
        return _verify_refusal(cand, expected)
    if expected_kind == "boolean":
        return _verify_eq(inp, cand, expected)
    return _verify_arithmetic(inp, cand)


def _expected_lane(expected_kind: Any) -> Any:
    """Map expected.kind sentinel to the lane name."""
    if expected_kind == "ok":      return "arithmetic"
    if expected_kind == "boolean": return "eq"
    if expected_kind == "tagged":  return "refusal"
    return None


def _candidate_lane(cand_kind: Any) -> Any:
    """Map candidate.kind to the lane name."""
    if cand_kind == "expression": return "arithmetic"
    if cand_kind == "boolean":    return "eq"
    if cand_kind == "tagged":     return "refusal"
    return "unknown"


def _aggregate(checks: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    failed = [k for k, v in checks.items() if not v["pass"]]
    return {
        "pass":   len(failed) == 0,
        "reason": "all invariants hold" if not failed else f"failed: {', '.join(failed)}",
        "checks": checks,
    }


# ---------------------------------------------------------------------
# Arithmetic-happy lane
# ---------------------------------------------------------------------


def _verify_arithmetic(inp: Dict, cand: Dict) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    checks["shape"] = _check_shape_arithmetic(cand)
    if not checks["shape"]["pass"]:
        for k in ("canonical_form", "vanishes_at_op_value", "index_matches_real_position"):
            checks[k] = {"pass": False, "detail": "skipped: shape failed"}
        return _aggregate(checks)

    cand_minpoly = root_value_to_sympy_minpoly(cand)
    cand_k = root_value_to_sympy_k(cand)

    checks["canonical_form"] = _check_canonical_form(cand_minpoly)
    if not checks["canonical_form"]["pass"]:
        for k in ("vanishes_at_op_value", "index_matches_real_position"):
            checks[k] = {"pass": False, "detail": "skipped: canonical_form failed"}
        return _aggregate(checks)

    # Compute the oracle's numerical reference value.
    a = inp["a"]
    b = inp.get("b")
    op = inp["op"]
    try:
        oracle = oracle_compute(a, b, op)
    except Exception as e:  # noqa: BLE001
        checks["vanishes_at_op_value"] = {
            "pass": False, "detail": f"oracle failed: {e}",
        }
        checks["index_matches_real_position"] = {
            "pass": False, "detail": "skipped: oracle failed",
        }
        return _aggregate(checks)

    if oracle["kind"] != "ok":
        checks["vanishes_at_op_value"] = {
            "pass": False,
            "detail": f"oracle returned non-ok ({oracle['kind']}) for a 'kind=ok' candidate",
        }
        checks["index_matches_real_position"] = {
            "pass": False, "detail": "skipped: oracle non-ok",
        }
        return _aggregate(checks)

    nv = oracle["result_sympy"]
    checks["vanishes_at_op_value"] = _check_vanishes(cand_minpoly, nv)
    checks["index_matches_real_position"] = _check_index_position(cand_minpoly, cand_k, nv)
    return _aggregate(checks)


def _check_shape_arithmetic(cand: Dict) -> Dict[str, Any]:
    if cand.get("kind") != "expression":
        return {"pass": False, "detail": f"candidate.kind={cand.get('kind')!r}, expected 'expression'"}
    if cand.get("head") != "Root":
        return {"pass": False, "detail": f"candidate.head={cand.get('head')!r}, expected 'Root'"}
    args = cand.get("args", [])
    if len(args) != 2:
        return {"pass": False, "detail": f"len(args)={len(args)}, expected 2"}
    poly_arg, k_arg = args
    if poly_arg.get("kind") != "expression" or poly_arg.get("head") != "Polynomial":
        return {"pass": False, "detail": "args[0] must be Polynomial[]-headed expression"}
    coefs = poly_arg.get("args", [])
    if len(coefs) < 2:
        return {"pass": False, "detail": "Polynomial must have ≥ 2 coefficients (degree ≥ 1)"}
    for i, c in enumerate(coefs):
        if c.get("kind") != "integer":
            return {"pass": False,
                    "detail": f"coefficient {i} kind={c.get('kind')!r}, expected 'integer'"}
    if k_arg.get("kind") != "integer":
        return {"pass": False, "detail": f"k-arg kind={k_arg.get('kind')!r}, expected 'integer'"}
    try:
        k_val = int(k_arg["value"])
    except (KeyError, ValueError):
        return {"pass": False, "detail": "k-arg value not parseable as integer"}
    if k_val < 0 or k_val >= len(coefs) - 1:
        return {"pass": False,
                "detail": f"k={k_val} out of [0, deg={len(coefs) - 1})"}
    return {"pass": True}


def _check_canonical_form(p: sp.Poly) -> Dict[str, Any]:
    coefs = [int(c) for c in p.all_coeffs()]
    if not p.is_irreducible:
        return {"pass": False, "detail": f"minpoly {p.as_expr()} is reducible over ℚ"}
    g = abs(coefs[-1])
    for c in coefs[:-1]:
        g = sp.gcd(g, abs(c))
    if int(g) != 1:
        return {"pass": False, "detail": f"minpoly not primitive (gcd={g})"}
    if coefs[0] <= 0:
        return {"pass": False,
                "detail": f"leading coefficient {coefs[0]} not positive"}
    return {"pass": True}


def _check_vanishes(p: sp.Poly, nv: sp.Expr) -> Dict[str, Any]:
    """Evaluate p(nv) symbolically and assert it simplifies to 0
    (or, fallback, evaluates to ≈ 0 numerically)."""
    val = p.as_expr().subs(x, nv)
    s = sp.simplify(val)
    if s == 0:
        return {"pass": True}
    n = float(sp.Abs(val).evalf(50))
    deg = p.degree()
    nv_n = float(sp.Abs(nv).evalf(50))
    tol = 1e-12 * max(1.0, nv_n ** deg)
    if n < tol:
        return {"pass": True, "detail": f"numerical fallback: |p(nv)|={n} < tol={tol}"}
    return {
        "pass": False,
        "detail": f"|p(nv)|={n} ≥ tol={tol} (symbolic: {s})",
    }


def _check_index_position(p: sp.Poly, k: int, nv: sp.Expr) -> Dict[str, Any]:
    """Verify that nv numerically falls inside the k-th interval of
    p's ascending real-roots list."""
    real_roots = p.real_roots(multiple=True)
    if k >= len(real_roots):
        return {
            "pass": False,
            "detail": f"k={k} ≥ #real-roots ({len(real_roots)})",
        }
    target_n = float(nv.evalf(50))
    actual_n = float(real_roots[k].evalf(50))
    diff = abs(target_n - actual_n)
    tol = 1e-10 * max(1.0, abs(target_n))
    if diff < tol:
        return {"pass": True}
    # Find which k matches and report.
    for i, r in enumerate(real_roots):
        rn = float(r.evalf(50))
        if abs(rn - target_n) < tol:
            return {
                "pass": False,
                "detail": f"candidate k={k}, but oracle's nv={target_n} matches k={i}",
            }
    return {
        "pass": False,
        "detail": f"candidate k={k} (real_roots[k]={actual_n}), but oracle nv={target_n} doesn't match any k (diff to real_roots[k]={diff})",
    }


# ---------------------------------------------------------------------
# Eq lane
# ---------------------------------------------------------------------


def _verify_eq(inp: Dict, cand: Dict, expected: Dict) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    if cand.get("kind") != "boolean":
        return _aggregate({
            "shape": {"pass": False,
                      "detail": f"kind={cand.get('kind')!r}, expected 'boolean'"},
            "matches_oracle": {"pass": False, "detail": "skipped: shape failed"},
        })
    cand_val = cand.get("value")
    if cand_val not in (True, False):
        return _aggregate({
            "shape": {"pass": False,
                      "detail": f"value={cand_val!r}, must be true|false"},
            "matches_oracle": {"pass": False, "detail": "skipped: shape failed"},
        })
    checks["shape"] = {"pass": True}

    expected_val = expected.get("value")
    if cand_val != expected_val:
        checks["matches_oracle"] = {
            "pass": False,
            "detail": f"candidate={cand_val}, expected={expected_val}",
        }
    else:
        # Cross-check via SymPy oracle.
        try:
            oracle = oracle_compute(inp["a"], inp.get("b"), inp["op"])
        except Exception as e:  # noqa: BLE001
            checks["matches_oracle"] = {
                "pass": False, "detail": f"oracle failed: {e}",
            }
            return _aggregate(checks)
        if oracle.get("kind") != "boolean":
            checks["matches_oracle"] = {
                "pass": False,
                "detail": f"oracle returned non-boolean ({oracle.get('kind')})",
            }
        elif oracle["value"] != cand_val:
            checks["matches_oracle"] = {
                "pass": False,
                "detail": f"oracle says {oracle['value']}, candidate says {cand_val}",
            }
        else:
            checks["matches_oracle"] = {"pass": True}
    return _aggregate(checks)


# ---------------------------------------------------------------------
# Refusal lane
# ---------------------------------------------------------------------


def _verify_refusal(cand: Dict, expected: Dict) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    if cand.get("kind") != "tagged":
        checks["shape"] = {"pass": False, "detail": "non-tagged candidate"}
        checks["refusal_class_matches"] = {"pass": False, "detail": "skipped"}
        return _aggregate(checks)
    tag = cand.get("tag", "")
    if not tag.startswith("alg-num-arith/"):
        checks["shape"] = {
            "pass": False,
            "detail": f"tag {tag!r} not in alg-num-arith/* namespace",
        }
        checks["refusal_class_matches"] = {"pass": False, "detail": "skipped"}
        return _aggregate(checks)
    payload = cand.get("payload", {})
    if payload.get("kind") != "record":
        checks["shape"] = {"pass": False, "detail": "payload not a record"}
        checks["refusal_class_matches"] = {"pass": False, "detail": "skipped"}
        return _aggregate(checks)
    detail = payload.get("fields", {}).get("detail", {})
    if detail.get("kind") != "string" or not detail.get("value"):
        checks["shape"] = {"pass": False,
                           "detail": "payload.detail missing or empty"}
        checks["refusal_class_matches"] = {"pass": False, "detail": "skipped"}
        return _aggregate(checks)
    checks["shape"] = {"pass": True}

    expected_tag = expected.get("tag", "")
    if tag != expected_tag:
        checks["refusal_class_matches"] = {
            "pass": False,
            "detail": f"candidate tag={tag!r}, expected={expected_tag!r}",
        }
    else:
        checks["refusal_class_matches"] = {"pass": True}
    return _aggregate(checks)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def main() -> None:
    case = json.loads(sys.stdin.read())
    result = verify(case)
    print(json.dumps(result, indent=2, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
