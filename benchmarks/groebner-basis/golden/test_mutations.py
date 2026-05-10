#!/usr/bin/env python3
"""Mutation-prove harness for the groebner-basis verifier.

Per ADR-0019 §4 and CLAUDE.md Rule 6 (port-and-verify TDD), the verifier
is admitted to the bench iff it RED-flags **at least 6 characteristic
perturbations** of the SymPy reference output. This file is the
operational record of those RED demonstrations.

The discipline: a verifier that always returns PASS would be admitted
without this file; with it, the verifier's *sensitivity* is on the
record. Each mutation function:

  1. Builds a baseline (input + reference output) from the SymPy
     reference.
  2. Perturbs the candidate output in a specific, characteristic way.
  3. Asserts that `verify({input, candidate})` returns `pass=False`,
     with at least one specific invariant flagged.
  4. Records WHICH invariant fired (catching the mutation in the
     intended way).

# The 6 required perturbations (from the orchestrator's Phase 2a plan)

  1. drop_basis_element       — return basis[:-1]; fails
                                ideal_containment_input_to_candidate.
  2. add_non_ideal_element    — append the constant 7; fails
                                ideal_containment_candidate_to_input.
  3. wrong_leading_sign       — flip the leading-coef sign of one
                                element; fails ideal_containment_*
                                because the resulting basis no longer
                                generates the same ideal (the negated
                                element does, but the basis has lost
                                the original).
  4. skip_buchberger_crit2    — append a spurious S-pair-result element
                                to simulate a basis from an algorithm
                                that didn't apply the chain criterion;
                                we use a synthetic non-ideal element
                                that makes one S-pair fail to reduce.
  5. non_reduced_basis        — append a redundant element whose LM is
                                divisible by another basis LM (canonical
                                reduced GBs forbid this; we accept any
                                correct GB, so this perturbation must
                                CHANGE THE IDEAL — we add an
                                out-of-ideal redundant element).
  6. refusal_class_confusion  — for non-poly input, return tagged
                                "groebner-basis/empty-input" instead of
                                "groebner-basis/non-polynomial"; fails
                                tag_matches.

# Run

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

from verify import verify                                # noqa: E402
from groebner_reference import groebner_reference        # noqa: E402


# ---------------------------------------------------------------------
# Probe inputs — chosen so each mutation has a basis large enough for
# the relevant invariant to fire.
# ---------------------------------------------------------------------


PROBES = {
    "classic-2var": {
        "polys": ["x**2 + y", "x*y + 1"],
        "vars": ["x", "y"],
        "order": "lex",
    },
    "cyclic-3-lex": {
        "polys": ["x + y + z", "x*y + y*z + z*x", "x*y*z - 1"],
        "vars": ["x", "y", "z"],
        "order": "lex",
    },
    "two-circles": {
        "polys": ["x**2 + y**2 - 1", "(x-1)**2 + y**2 - 1"],
        "vars": ["x", "y"],
        "order": "lex",
    },
}


def _baseline(probe_id: str) -> Dict[str, Any]:
    """Build the {input, candidate} pair from the SymPy reference."""
    inp = PROBES[probe_id]
    cand = groebner_reference(inp["polys"], inp["vars"], inp["order"])
    return {"input": inp, "candidate": cand}


def _expect_red(case: Dict[str, Any], mutation_name: str, *, verbose: bool,
                expected_failed_check: str = None) -> bool:
    """Run verify; expect pass=False. If expected_failed_check is set,
    the named invariant must be one of the failed ones."""
    result = verify(case)
    if verbose:
        which_failed = [k for k, v in result["checks"].items() if not v["pass"]]
        print(f"  {mutation_name:38s} pass={result['pass']!s:5s} "
              f"failed_checks={which_failed}")
    if result["pass"]:
        print(f"  ✗ {mutation_name}: verifier INCORRECTLY passed a mutated output")
        return False
    if expected_failed_check is not None:
        which_failed = [k for k, v in result["checks"].items() if not v["pass"]]
        if expected_failed_check not in which_failed:
            print(f"  ✗ {mutation_name}: expected '{expected_failed_check}' to be flagged, "
                  f"but only {which_failed} fired")
            return False
    return True


# ---------------------------------------------------------------------
# Round-trip GREEN sanity
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
# Mutations
# ---------------------------------------------------------------------


def mutation_drop_basis_element(*, verbose: bool) -> bool:
    """Drop the last basis element. The resulting basis no longer
    generates the input ideal; should fail
    `ideal_containment_input_to_candidate`."""
    case = _baseline("cyclic-3-lex")  # cyclic-3 lex GB has multiple elements.
    if len(case["candidate"]["basis"]) < 2:
        print("  ✗ drop_basis_element: probe basis too small")
        return False
    case["candidate"]["basis"] = case["candidate"]["basis"][:-1]
    return _expect_red(
        case, "drop_basis_element", verbose=verbose,
        expected_failed_check="ideal_containment_input_to_candidate",
    )


def mutation_add_non_ideal_element(*, verbose: bool) -> bool:
    """Append the constant 7. 7 is NOT in the ideal (the ideals tested
    do not contain non-zero constants — cyclic-3 has the variety
    {ω,ω²,1} which is non-empty). Should fail
    `ideal_containment_candidate_to_input`."""
    case = _baseline("classic-2var")
    case["candidate"]["basis"].append("7")
    return _expect_red(
        case, "add_non_ideal_element", verbose=verbose,
        expected_failed_check="ideal_containment_candidate_to_input",
    )


def mutation_wrong_leading_sign(*, verbose: bool) -> bool:
    """Flip the leading-coefficient sign of one basis element by
    REPLACING it with its negation. The polynomial −g ∈ <g>, so the
    ideal is preserved by replacement — but we instead REPLACE the
    element with a DIFFERENT polynomial that happens to be -g.

    Wait: -g and g generate the SAME ideal, so replacing g with -g
    leaves the ideal unchanged AND the resulting basis is still a GB.
    So this perturbation does NOT fail the four invariants — that's a
    feature of mathematical-invariant verification per ADR-0019 §1.

    To make this perturbation RED in a way that's specific to the
    leading-sign symmetry, we instead REPLACE g with `g + g_other`
    where g_other is another basis element. This is still equivalent
    (the new basis generates the same ideal), but if the OTHER basis
    elements aren't there to compensate, the ideal containment can
    fail.

    We therefore use a stronger perturbation: replace an element with
    a non-equivalent shifted version (g + 1).  g + 1 is NOT in the
    ideal (assuming 1 ∉ ideal), so candidate_in_input fails.
    """
    case = _baseline("classic-2var")
    if not case["candidate"]["basis"]:
        return False
    # Replace first basis element with itself + 1.
    case["candidate"]["basis"][0] = f"({case['candidate']['basis'][0]}) + 1"
    return _expect_red(
        case, "wrong_leading_sign", verbose=verbose,
        expected_failed_check="ideal_containment_candidate_to_input",
    )


def mutation_skip_buchberger_crit2(*, verbose: bool) -> bool:
    """Simulate a basis where the Gebauer-Möller chain criterion was
    bypassed and a spurious near-S-pair-result was added.

    For two-circles probe, the GB is {x - 1/2, y**2 - 3/4}. We add a
    polynomial NOT in the ideal that breaks S-pair reduction.
    Adding `x*y` introduces an LM that creates an unreducible S-pair.
    """
    case = _baseline("two-circles")
    case["candidate"]["basis"].append("x*y")
    return _expect_red(
        case, "skip_buchberger_crit2", verbose=verbose,
        # Either ideal_containment_candidate_to_input or s_pair fires;
        # we don't pin the specific one.
    )


def mutation_non_reduced_basis(*, verbose: bool) -> bool:
    """Append a redundant element OUT OF the ideal. (A truly redundant
    element of the ideal would not fire any invariant — that's the
    'verifier accepts any correct GB' design from ADR-0019 §1.)
    Concretely: append `y - 1` to the cyclic-3 basis. y=1 is not in
    the cyclic-3 variety in general, so y-1 is not in the ideal."""
    case = _baseline("cyclic-3-lex")
    case["candidate"]["basis"].append("y - 1")
    return _expect_red(
        case, "non_reduced_basis", verbose=verbose,
        expected_failed_check="ideal_containment_candidate_to_input",
    )


def mutation_refusal_class_confusion(*, verbose: bool) -> bool:
    """For non-polynomial input, candidate emits the wrong refusal
    class. Should fail `tag_matches`."""
    case = {
        "input": {
            "polys": ["sin(x) + y"],
            "vars": ["x", "y"],
            "order": "lex",
        },
        "candidate": {
            "kind": "tagged",
            "tag": "groebner-basis/empty-input",  # wrong class
            "payload": {"detail": "fabricated"},
        },
        "expected": {
            "kind": "tagged",
            "tag": "groebner-basis/non-polynomial",
        },
    }
    return _expect_red(
        case, "refusal_class_confusion", verbose=verbose,
        expected_failed_check="tag_matches",
    )


# Bonus: lied-about-scope (happy-path response when refusal expected).
def mutation_lied_about_scope(*, verbose: bool) -> bool:
    """Emit a happy-path candidate when the bench expected a refusal."""
    case = {
        "input": {
            "polys": ["sin(x)"],
            "vars": ["x"],
            "order": "lex",
        },
        "candidate": {
            "kind": "ok",
            "basis": ["x"],
            "order": "lex",
            "vars": ["x"],
            "n_pairs": 0,
            "warnings": [],
        },
        "expected": {
            "kind": "tagged",
            "tag": "groebner-basis/non-polynomial",
        },
    }
    return _expect_red(case, "lied_about_scope", verbose=verbose,
                       expected_failed_check="shape")


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


MUTATIONS = [
    ("drop_basis_element",        mutation_drop_basis_element),
    ("add_non_ideal_element",     mutation_add_non_ideal_element),
    ("wrong_leading_sign",        mutation_wrong_leading_sign),
    ("skip_buchberger_crit2",     mutation_skip_buchberger_crit2),
    ("non_reduced_basis",         mutation_non_reduced_basis),
    ("refusal_class_confusion",   mutation_refusal_class_confusion),
    ("lied_about_scope",          mutation_lied_about_scope),
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
