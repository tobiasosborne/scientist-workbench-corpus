#!/usr/bin/env python3
"""Reference stiff IVP solver via SciPy `solve_ivp(method='Radau')`.

Reads JSON I/O contract; emits candidate-shape output. Used to:
  1. Provide oracle trajectories for `expected.json`.
  2. Provide reference Jacobian-counter / LU-counter values.

Tolerance regime: `rtol=1e-13, atol=1e-14` — Radau-IIA(5) at
machine-precision-bounded local error. Reference accuracy on smooth
stiff problems: ~1e-12 absolute.

Boundary handling:
  - `t0 == tf` → tagged `degenerate-tspan`.
  - RHS / Jacobian non-finite → tagged `non-finite-during-eval`.
  - Singular Jacobian → tagged `jacobian-singular`.
  - `options.method == 'bdf'` → tagged `method-not-implemented`.

Usage:
  python3 stiff_reference.py < input.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import scipy.integrate

# Reuse the path-finder bench's RHS parser — shared closed vocabulary.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "integrate-ode-ivp" / "reference"))
from ivp_reference import _parse_rhs  # noqa: E402

DEFAULT_RTOL = 1e-3
DEFAULT_ATOL = 1e-6
REFERENCE_RTOL = 1e-13
REFERENCE_ATOL = 1e-14


def integrate_stiff(payload: dict, *, rtol: float | None = None,
                    atol: float | None = None) -> dict:
    options = payload.get("options", {}) or {}
    method = options.get("method", "radau")
    if method == "bdf":
        return {
            "kind": "tagged",
            "tag": "integrate-ode-stiff/method-not-implemented",
            "payload": {"method": "bdf"},
        }
    if method != "radau":
        return {
            "kind": "tool_error",
            "name": "UnknownMethod",
            "message": f"options.method must be 'radau' or 'bdf', got {method!r}",
        }

    f_str = payload["f_str"]
    state_vars = payload["vars"]
    t_var = payload["t_var"]
    y0 = np.asarray(payload["y0"], dtype=np.float64)
    t0 = float(payload["t_span"]["t0"])
    tf = float(payload["t_span"]["tf"])

    use_rtol = rtol if rtol is not None else float(options.get("rtol", DEFAULT_RTOL))
    use_atol = atol if atol is not None else float(options.get("atol", DEFAULT_ATOL))
    max_step = options.get("max_step", None)
    t_eval = options.get("t_eval", None)

    if t0 == tf:
        return {
            "kind": "tagged",
            "tag": "integrate-ode-stiff/degenerate-tspan",
            "payload": {"t0": t0, "tf": tf},
        }

    if len(f_str) != len(y0) or len(state_vars) != len(y0):
        return {
            "kind": "tool_error",
            "name": "DimensionMismatch",
            "message": (f"len(f_str)={len(f_str)}, len(vars)={len(state_vars)}, "
                        f"len(y0)={len(y0)}; all must match"),
        }

    try:
        f = _parse_rhs(f_str, t_var, state_vars)
    except ValueError as e:
        return {
            "kind": "tool_error",
            "name": "UnknownVocabulary" if "unknown head" in str(e).lower()
                    else "RhsParseFailed",
            "message": str(e),
        }

    if t_eval is None:
        t_eval_arr = np.array([t0, tf])
    else:
        t_eval_arr = np.asarray(t_eval, dtype=np.float64)

    solve_kwargs = dict(rtol=use_rtol, atol=use_atol, t_eval=t_eval_arr,
                        method="Radau", dense_output=True)
    if max_step is not None:
        solve_kwargs["max_step"] = float(max_step)

    try:
        sol = scipy.integrate.solve_ivp(f, [t0, tf], y0, **solve_kwargs)
    except (FloatingPointError, ValueError, OverflowError) as e:
        return {
            "kind": "tagged",
            "tag": "integrate-ode-stiff/non-finite-during-eval",
            "payload": {"at_t": t0, "at_y": y0.tolist(),
                        "kind": "NaN", "scipy_error": str(e)},
        }

    if sol.status == -1:
        return {
            "kind": "tagged",
            "tag": "integrate-ode-stiff/non-finite-during-eval",
            "payload": {"at_t": float(sol.t[-1]) if len(sol.t) > 0 else t0,
                        "at_y": sol.y[:, -1].tolist() if sol.y.size > 0 else y0.tolist(),
                        "kind": "step-size-underflow",
                        "scipy_message": sol.message},
        }

    if not np.all(np.isfinite(sol.y)):
        for j in range(sol.y.shape[1]):
            if not np.all(np.isfinite(sol.y[:, j])):
                col = sol.y[:, j]
                kind = "NaN" if np.any(np.isnan(col)) else "Infinity"
                return {
                    "kind": "tagged",
                    "tag": "integrate-ode-stiff/non-finite-during-eval",
                    "payload": {"at_t": float(sol.t[j]),
                                "at_y": col.tolist(),
                                "kind": kind},
                }

    return {
        "trajectory": sol.y.T.tolist(),
        "t_values": sol.t.tolist(),
        "error_estimate": float(use_atol),    # placeholder; candidate is graded
        "n_evals": int(sol.nfev),
        "n_steps_accepted": max(int(len(sol.t)) - 1, 0),
        "n_steps_rejected": 0,                 # scipy doesn't expose; informational
        "n_jacobian_evals": int(getattr(sol, "njev", 0)),
        "n_lu_decompositions": int(getattr(sol, "nlu", 0)),
        "converged": sol.status == 0,
        "status": "success" if sol.status == 0 else "tspan_exhausted",
        "method": "radau-iia-5-reference",
        "warnings": [],
    }


def main() -> None:
    payload = json.load(sys.stdin)
    result = integrate_stiff(payload, rtol=REFERENCE_RTOL, atol=REFERENCE_ATOL)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
