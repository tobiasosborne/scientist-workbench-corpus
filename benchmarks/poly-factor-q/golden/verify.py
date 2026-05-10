#!/usr/bin/env python3
"""poly-factor-q verifier — 5 invariant checks per ADR-0019 §1 + the
refusal-class match (`tagged "poly-factor-q/non-polynomial"`).

Reads `{input, candidate, id, expected?}` JSON on stdin, writes
`{pass, reason, checks}` JSON on stdout, exit 0 on PASS, 1 on FAIL.

The `expected` field is optional and used only to discriminate Tier H
refusal cases from happy-path cases (when present, it carries the
expected `kind` and `tag`). Without it, the verifier infers from
`candidate.kind` whether to apply happy-path or refusal-class checks.

Tolerance regime: NONE. Every check is exact-rational equality (or
boolean equality for the irreducibility delegate).
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from functools import reduce
from math import gcd
from typing import Any, Dict, List, Tuple

import sympy as sp


# ---------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------


def verify(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run all applicable checks on a single case envelope.

    Returns the full result envelope (always; even when shape fails,
    the n/a checks are reported for traceability).
    """
    inp = case["input"]
    cand = case["candidate"]
    expected = case.get("expected", {})

    checks: Dict[str, Dict[str, Any]] = {}

    # 0. Resolve which mode (happy-path / tagged) we are in. Mismatch
    # between candidate's claim and the expected is itself a failure of
    # `shape` — a candidate that says "ok" when "tagged" was required is
    # a "lied about scope" bug.
    cand_kind = cand.get("kind")
    expected_kind = expected.get("kind")  # may be missing if no expected was supplied

    if expected_kind and cand_kind != expected_kind:
        # Lied-about-scope case.
        checks["shape"] = {
            "pass": False,
            "detail": f"candidate.kind={cand_kind!r} but expected.kind={expected_kind!r}",
        }
        for k in (
            "product_equals_input",
            "each_factor_irreducible",
            "factors_primitive",
            "factors_positive_leading",
            "refusal_class_matches",
        ):
            checks[k] = {"pass": False, "detail": "skipped: shape failed at kind mismatch"}
        return _aggregate(checks)

    # 1. Tagged refusal path.
    if cand_kind == "tagged":
        checks["shape"] = _check_shape_tagged(cand)
        checks["refusal_class_matches"] = _check_refusal_class(cand, expected)
        for k in (
            "product_equals_input",
            "each_factor_irreducible",
            "factors_primitive",
            "factors_positive_leading",
        ):
            checks[k] = {"pass": True, "detail": "n/a for kind=tagged"}
        return _aggregate(checks)

    # 2. Happy-path.
    checks["shape"] = _check_shape_ok(inp, cand)
    if not checks["shape"]["pass"]:
        # Down-stream checks need parsable factors; bail with n/a.
        for k in (
            "product_equals_input",
            "each_factor_irreducible",
            "factors_primitive",
            "factors_positive_leading",
        ):
            checks[k] = {"pass": False, "detail": "skipped: shape failed"}
        checks["refusal_class_matches"] = {"pass": True, "detail": "n/a for kind=ok"}
        return _aggregate(checks)

    var = sp.Symbol(inp["var"])
    f_expr = sp.sympify(inp["f"], locals={inp["var"]: var})
    f_poly = sp.Poly(f_expr, var, domain="QQ")

    content = Fraction(cand["content"])
    factor_polys: List[Tuple[sp.Poly, int]] = []
    for entry in cand["factors"]:
        fac_str = entry["factor"]
        # The shape check has already verified each parses cleanly over ℤ.
        fac_poly = sp.Poly(
            sp.sympify(fac_str, locals={inp["var"]: var}), var, domain="ZZ"
        )
        factor_polys.append((fac_poly, entry["multiplicity"]))

    checks["product_equals_input"] = _check_product(f_poly, content, factor_polys, var)
    checks["each_factor_irreducible"] = _check_irreducible(factor_polys, var)
    checks["factors_primitive"] = _check_primitive(factor_polys)
    checks["factors_positive_leading"] = _check_positive_leading(factor_polys)
    checks["refusal_class_matches"] = {"pass": True, "detail": "n/a for kind=ok"}
    return _aggregate(checks)


