#!/usr/bin/env python3
"""Mutation-prove harness for the poly-factor-q verifier.

Per ADR-0019 §4: the verifier is admitted to the bench iff it RED-flags
**at least 5 characteristic perturbations** of the reference output.
This file is the operational record of those RED demonstrations.

The discipline: a verifier that always returns PASS would be admitted
without this file; with it, the verifier's *sensitivity* is on the
record. Each mutation function asserts that `verify({input,
mutated_candidate})` returns `pass=False`.

Run:
    python3 test_mutations.py        # all 6 mutations; prints PASS/FAIL
    python3 test_mutations.py -v     # verbose
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                            # for verify
sys.path.insert(0, str(HERE.parent / "reference"))       # for reference

from verify import verify                                  # noqa: E402
from poly_factor_q_reference import factor as ref_factor   # noqa: E402


# ---------------------------------------------------------------------
# Probe inputs — chosen so each mutation has a candidate that touches
# the relevant invariant. Must be valid polynomials in ℚ[x] of degree
# ≥ 2 with at least two factors.
# ---------------------------------------------------------------------

PROBES = {
    "basic-quartic": {"f": "x^4 - 5*x^2 + 4", "var": "x"},  # = (x-1)(x+1)(x-2)(x+2)
    "with-content":  {"f": "6*(x - 1)*(x + 2)", "var": "x"},
    "with-mults":    {"f": "(x - 1)^3 * (x^2 + 1)", "var": "x"},
    "deg-1":         {"f": "x - 7", "var": "x"},
}


def _baseline(probe_id: str) -> Dict[str, Any]:
    """Build the {input, candidate} pair from the reference."""
    inp = PROBES[probe_id]
    cand = ref_factor(inp["f"], inp["var"])
    return {"input": inp, "candidate": cand}


def _expect_red(case: Dict[str, Any], mutation_name: str, *, verbose: bool) -> bool:
    """Run verify; expect pass=False. Print result. Return True if RED
    (verifier correctly rejected), False if it incorrectly PASSED."""
    result = verify(case)
    if verbose:
        which_failed = [k for k, v in result["checks"].items() if not v["pass"]]
        print(f"  {mutation_name:38s} pass={result['pass']!s:5s} "
              f"failed_checks={which_failed}")
    if result["pass"]:
        print(f"  ✗ {mutation_name}: verifier INCORRECTLY passed a mutated output")
        return False
    return True


# ---------------------------------------------------------------------
# Round-trip GREEN sanity (the reference's unmodified output should
# always PASS the verifier; if it doesn't, the verifier itself is
# broken and no mutation conclusion can be drawn).
# ---------------------------------------------------------------------


def green_baseline(*, verbose: bool) -> bool:
    """Reference output → verifier → PASS, on every probe.

    This is the GREEN of red-green TDD. Run before any mutation.
    """
    all_ok = True
    for probe_id in PROBES:
        case = _baseline(probe_id)
        result = verify(case)
        if verbose:
            print(f"  green {probe_id:30s} pass={result['pass']!s:5s} "
                  f"reason={result['reason']!r}")
        if not result["pass"]:
            print(f"  ✗ baseline {probe_id} FAILED — verifier is broken")
            all_ok = False
    return all_ok


# ---------------------------------------------------------------------
# Mutations — one function per characteristic regression class. Each
# returns True if RED was achieved (verifier correctly rejected).
# ---------------------------------------------------------------------


def mutation_drop_factor(*, verbose: bool) -> bool:
    """Drop the last factor entirely. The product no longer equals
    the input. Should fail `product_equals_input`."""
    case = _baseline("basic-quartic")
    case["candidate"]["factors"].pop()
    return _expect_red(case, "drop_factor", verbose=verbose)


def mutation_off_by_one_multiplicity(*, verbose: bool) -> bool:
    """Increment one factor's multiplicity by 1. The product no
    longer equals the input. Should fail `product_equals_input`."""
    case = _baseline("with-mults")
    case["candidate"]["factors"][0]["multiplicity"] += 1
    return _expect_red(case, "off_by_one_multiplicity", verbose=verbose)


def mutation_content_leak(*, verbose: bool) -> bool:
    """Multiply a factor by 2 (introducing integer content) without
    pulling that 2 into `content`. Should fail `factors_primitive`
    (and `product_equals_input` since the content moved)."""
    case = _baseline("with-content")
    # The reference sorts by degree-asc, lex-coefs-asc. For 6*(x-1)*(x+2),
    # the factors are [(x-1, 1), (x+2, 1)] sorted as (x+2 first by lex
    # on coefs (1, 2) vs (1, -1) — actually (1, -1) < (1, 2) so x-1 first).
    # Mutate the first factor.
    f0 = case["candidate"]["factors"][0]
    # Parse, scale, re-emit.
    import sympy as sp
    var = sp.Symbol(case["input"]["var"])
    p = sp.Poly(sp.sympify(f0["factor"], locals={case["input"]["var"]: var}),
                var, domain="ZZ")
    coefs = [2 * int(c) for c in p.all_coeffs()]
    leaked_poly = sp.Poly(coefs, var, domain="ZZ")
    f0["factor"] = sp.sstr(leaked_poly.as_expr(), order="lex")
    return _expect_red(case, "content_leak", verbose=verbose)


def mutation_sign_flip_leading(*, verbose: bool) -> bool:
    """Negate one factor (flipping the sign of the leading coefficient).
    The product is preserved if we also flip `content`, but THIS mutation
    deliberately does NOT flip content — so both `factors_positive_leading`
    AND `product_equals_input` fail.

    A more targeted mutation that flips both factor and content fails
    only `factors_positive_leading` — also a valid RED demonstration."""
    case = _baseline("basic-quartic")
    f0 = case["candidate"]["factors"][0]
    import sympy as sp
    var = sp.Symbol(case["input"]["var"])
    p = sp.Poly(sp.sympify(f0["factor"], locals={case["input"]["var"]: var}),
                var, domain="ZZ")
    coefs = [-int(c) for c in p.all_coeffs()]
    flipped_poly = sp.Poly(coefs, var, domain="ZZ")
    f0["factor"] = sp.sstr(flipped_poly.as_expr(), order="lex")
    # Compensate `content` so `product_equals_input` *would* still hold,
    # ensuring the failing check is specifically `factors_positive_leading`.
    from fractions import Fraction
    c = Fraction(case["candidate"]["content"])
    multiplicity = f0["multiplicity"]
    case["candidate"]["content"] = (
        str((-c if multiplicity % 2 == 1 else c).numerator)
        + ("/" + str((-c if multiplicity % 2 == 1 else c).denominator)
           if (-c if multiplicity % 2 == 1 else c).denominator != 1 else "")
    )
    return _expect_red(case, "sign_flip_leading", verbose=verbose)


def mutation_returned_reducible_factor(*, verbose: bool) -> bool:
    """Replace `(x - 1)(x + 1) = x^2 - 1` (two irreducible factors of
    degree 1) with the SINGLE reducible factor `x^2 - 1`. The product
    is preserved but `each_factor_irreducible` should fail."""
    case = _baseline("basic-quartic")
    # basic-quartic is x^4 - 5x^2 + 4 = (x-1)(x+1)(x-2)(x+2). Replace
    # the (x-1) and (x+1) factors with (x^2 - 1).
    factors = case["candidate"]["factors"]
    # Find the (x-1) and (x+1) entries; remove them, add (x^2 - 1).
    new_factors: List[Dict[str, Any]] = []
    removed_pair = False
    for f in factors:
        if f["factor"] in ("x - 1", "x + 1") and not removed_pair:
            continue
        new_factors.append(f)
    # Strictly: the loop drops both x-1 and x+1 if both present.
    new_factors.append({"factor": "x^2 - 1", "multiplicity": 1})
    case["candidate"]["factors"] = new_factors
    return _expect_red(case, "returned_reducible_factor", verbose=verbose)


def mutation_lied_about_scope(*, verbose: bool) -> bool:
    """For a Tier-H-style non-polynomial input, candidate emits a
    happy-path 'ok' result instead of a refusal tag. Should fail
    `shape` (kind mismatch with expected) — the bench verifier
    catches lied-about-scope candidates this way."""
    inp = {"f": "sin(x)", "var": "x"}
    cand = {
        "kind":     "ok",
        "content":  "1",
        "factors":  [{"factor": "x", "multiplicity": 1}],
        "method":   "fabricated",
        "warnings": [],
    }
    expected = {
        "kind":              "tagged",
        "tag":               "poly-factor-q/non-polynomial",
        "payload_predicate": "detail-non-empty",
    }
    case = {"input": inp, "candidate": cand, "expected": expected}
    return _expect_red(case, "lied_about_scope", verbose=verbose)


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


MUTATIONS = [
    ("drop_factor",                    mutation_drop_factor),
    ("off_by_one_multiplicity",        mutation_off_by_one_multiplicity),
    ("content_leak",                   mutation_content_leak),
    ("sign_flip_leading",              mutation_sign_flip_leading),
    ("returned_reducible_factor",      mutation_returned_reducible_factor),
    ("lied_about_scope",               mutation_lied_about_scope),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("GREEN baseline (reference output → verifier → PASS):")
    if not green_baseline(verbose=args.verbose):
        print("FAIL: baseline broken; cannot proceed with mutation prove.")
        return 1
    print("  ok")
    print()

    print(f"RED mutations ({len(MUTATIONS)} characteristic perturbations):")
    failures: List[str] = []
    for name, fn in MUTATIONS:
        if not fn(verbose=args.verbose):
            failures.append(name)
        else:
            if not args.verbose:
                print(f"  ✓ {name}")

    print()
    if failures:
        print(f"FAIL: {len(failures)} mutation(s) NOT caught by verifier:")
        for n in failures:
            print(f"      {n}")
        return 1
    print(f"PASS: all {len(MUTATIONS)} mutations RED. Verifier is sensitive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
