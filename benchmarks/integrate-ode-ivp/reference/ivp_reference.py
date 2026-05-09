#!/usr/bin/env python3
"""Reference non-stiff IVP solver via SciPy `solve_ivp(method='DOP853')`.

Reads JSON I/O contract; emits candidate-shaped output. Used to:
  1. Provide oracle trajectories for `expected.json`.
  2. Provide reference invariants (energy, momentum) for conservation checks.
  3. Cross-check the verifier against SciPy at high tolerance.

Tolerance regime: `rtol=1e-13, atol=1e-14` — Dormand-Prince 8(5,3),
the highest-order embedded RK pair in scipy. Reference accuracy on
smooth problems: ~1e-12 absolute. This is the gold-standard non-stiff
oracle short of arbitrary-precision Taylor series.

Boundary handling:
  - `t0 == tf` → tagged "integrate-ode-ivp/degenerate-tspan".
  - RHS returns NaN/±Inf at some t → tagged
    "integrate-ode-ivp/non-finite-during-eval" (caught via SciPy's
    own propagation; we wrap solve_ivp's exception path).
  - Unknown sympy head / dim mismatch → emitted as
    `{"kind": "tool_error", "name": ..., "message": ...}`.

Usage:
  python3 ivp_reference.py < input.json
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Callable

import numpy as np
import scipy.integrate
import sympy

# Map of head names → sympy callables. Mirrors `packages/quadrature/src/eval-expr.ts`
# `ADMITTED_HEADS`. Keep in lockstep with that file.
ADMITTED_HEADS = {
    "+", "-", "*", "/", "^", "neg",
    "exp", "sin", "cos", "tan", "log", "sqrt", "abs",
    "asin", "acos", "atan",
    "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "log2", "log10",
}

# Hand-built sympy local dict so parse_expr only sees admitted heads.
# `^` is parsed as sympy's Pow when transformations include 'convert_xor'.
SYMPY_LOCALS = {
    "exp": sympy.exp, "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan,
    "log": sympy.log, "sqrt": sympy.sqrt, "abs": sympy.Abs,
    "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
    "sinh": sympy.sinh, "cosh": sympy.cosh, "tanh": sympy.tanh,
    "asinh": sympy.asinh, "acosh": sympy.acosh, "atanh": sympy.atanh,
    "log2": lambda x: sympy.log(x, 2), "log10": lambda x: sympy.log(x, 10),
    "pi": sympy.pi, "e": sympy.E,
}

DEFAULT_RTOL = 1e-3
DEFAULT_ATOL = 1e-6
REFERENCE_RTOL = 1e-13
REFERENCE_ATOL = 1e-14


def _parse_rhs(f_str_list: list[str], t_var: str,
               state_vars: list[str]) -> Callable[[float, np.ndarray], np.ndarray]:
    """Parse list of expression strings into a callable f(t, y) -> ndarray."""
    syms = {v: sympy.Symbol(v) for v in [t_var] + state_vars}
    parsed: list[Any] = []
    for s in f_str_list:
        try:
            expr = sympy.parse_expr(
                s,
                local_dict={**SYMPY_LOCALS, **syms},
                transformations=sympy.parsing.sympy_parser.standard_transformations + (
                    sympy.parsing.sympy_parser.convert_xor,
                ),
            )
        except (sympy.SympifyError, SyntaxError, TypeError) as e:
            raise ValueError(f"failed to parse RHS component {s!r}: {e}")
        # Validate every function head used is in the admitted set.
        for sub in expr.atoms(sympy.Function):
            head = type(sub).__name__
            if head not in ADMITTED_HEADS and not isinstance(sub, sympy.Symbol):
                raise ValueError(
                    f"unknown head {head!r} in RHS — admitted: "
                    f"{sorted(ADMITTED_HEADS)}"
                )
        parsed.append(expr)

    # Build vectorised lambda. lambdify produces a numpy-using callable.
    t_sym = syms[t_var]
    state_syms = [syms[v] for v in state_vars]
    lambdified = [sympy.lambdify([t_sym] + state_syms, e, modules="numpy")
                  for e in parsed]

    def f(t: float, y: np.ndarray) -> np.ndarray:
        out = np.empty(len(y), dtype=np.float64)
        for i, fn in enumerate(lambdified):
            v = fn(t, *y)
            out[i] = float(v)
        return out

    return f


def integrate_ivp(payload: dict, *, rtol: float | None = None,
                  atol: float | None = None) -> dict:
    """Run scipy DOP853 at high tolerance and emit a candidate-shape record.

    `rtol`/`atol` arguments override those in `payload['options']`. By
    default, uses the user-requested tolerance from input.options
    (defaults to 1e-3/1e-6). Pass `rtol=REFERENCE_RTOL, atol=REFERENCE_ATOL`
    to compute a reference trajectory.
    """
    f_str = payload["f_str"]
    state_vars = payload["vars"]
    t_var = payload["t_var"]
    y0 = np.asarray(payload["y0"], dtype=np.float64)
    t0 = float(payload["t_span"]["t0"])
    tf = float(payload["t_span"]["tf"])
    options = payload.get("options", {}) or {}

    use_rtol = rtol if rtol is not None else float(options.get("rtol", DEFAULT_RTOL))
    use_atol = atol if atol is not None else float(options.get("atol", DEFAULT_ATOL))
    max_step = options.get("max_step", None)
    t_eval = options.get("t_eval", None)

    # Boundary: degenerate-tspan
    if t0 == tf:
        return {
            "kind": "tagged",
            "tag": "integrate-ode-ivp/degenerate-tspan",
            "payload": {"t0": t0, "tf": tf},
        }

    # Boundary: dim mismatch → ToolError
    if len(f_str) != len(y0) or len(state_vars) != len(y0):
        return {
            "kind": "tool_error",
            "name": "DimensionMismatch",
            "message": (f"len(f_str)={len(f_str)}, len(vars)={len(state_vars)}, "
                        f"len(y0)={len(y0)}; all must match"),
        }

    # Parse the RHS. Unknown head → ToolError.
    try:
        f = _parse_rhs(f_str, t_var, state_vars)
    except ValueError as e:
        return {
            "kind": "tool_error",
            "name": "UnknownVocabulary" if "unknown head" in str(e).lower()
                    else "RhsParseFailed",
            "message": str(e),
        }

    # Default t_eval is just [t0, tf].
    if t_eval is None:
        t_eval_arr = np.array([t0, tf])
    else:
        t_eval_arr = np.asarray(t_eval, dtype=np.float64)

    solve_kwargs = dict(rtol=use_rtol, atol=use_atol, t_eval=t_eval_arr,
                        method="DOP853", dense_output=True)
    if max_step is not None:
        solve_kwargs["max_step"] = float(max_step)

    try:
        sol = scipy.integrate.solve_ivp(f, [t0, tf], y0, **solve_kwargs)
    except (FloatingPointError, ValueError, OverflowError) as e:
        # SciPy raises on non-finite RHS in some configurations.
        return {
            "kind": "tagged",
            "tag": "integrate-ode-ivp/non-finite-during-eval",
            "payload": {"at_t": t0, "at_y": y0.tolist(),
                        "kind": "NaN", "scipy_error": str(e)},
        }

    # SciPy reports status: 0 = success, 1 = termination event, -1 = failure
    if sol.status == -1:
        # SciPy hit a step-size underflow or non-finite — most likely a pole.
        # Try to find the failure point.
        return {
            "kind": "tagged",
            "tag": "integrate-ode-ivp/non-finite-during-eval",
            "payload": {"at_t": float(sol.t[-1]) if len(sol.t) > 0 else t0,
                        "at_y": sol.y[:, -1].tolist() if sol.y.size > 0 else y0.tolist(),
                        "kind": "step-size-underflow",
                        "scipy_message": sol.message},
        }

    # Check trajectory for non-finite (poles in the interior the controller
    # didn't catch).
    if not np.all(np.isfinite(sol.y)):
        # Find first non-finite column.
        for j in range(sol.y.shape[1]):
            if not np.all(np.isfinite(sol.y[:, j])):
                col = sol.y[:, j]
                kind = "NaN" if np.any(np.isnan(col)) else (
                    "Infinity" if np.any(col > 0) else "-Infinity")
                return {
                    "kind": "tagged",
                    "tag": "integrate-ode-ivp/non-finite-during-eval",
                    "payload": {"at_t": float(sol.t[j]),
                                "at_y": col.tolist(),
                                "kind": kind},
                }

    # Compute self-report fields.
    trajectory = sol.y.T.tolist()  # (m × n)
    t_values = sol.t.tolist()
    n_evals = int(sol.nfev)
    # SciPy doesn't report accepted/rejected step counts directly in solve_ivp;
    # use n_evals heuristic: DOP853 uses 12 stages + FSAL → 11 per accepted step.
    # For DOPRI5(4) at runtime this would be 6 per accepted step.
    # The reference value here is informational; the bench's candidate computes
    # its own; the verifier checks `n_evals ≥ 6·n_steps_accepted` for DOPRI5.
    # For DOP853 reference we estimate n_steps_accepted from sol.t length.
    n_steps_accepted = max(int(len(sol.t)) - 1, 0)
    n_steps_rejected = max(n_evals // 12 - n_steps_accepted, 0)

    # Error-estimate self-report: SciPy doesn't expose the controller's last
    # local error directly, so we estimate from the final dense-output residual.
    # This is informational only on the reference path — the candidate must
    # report its own tracker.
    error_estimate = use_atol  # placeholder for reference; candidate is graded
    # independently against the verifier.

    return {
        "trajectory": trajectory,
        "t_values": t_values,
        "error_estimate": float(error_estimate),
        "n_evals": n_evals,
        "n_steps_accepted": n_steps_accepted,
        "n_steps_rejected": n_steps_rejected,
        "converged": sol.status == 0,
        "status": "success" if sol.status == 0 else "tspan_exhausted",
        "method": "dormand-prince-853-reference",
        "warnings": [],
    }


def main() -> None:
    payload = json.load(sys.stdin)
    # Reference always runs at high tolerance for use as oracle.
    result = integrate_ivp(payload, rtol=REFERENCE_RTOL, atol=REFERENCE_ATOL)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
