#!/usr/bin/env python3
"""integrate-ode-stiff verifier — invariant-based, language-neutral.

Mirrors bench/integrate-ode-ivp/golden/verify.py with two stiff-specific
additions: `stiffness_handled` and `jacobian_consumed`. No horizon
scaling on `trajectory_accuracy` — Radau is stiffly bounded.
"""

from __future__ import annotations

import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np

EPS = 2.220446049250313e-16
SAFETY = 100.0
RTOL_DEFAULT = 1e-3
ATOL_DEFAULT = 1e-6

HERE = Path(__file__).resolve().parent

# Reuse helpers from the path-finder verifier without name collision.
import importlib.util as _ilu
_ivp_verify_path = HERE.parent.parent / "integrate-ode-ivp" / "golden" / "verify.py"
_spec = _ilu.spec_from_file_location("ivp_verify", _ivp_verify_path)
ivp_verify = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ivp_verify)

_is_finite = ivp_verify._is_finite
_shape_of = ivp_verify._shape_of
_vec_finite = ivp_verify._vec_finite
_sup_norm = ivp_verify._sup_norm
check_finite_entries = ivp_verify.check_finite_entries
check_monotone_t_values = ivp_verify.check_monotone_t_values
_check_conservation_path = ivp_verify.check_conservation
conserved_quantity = ivp_verify.conserved_quantity

_EXPECTED_INDEX: dict[str, dict] | None = None


def _load_expected() -> dict[str, dict]:
    global _EXPECTED_INDEX
    if _EXPECTED_INDEX is None:
        path = HERE / "expected.json"
        if not path.exists():
            _EXPECTED_INDEX = {}
            return _EXPECTED_INDEX
        payload = json.loads(path.read_text())
        _EXPECTED_INDEX = {c["id"]: c["expected"] for c in payload["cases"]}
    return _EXPECTED_INDEX


def check_shape(inp: dict, candidate: dict) -> dict:
    n = len(inp["y0"])
    options = inp.get("options", {}) or {}
    t_eval = options.get("t_eval")
    expected_m = len(t_eval) if t_eval is not None else 2

    required = {
        "trajectory", "t_values", "error_estimate",
        "n_evals", "n_steps_accepted", "n_steps_rejected",
        "n_jacobian_evals", "n_lu_decompositions",
        "converged", "status", "method", "warnings",
    }
    missing = required - set(candidate.keys())
    if missing:
        return {"pass": False, "detail": f"missing fields: {sorted(missing)}"}
    s = _shape_of(candidate["trajectory"])
    if s != (expected_m, n):
        return {"pass": False, "detail": f"trajectory shape {s}, expected {(expected_m, n)}"}
    if len(candidate["t_values"]) != expected_m:
        return {"pass": False, "detail": f"t_values length mismatch"}
    valid_status = {"success", "max_step_exceeded", "tspan_exhausted", "newton-divergence"}
    if candidate["status"] not in valid_status:
        return {"pass": False, "detail": f"status {candidate['status']!r} not in {valid_status}"}
    for f in ("n_evals", "n_steps_accepted", "n_steps_rejected",
              "n_jacobian_evals", "n_lu_decompositions"):
        if not isinstance(candidate[f], int):
            return {"pass": False, "detail": f"{f} must be an integer"}
    return {"pass": True, "detail": f"trajectory {s}"}


def check_status_consistency(candidate: dict) -> dict:
    """Structural bookkeeping invariants only.

    The original draft tried to bound `n_jacobian_evals` and
    `n_lu_decompositions` by `n_steps_accepted + n_steps_rejected`,
    but on stiff problems with Newton-divergence retries (Robertson,
    vdP at high mu) the Jacobian/LU counts can exceed step counts by
    1-2 orders of magnitude. The check now enforces only
    non-negativity, `converged ↔ status == success`, and that f-evals
    happened iff steps did.
    """
    converged = bool(candidate["converged"])
    status = candidate["status"]
    if converged != (status == "success"):
        return {"pass": False, "detail": f"converged={converged} but status={status!r}"}
    n_evals = int(candidate["n_evals"])
    n_acc = int(candidate["n_steps_accepted"])
    n_rej = int(candidate["n_steps_rejected"])
    n_jac = int(candidate["n_jacobian_evals"])
    n_lu = int(candidate["n_lu_decompositions"])
    for label, val in [("n_evals", n_evals), ("n_steps_accepted", n_acc),
                        ("n_steps_rejected", n_rej), ("n_jacobian_evals", n_jac),
                        ("n_lu_decompositions", n_lu)]:
        if val < 0:
            return {"pass": False, "detail": f"{label}={val} negative"}
    if n_acc > 0 and n_evals == 0:
        return {"pass": False,
                "detail": f"n_steps_accepted={n_acc} but n_evals=0"}
    return {"pass": True, "detail": (f"converged={converged}, status={status}, "
                                     f"n_evals={n_evals}, n_jac={n_jac}, n_lu={n_lu}")}


