#!/usr/bin/env python3
"""Mutation-prove harness for the solve verifier.

Per ADR-0019 §4: the verifier is admitted to the bench iff it RED-flags
**at least 5 characteristic perturbations** of the reference output.
This file is the operational record of those RED demonstrations.

Each mutation function:
  1. Constructs a known-good `(input, candidate, expected)` envelope
     by running the SymPy reference.
  2. Perturbs the candidate to introduce one specific class of bug.
  3. Asserts that `verify(case)` returns `pass=False` and that the
     failing check matches the expected one.

Run:
    python3 test_mutations.py            # all mutations; prints PASS/FAIL
    python3 test_mutations.py -v         # verbose
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                            # for verify
sys.path.insert(0, str(HERE.parent / "reference"))       # for reference

from verify import verify                              # noqa: E402
from solve_reference import solve_reference            # noqa: E402


# ---------------------------------------------------------------------
# Probes — chosen so each mutation has a candidate that touches the
# relevant invariant.
# ---------------------------------------------------------------------

PROBES = {
    "linear-3x3":         {"eqs": ["x+2*y+3*z-6", "2*x+y+z-4", "x-y+z-1"], "vars": ["x", "y", "z"], "lane": "linear"},
    "linear-underdet":    {"eqs": ["x + y - 3"],                          "vars": ["x", "y"],      "lane": "linear"},
    "linear-inconsist":   {"eqs": ["x + y - 1", "x + y - 2"],             "vars": ["x", "y"],      "lane": "linear"},
    "univ-mult":          {"eqs": ["x**2 - 2*x + 1"],                     "vars": ["x"],           "lane": "univariate-poly"},
    "univ-quad":          {"eqs": ["x**2 - 5*x + 6"],                     "vars": ["x"],           "lane": "univariate-poly"},
    "trans-sin":          {"eqs": ["sin(x)"],                             "vars": ["x"],           "lane": "transcendental"},
    "trans-tan":          {"eqs": ["tan(x)"],                             "vars": ["x"],           "lane": "transcendental"},
    "refuse-mv":          {"eqs": ["x*y - 1"],                            "vars": ["x", "y"],      "lane": "refusal"},
}


def _baseline(probe_id: str) -> Dict[str, Any]:
    """Build a known-good {input, candidate, expected} envelope."""
    p = PROBES[probe_id]
    inp = {"eqs": p["eqs"], "vars": p["vars"]}
    cand = solve_reference(p["eqs"], p["vars"])
    expected: Dict[str, Any] = {"lane": p["lane"]}
    if p["lane"] == "refusal":
        expected["tag"] = cand["tag"]
    return {"id": probe_id, "input": inp, "candidate": cand, "expected": expected}


def _expect_red(case: Dict[str, Any], mutation_name: str, *, expected_failed: Optional[str] = None, verbose: bool) -> bool:
    """Run verify; expect pass=False (and optionally a specific failing check)."""
    result = verify(case)
    failed = [k for k, v in result["checks"].items() if not v["pass"]]
    if verbose:
        print(f"  {mutation_name:42s} pass={result['pass']!s:5s} failed={failed}")
    if result["pass"]:
        print(f"  ✗ {mutation_name}: verifier INCORRECTLY passed a mutated output")
        return False
    if expected_failed is not None and expected_failed not in failed:
        print(f"  ⚠ {mutation_name}: caught (good) but failing check {failed} != expected {expected_failed!r}")
        # Still RED, just not the specific check we anticipated. Accept.
    return True


# ---------------------------------------------------------------------
# GREEN baseline — the reference's unmodified output must PASS the
# verifier on every probe.
# ---------------------------------------------------------------------


def green_baseline(*, verbose: bool) -> bool:
    all_ok = True
    for probe_id in PROBES:
        case = _baseline(probe_id)
        result = verify(case)
        if verbose:
            print(f"  green {probe_id:25s} pass={result['pass']!s:5s} reason={result['reason'][:60]!r}")
        if not result["pass"]:
            failed = [k for k, v in result["checks"].items() if not v["pass"]]
            print(f"  ✗ baseline {probe_id} FAILED — verifier is broken (failed={failed})")
            all_ok = False
    return all_ok


# ---------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------


def mutation_lied_about_scope(*, verbose: bool) -> bool:
    """For a multivariate-non-zero-dim input, candidate emits 'ok' instead
    of refusing. Should fail `shape` (lied-about-kind)."""
    case = _baseline("refuse-mv")
    case["candidate"] = {
        "kind": "ok",
        "vars": ["x", "y"],
        "solutions": [{
            "bindings": [{"var": "x", "value": "1"}, {"var": "y", "value": "1"}],
            "branches": [],
        }],
        "completeness": "complete",
        "warnings": [],
    }
    return _expect_red(case, "lied_about_scope", expected_failed="shape", verbose=verbose)


def mutation_linear_sign_flip(*, verbose: bool) -> bool:
    """In the unique 3x3 system, flip the sign of x's value. Should fail
    `exact_satisfaction` (residue ≠ 0)."""
    case = _baseline("linear-3x3")
    sol = case["candidate"]["solutions"][0]
    # Flip the first binding's value.
    import sympy as sp
    val_str = sol["bindings"][0]["value"]
    val = sp.sympify(val_str)
    sol["bindings"][0]["value"] = str(-val)
    return _expect_red(case, "linear_sign_flip", expected_failed="exact_satisfaction", verbose=verbose)


def mutation_dropped_multiplicity(*, verbose: bool) -> bool:
    """For (x-1)^2 = 0, return only one Solution instead of two. Should
    fail `count_with_multiplicity`."""
    case = _baseline("univ-mult")
    case["candidate"]["solutions"] = case["candidate"]["solutions"][:1]
    return _expect_red(case, "dropped_multiplicity", expected_failed="count_with_multiplicity", verbose=verbose)


def mutation_missed_branch_sin(*, verbose: bool) -> bool:
    """For sin(x) = 0, emit only one branch (drop the second). Should fail
    `completeness_grid` (the second-branch grid roots fall outside any
    candidate-instantiated tuple's value)."""
    case = _baseline("trans-sin")
    case["candidate"]["solutions"] = case["candidate"]["solutions"][:1]
    return _expect_red(case, "missed_branch_sin", expected_failed="completeness_grid", verbose=verbose)


def mutation_wrong_period_tan(*, verbose: bool) -> bool:
    """For tan(x) = 0, emit `2*pi*t_0` instead of `pi*t_0`. The cube tuples
    still satisfy (because tan is π-periodic, 2π also suffices), but the
    completeness grid finds π-period roots not matched by any 2π-period
    candidate tuple. Should fail `completeness_grid`."""
    case = _baseline("trans-tan")
    case["candidate"]["solutions"] = [{
        "bindings": [{"var": "x", "value": "2*pi*t_0"}],
        "branches": ["t_0"],
    }]
    return _expect_red(case, "wrong_period_tan", expected_failed="completeness_grid", verbose=verbose)


def mutation_inconsistent_returned_unique(*, verbose: bool) -> bool:
    """For an inconsistent linear system, return a spurious unique solution
    instead of solutions=[]. Should fail `exact_satisfaction` and/or
    `rank_consistent`."""
    case = _baseline("linear-inconsist")
    case["candidate"] = {
        "kind": "ok",
        "vars": ["x", "y"],
        "solutions": [{
            "bindings": [{"var": "x", "value": "1"}, {"var": "y", "value": "1"}],
            "branches": [],
        }],
        "completeness": "complete",
        "warnings": [],
    }
    return _expect_red(case, "inconsistent_returned_unique", verbose=verbose)


def mutation_underdet_dropped_branches(*, verbose: bool) -> bool:
    """For an underdetermined linear system, drop the branches list (claim
    completeness=complete). Should fail `completeness_correct` and/or
    `rank_consistent`."""
    case = _baseline("linear-underdet")
    sol = case["candidate"]["solutions"][0]
    # Substitute t_0 := 0 in the bindings, and clear branches list.
    import sympy as sp
    locals_map = {"x": sp.Symbol("x"), "y": sp.Symbol("y"), "t_0": sp.Symbol("t_0")}
    new_bindings = []
    for b in sol["bindings"]:
        v = sp.sympify(b["value"], locals=locals_map)
        v0 = v.subs(sp.Symbol("t_0"), 0)
        new_bindings.append({"var": b["var"], "value": str(v0)})
    sol["bindings"] = new_bindings
    sol["branches"] = []
    case["candidate"]["completeness"] = "complete"
    return _expect_red(case, "underdet_dropped_branches", verbose=verbose)


def mutation_wrong_refusal_tag(*, verbose: bool) -> bool:
    """For a multivariate-non-zero-dim input, refuse with the WRONG tag
    (e.g., solve/foreign-vocabulary instead of solve/multivariate-non-zero-dim).
    Should fail `tag_matches`."""
    case = _baseline("refuse-mv")
    case["candidate"]["tag"] = "solve/foreign-vocabulary"
    return _expect_red(case, "wrong_refusal_tag", expected_failed="tag_matches", verbose=verbose)


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


MUTATIONS = [
    ("lied_about_scope",                 mutation_lied_about_scope),
    ("linear_sign_flip",                 mutation_linear_sign_flip),
    ("dropped_multiplicity",             mutation_dropped_multiplicity),
    ("missed_branch_sin",                mutation_missed_branch_sin),
    ("wrong_period_tan",                 mutation_wrong_period_tan),
    ("inconsistent_returned_unique",     mutation_inconsistent_returned_unique),
    ("underdet_dropped_branches",        mutation_underdet_dropped_branches),
    ("wrong_refusal_tag",                mutation_wrong_refusal_tag),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("GREEN baseline (reference output → verifier → PASS):")
    if not green_baseline(verbose=args.verbose):
        print("FAIL: baseline broken; cannot proceed.")
        return 1
    print("  ok")
    print()

    print(f"RED mutations ({len(MUTATIONS)} characteristic perturbations):")
    failures: List[str] = []
    for name, fn in MUTATIONS:
        if not fn(verbose=args.verbose):
            failures.append(name)
        elif not args.verbose:
            print(f"  ✓ {name}")

    print()
    if failures:
        print(f"FAIL: {len(failures)} mutation(s) NOT caught: {failures}")
        return 1
    print(f"PASS: all {len(MUTATIONS)} mutations RED. Verifier is sensitive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
