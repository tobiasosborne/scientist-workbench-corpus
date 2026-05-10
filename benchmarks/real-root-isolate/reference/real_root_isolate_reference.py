#!/usr/bin/env python3
"""SymPy-backed reference for `tools/real-root-isolate`.

Surface (matches the future `tools/real-root-isolate` wire envelope):

    {
      "kind":      "ok",
      "intervals": [{"lo": "<rational>", "hi": "<rational>"}, ...],
      "method":    "sympy-intervals",
      "warnings":  []
    }

    or {"kind": "tagged", "tag": "real-root-isolate/<class>",
        "payload": {"detail": "..."}}

The reference accepts a polynomial `f ∈ ℚ[x]` that **must be squarefree**
(precondition VAS itself imposes — caller is expected to compose with
`packages/poly-factor::squareFree` first). On a non-squarefree input the
reference refuses with `tagged "real-root-isolate/not-squarefree"`. The
non-squarefree boundary is the cleanest division of labour between the
isolation tool and the factor pipeline that already ships (worklog 052):
the isolation algorithm operates on a polynomial whose distinct real
roots are in bijection with sign-changes of its derivative chain, and
that bijection is what *makes* VAS work.

Multiplicity is **not** reported here — multiplicity is a factor-list
concern (per `tools/poly-factor`'s output shape and ADR-0017's solution
set conventions). A caller wanting multiplicity composes:
factor → squarefree-decompose → isolate-per-factor → re-attach mult.

Rational-root convention. SymPy's `Poly.intervals()` returns a degenerate
interval `(r, r)` when `r` is a rational root (both endpoints equal `r`,
with `f(r) = 0`). The reference passes this through verbatim. The
verifier accepts the degenerate form (`lo == hi`) iff `f(lo) == 0`;
otherwise it requires strict sign-change at irrational endpoints.

Per ADR-0019, this reference is the bench's primary oracle; Wolfram's
`RootIntervals[]` is the cross-witness for the *count* of real roots
(byte-equality of the intervals themselves is not required — Wolfram
and SymPy use different bisection cadences and produce different
numeric endpoints for the same root).
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

import sympy as sp


CLS_NOT_SQUAREFREE = "real-root-isolate/not-squarefree"
CLS_NON_POLYNOMIAL = "real-root-isolate/non-polynomial"
CLS_MULTIVARIATE   = "real-root-isolate/multivariate"


def real_root_isolate_reference(f_str: str, var_name: str) -> Dict[str, Any]:
    """Reference implementation of `tools/real-root-isolate`."""
    x = sp.Symbol(var_name)
    locals_map = {var_name: x}
    try:
        expr = sp.sympify(f_str, locals=locals_map)
    except (sp.SympifyError, SyntaxError, TypeError) as exc:
        return _refuse(CLS_NON_POLYNOMIAL, f"sympify failed: {exc}")

    extra = expr.free_symbols - {x}
    if extra:
        return _refuse(
            CLS_MULTIVARIATE,
            f"polynomial mentions non-{var_name} symbol(s) {sorted(map(str, extra))}",
        )

    try:
        p = sp.Poly(expr, x, domain="QQ")
    except (sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed) as exc:
        return _refuse(CLS_NON_POLYNOMIAL, f"not a polynomial in {var_name} over ℚ: {exc}")

    if p.total_degree() < 1:
        return _refuse(CLS_NON_POLYNOMIAL, f"polynomial has degree {p.total_degree()}; need ≥ 1")

    # Squarefree check: gcd(f, f') must be a unit. SymPy's `is_squarefree`
    # is defined over the polynomial's domain (here ℚ), so it returns the
    # mathematically correct answer.
    if not p.is_sqf:
        return _refuse(
            CLS_NOT_SQUAREFREE,
            "polynomial has repeated factors; squarefree-decompose (Yun) before isolation",
        )

    # Compute isolating intervals. `Poly.intervals()` returns
    # `[((lo, hi), multiplicity), ...]`. For squarefree input the
    # multiplicity is always 1; we drop it (it's structurally redundant)
    # and stringify the rational endpoints.
    raw = p.intervals()
    intervals: List[Dict[str, str]] = []
    for ((lo, hi), _mult) in raw:
        intervals.append({"lo": _rat_str(lo), "hi": _rat_str(hi)})

    return {
        "kind":      "ok",
        "intervals": intervals,
        "method":    "sympy-intervals",
        "warnings":  [],
    }


def _rat_str(x) -> str:
    """Canonical lowest-terms rational string."""
    f = sp.Rational(x)
    if f.q == 1:
        return str(f.p)
    return f"{f.p}/{f.q}"


def _refuse(tag: str, detail: str) -> Dict[str, Any]:
    return {
        "kind":    "tagged",
        "tag":     tag,
        "payload": {"detail": detail},
    }


def main() -> int:
    raw = sys.stdin.read()
    case = json.loads(raw)
    out = real_root_isolate_reference(case["f"], case["var"])
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