def check_trajectory_accuracy(inp: dict, candidate: dict, expected: dict) -> dict:
    rtol = float(expected.get("rtol", inp.get("options", {}).get("rtol", RTOL_DEFAULT)))
    atol = float(expected.get("atol", inp.get("options", {}).get("atol", ATOL_DEFAULT)))
    traj_c = np.asarray(candidate["trajectory"], dtype=np.float64)
    traj_r = np.asarray(expected["trajectory"], dtype=np.float64)
    if traj_c.shape != traj_r.shape:
        return {"pass": False, "detail": f"trajectory shape mismatch"}
    safety = float(expected.get("traj_tol_factor", SAFETY))
    worst_idx = -1
    worst_err = 0.0
    worst_tol = 0.0
    for i in range(traj_c.shape[0]):
        ref = traj_r[i]
        err = _sup_norm(traj_c[i] - ref)
        tol = max(safety * rtol * _sup_norm(ref), safety * atol)
        if err > tol:
            if worst_idx < 0 or err / max(tol, EPS) > worst_err / max(worst_tol, EPS):
                worst_idx = i; worst_err = err; worst_tol = tol
    if worst_idx >= 0:
        return {"pass": False,
                "detail": (f"trajectory[{worst_idx}] error {worst_err:.3e} > "
                           f"tol {worst_tol:.3e} (rtol={rtol})")}
    return {"pass": True, "detail": f"trajectory matches reference within {traj_c.shape[0]} points"}


def check_self_reported_error_estimate(inp: dict, candidate: dict, expected: dict) -> dict:
    reported = float(candidate["error_estimate"])
    if reported < 0:
        return {"pass": False, "detail": f"error_estimate={reported:.3e} negative"}
    if candidate.get("status") == "success":
        atol = float(inp.get("options", {}).get("atol", ATOL_DEFAULT))
        bound = max(1.0, atol * 1e6)
        if reported > bound:
            return {"pass": False,
                    "detail": (f"error_estimate={reported:.3e} > {bound:.3e} "
                               f"despite status=success")}
    return {"pass": True, "detail": f"error_estimate={reported:.3e}"}


def check_stiffness_handled(candidate: dict) -> dict:
    """Structural floor: a successful integration must have done some
    work (n_evals > 0) and reported some Jacobian use (n_jacobian_evals >
    0) — Radau is implicit, no Jacobian evaluations means the candidate
    isn't actually doing implicit integration. Tighter ratio bounds were
    dropped because they require knowing internal step counts which
    scipy doesn't expose.
    """
    n_evals = int(candidate["n_evals"])
    n_jac = int(candidate["n_jacobian_evals"])
    if n_evals == 0:
        return {"pass": False, "detail": "n_evals=0 on success path"}
    if n_jac == 0:
        return {"pass": False,
                "detail": "n_jacobian_evals=0 on Radau (implicit method must use Jacobian)"}
    return {"pass": True, "detail": f"n_evals={n_evals}, n_jac={n_jac}"}


def check_jacobian_consumed(inp: dict, candidate: dict) -> dict:
    options = inp.get("options", {}) or {}
    if "jacobian" not in options:
        return {"pass": True, "detail": "no analytic jacobian provided; check skipped"}
    if int(candidate["n_jacobian_evals"]) < 1:
        return {"pass": False,
                "detail": "options.jacobian provided but n_jacobian_evals == 0"}
    return {"pass": True, "detail": f"n_jacobian_evals={candidate['n_jacobian_evals']}"}