def _aggregate(checks: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Combine per-check results into the top-level envelope."""
    failed = [k for k, v in checks.items() if not v["pass"]]
    return {
        "pass": len(failed) == 0,
        "reason": "all invariants hold" if not failed else f"failed: {', '.join(failed)}",
        "checks": checks,
    }


# ---------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------


def _check_shape_ok(inp: Dict, cand: Dict) -> Dict[str, Any]:
    """Static structural validation for happy-path candidates."""
    if not isinstance(inp.get("f"), str):
        return {"pass": False, "detail": "input.f is not a string"}
    if not isinstance(inp.get("var"), str):
        return {"pass": False, "detail": "input.var is not a string"}
    if cand.get("kind") != "ok":
        return {"pass": False, "detail": f"candidate.kind={cand.get('kind')!r}, expected 'ok'"}
    if not isinstance(cand.get("content"), str):
        return {"pass": False, "detail": "candidate.content is not a string"}
    try:
        Fraction(cand["content"])
    except (ValueError, TypeError) as exc:
        return {"pass": False, "detail": f"candidate.content does not parse as rational: {exc}"}
    if not isinstance(cand.get("factors"), list):
        return {"pass": False, "detail": "candidate.factors is not a list"}

    var = sp.Symbol(inp["var"])
    for i, entry in enumerate(cand["factors"]):
        if not isinstance(entry, dict):
            return {"pass": False, "detail": f"factors[{i}] is not a record"}
        fac = entry.get("factor")
        mult = entry.get("multiplicity")
        if not isinstance(fac, str):
            return {"pass": False, "detail": f"factors[{i}].factor is not a string"}
        if not isinstance(mult, int) or mult < 1:
            return {"pass": False, "detail": f"factors[{i}].multiplicity is not a positive int"}
        try:
            poly = sp.Poly(
                sp.sympify(fac, locals={inp["var"]: var}), var, domain="ZZ"
            )
        except (sp.SympifyError, sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed,
                ValueError, TypeError) as exc:
            return {
                "pass": False,
                "detail": f"factors[{i}].factor does not parse as ℤ[x] in {inp['var']!r}: {exc}",
            }
        if poly.degree() < 1:
            return {
                "pass": False,
                "detail": f"factors[{i}] is degree-0 (a constant); content belongs in candidate.content",
            }
    return {"pass": True, "detail": f"{len(cand['factors'])} factor entries; structure ok"}


def _check_shape_tagged(cand: Dict) -> Dict[str, Any]:
    tag = cand.get("tag")
    if not isinstance(tag, str):
        return {"pass": False, "detail": "candidate.tag is not a string"}
    if not tag.startswith("poly-factor-q/"):
        return {"pass": False, "detail": f"candidate.tag={tag!r} not in poly-factor-q namespace"}
    payload = cand.get("payload", {})
    if not isinstance(payload, dict):
        return {"pass": False, "detail": "candidate.payload is not a record"}
    return {"pass": True, "detail": f"tag={tag!r}"}


def _check_product(
    f_poly: sp.Poly,
    content: Fraction,
    factor_polys: List[Tuple[sp.Poly, int]],
    var: sp.Symbol,
) -> Dict[str, Any]:
    """`content × ∏_i factor_i^multiplicity_i  ==  f` exactly in ℚ[x]."""
    reconstructed = sp.Poly(sp.Rational(content.numerator, content.denominator),
                            var, domain="QQ")
    for fac, mult in factor_polys:
        reconstructed = reconstructed * fac.set_domain(sp.QQ) ** mult
    diff = f_poly - reconstructed
    if diff.is_zero:
        return {"pass": True, "detail": "exact match"}
    return {
        "pass": False,
        "detail": f"reconstruction differs by {sp.sstr(diff.as_expr())!r}",
    }


def _check_irreducible(
    factor_polys: List[Tuple[sp.Poly, int]],
    var: sp.Symbol,
) -> Dict[str, Any]:
    """Every factor is irreducible over ℚ (delegated to SymPy)."""
    bad: List[str] = []
    for i, (fac, _mult) in enumerate(factor_polys):
        # is_irreducible is defined on Poly over a domain. Move to QQ for
        # the irreducibility test.
        fac_q = fac.set_domain(sp.QQ)
        try:
            irreducible = fac_q.is_irreducible
        except sp.PolynomialError as exc:
            bad.append(f"factors[{i}]: irreducibility test errored: {exc}")
            continue
        if not irreducible:
            bad.append(f"factors[{i}]={sp.sstr(fac.as_expr())!r} is reducible over ℚ")
    if bad:
        return {"pass": False, "detail": "; ".join(bad)}
    return {"pass": True, "detail": f"{len(factor_polys)} factor(s) irreducible over ℚ"}


def _check_primitive(factor_polys: List[Tuple[sp.Poly, int]]) -> Dict[str, Any]:
    """Every factor has integer-coef gcd equal to 1."""
    bad: List[str] = []
    for i, (fac, _mult) in enumerate(factor_polys):
        coefs = [int(c) for c in fac.all_coeffs()]
        nz = [abs(c) for c in coefs if c != 0]
        if not nz:
            bad.append(f"factors[{i}] is the zero polynomial")
            continue
        g = reduce(gcd, nz)
        if g != 1:
            bad.append(
                f"factors[{i}]={sp.sstr(fac.as_expr())!r} has integer content {g} > 1"
            )
    if bad:
        return {"pass": False, "detail": "; ".join(bad)}
    return {"pass": True, "detail": f"{len(factor_polys)} factor(s) primitive"}


def _check_positive_leading(
    factor_polys: List[Tuple[sp.Poly, int]],
) -> Dict[str, Any]:
    """Every factor's leading coefficient is strictly positive."""
    bad: List[str] = []
    for i, (fac, _mult) in enumerate(factor_polys):
        lc = int(fac.all_coeffs()[0])
        if lc <= 0:
            bad.append(
                f"factors[{i}]={sp.sstr(fac.as_expr())!r} has leading coef {lc} ≤ 0"
            )
    if bad:
        return {"pass": False, "detail": "; ".join(bad)}
    return {"pass": True, "detail": f"all {len(factor_polys)} factor(s) positive-leading"}


def _check_refusal_class(cand: Dict, expected: Dict) -> Dict[str, Any]:
    """Tag matches expected; payload satisfies the predicate."""
    cand_tag = cand.get("tag")
    expected_tag = expected.get("tag")
    if not expected_tag:
        # No expected tag was supplied — accept any tagged refusal.
        return {"pass": True, "detail": f"refused with tag={cand_tag!r}"}
    if cand_tag != expected_tag:
        return {
            "pass": False,
            "detail": f"candidate.tag={cand_tag!r} does not match expected.tag={expected_tag!r}",
        }
    predicate = expected.get("payload_predicate")
    payload = cand.get("payload", {})
    if predicate == "detail-non-empty":
        detail = payload.get("detail")
        if not isinstance(detail, str) or not detail:
            return {
                "pass": False,
                "detail": "predicate detail-non-empty: payload.detail missing or empty",
            }
    return {"pass": True, "detail": f"tag={cand_tag!r} + predicate satisfied"}


# ---------------------------------------------------------------------
# CLI entry point
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
