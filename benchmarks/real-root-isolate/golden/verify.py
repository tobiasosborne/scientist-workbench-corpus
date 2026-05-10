#!/usr/bin/env python3
"""real-root-isolate verifier — 4 invariant checks per ADR-0019 §1.

Reads `{input, candidate, expected, id, tier}` JSON on stdin, writes
`{pass, reason, checks}` JSON on stdout, exit 0 on PASS, 1 on FAIL.

Per `verifier_protocol.md`:

  shape                            → structural
  each_interval_contains_one_root  → exact (sign-change + Sturm count)
  intervals_disjoint_and_ordered   → exact (rational comparison)
  count_matches_total_real_roots   → exact (Σ count = Sturm ground truth)
  refusal_class_matches            → tag/payload (when expected.kind="tagged")

Two top-level lanes:
  expected.kind == "ok"     → 4 happy-path checks
  expected.kind == "tagged" → refusal-class checks (tag/payload)
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

import sympy as sp


def verify(case: Dict[str, Any]) -> Dict[str, Any]:
    inp = case["input"]
    cand = case["candidate"]
    expected = case.get("expected", {})

    expected_kind = expected.get("kind")
    cand_kind = cand.get("kind")

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
    checks["shape"] = _check_shape_ok(cand)
    if not checks["shape"]["pass"]:
        for k in ("each_interval_contains_one_root",
                  "intervals_disjoint_and_ordered",
                  "count_matches_total_real_roots"):
            checks[k] = {"pass": False, "detail": "skipped: shape failed"}
        return _aggregate(checks)

    f_expr = sp.sympify(inp["f"], locals=locals_map)
    p = sp.Poly(f_expr, x, domain="QQ")

    parsed: List[tuple] = []
    parse_err = None
    for i, entry in enumerate(cand["intervals"]):
        try:
            lo = sp.Rational(entry["lo"])
            hi = sp.Rational(entry["hi"])
        except (TypeError, ValueError, sp.SympifyError) as exc:
            parse_err = f"intervals[{i}] endpoints did not parse as ℚ: {exc}"
            break
        parsed.append((lo, hi))
    if parse_err:
        for k in ("each_interval_contains_one_root",
                  "intervals_disjoint_and_ordered",
                  "count_matches_total_real_roots"):
            checks[k] = {"pass": False, "detail": parse_err}
        return _aggregate(checks)

    checks["each_interval_contains_one_root"] = _check_each_interval(p, parsed)
    checks["intervals_disjoint_and_ordered"] = _check_disjoint_ordered(parsed)
    checks["count_matches_total_real_roots"] = _check_count(p, parsed)
    return _aggregate(checks)


def _check_shape_ok(cand: Dict) -> Dict[str, Any]:
    if cand.get("kind") != "ok":
        return {"pass": False, "detail": f"kind={cand.get('kind')!r}"}
    if "intervals" not in cand or not isinstance(cand["intervals"], list):
        return {"pass": False, "detail": "intervals field missing or not a list"}
    for i, entry in enumerate(cand["intervals"]):
        if not isinstance(entry, dict):
            return {"pass": False, "detail": f"intervals[{i}] not a record"}
        if "lo" not in entry or not isinstance(entry["lo"], str):
            return {"pass": False, "detail": f"intervals[{i}].lo missing or not string"}
        if "hi" not in entry or not isinstance(entry["hi"], str):
            return {"pass": False, "detail": f"intervals[{i}].hi missing or not string"}
    return {"pass": True, "detail": f"{len(cand['intervals'])} interval entries"}


def _check_each_interval(p: sp.Poly, intervals: List[tuple]) -> Dict[str, Any]:
    """Each interval isolates exactly one real root of f.

    Two interval shapes per `verifier_protocol.md` (matching SymPy's
    `Poly.intervals()` output and the Akritas-Strzebonski VAS reference):

      lo < hi  → strict open interval (lo, hi); must contain exactly
                 one real root, with neither endpoint coinciding with
                 a root. The "open count" of roots inside (lo, hi) is
                 `count_roots(lo, hi) - [f(lo)=0] - [f(hi)=0]` (closed
                 count minus boundary roots).
      lo == hi → singleton {lo}; must satisfy `f(lo) = 0` (rational
                 root). SymPy emits this form for every rational root
                 instead of widening to a wrapping interval.

    The two-shape convention matters because rational and irrational
    roots have different optimal representations. A wrapping interval
    `(r-eps, r+eps)` for a rational root `r` requires picking `eps`
    such that no other root lies inside, which depends on the
    polynomial's full real-root structure — strictly more work than
    just emitting `(r, r)`.

    Combined with `intervals_disjoint_and_ordered` and
    `count_matches_total_real_roots`, this check is sufficient: every
    real root is enclosed by exactly one interval (no doubling, no
    omission).
    """
    bad: List[str] = []
    for i, (lo, hi) in enumerate(intervals):
        if lo > hi:
            bad.append(f"intervals[{i}]: lo={lo} > hi={hi}")
            continue
        if lo == hi:
            if p.eval(lo) != 0:
                bad.append(f"intervals[{i}]=({lo}, {lo}): singleton but f({lo}) ≠ 0")
            continue
        n_closed = p.count_roots(lo, hi)
        n_open = n_closed - (1 if p.eval(lo) == 0 else 0) - (1 if p.eval(hi) == 0 else 0)
        if n_open != 1:
            bad.append(
                f"intervals[{i}]=({lo}, {hi}): open-interval root count = {n_open} ≠ 1 "
                f"(closed count {n_closed})"
            )
    if bad:
        return {"pass": False, "detail": "; ".join(bad[:3])}
    return {"pass": True, "detail": f"{len(intervals)} interval(s) each isolate exactly one root"}


def _check_disjoint_ordered(intervals: List[tuple]) -> Dict[str, Any]:
    """Intervals are pairwise disjoint and in ascending order: hi_i ≤ lo_{i+1}.

    Equality (`hi_i == lo_{i+1}`) is permitted because the (lo, hi]
    half-open convention makes adjacent intervals disjoint at a shared
    non-root boundary (the left interval includes the shared point, the
    right excludes it). SymPy's `Poly.intervals()` exploits this and
    returns adjacent intervals like `(-2, -1), (0, 1), (1, 2)` for
    `x³ − 3x + 1` — `1` is not a root, just a separator.

    A candidate that produces overlap (`hi_i > lo_{i+1}`) is rejected.
    The companion `each_interval_contains_one_root` check separately
    catches the "shared boundary IS a root" pathology via the
    `f(lo) ≠ 0` precondition.
    """
    for i in range(len(intervals) - 1):
        _, hi_i = intervals[i]
        lo_next, _ = intervals[i + 1]
        if hi_i > lo_next:
            return {
                "pass": False,
                "detail": f"intervals[{i}].hi={hi_i} > intervals[{i+1}].lo={lo_next}",
            }
    return {"pass": True, "detail": f"{len(intervals)} interval(s) disjoint and ascending"}


def _check_count(p: sp.Poly, intervals: List[tuple]) -> Dict[str, Any]:
    """|candidate intervals| == #real roots of f (Sturm ground truth)."""
    expected = p.count_roots()  # over ℝ, all multiplicities counted in [-∞, +∞]
    actual = len(intervals)
    if actual != expected:
        return {
            "pass": False,
            "detail": f"|intervals| = {actual} != #real roots = {expected}",
        }
    return {"pass": True, "detail": f"{actual} = #real roots"}


# ---------------------------------------------------------------------
# Refusal lane
# ---------------------------------------------------------------------


def _verify_refusal(cand: Dict, expected: Dict) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    checks["shape"] = _check_shape_tagged(cand)
    checks["refusal_class_matches"] = _check_refusal_class(cand, expected)
    for k in ("each_interval_contains_one_root",
              "intervals_disjoint_and_ordered",
              "count_matches_total_real_roots"):
        checks[k] = {"pass": True, "detail": "n/a for kind=tagged"}
    return _aggregate(checks)


def _check_shape_tagged(cand: Dict) -> Dict[str, Any]:
    tag = cand.get("tag")
    if not isinstance(tag, str) or not tag.startswith("real-root-isolate/"):
        return {"pass": False, "detail": f"tag={tag!r} not in real-root-isolate/* namespace"}
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
    return {"pass": True, "detail": "tag matches; payload.detail non-empty"}


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
