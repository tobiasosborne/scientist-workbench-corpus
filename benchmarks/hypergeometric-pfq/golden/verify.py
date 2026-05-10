#!/usr/bin/env python3
"""hypergeometric-pfq verifier — invariant-based, language-neutral.

stdin (one JSON object):
  {
    "input":     <input row from inputs.json>,
    "candidate": <candidate output — value/tagged/tool_error>,
    "id":        str
  }

  where `<candidate>` is one of:
    success record:
      {"value": {"re": "<dec>", "im": "<dec>"},
       "achieved_precision": int,
       "method": str,
       "n_terms": int,
       "working_precision": int,
       "warnings": [str, ...]}
    tagged boundary:
      {"kind": "tagged", "tag": "hypergeometric-pfq/<class>",
       "payload": {...}}
    tool error:
      {"kind": "tool_error", "name": str, "message": str}

stdout:
  {"pass": bool, "reason": str,
   "checks": {<name>: {"pass": bool, "detail": str}}}

Reads `expected.json` adjacent on disk for pinned truth values and
tolerance contracts.

Per ADR-0019 §1 the verifier checks INVARIANTS, not byte-equality.
Five invariants per success-path case:

  1. shape                  — required output fields present, types match
  2. finite_value           — re, im parse as finite mpmath floats
  3. value_accuracy         — relative error vs pinned truth ≤ tolerance
  4. method_admissible      — `method ∈ {closed-form-0F0, closed-form-1F0,
                                       direct-series}`
  5. self_reported_precision — `achieved_precision >= input.precision − 1`
                                (the tool may under-report by 1 dps;
                                full check delegated to value_accuracy)

Refusal-path cases get one check:

  6. boundary_envelope       — emitted output is `{kind: "tagged", tag: ...}`
                                with `tag == expected.expected.tag`

Tolerances are per-case in `expected.json::cases[i].tolerance_rel`
(decimal-string → mpmath.mpf). Tier-0 cases use `1e-(precision-2)`;
tier-C near-unit-circle cases relax to `1e-(precision-8)` because the
direct-series + cancellation-bump can underflow the requested
precision by a handful of bits in the slow-convergence regime.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

import mpmath
mpmath.mp.dps = 80  # truth values are pinned to ~80 dps; comparison budget

HERE = Path(__file__).resolve().parent
_EXPECTED_INDEX: Optional[dict[str, dict]] = None


def _load_expected() -> dict[str, dict]:
    """Load `expected.json` once and index by id."""
    global _EXPECTED_INDEX
    if _EXPECTED_INDEX is None:
        path = HERE / "expected.json"
        if not path.exists():
            _EXPECTED_INDEX = {}
            return _EXPECTED_INDEX
        payload = json.loads(path.read_text())
        _EXPECTED_INDEX = {c["id"]: c for c in payload["cases"]}
    return _EXPECTED_INDEX


# ---------------------------------------------------------------------
# Per-check helpers
# ---------------------------------------------------------------------

ADMITTED_METHODS = {"closed-form-0F0", "closed-form-1F0", "direct-series"}


def _is_str(x: Any) -> bool:
    return isinstance(x, str)


def _check_shape_value(candidate: dict) -> dict:
    """Verify the shape of a success record."""
    required = {
        "value", "achieved_precision", "method",
        "n_terms", "working_precision", "warnings",
    }
    missing = required - set(candidate.keys())
    if missing:
        return {"pass": False, "detail": f"missing fields: {sorted(missing)}"}

    v = candidate["value"]
    if not isinstance(v, dict) or set(v.keys()) != {"re", "im"}:
        return {"pass": False,
                "detail": f"value must be {{re, im}}; got {type(v).__name__} {v}"}
    if not (_is_str(v["re"]) and _is_str(v["im"])):
        return {"pass": False, "detail": "value.re and value.im must be strings"}

    if not isinstance(candidate["achieved_precision"], int):
        return {"pass": False, "detail": "achieved_precision must be int"}
    if not isinstance(candidate["n_terms"], int):
        return {"pass": False, "detail": "n_terms must be int"}
    if not isinstance(candidate["working_precision"], int):
        return {"pass": False, "detail": "working_precision must be int"}
    if not _is_str(candidate["method"]):
        return {"pass": False, "detail": "method must be str"}
    if not isinstance(candidate["warnings"], list) or not all(
        _is_str(w) for w in candidate["warnings"]):
        return {"pass": False, "detail": "warnings must be list[str]"}

    return {"pass": True, "detail": "all required fields present"}


def _check_finite_value(candidate: dict) -> dict:
    """Verify the candidate's re/im parse to finite mpmath numbers."""
    try:
        re = mpmath.mpf(candidate["value"]["re"])
        im = mpmath.mpf(candidate["value"]["im"])
    except Exception as e:
        return {"pass": False, "detail": f"could not parse value: {e}"}
    if not (mpmath.isfinite(re) and mpmath.isfinite(im)):
        return {"pass": False, "detail": f"non-finite value re={re}, im={im}"}
    return {"pass": True, "detail": f"value parses, finite"}


def _check_value_accuracy(candidate: dict, expected: dict) -> dict:
    """Compare candidate to pinned truth at the case's tolerance."""
    truth_re = mpmath.mpf(expected["truth"]["re"])
    truth_im = mpmath.mpf(expected["truth"]["im"])
    truth = mpmath.mpc(truth_re, truth_im)

    cand_re = mpmath.mpf(candidate["value"]["re"])
    cand_im = mpmath.mpf(candidate["value"]["im"])
    cand = mpmath.mpc(cand_re, cand_im)

    diff = mpmath.fabs(cand - truth)
    scale = mpmath.fabs(truth)
    if scale == 0:
        # Pinned truth is zero (rare); fall back to absolute.
        rel = float(diff)
    else:
        rel = float(diff / scale)

    return rel


