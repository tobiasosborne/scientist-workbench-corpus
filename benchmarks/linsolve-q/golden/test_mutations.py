"""Mutation-prove harness for the linsolve-q verifier.

Per ADR-0019 §4, every solve-tier verifier MUST demonstrate RED on
at least 5 characteristic perturbations of the reference. Without
this, a verifier that always returned PASS would be admitted.
With it, the verifier's *sensitivity* is on the record.

These mutations correspond to the catalog in DESCRIPTION.md
"Mutation-prove harness". Each test:
  1. Picks a representative golden case from the bench.
  2. Constructs the candidate output that the perturbed reference
     *would* have produced (the documented wrong answer for that
     mutation class).
  3. Runs the verifier.
  4. Asserts the verifier returns `pass=False` AND that the FAILING
     check is the one expected for the mutation class. (Failing the
     wrong check would mean the mutation isn't isolated; the
     verifier's "sensitivity" claim weakens.)

Run:  python3 -m pytest test_mutations.py -v
"""
from __future__ import annotations
import json
import sys
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reference"))

from verify import verify  # noqa: E402
from linsolve_q_reference import bareiss_solve, fmt_rat  # noqa: E402

INPUTS = json.load(open(HERE / "inputs.json"))["cases"]
EXPECTED = {c["id"]: c["expected"] for c in
            json.load(open(HERE / "expected.json"))["cases"]}


def _case(id: str) -> Dict[str, Any]:
    for c in INPUTS:
        if c["id"] == id:
            return c
    raise KeyError(id)


def _baseline_passes(case_id: str):
    """Sanity: golden expected output passes the verifier."""
    case = _case(case_id)
    out = verify(case["input"], EXPECTED[case_id])
    assert out["pass"], (
        f"{case_id}: golden expected output FAILS verifier — "
        f"verifier is broken before mutation tests run. "
        f"reason={out['reason']}"
    )


# ---------------------------------------------------------------------
# Mutation 1 — Off-by-one in pivot row index
# Symptom: the "pivot row" gets reduced too (i ≥ k instead of i > k),
# corrupting the pivot row's coefficients. Fails exact_satisfaction
# on a 2×2 case.
# ---------------------------------------------------------------------


def test_mutation_offbyone_pivot_row():
    _baseline_passes("A4-2x2-identity")
    # 2×2 identity, b = [7, -3]. Off-by-one would zero out the pivot
    # row itself, leaving x ill-defined. Simulate: x = [0, 0] reported
    # as unique.
    bad = {
        "kind": "unique",
        "x": ["0", "0"],
        "free_vars": [],
        "rank": 2,
        "method": "bareiss-with-bug",
        "warnings": [],
    }
    case = _case("A4-2x2-identity")
    out = verify(case["input"], bad)
    assert not out["pass"], "off-by-one pivot must fail"
    assert not out["checks"]["exact_satisfaction_unique"]["pass"], (
        f"expected exact_satisfaction failure; got {out['reason']}"
    )


# ---------------------------------------------------------------------
# Mutation 2 — Sign flip in cross-multiply
# Symptom: a_kk·a_ij + a_ik·a_kj instead of −. On 2×2 systems this
# corrupts the residual asymmetrically. Pick B-randwell-n3.
# ---------------------------------------------------------------------


def test_mutation_sign_flip_cross_multiply():
    _baseline_passes("B-randwell-n3")
    # Construct an obviously-wrong x of the right shape.
    case = _case("B-randwell-n3")
    expected = EXPECTED["B-randwell-n3"]
    # Negate every entry of x — sign-flip-in-cross-multiply at every
    # row inverts the residual.
    bad_x = []
    for s in expected["x"]:
        f = Fraction(s)
        bad_x.append(fmt_rat(-f))
    bad = deepcopy(expected)
    bad["x"] = bad_x
    out = verify(case["input"], bad)
    assert not out["pass"]
    assert not out["checks"]["exact_satisfaction_unique"]["pass"]


# ---------------------------------------------------------------------
# Mutation 3 — Forgot the divisor (naive ℚ-Gaussian intermediate growth)
# Symptom: answer correct in ℚ; max intermediate bit length blown up.
# Caught by Tier-H bit-budget check.
# ---------------------------------------------------------------------


def test_mutation_naive_q_gaussian_bit_blowup():
    _baseline_passes("H-rand3-n10")
    case = _case("H-rand3-n10")
    expected = EXPECTED["H-rand3-n10"]
    # Right answer, wrong reported bit-length — exceeds budget by 100x.
    budget = case["input"]["expected_max_bit_length"]
    bad = deepcopy(expected)
    bad["warnings"] = [f"max intermediate bit length: {budget * 100}"]
    out = verify(case["input"], bad)
    assert not out["pass"]
    assert not out["checks"]["bit_budget"]["pass"], out["reason"]


# ---------------------------------------------------------------------
# Mutation 4 — Dropped augmented column from row reduction
# Symptom: the b-side updates aren't applied; A reduces correctly
# but b is the original input's b throughout. Fails on any case where
# b ≠ 0 and the row reduction changes the rhs.
# ---------------------------------------------------------------------


