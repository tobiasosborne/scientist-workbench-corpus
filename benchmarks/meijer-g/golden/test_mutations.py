#!/usr/bin/env python3
"""Mutation-prove tests for the meijer-g bench verifier.

Per CLAUDE.md Rule 6 ("Two TDD shapes — both valid... mutation-prove
the tests catch regressions"), this script demonstrates that the
verifier reports failure on five characteristic perturbations of the
candidate output.  Each mutation flips a different invariant; the
expectation is RED.

Run as:
    python3 bench/meijer-g/golden/test_mutations.py

Exit code 0 = all five mutations correctly caught (RED on every
perturbation).  Exit code 1 = at least one mutation slipped past the
verifier — meaning a follow-up should tighten the affected check.

The mutations are not run as part of `bun run check` (per ADR-0019
§4 wider workbench mutation-prove gating is a separate workstream);
they are a discipline / audit script, run on demand.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Pick representative cases for each mutation.  We rely on the
# bench's actual `inputs.json` + `expected.json` (which must already
# be generated).
INPUTS_PATH = HERE / "inputs.json"
EXPECTED_PATH = HERE / "expected.json"
VERIFY_PATH = HERE / "verify.py"


def _load() -> tuple[dict, dict]:
    inputs = json.loads(INPUTS_PATH.read_text())
    expected = json.loads(EXPECTED_PATH.read_text())
    return inputs, expected


def _run_verifier(case_id: str, candidate: dict, inp: dict) -> dict:
    """Run verify.py on a synthesised payload."""
    payload = json.dumps({
        "input": inp, "candidate": candidate, "id": case_id,
    })
    result = subprocess.run(
        ["python3", str(VERIFY_PATH)],
        input=payload, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"verifier crashed: {result.stderr}")
    return json.loads(result.stdout)


def _expect_red(name: str, candidate: dict, inp: dict, case_id: str) -> bool:
    out = _run_verifier(case_id, candidate, inp)
    if out["pass"]:
        print(f"  FAIL  mutation '{name}' was NOT caught (verifier passed)",
              file=sys.stderr)
        print(f"        candidate: {json.dumps(candidate)[:200]}",
              file=sys.stderr)
        return False
    print(f"  pass  mutation '{name}' caught: {out['reason'][:120]}",
          file=sys.stderr)
    return True


def main() -> int:
    inputs, expected = _load()
    inputs_idx = {c["id"]: c for c in inputs["cases"]}
    expected_idx = {c["id"]: c for c in expected["cases"]}

    results: list[bool] = []

    # ---- Mutation 1: flip a Tier-0 truth value to its negation -----
    # Case: t0-G1001-b0-z2 (truth = e^{-2}).  Build a *wrong*-valued
    # numerical candidate and confirm `value_accuracy` rejects.
    case_id = "t0-G1001-b0-z2"
    inp = inputs_idx[case_id]["input"]
    truth = expected_idx[case_id]["expected"]["truth"]
    bogus_re = "-" + truth["re"].lstrip("-")  # negated
    cand = {
        "kind": "numerical",
        "value": {"re": bogus_re, "im": truth["im"]},
        "achieved_precision": 50,
        "method": "slater-series-1",
        "working_precision": 200,
        "warnings": [],
        "diagnostics": {},
    }
    results.append(_expect_red(
        "1-flip-tier0-truth-sign", cand, inp, case_id))

    # ---- Mutation 2: flip a Tier-G refusal to a value record ------
    # Case: tG-degen-mn-zero (expected: tagged refusal).  Submit a
    # value record; expect `shape` failure.
    case_id = "tG-degen-mn-zero"
    inp = inputs_idx[case_id]["input"]
    cand = {
        "kind": "numerical",
        "value": {"re": "1.0", "im": "0.0"},
        "achieved_precision": 50,
        "method": "slater-series-1",
        "working_precision": 200,
        "warnings": [],
        "diagnostics": {},
    }
    results.append(_expect_red(
        "2-flip-tierG-refusal-to-value", cand, inp, case_id))

    # ---- Mutation 3: tighten Tier C tolerance and submit candidate ----
    # We can't tighten the corpus tolerance from this script, but we
    # CAN submit a candidate value with a nudge larger than the
    # tolerance (`5x` larger than tolerance_rel) and confirm RED.
    case_id = "tC-G1002-frac-bz"
    inp = inputs_idx[case_id]["input"]
    truth = expected_idx[case_id]["expected"]["truth"]
    tol_rel = float(expected_idx[case_id]["tolerance_rel"])
    # Perturb truth.re by 5x the relative tolerance so it just fails.
    from decimal import Decimal, getcontext
    getcontext().prec = 80
    truth_re_dec = Decimal(truth["re"])
    nudge = abs(truth_re_dec) * Decimal(5 * tol_rel)
    cand_re = str(truth_re_dec + nudge)
    cand = {
        "kind": "numerical",
        "value": {"re": cand_re, "im": truth["im"]},
        "achieved_precision": 50,
        "method": "slater-series-1",
        "working_precision": 200,
        "warnings": [],
        "diagnostics": {},
    }
    results.append(_expect_red(
        "3-perturb-by-5x-tolerance", cand, inp, case_id))

    # ---- Mutation 4: over-report achieved_precision ----
    # Case: t0-G1001-b0-z2.  Submit `achieved_precision = 100` (greater
    # than `requested = 50`); expect `self_reported_precision` failure.
    case_id = "t0-G1001-b0-z2"
    inp = inputs_idx[case_id]["input"]
    truth = expected_idx[case_id]["expected"]["truth"]
    cand = {
        "kind": "numerical",
        "value": {"re": truth["re"], "im": truth["im"]},
        "achieved_precision": 100,  # requested = 50; over-report
        "method": "slater-series-1",
        "working_precision": 200,
        "warnings": [],
        "diagnostics": {},
    }
    results.append(_expect_red(
        "4-overreport-achieved-precision", cand, inp, case_id))

    # ---- Mutation 5: flip method to unknown ----
    # Case: tC-G1002-frac-bz.  Submit `method = "unknown-method"`;
    # expect `method_admissible` failure.
    case_id = "tC-G1002-frac-bz"
    inp = inputs_idx[case_id]["input"]
    truth = expected_idx[case_id]["expected"]["truth"]
    cand = {
        "kind": "numerical",
        "value": {"re": truth["re"], "im": truth["im"]},
        "achieved_precision": 50,
        "method": "unknown-method",
        "working_precision": 200,
        "warnings": [],
        "diagnostics": {},
    }
    results.append(_expect_red(
        "5-flip-method-unknown", cand, inp, case_id))

    print()
    n_caught = sum(results)
    n_total = len(results)
    if n_caught == n_total:
        print(f"all {n_total} mutations caught — verifier discipline holds",
              file=sys.stderr)
        return 0
    print(f"{n_total - n_caught}/{n_total} mutations slipped past — "
          "verifier coverage gap", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
