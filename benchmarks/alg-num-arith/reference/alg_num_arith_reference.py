#!/usr/bin/env python3
"""SymPy-backed reference for `tools/alg-num-arith`.

Two responsibilities:

1. **Oracle evaluation.** Given a wire-encoded `Root[poly, k]`
   expression, return the corresponding SymPy `AlgebraicNumber`-class
   value (numerical or symbolic) so the verifier can compute
   `op(a, b)` to high precision and check the candidate's polynomial
   vanishes there.

2. **Reference behaviour.** Given a (a, b, op) triple, return the
   SymPy-computed minpoly and k of `op(a, b)`. The verifier compares
   the candidate's minpoly to this reference, but the *binding*
   verification is the polynomial-vanishes-at-numerical-value check
   (so the bench tolerates representation freedom in the candidate
   that a strict byte-equality check would mark wrong).

Wire encoding (ADR-0018):

    {"kind": "expression", "head": "Root", "args": [
       {"kind": "expression", "head": "Polynomial", "args": [<int c_0>, ..., <int c_n>]},
       {"kind": "integer", "value": "<k>"}
    ]}

The polynomial is integer-coefficient low-to-high in the implicit
variable `x`; `k` is 0-indexed in the canonical sort order (real
roots first ascending, then complex by (Im, Re)). v0.1 of this
oracle handles real-only Roots.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import sympy as sp


CLS_INV_OF_ZERO = "alg-num-arith/inv-of-zero"
CLS_DIV_BY_ZERO = "alg-num-arith/div-by-zero"


x = sp.Symbol("x")


# ---------------------------------------------------------------------
# Wire-encoded Root[] ⟷ SymPy
# ---------------------------------------------------------------------


def root_value_to_sympy_minpoly(root_value: Dict[str, Any]) -> sp.Poly:
    """Decode a wire `Root[poly, k]` value into the SymPy `Poly` for
    its minimal polynomial."""
    if root_value.get("kind") != "expression" or root_value.get("head") != "Root":
        raise ValueError(f"expected Root[]-headed expression, got {root_value!r}")
    args = root_value["args"]
    if len(args) != 2:
        raise ValueError(f"Root expression must have 2 args, got {len(args)}")
    poly_arg = args[0]
    if poly_arg.get("kind") != "expression" or poly_arg.get("head") != "Polynomial":
        raise ValueError(f"first Root arg must be Polynomial[], got {poly_arg!r}")
    coefs_low_to_high = [int(c["value"]) for c in poly_arg["args"]]
    # SymPy Poly takes coefficients high-to-low.
    coefs_high_to_low = list(reversed(coefs_low_to_high))
    return sp.Poly(coefs_high_to_low, x, domain="QQ")


def root_value_to_sympy_k(root_value: Dict[str, Any]) -> int:
    """Decode the index `k`."""
    args = root_value["args"]
    return int(args[1]["value"])


def root_value_to_sympy_real(root_value: Dict[str, Any]) -> sp.Expr:
    """Decode a wire `Root[]` to a SymPy real algebraic number.

    Picks the k-th real root of the minpoly in ascending order — the
    workbench's sort convention (ADR-0018 §"Index k", real prefix).
    Returns a high-precision SymPy expression suitable for
    `sympy.simplify` / `evalf` against arithmetic results.
    """
    p = root_value_to_sympy_minpoly(root_value)
    k = root_value_to_sympy_k(root_value)
    real_roots = sp.Poly(p, x, domain="QQ").real_roots(multiple=True)
    if k >= len(real_roots):
        raise ValueError(
            f"k={k} ≥ #real roots ({len(real_roots)}) of {p.as_expr()}"
        )
    return real_roots[k]


# ---------------------------------------------------------------------
# Oracle reference for an (a, b, op) triple
# ---------------------------------------------------------------------


def oracle_compute(a_value: Dict[str, Any],
                   b_value: Optional[Dict[str, Any]],
                   op: str) -> Dict[str, Any]:
    """Compute the SymPy oracle reference for `op(a, b)`.

    Returns one of:

      {"kind": "ok",
       "result_sympy": <sp.Expr>,                  # numerical value
       "result_minpoly_high_to_low": [<int>, ...], # minpoly in ℤ[x]
       "result_k": <int>}                          # index in real-root list

      {"kind": "boolean", "value": <bool>}            # for op == "eq"

      {"kind": "tagged", "tag": "<class>", "detail": "<str>"}  # refusals

    The minpoly + k are computed via `sp.minimal_polynomial(...)`
    and `sp.Poly.intervals()`; the verifier uses `result_sympy` as
    the high-precision numerical witness for the
    `vanishes_at_op_value` check.
    """
    a_sym = root_value_to_sympy_real(a_value)
    b_sym = (root_value_to_sympy_real(b_value) if b_value else None)

    # Refusal short-circuits
    if op == "inv" and a_sym == 0:
        return {"kind": "tagged", "tag": CLS_INV_OF_ZERO,
                "detail": "inv(0) is undefined"}
    if op == "div" and b_sym == 0:
        return {"kind": "tagged", "tag": CLS_DIV_BY_ZERO,
                "detail": "division by zero"}

    if op == "eq":
        return {"kind": "boolean", "value": _sympy_alg_eq(a_sym, b_sym)}

    # Arithmetic
    if op == "add":
        result = a_sym + b_sym
    elif op == "sub":
        result = a_sym - b_sym
    elif op == "mul":
        result = a_sym * b_sym
    elif op == "div":
        result = a_sym / b_sym
    elif op == "neg":
        result = -a_sym
    elif op == "inv":
        result = 1 / a_sym
    else:
        raise ValueError(f"unknown op: {op}")

    # Compute the canonical minpoly (irreducible primitive ℤ[x],
    # positive leading) of the result.
    mp_expr = sp.minimal_polynomial(result, x)
    mp_poly = sp.Poly(mp_expr, x, domain="QQ")
    mp_int = _canonical_int_poly(mp_poly)
    coefs_high_to_low = list(mp_int.all_coeffs())
    real_roots = sp.Poly(mp_int, x, domain="QQ").real_roots(multiple=True)
    # Find the index of `result` in the real-roots list.
    k = _find_real_root_index(real_roots, result)

    return {
        "kind": "ok",
        "result_sympy": result,
        "result_minpoly_high_to_low": [int(c) for c in coefs_high_to_low],
        "result_k": k,
    }


def _sympy_alg_eq(a: sp.Expr, b: sp.Expr) -> bool:
    """Algebraic-number equality via simplify on the difference."""
    diff = sp.simplify(a - b)
    if diff == 0:
        return True
    # Numerical fallback for cases sympy.simplify is conservative on.
    n = complex(diff.evalf(50))
    return abs(n) < 1e-30


def _canonical_int_poly(p: sp.Poly) -> sp.Poly:
    """Clear denominators, strip integer content, sign-fix leading."""
    # sp.Poly.all_coeffs() returns Rational; clear via lcm of denominators.
    coefs = [sp.Rational(c) for c in p.all_coeffs()]
    denom_lcm = sp.lcm([sp.denom(c) for c in coefs])
    int_coefs = [int(c * denom_lcm) for c in coefs]
    g = abs(int_coefs[0])
    for c in int_coefs[1:]:
        g = sp.gcd(g, abs(c))
    g = max(int(g), 1)
    int_coefs = [c // g for c in int_coefs]
    if int_coefs[0] < 0:
        int_coefs = [-c for c in int_coefs]
    return sp.Poly(int_coefs, x, domain="QQ")


def _find_real_root_index(real_roots: list, target: sp.Expr) -> int:
    """Identify `target` in the ascending list of real roots."""
    target_n = float(target.evalf(50))
    best_idx = -1
    best_diff = float("inf")
    for i, r in enumerate(real_roots):
        rn = float(r.evalf(50))
        d = abs(rn - target_n)
        if d < best_diff:
            best_diff = d
            best_idx = i
    if best_diff > 1e-20:
        # A wider tolerance fallback for high-degree minpolys.
        if best_diff > 1e-10:
            raise ValueError(
                f"target {target_n} doesn't match any real root in {real_roots} "
                f"(closest diff {best_diff})"
            )
    return best_idx


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def main() -> None:
    import json
    import sys

    if len(sys.argv) < 3:
        print("usage: alg_num_arith_reference.py <op> <a-json> [<b-json>]",
              file=sys.stderr)
        sys.exit(2)

    op = sys.argv[1]
    a = json.loads(sys.argv[2])
    b = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    out = oracle_compute(a, b, op)
    # Serialise — SymPy expressions become strings.
    serialisable = dict(out)
    if "result_sympy" in serialisable:
        serialisable["result_sympy"] = str(serialisable["result_sympy"])
    print(json.dumps(serialisable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
