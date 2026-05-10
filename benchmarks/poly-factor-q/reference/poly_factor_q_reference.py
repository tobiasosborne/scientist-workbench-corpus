"""SymPy-backed reference implementation of poly-factor-q.

`factor(f_str, var_str)` returns a candidate dict matching the bench's
output schema:

    {
      "kind":     "ok",
      "content":  "<canonical rational string>",
      "factors":  [{"factor": "<expr>", "multiplicity": <int>}, ...],
      "method":   "sympy-Poly.factor_list",
      "warnings": []
    }

or a tagged record for non-polynomial input:

    {
      "kind":    "tagged",
      "tag":     "poly-factor-q/non-polynomial",
      "payload": {"detail": "<reason>"}
    }

Used by `test_mutations.py` as the baseline to perturb. Not used at
verification time — the verifier consults SymPy's `is_irreducible`
directly for irreducibility, but the reference here is the higher-
level "factor and emit a candidate-shaped record" oracle.
"""
from __future__ import annotations

from fractions import Fraction
from functools import reduce
from math import gcd
from typing import Any, Dict, List, Tuple

import sympy as sp


def _rat_str(x) -> str:
    f = Fraction(x) if not isinstance(x, Fraction) else x
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _integer_gcd(coefs: List[int]) -> int:
    nz = [abs(c) for c in coefs if c != 0]
    return reduce(gcd, nz) if nz else 1


def _is_univariate_polynomial(f, var: sp.Symbol) -> bool:
    if f.free_symbols - {var}:
        return False
    try:
        sp.Poly(f, var, domain="QQ")
    except (sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed):
        return False
    return True


def factor(f_str: str, var_str: str) -> Dict[str, Any]:
    var = sp.Symbol(var_str)
    try:
        f = sp.sympify(f_str, locals={var_str: var})
    except (sp.SympifyError, SyntaxError) as exc:
        return {
            "kind":    "tagged",
            "tag":     "poly-factor-q/non-polynomial",
            "payload": {"detail": f"sympify failed: {exc}"},
        }

    if not _is_univariate_polynomial(f, var):
        offending = f.free_symbols - {var}
        if offending:
            detail = f"contains symbol(s) other than {var_str!r}: {sorted(str(s) for s in offending)}"
        else:
            detail = "not a polynomial in the declared variable"
        return {
            "kind":    "tagged",
            "tag":     "poly-factor-q/non-polynomial",
            "payload": {"detail": detail},
        }

    poly = sp.Poly(f, var, domain="QQ")
    if poly.is_zero or poly.degree() <= 0:
        # Constant input is malformed (positive degree required).
        raise ValueError(f"poly-factor-q reference: f={f_str!r} has degree ≤ 0")

    c, factors = poly.factor_list()
    canonical: List[Tuple[str, int]] = []
    content = sp.Rational(c)

    for fac, mult in factors:
        # Convert SymPy QQ-domain factor to canonical primitive ℤ[x] +
        # absorb the difference into the running content.
        coefs_q = fac.all_coeffs()
        denoms = [Fraction(str(co)).denominator for co in coefs_q]
        common_denom = reduce(lambda a, b: a * b // gcd(a, b), denoms, 1)
        int_coefs = [int(Fraction(str(co)) * common_denom) for co in coefs_q]
        g = _integer_gcd(int_coefs)
        int_coefs = [co // g for co in int_coefs]
        if int_coefs[0] < 0:
            int_coefs = [-co for co in int_coefs]
            sign_flip = -1
        else:
            sign_flip = 1
        # The factor's QQ-content was (g * sign_flip) / common_denom; raised
        # to mult, that absorbs into the running content.
        adjustment = Fraction(g * sign_flip, common_denom) ** mult
        content = content * sp.Rational(adjustment.numerator, adjustment.denominator)

        prim_poly = sp.Poly(int_coefs, var, domain="ZZ")
        canonical.append((sp.sstr(prim_poly.as_expr(), order="lex"), mult))

    # Sort by (degree, coef tuple).
    def _key(item):
        f_str_inner, _ = item
        p = sp.Poly(sp.sympify(f_str_inner, locals={var_str: var}), var, domain="ZZ")
        return (p.degree(), tuple(int(c) for c in p.all_coeffs()))

    canonical.sort(key=_key)

    return {
        "kind":     "ok",
        "content":  _rat_str(content),
        "factors":  [{"factor": f, "multiplicity": m} for f, m in canonical],
        "method":   "sympy-Poly.factor_list",
        "warnings": [],
    }
