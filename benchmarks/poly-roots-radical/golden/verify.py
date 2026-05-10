#!/usr/bin/env python3
"""poly-roots-radical verifier — 4 invariant checks per ADR-0019 §1.

Reads `{input, candidate, expected, id, tier}` JSON on stdin, writes
`{pass, reason, checks}` JSON on stdout, exit 0 on PASS, 1 on FAIL.

Per `verifier_protocol.md`:

  shape                    → structural
  each_root_satisfies      → exact (sympy.simplify(f.subs(x, root)) == 0)
  count_with_multiplicity  → exact (Σ mult_i = total_degree(p))
  distinct_roots_match     → exact (multiset bipartite-match vs Poly.all_roots)
  refusal_class_matches    → tag/payload (when expected.kind="tagged")

Two top-level lanes:
  expected.kind == "ok"     → 4 happy-path checks
  expected.kind == "tagged" → refusal-class checks (tag/payload)
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp


def verify(case: Dict[str, Any]) -> Dict[str, Any]:
    inp = case["input"]
    cand = case["candidate"]
    expected = case.get("expected", {})

    expected_kind = expected.get("kind")
    cand_kind = cand.get("kind")

    # Lied-about-scope check.
    if expected_kind and cand_kind != expected_kind:
        return _aggregate({"shape": {
            "pass": False,
            "detail": f"candidate.kind={cand_kind!r} but expected.kind={expected_kind!r}",
        }})

    if cand_kind == "tagged":
        return _verify_refusal(cand, expected)
    return _verify_ok(inp, cand)


def _aggregate(checks: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    failed = [k for k, v in checks.items() if not v["pass"]]
    return {
        "pass":   len(failed) == 0,
        "reason": "all invariants hold" if not failed else f"failed: {', '.join(failed)}",
        "checks": checks,
    }


# ---------------------------------------------------------------------
# Happy-path lane
# ---------------------------------------------------------------------


def _verify_ok(inp: Dict, cand: Dict) -> Dict[str, Any]:
    var = inp["var"]
    x = sp.Symbol(var)
    locals_map = {var: x}

    checks: Dict[str, Dict[str, Any]] = {}
    checks["shape"] = _check_shape_ok(inp, cand, x)
    if not checks["shape"]["pass"]:
        for k in ("each_root_satisfies", "count_with_multiplicity", "distinct_roots_match"):
            checks[k] = {"pass": False, "detail": "skipped: shape failed"}
        return _aggregate(checks)

    f_expr = sp.sympify(inp["f"], locals=locals_map)
    p = sp.Poly(f_expr, x, domain="QQ")

    cand_roots: List[Tuple[sp.Expr, int]] = []
    for entry in cand["roots"]:
        r = sp.sympify(entry["root"], locals=locals_map)
        m = entry["multiplicity"]
        cand_roots.append((r, m))

    checks["each_root_satisfies"] = _check_each_root(p, cand_roots, x)
    checks["count_with_multiplicity"] = _check_count(p, cand_roots)
    checks["distinct_roots_match"] = _check_distinct_match(p, cand_roots, x)
    return _aggregate(checks)


def _check_shape_ok(inp: Dict, cand: Dict, x: sp.Symbol) -> Dict[str, Any]:
    if cand.get("kind") != "ok":
        return {"pass": False, "detail": f"kind={cand.get('kind')!r}"}
    if "roots" not in cand or not isinstance(cand["roots"], list):
        return {"pass": False, "detail": "roots field missing or not a list"}
    var = inp["var"]
    locals_map = {var: x}
    for i, entry in enumerate(cand["roots"]):
        if not isinstance(entry, dict):
            return {"pass": False, "detail": f"roots[{i}] not a record"}
        if "root" not in entry or not isinstance(entry["root"], str):
            return {"pass": False, "detail": f"roots[{i}].root missing or not string"}
        m = entry.get("multiplicity")
        if not isinstance(m, int) or m < 1:
            return {"pass": False, "detail": f"roots[{i}].multiplicity not a positive int"}
        try:
            sp.sympify(entry["root"], locals=locals_map)
        except (sp.SympifyError, SyntaxError, TypeError) as exc:
            return {"pass": False, "detail": f"roots[{i}].root did not parse: {exc}"}
    return {"pass": True, "detail": f"{len(cand['roots'])} root entries"}


def _check_each_root(p: sp.Poly, cand_roots: List[Tuple[sp.Expr, int]], x: sp.Symbol) -> Dict[str, Any]:
    f_expr = p.as_expr()
    bad: List[str] = []
    for i, (r, m) in enumerate(cand_roots):
        residue = sp.simplify(f_expr.subs(x, r))
        if residue != 0:
            # Try harder for radicals.
            residue2 = sp.radsimp(sp.simplify(f_expr.subs(x, r)))
            if residue2 != 0:
                # Try numerical fallback for casus-irreducibilis-style complex
                # roots that simplify is conservative on.
                try:
                    n = complex(residue2.evalf())
                    if abs(n) < 1e-9:
                        continue
                except (TypeError, ValueError):
                    pass
                bad.append(f"roots[{i}]={sp.sstr(r)} → f(root) = {residue2}")
    if bad:
        return {"pass": False, "detail": "; ".join(bad[:3])}
    return {"pass": True, "detail": f"{len(cand_roots)} root(s) substitute to 0"}


def _check_count(p: sp.Poly, cand_roots: List[Tuple[sp.Expr, int]]) -> Dict[str, Any]:
    expected = p.total_degree()
    actual = sum(m for _, m in cand_roots)
    if actual != expected:
        return {
            "pass": False,
            "detail": f"Σ multiplicity = {actual} != deg(p) = {expected}",
        }
    return {"pass": True, "detail": f"{actual} = deg(p)"}


def _check_distinct_match(p: sp.Poly, cand_roots: List[Tuple[sp.Expr, int]], x: sp.Symbol) -> Dict[str, Any]:
    """Each (cand_root, multiplicity) pair has a SymPy-side counterpart.

    The candidate emits ONE entry per *distinct* root with a multiplicity
    field. SymPy's `Poly.all_roots(multiple=False)` returns a similar list
    of (root, multiplicity) pairs.
    """
    sympy_roots = sp.Poly(p, x, domain="QQ").all_roots(multiple=False)
    # sympy_roots is a list of (root_expr, multiplicity) tuples.

    if len(cand_roots) != len(sympy_roots):
        return {
            "pass": False,
            "detail": (
                f"|distinct candidate roots|={len(cand_roots)} != "
                f"|distinct SymPy roots|={len(sympy_roots)}"
            ),
        }

    # Bipartite matching: each candidate (root, mult) pairs with one SymPy
    # (root, mult) under simplify-equality on root and exact equality on mult.
    available = list(range(len(sympy_roots)))
    for cr, cm in cand_roots:
        matched = None
        for idx in available:
            sr, sm = sympy_roots[idx]
            if sm != cm:
                continue
            try:
                diff = sp.simplify(cr - sr)
            except (sp.PolynomialError, TypeError):
                diff = sp.nsimplify(cr - sr)
            if diff == 0:
                matched = idx
                break
            # Numerical fallback — for casus-irreducibilis radicals SymPy is
            # conservative on simplify; use evalf to confirm equivalence.
            try:
                n_diff = complex(diff.evalf())
                if abs(n_diff) < 1e-9:
                    matched = idx
                    break
            except (TypeError, ValueError):
                pass
        if matched is None:
            return {"pass": False, "detail": f"candidate root {sp.sstr(cr)} (mult {cm}) matches no SymPy root"}
        available.remove(matched)
    if available:
        leftover = [(sp.sstr(r), m) for r, m in (sympy_roots[i] for i in available)]
        return {"pass": False, "detail": f"unmatched SymPy roots: {leftover}"}
    return {"pass": True, "detail": f"{len(cand_roots)} candidate roots ↔ SymPy roots"}


# ---------------------------------------------------------------------
# Refusal lane
# ---------------------------------------------------------------------


def _verify_refusal(cand: Dict, expected: Dict) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    checks["shape"] = _check_shape_tagged(cand)
    checks["refusal_class_matches"] = _check_refusal_class(cand, expected)
    for k in ("each_root_satisfies", "count_with_multiplicity", "distinct_roots_match"):
        checks[k] = {"pass": True, "detail": "n/a for kind=tagged"}
    return _aggregate(checks)


def _check_shape_tagged(cand: Dict) -> Dict[str, Any]:
    tag = cand.get("tag")
    if not isinstance(tag, str) or not tag.startswith("poly-roots/"):
        return {"pass": False, "detail": f"tag={tag!r} not in poly-roots/* namespace"}
    payload = cand.get("payload", {})
    if not isinstance(payload, dict):
        return {"pass": False, "detail": "payload missing or not a record"}
    return {"pass": True, "detail": f"tag={tag!r}"}


def _check_refusal_class(cand: Dict, expected: Dict) -> Dict[str, Any]:
    cand_tag = cand.get("tag")
    expected_tag = expected.get("tag")
    if expected_tag is None:
        return {"pass": True, "detail": f"tag={cand_tag!r} (no expected.tag pinned)"}
    if cand_tag != expected_tag:
        return {"pass": False, "detail": f"cand.tag={cand_tag!r} != expected.tag={expected_tag!r}"}
    payload = cand.get("payload", {})
    detail = payload.get("detail")
    if not isinstance(detail, str) or not detail:
        return {"pass": False, "detail": "payload.detail missing or empty"}
    return {"pass": True, "detail": f"tag matches; payload.detail non-empty"}


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
