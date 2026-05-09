#!/usr/bin/env python3
"""integrate-ode-symplectic verifier — invariant-based, language-neutral.

The headline check is `energy_drift_not_secular` — a symplectic
integrator on a separable Hamiltonian must keep energy drift bounded
regardless of horizon (HLW §VI.6 backward error analysis).
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

HERE = Path(__file__).resolve().parent

# Reuse separability-check / parser from reference (it has sympy machinery).
sys.path.insert(0, str(HERE.parent / "reference"))
from symplectic_reference import _parse_hamiltonian, _check_separability  # noqa: E402

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


def _shape_of(traj: Any) -> tuple[int, int] | None:
    if not isinstance(traj, list): return None
    if not traj: return (0, 0)
    if not all(isinstance(row, list) for row in traj): return None
    n_cols = len(traj[0])
    if not all(len(row) == n_cols for row in traj): return None
    if not all(isinstance(v, (int, float)) for row in traj for v in row): return None
    return (len(traj), n_cols)


def _sup_norm(v: np.ndarray) -> float:
    if v.size == 0: return 0.0
    return float(np.max(np.abs(v)))


def check_shape(inp: dict, candidate: dict) -> dict:
    n_q = len(inp["q0"])
    n_p = len(inp["p0"])
    n_steps = int(inp["n_steps"])
    expected_m = n_steps + 1

    required = {"q_trajectory", "p_trajectory", "t_values", "energy",
                "energy_drift_max", "energy_drift_secular", "n_evals",
                "n_steps", "converged", "status", "method", "warnings"}
    missing = required - set(candidate.keys())
    if missing:
        return {"pass": False, "detail": f"missing fields: {sorted(missing)}"}

    sQ = _shape_of(candidate["q_trajectory"])
    sP = _shape_of(candidate["p_trajectory"])
    if sQ != (expected_m, n_q):
        return {"pass": False, "detail": f"q_trajectory shape {sQ}, expected {(expected_m, n_q)}"}
    if sP != (expected_m, n_p):
        return {"pass": False, "detail": f"p_trajectory shape {sP}, expected {(expected_m, n_p)}"}

    if not isinstance(candidate["t_values"], list) or len(candidate["t_values"]) != expected_m:
        return {"pass": False, "detail": "t_values length mismatch"}
    if not isinstance(candidate["energy"], list) or len(candidate["energy"]) != expected_m:
        return {"pass": False, "detail": "energy length mismatch"}
    if candidate["status"] not in {"success", "non-finite-during-eval"}:
        return {"pass": False, "detail": f"status {candidate['status']!r} invalid"}
    if not isinstance(candidate["energy_drift_secular"], bool):
        return {"pass": False, "detail": "energy_drift_secular must be a bool"}

    return {"pass": True, "detail": f"q {sQ}, p {sP}"}


def check_finite_entries(candidate: dict) -> dict:
    for field in ("q_trajectory", "p_trajectory"):
        for i, row in enumerate(candidate[field]):
            for v in row:
                if not (isinstance(v, (int, float)) and math.isfinite(float(v))):
                    return {"pass": False, "detail": f"non-finite {field}[{i}]"}
    for field in ("energy",):
        for i, v in enumerate(candidate[field]):
            if not (isinstance(v, (int, float)) and math.isfinite(float(v))):
                return {"pass": False, "detail": f"non-finite {field}[{i}]"}
    if not (isinstance(candidate["energy_drift_max"], (int, float))
            and math.isfinite(candidate["energy_drift_max"])):
        return {"pass": False, "detail": "energy_drift_max non-finite"}
    return {"pass": True, "detail": "all entries finite"}


def check_monotone_t_values(inp: dict, candidate: dict) -> dict:
    t0 = float(inp["t_span"]["t0"])
    tf = float(inp["t_span"]["tf"])
    n_steps = int(inp["n_steps"])
    h = (tf - t0) / n_steps
    expected_t = [t0 + i * h for i in range(n_steps + 1)]
    for i, (a, b) in enumerate(zip(expected_t, candidate["t_values"])):
        if abs(a - b) > 1e-10 * max(abs(a), 1.0):
            return {"pass": False, "detail": f"t_values[{i}]={b} != {a}"}
    return {"pass": True, "detail": f"uniform grid over {n_steps+1} points"}


def check_status_consistency(inp: dict, candidate: dict) -> dict:
    if bool(candidate["converged"]) != (candidate["status"] == "success"):
        return {"pass": False, "detail": "converged/status mismatch"}
    if int(candidate["n_steps"]) != int(inp["n_steps"]):
        return {"pass": False, "detail": f"n_steps {candidate['n_steps']} != input {inp['n_steps']}"}
    if int(candidate["n_evals"]) < 0:
        return {"pass": False, "detail": "n_evals negative"}
    return {"pass": True, "detail": f"n_steps={candidate['n_steps']}, n_evals={candidate['n_evals']}"}


def check_trajectory_accuracy(inp: dict, candidate: dict, expected: dict) -> dict:
    if "q_trajectory" not in expected:
        return {"pass": True, "detail": "no reference trajectory; check skipped"}
    t0 = float(inp["t_span"]["t0"])
    tf = float(inp["t_span"]["tf"])
    n_steps = int(inp["n_steps"])
    h = (tf - t0) / n_steps
    method = candidate.get("method", "velocity-verlet")
    p = 4 if method == "yoshida-4" else 2

    q_c = np.asarray(candidate["q_trajectory"], dtype=np.float64)
    q_r = np.asarray(expected["q_trajectory"], dtype=np.float64)
    p_c = np.asarray(candidate["p_trajectory"], dtype=np.float64)
    p_r = np.asarray(expected["p_trajectory"], dtype=np.float64)
    if q_c.shape != q_r.shape or p_c.shape != p_r.shape:
        return {"pass": False, "detail": f"trajectory shape mismatch"}

    tol_factor = float(expected.get("traj_tol_factor", 1000.0))
    tol_h = tol_factor * (h ** p)
    worst_q = 0.0; worst_p = 0.0
    for i in range(q_c.shape[0]):
        norm_q = max(_sup_norm(q_r[i]), 1.0)
        norm_p = max(_sup_norm(p_r[i]), 1.0)
        worst_q = max(worst_q, _sup_norm(q_c[i] - q_r[i]) / norm_q)
        worst_p = max(worst_p, _sup_norm(p_c[i] - p_r[i]) / norm_p)
    worst = max(worst_q, worst_p)
    if worst > tol_h:
        return {"pass": False,
                "detail": f"trajectory rel-err max {worst:.3e} > tol {tol_h:.3e} (h^{p}={h**p:.3e}, factor={tol_factor})"}
    return {"pass": True, "detail": f"trajectory rel-err max {worst:.3e} ≤ tol {tol_h:.3e}"}


def check_energy_drift_bounded(inp: dict, candidate: dict, expected: dict) -> dict:
    drift_max = float(candidate["energy_drift_max"])
    t0 = float(inp["t_span"]["t0"])
    tf = float(inp["t_span"]["tf"])
    n_steps = int(inp["n_steps"])
    h = (tf - t0) / n_steps
    method = candidate.get("method", "velocity-verlet")
    p = 4 if method == "yoshida-4" else 2

    drift_constant = float(expected.get("drift_constant", 1.0))
    tol = SAFETY * (h ** p) * drift_constant
    if drift_max > tol:
        return {"pass": False,
                "detail": f"energy_drift_max={drift_max:.3e} > tol={tol:.3e} (100·h^{p}·{drift_constant})"}
    return {"pass": True, "detail": f"energy_drift_max={drift_max:.3e} ≤ tol={tol:.3e}"}


def check_energy_drift_not_secular(expected: dict, candidate: dict) -> dict:
    if not expected.get("long_time"):
        return {"pass": True, "detail": "non-long-time case; check skipped"}
    if bool(candidate["energy_drift_secular"]):
        return {"pass": False,
                "detail": "energy_drift_secular: true on long-time case (non-symplectic candidate?)"}
    return {"pass": True, "detail": "energy_drift bounded across long horizon"}


def verify_tagged(inp: dict, candidate: dict) -> dict:
    tag = candidate.get("tag", "")
    payload = candidate.get("payload", {})

    if tag == "integrate-ode-symplectic/degenerate-tspan":
        t0, tf = float(inp["t_span"]["t0"]), float(inp["t_span"]["tf"])
        if not (t0 == tf or int(inp.get("n_steps", 0)) == 0):
            return {"pass": False, "reason": "degenerate-tspan tagged but neither t0==tf nor n_steps==0",
                    "checks": {"boundary": {"pass": False, "detail": "input mismatch"}}}
        return {"pass": True, "reason": "degenerate-tspan correctly tagged",
                "checks": {"boundary": {"pass": True, "detail": tag}}}

    if tag == "integrate-ode-symplectic/non-separable-hamiltonian":
        # Re-check separability via sympy on the input H.
        try:
            H, syms = _parse_hamiltonian(inp["H_str"], inp["q_vars"], inp["p_vars"])
            sep_reason = _check_separability(H, syms, inp["q_vars"], inp["p_vars"])
            if sep_reason is None:
                return {"pass": False, "reason": "non-separable-hamiltonian tagged but H IS separable",
                        "checks": {"boundary": {"pass": False, "detail": "input misclassified"}}}
        except Exception as e:
            return {"pass": False, "reason": f"non-separable check crashed: {e}",
                    "checks": {"boundary": {"pass": False, "detail": str(e)}}}
        return {"pass": True, "reason": "non-separable-hamiltonian correctly tagged",
                "checks": {"boundary": {"pass": True, "detail": tag}}}

    if tag == "integrate-ode-symplectic/non-finite-during-eval":
        if not isinstance(payload, dict):
            return {"pass": False, "reason": "payload not dict",
                    "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        for required in ("at_t", "at_q", "at_p"):
            if required not in payload:
                return {"pass": False, "reason": f"payload missing {required}",
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
                    "reason": f"emitted tagged but expected {expected.get('kind', 'success')!r}",
                    "checks": {"category": {"pass": False, "detail": "category mismatch"}}}
        if candidate.get("tag") != expected.get("tag"):
            return {"pass": False,
                    "reason": f"tag mismatch: {candidate.get('tag')!r} vs {expected.get('tag')!r}",
                    "checks": {"category": {"pass": False, "detail": "tag mismatch"}}}
        return verify_tagged(inp, candidate)

    if cand_kind == "tool_error":
        if expected.get("kind") != "tool_error":
            return {"pass": False,
                    "reason": f"emitted tool_error but expected {expected.get('kind', 'success')!r}",
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
    if not checks["shape"]["pass"]: return _wrap(checks)
    checks["finite_entries"] = check_finite_entries(candidate)
    if not checks["finite_entries"]["pass"]: return _wrap(checks)
    checks["monotone_t_values"] = check_monotone_t_values(inp, candidate)
    checks["status_consistency"] = check_status_consistency(inp, candidate)
    checks["trajectory_accuracy"] = check_trajectory_accuracy(inp, candidate, expected)
    checks["energy_drift_bounded"] = check_energy_drift_bounded(inp, candidate, expected)
    checks["energy_drift_not_secular"] = check_energy_drift_not_secular(expected, candidate)
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
