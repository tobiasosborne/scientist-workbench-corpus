#!/usr/bin/env python3
"""SymPy-backed reference for `tools/poly-roots`.

Returns the same wire envelope `tools/poly-roots` emits:

    {
      "kind": "ok",
      "content": "1",   # leading rational coefficient (informational)
      "roots":  [{"root": "<expr-string>", "multiplicity": <int>}, …],
      "method": "factor-then-radicals",
      "warnings": []
    }

    or {"kind": "tagged", "tag": "poly-roots/<class>", "payload": {"detail": "..."}}

The reference factors `f` into ℚ-irreducibles via `Poly.factor_list()`,
then dispatches per factor degree:

    deg 1 → rational root
    deg 2 → discriminant formula (real or complex per Δ)
    deg 3 → Cardano (per ADR-1yu: emits faithful complex form in casus
            irreducibilis; sympy's `roots(...)` already produces this)
    deg 4 → Ferrari + biquadratic fast path
    deg ≥ 5 → If all (deg) roots are real, the workbench tool emits
              `Root[poly, k]` values for k = 0..deg-1 (ADR-0018, bead
              `yoc`). If any root is complex, it refuses with
              `tagged "poly-roots/complex-roots-not-yet-named"` —
              alg-num v0.1 names real algebraic numbers only. The
              reference here mirrors the *refusal* path for the bench's
              G-tier mixed-real-complex cases; the all-real Root[] path
              is checked by the workbench's per-tool goldens battery
              (the reference would need a `Root[]` formatter to emit
              the same canonical bytes; deferred).

Multiplicity preserved as repetition: `(x − 1)²` produces two
`Solution`-equivalent root entries each with multiplicity field equal
to the factor's multiplicity (workbench convention).

Per ADR-0019, this reference is the bench's primary oracle.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp


CLS_COMPLEX_NOT_YET = "poly-roots/complex-roots-not-yet-named"
CLS_NON_POLYNOMIAL  = "poly-roots/non-polynomial"
CLS_MULTIVARIATE    = "poly-roots/multivariate"


def poly_roots_reference(f_str: str, var_name: str) -> Dict[str, Any]:
    """Reference implementation of `tools/poly-roots`."""
    x = sp.Symbol(var_name)
    locals_map = {var_name: x}
    try:
        expr = sp.sympify(f_str, locals=locals_map)
    except (sp.SympifyError, SyntaxError, TypeError) as exc:
        return _refuse(CLS_NON_POLYNOMIAL, f"sympify failed: {exc}")

    # Multivariate check: any free symbol other than `x`.
    extra = expr.free_symbols - {x}
    if extra:
        return _refuse(
            CLS_MULTIVARIATE,
            f"polynomial mentions non-{var_name} symbol(s) {sorted(map(str, extra))}",
        )

    # Polynomial check.
    try:
        p = sp.Poly(expr, x, domain="QQ")
    except (sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed) as exc:
        return _refuse(CLS_NON_POLYNOMIAL, f"not a polynomial in {var_name} over ℚ: {exc}")

    if p.total_degree() < 1:
        # Constant polynomial. The workbench tool reports this as
        # ToolError (malformed input), not a tagged refusal — generators
        # should avoid emitting these. We surface as non-polynomial here
        # for diagnostic purposes.
        return _refuse(CLS_NON_POLYNOMIAL, f"polynomial has degree {p.total_degree()}; need ≥ 1")

    # Factor list.
    leading_coeff, factors = p.factor_list()
    leading_str = _rat_str(leading_coeff)

    roots: List[Dict[str, Any]] = []
    for fac, mult in factors:
        d = fac.total_degree()
        if d >= 5:
            # Workbench v0.2 (bead yoc): all-real ⇒ emit Root[] values;
            # mixed-real-complex ⇒ refuse with `complex-roots-not-yet-named`.
            # The reference oracle for the *all-real* path requires a Root[]
            # canonical formatter; tracked separately. Today we mirror the
            # refusal half of the contract — sufficient for the G-tier cases
            # in the bench, which all have complex roots.
            real_count = len(sp.Poly(fac, x, domain="QQ").real_roots(multiple=True))
            if real_count == d:
                return _refuse(
                    CLS_COMPLEX_NOT_YET,
                    f"factor of degree {d} has all real roots; reference oracle for "
                    f"the Root[]-emit path is not yet wired (workbench tool produces "
                    f"{d} Root[poly, k] values; see ADR-0018).",
                )
            return _refuse(
                CLS_COMPLEX_NOT_YET,
                f"factor of degree {d} has {d - real_count} complex root(s); "
                f"alg-num v0.1 names real algebraic numbers only",
            )
        # Closed-form roots via SymPy. `Poly.all_roots(multiple=True)` returns
        # the d roots of fac (counting multiplicity, but each fac is square-
        # free already since it's an irreducible factor — so multiplicity per
        # root within one factor is 1). The outer multiplicity comes from `mult`.
        fac_roots = sp.Poly(fac, x, domain="QQ").all_roots(multiple=True)
        for r in fac_roots:
            roots.append({
                "root":         _expr_to_str(r),
                "multiplicity": int(mult),
            })

    return {
        "kind":     "ok",
        "content":  leading_str,
        "roots":    roots,
        "method":   "factor-then-radicals",
        "warnings": [],
    }


def _rat_str(x) -> str:
    """Canonical lowest-terms rational string."""
    f = sp.Rational(x)
    if f.q == 1:
        return str(f.p)
    return f"{f.p}/{f.q}"


def _expr_to_str(e: sp.Expr) -> str:
    return sp.sstr(e)


def _refuse(tag: str, detail: str) -> Dict[str, Any]:
    return {
        "kind":   "tagged",
        "tag":    tag,
        "payload": {"detail": detail},
    }


def main() -> int:
    raw = sys.stdin.read()
    case = json.loads(raw)
    out = poly_roots_reference(case["f"], case["var"])
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