def test_mutation_dropped_augmented_column():
    _baseline_passes("B-randwell-n5")
    case = _case("B-randwell-n5")
    expected = EXPECTED["B-randwell-n5"]
    # An x that came from solving A·x = some-other-b. Use zeros.
    bad = deepcopy(expected)
    bad["x"] = ["0"] * len(bad["x"])
    out = verify(case["input"], bad)
    assert not out["pass"]
    assert not out["checks"]["exact_satisfaction_unique"]["pass"]


# ---------------------------------------------------------------------
# Mutation 5 — Misclassified under-determined as unique
# Symptom: rank-deficient matrix returns a single concrete x rather
# than parametric form. Fails free_var_count_correct (tries to pass
# an under-determined case as unique with rank < n).
# ---------------------------------------------------------------------


def test_mutation_underdetermined_as_unique():
    _baseline_passes("E-3x3-rank2-consistent")
    case = _case("E-3x3-rank2-consistent")
    expected = EXPECTED["E-3x3-rank2-consistent"]
    # Misclassify: pretend it's unique with t_0 := 0 substituted in.
    # The substitution into the affine form gives a concrete x that
    # WILL satisfy A·x = b at that one trial, but is reported as unique
    # despite rank<n.
    # (Pick t_0=0 substitution from the parametric x.)
    free_vars = expected["free_vars"]
    affines = expected["x"]
    # Concretize at t_i = 0 for all i.
    import sympy as sp
    syms = {n: sp.Symbol(n) for n in free_vars}
    concrete_x = []
    for s in affines:
        e = sp.sympify(s, locals=syms)
        e0 = e.subs({syms[n]: 0 for n in free_vars})
        concrete_x.append(str(e0))
    bad = {
        "kind": "unique",
        "x": concrete_x,
        "free_vars": [],
        "rank": expected["rank"],
        "method": "bareiss-misclassified",
        "warnings": expected.get("warnings", []),
    }
    case = _case("E-3x3-rank2-consistent")
    out = verify(case["input"], bad)
    # The catch: kind_rank_consistent fires because rank<n but
    # kind=unique. exact_satisfaction may pass (t=0 substitution is
    # valid) but the structural invariant cannot.
    assert not out["pass"]
    assert not out["checks"]["kind_rank_consistent"]["pass"], (
        f"expected kind_rank_consistent failure; got {out['reason']}"
    )


def test_mutation_underdetermined_as_unique_strict():
    """Stricter: pick a case where the t=0 substitution DOESN'T
    coincide with a valid solution; missing the free-variable basis
    is then immediately wrong."""
    _baseline_passes("E-2x3")
    case = _case("E-2x3")
    expected = EXPECTED["E-2x3"]
    # Take expected x at t=0 — IF it satisfies, this mutation pattern
    # doesn't fire on this case. Construct an explicit-wrong case
    # instead: report `unique` with a *random* concrete x (not derived
    # from t=0).
    bad = {
        "kind": "unique",
        "x": ["999", "0", "0"],   # arbitrary nonsense
        "free_vars": [],
        "rank": 2,
        "method": "bareiss-misclassified",
        "warnings": [],
    }
    out = verify(case["input"], bad)
    assert not out["pass"]
    assert not out["checks"]["exact_satisfaction_unique"]["pass"], (
        f"expected exact_satisfaction failure; got {out['reason']}"
    )


# ---------------------------------------------------------------------
# Mutation 6 — Misclassified inconsistent as unique
# Symptom: row [0,…,0 | β] (β≠0) ignored; spurious x returned.
# Fails exact_satisfaction trivially.
# ---------------------------------------------------------------------


def test_mutation_inconsistent_as_unique():
    _baseline_passes("F-2x1-overdet-incons")
    case = _case("F-2x1-overdet-incons")
    # F-2x1: A=[[1],[2]], b=[3,7] — inconsistent (x=3 vs x=3.5).
    # Mutation: claim x=3 (matches first row) and report unique.
    bad = {
        "kind": "unique",
        "x": ["3"],
        "free_vars": [],
        "rank": 1,
        "method": "bareiss-misclassified",
        "warnings": [],
    }
    out = verify(case["input"], bad)
    assert not out["pass"]
    # 2*3 = 6 ≠ 7 — exact_satisfaction must catch it.
    assert not out["checks"]["exact_satisfaction_unique"]["pass"]


# ---------------------------------------------------------------------
# Mutation 7 — Wrong rank reported (rank-consistency catch)
# ---------------------------------------------------------------------


def test_mutation_wrong_rank():
    _baseline_passes("B-randwell-n5")
    case = _case("B-randwell-n5")
    expected = EXPECTED["B-randwell-n5"]
    bad = deepcopy(expected)
    bad["rank"] = bad["rank"] + 1   # off by one
    out = verify(case["input"], bad)
    assert not out["pass"]
    assert not out["checks"]["rank_consistent"]["pass"]


# ---------------------------------------------------------------------
# Mutation 8 — Wrong free-variable count
# ---------------------------------------------------------------------


def test_mutation_wrong_free_var_count():
    _baseline_passes("E-2x3")
    case = _case("E-2x3")
    expected = EXPECTED["E-2x3"]
    bad = deepcopy(expected)
    # Drop one free variable name without changing the parametrisation —
    # that's structurally inconsistent (free_var_count != n - rank).
    bad["free_vars"] = bad["free_vars"][:-1]
    out = verify(case["input"], bad)
    assert not out["pass"]
    assert not out["checks"]["free_var_count_correct"]["pass"]