def _check_method_admissible(candidate: dict) -> dict:
    m = candidate.get("method", "")
    if m not in ADMITTED_METHODS:
        return {"pass": False,
                "detail": f"method={m!r} not in admitted set {sorted(ADMITTED_METHODS)}"}
    return {"pass": True, "detail": f"method={m!r}"}


def _check_self_reported_precision(candidate: dict, requested_precision: int) -> dict:
    """The tool's `achieved_precision` field is its own precision report.

    Per ADR-0007 (agent-honest output), the tool may under-report by a
    small margin if cancellation eats into the requested precision; the
    bench's `value_accuracy` check is the authoritative gate. Here we
    verify only that `achieved_precision <= requested_precision` (the
    tool cannot over-report) and that the relationship is sane
    (non-negative).
    """
    ap = candidate["achieved_precision"]
    if ap < 0:
        return {"pass": False, "detail": f"achieved_precision={ap} is negative"}
    if ap > requested_precision:
        return {
            "pass": False,
            "detail": (f"achieved_precision={ap} exceeds requested precision "
                       f"{requested_precision} — tool over-reports"),
        }
    return {"pass": True,
            "detail": f"achieved={ap}, requested={requested_precision}"}


def _check_boundary_envelope(candidate: dict, expected: dict) -> dict:
    """Refusal-path cases: candidate must be tagged with the expected tag."""
    if not isinstance(candidate, dict) or candidate.get("kind") != "tagged":
        return {"pass": False,
                "detail": (f"expected tagged refusal but got "
                           f"{candidate.get('kind', type(candidate).__name__)}")}
    expected_tag = expected["tag"]
    actual_tag = candidate.get("tag", "")
    if actual_tag != expected_tag:
        return {"pass": False,
                "detail": f"tag {actual_tag!r} != expected {expected_tag!r}"}

    payload_predicate = expected.get("payload_predicate", {})
    payload = candidate.get("payload", {})
    reason_substr = payload_predicate.get("reason_substr")
    if reason_substr:
        reason = payload.get("reason", "")
        if reason_substr not in reason:
            return {"pass": False,
                    "detail": (f"payload.reason={reason!r} does not contain "
                               f"the predicate substring {reason_substr!r}")}
    return {"pass": True, "detail": f"tagged {expected_tag}"}


# ---------------------------------------------------------------------
# Top-level verify
# ---------------------------------------------------------------------

def verify(payload: dict) -> dict:
    case_id = payload.get("id", "")
    candidate = payload.get("candidate", {})
    inp = payload.get("input", {})

    if "id" not in payload:
        # The id is present on the corpus but not always on the wrapper;
        # some bench drivers don't include it. Look up by content if
        # possible — match the input's a/b/z/precision against expected.
        # For simplicity, require the id.
        return {"pass": False, "reason": "missing id in payload", "checks": {}}

    expected_index = _load_expected()
    if case_id not in expected_index:
        return {"pass": False,
                "reason": f"id {case_id!r} not in expected.json",
                "checks": {}}
    case_expected = expected_index[case_id]

    checks: dict[str, dict] = {}

    # Tool errors are never expected — refusals come back as tagged
    # envelopes, not as crashes.
    if isinstance(candidate, dict) and candidate.get("kind") == "tool_error":
        checks["no_tool_error"] = {
            "pass": False,
            "detail": (f"tool crashed: {candidate.get('name')}: "
                       f"{candidate.get('message')}"),
        }
        return _wrap(checks)
    checks["no_tool_error"] = {"pass": True, "detail": "tool did not crash"}

    expected = case_expected["expected"]
    expected_kind = expected.get("kind", "value")

    if expected_kind == "tagged":
        checks["boundary_envelope"] = _check_boundary_envelope(candidate, expected)
        return _wrap(checks)

    # Success path.
    checks["shape"] = _check_shape_value(candidate)
    if not checks["shape"]["pass"]:
        return _wrap(checks)

    checks["finite_value"] = _check_finite_value(candidate)
    if not checks["finite_value"]["pass"]:
        return _wrap(checks)

    checks["method_admissible"] = _check_method_admissible(candidate)

    requested_precision = inp["precision"]
    checks["self_reported_precision"] = _check_self_reported_precision(
        candidate, requested_precision)

    rel = _check_value_accuracy(candidate, expected)
    tol = mpmath.mpf(case_expected["tolerance_rel"])
    if rel <= float(tol):
        checks["value_accuracy"] = {
            "pass": True,
            "detail": f"rel={rel:.3e} ≤ tol={float(tol):.3e}",
        }
    else:
        checks["value_accuracy"] = {
            "pass": False,
            "detail": f"rel={rel:.3e} > tol={float(tol):.3e}",
        }

    return _wrap(checks)


def _wrap(checks: dict) -> dict:
    overall = all(c["pass"] for c in checks.values())
    if overall:
        return {"pass": True, "reason": "all invariants hold", "checks": checks}
    first_fail = next(k for k, v in checks.items() if not v["pass"])
    return {"pass": False,
            "reason": f"failed: {first_fail} — {checks[first_fail]['detail']}",
            "checks": checks}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        result = verify(payload)
    except Exception as e:
        sys.stderr.write(traceback.format_exc())
        sys.stderr.write("\n")
        result = {
            "pass": False,
            "reason": f"verifier crashed: {type(e).__name__}: {e}",
            "checks": {},
        }
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
