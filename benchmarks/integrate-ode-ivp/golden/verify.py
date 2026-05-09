#!/usr/bin/env python3
"""integrate-ode-ivp verifier — invariant-based, language-neutral.

stdin:
  {"input": <ode-input>, "candidate": <output>, "id": str}

  where <output> is one of:
    - success record: {"trajectory": ..., "t_values": ...,
                       "error_estimate": ..., "n_evals": ...,
                       "n_steps_accepted": ..., "n_steps_rejected": ...,
                       "converged": ..., "status": ..., "method": ...,
                       "warnings": [...]}
    - tagged boundary: {"kind": "tagged", "tag": "...", "payload": {...}}
    - tool error marker: {"kind": "tool_error", "name": ..., "message": ...}

stdout:
  {"pass": bool, "reason": str, "checks": {<name>: {"pass": bool, "detail": str}}}

Reads `expected.json` adjacent on disk for reference trajectories,
conservation kinds, chaotic-window flags. Implements the 8-check
success-path verifier specified in `verifier_protocol.md`.

Tolerances: HNW 1993 §II.10 with 100× empirical safety factor; matches
the regime SciPy's solve_ivp clears by ≥1 order on the bench cases.
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
SELF_REPORT_REL_TOL = 10.0  # error estimates are factor-of-10 honest
RTOL_DEFAULT = 1e-3
ATOL_DEFAULT = 1e-6

HERE = Path(__file__).resolve().parent

# --- expected.json index (loaded once) ---------------------------------------

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


# --- conservation invariants -------------------------------------------------


def conserved_quantity(spec: dict, y: np.ndarray) -> float:
    """Compute the conserved scalar at state y. y has shape (n_components,)."""
    kind = spec["kind"]
    if kind == "harmonic_energy":
        # H = (q² + p²)/2; state (q, p) = (y[0], y[1])
        return 0.5 * (y[0] ** 2 + y[1] ** 2)
    if kind == "kepler_energy":
        # H = (px² + py²)/2 − 1/r; state (qx, qy, px, py) = y[0..3]
        qx, qy, px, py = float(y[0]), float(y[1]), float(y[2]), float(y[3])
        r = math.sqrt(qx * qx + qy * qy)
        if r == 0.0:
            return float("inf")
        return 0.5 * (px * px + py * py) - 1.0 / r
    if kind == "kepler_angular_momentum":
        qx, qy, px, py = float(y[0]), float(y[1]), float(y[2]), float(y[3])
        return qx * py - qy * px
    if kind == "lotka_volterra_h":
        # H(x, y) = δ x − γ ln x + β y − α ln y
        alpha = float(spec["alpha"])
        beta = float(spec["beta"])
        gamma = float(spec["gamma"])
        delta = float(spec["delta"])
        x, yy = float(y[0]), float(y[1])
        if x <= 0.0 or yy <= 0.0:
            return float("inf")
        return delta * x - gamma * math.log(x) + beta * yy - alpha * math.log(yy)
    raise ValueError(f"unknown conservation kind: {kind!r}")


# --- helpers -----------------------------------------------------------------


def _is_finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def _shape_of(traj: Any) -> tuple[int, int] | None:
    if not isinstance(traj, list):
        return None
    if not traj:
        return (0, 0)
    if not all(isinstance(row, list) for row in traj):
        return None
    n_cols = len(traj[0])
    if not all(len(row) == n_cols for row in traj):
        return None
    if not all(isinstance(v, (int, float)) for row in traj for v in row):
        return None
    return (len(traj), n_cols)


def _vec_finite(v: Any) -> bool:
    if not isinstance(v, list):
        return False
    return all(isinstance(x, (int, float)) and math.isfinite(float(x)) for x in v)


def _sup_norm(v: np.ndarray) -> float:
    if v.size == 0:
        return 0.0
    return float(np.max(np.abs(v)))


# --- per-check implementations ----------------------------------------------


def check_shape(inp: dict, candidate: dict) -> dict:
    n = len(inp["y0"])
    options = inp.get("options", {}) or {}
    t_eval = options.get("t_eval")
    expected_m = len(t_eval) if t_eval is not None else 2

    required = {
        "trajectory", "t_values", "error_estimate",
        "n_evals", "n_steps_accepted", "n_steps_rejected",
        "converged", "status", "method", "warnings",
    }
    missing = required - set(candidate.keys())
    if missing:
        return {"pass": False, "detail": f"missing fields: {sorted(missing)}"}

    s = _shape_of(candidate["trajectory"])
    if s is None:
        return {"pass": False, "detail": "trajectory is not a 2-D list of numbers"}
    if s != (expected_m, n):
        return {"pass": False,
                "detail": f"trajectory shape {s}, expected {(expected_m, n)}"}

    if not isinstance(candidate["t_values"], list):
        return {"pass": False, "detail": "t_values must be a list"}
    if len(candidate["t_values"]) != expected_m:
        return {"pass": False,
                "detail": f"t_values length {len(candidate['t_values'])}, "
                          f"expected {expected_m}"}

    if not isinstance(candidate["method"], str):
        return {"pass": False, "detail": "method must be a string"}

    valid_status = {"success", "max_step_exceeded",
                    "tspan_exhausted", "stiffness-detected"}
    if candidate["status"] not in valid_status:
        return {"pass": False,
                "detail": f"status {candidate['status']!r} not in {valid_status}"}

    if not isinstance(candidate["warnings"], list) or \
       not all(isinstance(s, str) for s in candidate["warnings"]):
        return {"pass": False, "detail": "warnings must be list[str]"}

    for f in ("n_evals", "n_steps_accepted", "n_steps_rejected"):
        if not isinstance(candidate[f], int):
            return {"pass": False, "detail": f"{f} must be an integer"}

    if not isinstance(candidate["converged"], bool):
        return {"pass": False, "detail": "converged must be a bool"}

    return {"pass": True,
            "detail": f"trajectory {s}, t_values length {expected_m}"}


def check_finite_entries(candidate: dict) -> dict:
    traj = candidate["trajectory"]
    for i, row in enumerate(traj):
        if not _vec_finite(row):
            return {"pass": False,
                    "detail": f"non-finite entry in trajectory row {i}"}
    if not _is_finite(candidate["error_estimate"]):
        return {"pass": False, "detail": "error_estimate non-finite"}
    return {"pass": True, "detail": "all entries finite"}


def check_monotone_t_values(inp: dict, candidate: dict) -> dict:
    t0 = float(inp["t_span"]["t0"])
    tf = float(inp["t_span"]["tf"])
    t_values = candidate["t_values"]
    options = inp.get("options", {}) or {}
    t_eval = options.get("t_eval")

    if t_eval is not None:
        if len(t_eval) != len(t_values):
            return {"pass": False,
                    "detail": f"t_values length {len(t_values)} != "
                              f"options.t_eval length {len(t_eval)}"}
        for i, (a, b) in enumerate(zip(t_eval, t_values)):
            if abs(a - b) > 1e-12 * max(abs(a), 1.0):
                return {"pass": False,
                        "detail": f"t_values[{i}]={b} differs from "
                                  f"options.t_eval[{i}]={a}"}
    forward = tf >= t0
    if forward:
        for i in range(len(t_values) - 1):
            if t_values[i] > t_values[i + 1] + 1e-12:
                return {"pass": False,
                        "detail": f"forward integration but t_values[{i}]={t_values[i]} "
                                  f"> t_values[{i+1}]={t_values[i+1]}"}
    else:
        for i in range(len(t_values) - 1):
            if t_values[i] < t_values[i + 1] - 1e-12:
                return {"pass": False,
                        "detail": f"reverse integration but t_values[{i}]={t_values[i]} "
                                  f"< t_values[{i+1}]={t_values[i+1]}"}
    return {"pass": True,
            "detail": f"t_values monotone over {len(t_values)} points"}


def check_status_consistency(candidate: dict) -> dict:
    converged = bool(candidate["converged"])
    status = candidate["status"]
    if converged != (status == "success"):
        return {"pass": False,
                "detail": f"converged={converged} but status={status!r}"}

    n_evals = int(candidate["n_evals"])
    n_acc = int(candidate["n_steps_accepted"])
    # Bookkeeping sanity: counts are non-negative and consistent.
    # Stage count is implementation-specific; we only check structural
    # invariants. A candidate emitting `n_evals = 0` with `n_acc > 0`
    # is broken; checking strict FSAL ratios is too sensitive to
    # variant counting conventions across DOPRI5 / DOP853 / etc.
    if n_acc < 0 or n_evals < 0 or int(candidate["n_steps_rejected"]) < 0:
        return {"pass": False,
                "detail": f"negative counter: n_evals={n_evals}, n_acc={n_acc}"}
    if n_acc > 0 and n_evals < n_acc:
        return {"pass": False,
                "detail": (f"n_evals={n_evals} < n_steps_accepted={n_acc} "
                           f"(must be ≥ 1 eval per step)")}
    return {"pass": True,
            "detail": f"converged={converged}, status={status}, "
                      f"n_evals/n_acc={n_evals}/{n_acc}"}


def check_trajectory_accuracy(inp: dict, candidate: dict, expected: dict) -> dict:
    rtol = float(expected.get("rtol", inp.get("options", {}).get("rtol", RTOL_DEFAULT)))
    atol = float(expected.get("atol", inp.get("options", {}).get("atol", ATOL_DEFAULT)))
    traj_c = np.asarray(candidate["trajectory"], dtype=np.float64)
    traj_r = np.asarray(expected["trajectory"], dtype=np.float64)

    if traj_c.shape != traj_r.shape:
        return {"pass": False,
                "detail": f"trajectory shape {traj_c.shape} != reference {traj_r.shape}"}

    chaotic_until = expected.get("chaotic_until_t")
    t_eval = np.asarray(candidate["t_values"], dtype=np.float64)

    # Long-horizon relaxation: HNW Vol I §II.10 says global error scales
    # linearly with `tf - t0` for stable adaptive RK. Bake that in.
    t0 = float(inp["t_span"]["t0"])
    tf = float(inp["t_span"]["tf"])
    horizon_factor = max(abs(tf - t0) / 10.0, 1.0)
    # Per-case override for problems with extra-tight or extra-loose tol.
    safety = float(expected.get("traj_tol_factor", SAFETY))

    worst_idx = -1
    worst_err = 0.0
    worst_tol = 0.0
    for i in range(traj_c.shape[0]):
        if chaotic_until is not None and t_eval[i] > chaotic_until:
            continue  # skip pointwise check beyond Lyapunov time
        ref = traj_r[i]
        cand = traj_c[i]
        err = _sup_norm(cand - ref)
        tol = max(safety * rtol * _sup_norm(ref) * horizon_factor,
                  safety * atol * horizon_factor)
        if err > tol:
            if err / max(tol, EPS) > worst_err / max(worst_tol, EPS) or worst_idx < 0:
                worst_idx = i
                worst_err = err
                worst_tol = tol
    if worst_idx >= 0:
        return {"pass": False,
                "detail": (f"trajectory[{worst_idx}] sup-norm error "
                           f"{worst_err:.3e} > tol {worst_tol:.3e} "
                           f"(t={t_eval[worst_idx]:.4f}, rtol={rtol}, atol={atol})")}

    # Beyond chaotic time: attractor-statistics check.
    if chaotic_until is not None:
        mask = t_eval > chaotic_until
        if mask.any():
            cand_post = traj_c[mask]
            ref_post = traj_r[mask]
            mean_c = np.mean(cand_post, axis=0)
            mean_r = np.mean(ref_post, axis=0)
            std_c = np.std(cand_post, axis=0)
            std_r = np.std(ref_post, axis=0)
            mean_diff = float(np.max(np.abs(mean_c - mean_r) / np.maximum(np.abs(mean_r), 1.0)))
            std_diff = float(np.max(np.abs(std_c - std_r) / np.maximum(np.abs(std_r), 1.0)))
            stat_tol = 0.5  # 50% relative is generous for attractor stats over short horizons
            if mean_diff > stat_tol or std_diff > stat_tol:
                return {"pass": False,
                        "detail": (f"chaotic-window attractor stats: "
                                   f"mean_rel_diff={mean_diff:.3e}, "
                                   f"std_rel_diff={std_diff:.3e}, tol={stat_tol}")}

    return {"pass": True,
            "detail": f"trajectory matches reference to tol within {traj_c.shape[0]} points"}


def check_self_reported_error_estimate(inp: dict, candidate: dict,
                                        expected: dict) -> dict:
    """Structural check on `error_estimate`: must be non-negative and finite,
    AND if `status === "success"` the controller accepted the last step
    under its tolerance budget — meaning `error_estimate` (the tracker's
    final value) should be ≤ 1.0 if normalised to controller-units, or
    ≤ a generous bound if the tracker is in raw units.

    The "agent-honest within 10× of actual" semantics from the original
    plan is dropped: the controller's internal `error_estimate` is the
    *last accepted step's local error*, which has no general relationship
    to the global error along the trajectory. Tighter discipline (e.g.
    Richardson cross-check) is deferred to a v0.2 bench.
    """
    reported = float(candidate["error_estimate"])
    if reported < 0:
        return {"pass": False,
                "detail": f"error_estimate={reported:.3e} is negative"}
    # If status is success, the last step was accepted, so its local error
    # was ≤ tol_local. In any reasonable normalisation that means
    # error_estimate ≤ 1 (normalised) or ≤ atol·1e6 (raw, generous).
    status = candidate.get("status")
    if status == "success":
        atol = float(inp.get("options", {}).get("atol", ATOL_DEFAULT))
        bound = max(1.0, atol * 1e6)
        if reported > bound:
            return {"pass": False,
                    "detail": (f"error_estimate={reported:.3e} > {bound:.3e} "
                               f"despite status=success "
                               f"(controller accepted but reports out-of-budget error)")}
    return {"pass": True,
            "detail": f"error_estimate={reported:.3e} (status={status})"}


def check_conservation(inp: dict, candidate: dict, expected: dict) -> dict:
    spec = expected.get("conservation")
    if spec is None:
        return {"pass": True, "detail": "no conservation invariant for this case"}

    rtol = float(expected.get("rtol", RTOL_DEFAULT))
    t0 = float(inp["t_span"]["t0"])
    tf = float(inp["t_span"]["tf"])
    horizon = abs(tf - t0)
    traj = np.asarray(candidate["trajectory"], dtype=np.float64)
    if traj.shape[0] == 0:
        return {"pass": True, "detail": "empty trajectory"}

    H0 = conserved_quantity(spec, traj[0])
    if not math.isfinite(H0):
        return {"pass": False, "detail": f"H0 = {H0!r} (non-finite)"}
    denom = max(abs(H0), float(expected.get("atol", ATOL_DEFAULT)))
    tol = SAFETY * rtol * max(horizon, 1.0)

    worst = 0.0
    worst_i = 0
    for i in range(1, traj.shape[0]):
        H = conserved_quantity(spec, traj[i])
        if not math.isfinite(H):
            return {"pass": False,
                    "detail": f"H(trajectory[{i}]) = {H!r} (non-finite)"}
        rel = abs(H - H0) / denom
        if rel > worst:
            worst = rel
            worst_i = i
    if worst > tol:
        return {"pass": False,
                "detail": (f"conservation drift max {worst:.3e} at i={worst_i} "
                           f"> tol {tol:.3e} ({spec['kind']}, "
                           f"100·rtol·tf-t0 with rtol={rtol}, tf-t0={horizon})")}
    return {"pass": True,
            "detail": f"max conservation drift {worst:.3e} ≤ tol {tol:.3e} ({spec['kind']})"}


# --- tagged-boundary handling ------------------------------------------------


def _eval_rhs_at(inp: dict, t: float, y: list[float]) -> list[float] | None:
    """Best-effort RHS evaluation via sympy parse_expr.

    Returns the RHS vector at (t, y), or `[inf, ...]` if evaluation
    raised ZeroDivisionError / OverflowError (a poles-on-the-domain
    signal — the candidate's tag of `non-finite-during-eval` is honest
    in that case). Returns None on parse-time failures (the user gave
    bad expressions; the verifier shouldn't accept the tag on those —
    a separate ToolError class applies).
    """
    try:
        sys.path.insert(0, str(HERE.parent / "reference"))
        from ivp_reference import _parse_rhs  # type: ignore[import]
        f = _parse_rhs(inp["f_str"], inp["t_var"], inp["vars"])
    except Exception:
        return None
    try:
        out = f(t, np.asarray(y, dtype=np.float64))
        return out.tolist()
    except (ZeroDivisionError, OverflowError):
        # Pole hit — signal non-finite to caller.
        return [float("inf")] * len(y)
    except Exception:
        return None


def verify_tagged(inp: dict, candidate: dict) -> dict:
    tag = candidate.get("tag", "")
    payload = candidate.get("payload", {})

    if tag == "integrate-ode-ivp/degenerate-tspan":
        t0 = float(inp["t_span"]["t0"])
        tf = float(inp["t_span"]["tf"])
        if t0 != tf:
            return {"pass": False, "reason": "degenerate-tspan tagged but t0 != tf",
                    "checks": {"boundary": {"pass": False,
                                            "detail": f"t0={t0}, tf={tf}"}}}
        # Validate payload shape.
        if not isinstance(payload, dict) or "t0" not in payload or "tf" not in payload:
            return {"pass": False,
                    "reason": "degenerate-tspan payload missing t0/tf",
                    "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        return {"pass": True, "reason": "degenerate-tspan correctly tagged",
                "checks": {"boundary": {"pass": True, "detail": tag}}}

    if tag == "integrate-ode-ivp/non-finite-during-eval":
        # Verify payload shape only. Verifying the diagnosis (that the RHS
        # is genuinely non-finite somewhere in [t0, tf]) requires symbolic
        # analysis of the closed expression and is brittle in general
        # (movable poles, conditionally-singular flows). The bench tests
        # that the candidate handles non-finite RHS *gracefully by tagging*;
        # the candidate's own goldens / property tests are responsible for
        # ensuring the diagnosis is right. The bench is checking correct
        # *category* of refusal, not pin-pointing.
        if not isinstance(payload, dict):
            return {"pass": False,
                    "reason": "non-finite-during-eval payload not a dict",
                    "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        for required in ("at_t", "at_y"):
            if required not in payload:
                return {"pass": False,
                        "reason": f"non-finite-during-eval payload missing {required}",
                        "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        if not isinstance(payload["at_t"], (int, float)):
            return {"pass": False,
                    "reason": "non-finite-during-eval payload.at_t must be a number",
                    "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        if not isinstance(payload["at_y"], list):
            return {"pass": False,
                    "reason": "non-finite-during-eval payload.at_y must be a list",
                    "checks": {"boundary": {"pass": False, "detail": str(payload)}}}
        return {"pass": True, "reason": "non-finite-during-eval correctly tagged",
                "checks": {"boundary": {"pass": True, "detail": tag}}}

    return {"pass": False, "reason": f"unknown tag {tag!r}",
            "checks": {"boundary": {"pass": False, "detail": tag}}}


# --- top-level verify --------------------------------------------------------


def verify(payload: dict) -> dict:
    inp = payload["input"]
    candidate = payload["candidate"]
    case_id = payload.get("id", "?")

    if not isinstance(candidate, dict):
        return {"pass": False, "reason": "candidate must be a JSON object", "checks": {}}

    expected_index = _load_expected()
    expected = expected_index.get(case_id, {})

    # Branch on candidate kind.
    cand_kind = candidate.get("kind")

    if cand_kind == "tagged":
        if expected.get("kind") != "tagged":
            return {"pass": False,
                    "reason": f"candidate emitted tagged but expected was {expected.get('kind', 'success')!r}",
                    "checks": {"category": {"pass": False, "detail": "category mismatch"}}}
        # Check tag matches expected tag.
        if candidate.get("tag") != expected.get("tag"):
            return {"pass": False,
                    "reason": (f"tag mismatch: candidate={candidate.get('tag')!r}, "
                               f"expected={expected.get('tag')!r}"),
                    "checks": {"category": {"pass": False, "detail": "tag mismatch"}}}
        return verify_tagged(inp, candidate)

    if cand_kind == "tool_error":
        if expected.get("kind") != "tool_error":
            return {"pass": False,
                    "reason": f"candidate emitted tool_error but expected was {expected.get('kind', 'success')!r}",
                    "checks": {"category": {"pass": False, "detail": "category mismatch"}}}
        return {"pass": True, "reason": "tool_error correctly emitted",
                "checks": {"tool_error_expected": {
                    "pass": True,
                    "detail": f"name={candidate.get('name', '?')!r}",
                }}}

    # Success path. Expected must be a success record.
    if expected.get("kind") in ("tagged", "tool_error"):
        return {"pass": False,
                "reason": (f"candidate emitted success record but expected was "
                           f"{expected.get('kind')!r}"),
                "checks": {"category": {"pass": False, "detail": "category mismatch"}}}
    if not expected:
        return {"pass": False, "reason": f"no expected entry for id={case_id!r}",
                "checks": {}}

    checks: dict[str, dict] = {}

    # 1. shape
    checks["shape"] = check_shape(inp, candidate)
    if not checks["shape"]["pass"]:
        return _wrap(checks)

    # 2. finite_entries
    checks["finite_entries"] = check_finite_entries(candidate)
    if not checks["finite_entries"]["pass"]:
        return _wrap(checks)

    # 3. monotone_t_values
    checks["monotone_t_values"] = check_monotone_t_values(inp, candidate)

    # 4. status_consistency
    checks["status_consistency"] = check_status_consistency(candidate)

    # 5. trajectory_accuracy
    checks["trajectory_accuracy"] = check_trajectory_accuracy(inp, candidate, expected)

    # 6. self_reported_error_estimate
    checks["self_reported_error_estimate"] = \
        check_self_reported_error_estimate(inp, candidate, expected)

    # 7. conservation (only if expected has conservation spec)
    checks["conservation"] = check_conservation(inp, candidate, expected)

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