def verify_tagged(inp: dict, candidate: dict) -> dict:
    tag = candidate.get("tag", "")
    payload = candidate.get("payload", {})

    if tag == "integrate-ode-stiff/degenerate-tspan":
        if float(inp["t_span"]["t0"]) != float(inp["t_span"]["tf"]):
            return {"pass": False, "reason": "degenerate-tspan tagged but t0 != tf",
                    "checks": {"boundary": {"pass": False, "detail": "input mismatch"}}}
        return {"pass": True, "reason": "degenerate-tspan correctly tagged",
                "checks": {"boundary": {"pass": True, "detail": tag}}}

    if tag == "integrate-ode-stiff/method-not-implemented":
        method = (inp.get("options") or {}).get("method")
        if method != "bdf":
            return {"pass": False, "reason": "method-not-implemented tagged but options.method != 'bdf'",
                    "checks": {"boundary": {"pass": False, "detail": f"method={method}"}}}
        return {"pass": True, "reason": "method-not-implemented correctly tagged",
                "checks": {"boundary": {"pass": True, "detail": tag}}}

    if tag in ("integrate-ode-stiff/non-finite-during-eval",
               "integrate-ode-stiff/jacobian-singular"):
        # Payload-shape check; pin-point analysis deferred.
        if not isinstance(payload, dict):
            return {"pass": False, "reason": f"{tag} payload not a dict",
                    "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        for required in ("at_t", "at_y"):
            if required not in payload:
                return {"pass": False, "reason": f"{tag} payload missing {required}",
                        "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        return {"pass": True, "reason": f"{tag} correctly tagged",
                "checks": {"boundary": {"pass": True, "detail": tag}}}

    return {"pass": False, "reason": f"unknown tag {tag!r}",
            "checks": {"boundary": {"pass": False, "detail": tag}}}


def verify(payload: dict) -> dict:
    inp = payload["input"]
    candidate = payload["candidate"]
    case_id = payload.get("id", "?")

    if not isinstance(candidate, dict):
        return {"pass": False, "reason": "candidate must be a JSON object", "checks": {}}

    expected = _load_expected().get(case_id, {})
    cand_kind = candidate.get("kind")

    if cand_kind == "tagged":
        if expected.get("kind") != "tagged":
            return {"pass": False,
                    "reason": f"emitted tagged but expected was {expected.get('kind', 'success')!r}",
                    "checks": {"category": {"pass": False, "detail": "category mismatch"}}}
        if candidate.get("tag") != expected.get("tag"):
            return {"pass": False,
                    "reason": f"tag mismatch: {candidate.get('tag')!r} vs {expected.get('tag')!r}",
                    "checks": {"category": {"pass": False, "detail": "tag mismatch"}}}
        return verify_tagged(inp, candidate)

    if cand_kind == "tool_error":
        if expected.get("kind") != "tool_error":
            return {"pass": False,
                    "reason": f"emitted tool_error but expected was {expected.get('kind', 'success')!r}",
                    "checks": {"category": {"pass": False, "detail": "category mismatch"}}}
        return {"pass": True, "reason": "tool_error correctly emitted",
                "checks": {"tool_error_expected": {"pass": True,
                                                    "detail": f"name={candidate.get('name')!r}"}}}

    if expected.get("kind") in ("tagged", "tool_error"):
        return {"pass": False,
                "reason": f"emitted success but expected {expected.get('kind')!r}",
                "checks": {"category": {"pass": False, "detail": "category mismatch"}}}
    if not expected:
        return {"pass": False, "reason": f"no expected entry for id={case_id!r}", "checks": {}}

    checks: dict[str, dict] = {}
    checks["shape"] = check_shape(inp, candidate)
    if not checks["shape"]["pass"]:
        return _wrap(checks)
    checks["finite_entries"] = check_finite_entries(candidate)
    if not checks["finite_entries"]["pass"]:
        return _wrap(checks)
    checks["monotone_t_values"] = check_monotone_t_values(inp, candidate)
    checks["status_consistency"] = check_status_consistency(candidate)
    checks["trajectory_accuracy"] = check_trajectory_accuracy(inp, candidate, expected)
    checks["self_reported_error_estimate"] = check_self_reported_error_estimate(inp, candidate, expected)
    checks["stiffness_handled"] = check_stiffness_handled(candidate)
    checks["conservation"] = _check_conservation_path(inp, candidate, expected)
    checks["jacobian_consumed"] = check_jacobian_consumed(inp, candidate)
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
        sys.stderr.write(traceback.format_exc()); sys.stderr.write("\n")
        result = {"pass": False, "reason": f"verifier crashed: {type(e).__name__}: {e}", "checks": {}}
    json.dump(result, sys.stdout); sys.stdout.write("\n")


if __name__ == "__main__":
    main()
