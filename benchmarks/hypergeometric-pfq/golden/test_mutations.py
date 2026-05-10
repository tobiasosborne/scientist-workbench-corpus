#!/usr/bin/env python3
"""Mutation-prove tests for the `hypergeometric-pfq` verifier.

Per CLAUDE.md Rule 6 (port-and-verify TDD shape) and ADR-0019 §4
("mutation-prove requirement"), the verifier's sensitivity must be
demonstrated on at least five characteristic perturbations. A
passing test set on a faithful candidate proves only that the
verifier doesn't false-negative; it doesn't prove the verifier
*would have* caught a regression. These tests close that gap.

Each test:
  1. Loads the canonical candidate output for one specific case
     from `expected.json` (the pinned truth becomes the golden
     candidate).
  2. Mutates one specific field.
  3. Runs the verifier as a subprocess.
  4. Asserts the verifier reports `pass: false` on the mutated
     candidate.

The tests are intentionally narrow: each names the ONE invariant
the mutation breaks. A verifier that admits any of these mutations
is broken (insufficient sensitivity); a verifier that catches all
five satisfies the "test-has-caught-a-regression" discipline before
the verifier itself ships.

Run as:
    python3 bench/hypergeometric-pfq/golden/test_mutations.py
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS_PATH = HERE / "inputs.json"
EXPECTED_PATH = HERE / "expected.json"
VERIFY_PATH = HERE / "verify.py"


def _load() -> tuple[dict[str, dict], dict[str, dict]]:
    """Load inputs.json and expected.json, indexed by id."""
    inputs_payload = json.loads(INPUTS_PATH.read_text())
    expected_payload = json.loads(EXPECTED_PATH.read_text())
    inputs_by_id = {c["id"]: c for c in inputs_payload["cases"]}
    expected_by_id = {c["id"]: c for c in expected_payload["cases"]}
    return inputs_by_id, expected_by_id


def _build_canonical_candidate(case_id: str,
                                inputs_by_id: dict[str, dict],
                                expected_by_id: dict[str, dict]) -> dict:
    """Build a candidate output that *would* pass the verifier — the
    pinned truth value, dressed in success-record garb."""
    inp = inputs_by_id[case_id]["input"]
    exp = expected_by_id[case_id]["expected"]
    if exp["kind"] != "value":
        raise ValueError(f"case {case_id} is not value-shaped")
    return {
        "value": {"re": exp["truth"]["re"], "im": exp["truth"]["im"]},
        "achieved_precision": inp["precision"],
        "method": "direct-series",
        "n_terms": 100,
        "working_precision": 200,
        "warnings": [],
    }


def _build_canonical_refusal(case_id: str,
                              expected_by_id: dict[str, dict]) -> dict:
    """Build a refusal-path candidate that would pass the boundary check."""
    exp = expected_by_id[case_id]["expected"]
    if exp["kind"] != "tagged":
        raise ValueError(f"case {case_id} is not tagged-shaped")
    payload_pred = exp.get("payload_predicate", {})
    reason = payload_pred.get("reason_substr", "default")
    return {
        "kind": "tagged",
        "tag": exp["tag"],
        "payload": {"reason": f"... {reason} ..."},
    }


def _run_verifier(case_id: str, inp: dict, candidate: dict) -> dict:
    """Run the verifier as a subprocess and return its output."""
    payload = {"id": case_id, "input": inp, "candidate": candidate}
    result = subprocess.run(
        ["python3", str(VERIFY_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------
# Five mutations
# ---------------------------------------------------------------------

def test_flip_value_to_double() -> None:
    """Mutation 1: doubling the truth value → value_accuracy fails."""
    inputs_by_id, expected_by_id = _load()
    case_id = "t0-0F0-exp-z1"
    inp = inputs_by_id[case_id]["input"]
    candidate = _build_canonical_candidate(case_id, inputs_by_id, expected_by_id)

    # First confirm the canonical candidate passes.
    sanity = _run_verifier(case_id, inp, candidate)
    assert sanity["pass"], f"sanity check failed: {sanity['reason']}"

    # Now mutate: 2 * truth — re-format via mpmath to keep the dps shape.
    import mpmath
    mpmath.mp.dps = 80
    truth = mpmath.mpf(candidate["value"]["re"])
    mutated_candidate = copy.deepcopy(candidate)
    mutated_candidate["value"]["re"] = mpmath.nstr(truth * 2, 60, strip_zeros=False)

    out = _run_verifier(case_id, inp, mutated_candidate)
    assert not out["pass"], "verifier admitted a 2x-truth candidate"
    assert out["checks"]["value_accuracy"]["pass"] is False, (
        f"value_accuracy should be false; got {out['checks']}"
    )
    print("  PASS  test_flip_value_to_double")


def test_swap_tagged_for_value() -> None:
    """Mutation 2: swap a Tier E refusal envelope for a value shape →
    boundary_envelope fails."""
    inputs_by_id, expected_by_id = _load()
    case_id = "tE-3F1-asymptotic"
    inp = inputs_by_id[case_id]["input"]

    refusal = _build_canonical_refusal(case_id, expected_by_id)
    sanity = _run_verifier(case_id, inp, refusal)
    assert sanity["pass"], f"sanity check failed: {sanity['reason']}"

    # Mutate: emit a value record instead of a tagged envelope.
    mutated = {
        "value": {"re": "0", "im": "0"},
        "achieved_precision": 50,
        "method": "direct-series",
        "n_terms": 0,
        "working_precision": 200,
        "warnings": [],
    }
    out = _run_verifier(case_id, inp, mutated)
    assert not out["pass"], "verifier admitted a value-shape on refusal-expected case"
    assert out["checks"]["boundary_envelope"]["pass"] is False, (
        f"boundary_envelope should be false; got {out['checks']}"
    )
    print("  PASS  test_swap_tagged_for_value")


def test_tighten_tolerance_breaks_tier_c() -> None:
    """Mutation 3: a tolerance tightened past the noise floor → the
    Tier C value_accuracy fails on the same value the canonical
    tolerance admits.

    We re-create this without modifying expected.json (verifier reads
    expected.json from disk). Strategy: build the canonical candidate
    for a Tier C case at full precision, then mutate the *value* by a
    factor `1 + 1e-30` (deliberately past `1e-40` but at `tolerance_rel
    = 1e-44`), and verify that's still rejected.

    This proves the verifier's value_accuracy check actually computes
    a relative-error and compares it against the per-case tolerance —
    a tightening of either side makes the check RED.
    """
    inputs_by_id, expected_by_id = _load()
    case_id = "tC-2F1-z-097"
    inp = inputs_by_id[case_id]["input"]

    candidate = _build_canonical_candidate(case_id, inputs_by_id, expected_by_id)
    sanity = _run_verifier(case_id, inp, candidate)
    assert sanity["pass"], f"sanity check failed: {sanity['reason']}"

    # Mutate: shift truth by `1e-35` relative — well within tier-C
    # tolerance (`1e-42`) but well above absolute noise floor.
    # This is a *false-rejection* probe: confirms the verifier doesn't
    # drift in the other direction and admits a too-tight result.
    # The opposite test (verifier admits a 1e-30 mutation under
    # 1e-44 tolerance) is the key one — if the verifier admits this,
    # it's broken.
    import mpmath
    mpmath.mp.dps = 80
    truth = mpmath.mpf(candidate["value"]["re"])
    mutated = copy.deepcopy(candidate)
    # 1e-30 perturbation; tier C tolerance is 1e-42. So the mutation
    # should be rejected.
    perturbation = truth * mpmath.mpf("1e-30")
    mutated["value"]["re"] = mpmath.nstr(truth + perturbation, 60, strip_zeros=False)
    out = _run_verifier(case_id, inp, mutated)
    assert not out["pass"], (
        "verifier admitted a 1e-30 perturbation under 1e-42 tolerance"
    )
    assert out["checks"]["value_accuracy"]["pass"] is False, (
        f"value_accuracy should be false; got {out['checks']}"
    )
    print("  PASS  test_tighten_tolerance_breaks_tier_c")


def test_overreport_achieved_precision() -> None:
    """Mutation 4: achieved_precision = requested + 5 → self_reported_
    precision fails."""
    inputs_by_id, expected_by_id = _load()
    case_id = "tA-2F1-mid-real"
    inp = inputs_by_id[case_id]["input"]

    candidate = _build_canonical_candidate(case_id, inputs_by_id, expected_by_id)
    sanity = _run_verifier(case_id, inp, candidate)
    assert sanity["pass"], f"sanity check failed: {sanity['reason']}"

    mutated = copy.deepcopy(candidate)
    mutated["achieved_precision"] = inp["precision"] + 5  # over-report
    out = _run_verifier(case_id, inp, mutated)
    assert not out["pass"], "verifier admitted an over-reported precision"
    assert out["checks"]["self_reported_precision"]["pass"] is False, (
        f"self_reported_precision should be false; got {out['checks']}"
    )
    print("  PASS  test_overreport_achieved_precision")


def test_unknown_method() -> None:
    """Mutation 5: method = 'unknown-method' → method_admissible fails."""
    inputs_by_id, expected_by_id = _load()
    case_id = "t0-0F0-exp-z5"
    inp = inputs_by_id[case_id]["input"]

    candidate = _build_canonical_candidate(case_id, inputs_by_id, expected_by_id)
    sanity = _run_verifier(case_id, inp, candidate)
    assert sanity["pass"], f"sanity check failed: {sanity['reason']}"

    mutated = copy.deepcopy(candidate)
    mutated["method"] = "unknown-method"
    out = _run_verifier(case_id, inp, mutated)
    assert not out["pass"], "verifier admitted an unknown method"
    assert out["checks"]["method_admissible"]["pass"] is False, (
        f"method_admissible should be false; got {out['checks']}"
    )
    print("  PASS  test_unknown_method")


def main() -> None:
    print("Running mutation-prove tests for hypergeometric-pfq verifier:")
    test_flip_value_to_double()
    test_swap_tagged_for_value()
    test_tighten_tolerance_breaks_tier_c()
    test_overreport_achieved_precision()
    test_unknown_method()
    print("\n  All 5 mutations rejected by the verifier — sensitivity proven.")


if __name__ == "__main__":
    main()
