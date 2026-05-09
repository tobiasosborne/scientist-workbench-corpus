#!/usr/bin/env python3
"""Reference symplectic integrator: hand-coded Velocity Verlet + Yoshida-4
in Python, with sympy-derived ∂H/∂q and ∂H/∂p.

Reads JSON I/O contract; emits candidate-shape output.

Boundary handling:
  - `t0 == tf` or `n_steps == 0` → tagged `degenerate-tspan`.
  - H non-separable (∂H/∂q depends on p, or ∂H/∂p depends on q) →
    tagged `non-separable-hamiltonian`.
  - H non-finite during step → tagged `non-finite-during-eval`.

Usage:
  python3 symplectic_reference.py < input.json
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Callable

import numpy as np
import sympy

ADMITTED_HEADS = {
    "+", "-", "*", "/", "^", "neg",
    "exp", "sin", "cos", "tan", "log", "sqrt", "abs",
    "asin", "acos", "atan",
    "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "log2", "log10",
}

SYMPY_LOCALS = {
    "exp": sympy.exp, "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan,
    "log": sympy.log, "sqrt": sympy.sqrt, "abs": sympy.Abs,
    "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
    "sinh": sympy.sinh, "cosh": sympy.cosh, "tanh": sympy.tanh,
    "asinh": sympy.asinh, "acosh": sympy.acosh, "atanh": sympy.atanh,
    "log2": lambda x: sympy.log(x, 2), "log10": lambda x: sympy.log(x, 10),
    "pi": sympy.pi, "e": sympy.E,
}

# Yoshida 4th-order Suzuki-Yoshida composition coefficients.
W = 1.0 / (2.0 - 2.0 ** (1.0 / 3.0))
YOSHIDA_W = [W, 1.0 - 2.0 * W, W]


def _parse_hamiltonian(H_str: str, q_vars: list[str], p_vars: list[str]
                       ) -> tuple[Any, dict[str, sympy.Symbol]]:
    syms = {v: sympy.Symbol(v) for v in q_vars + p_vars + ["t"]}
    try:
        H = sympy.parse_expr(
            H_str,
            local_dict={**SYMPY_LOCALS, **syms},
            transformations=sympy.parsing.sympy_parser.standard_transformations + (
                sympy.parsing.sympy_parser.convert_xor,
            ),
        )
    except (sympy.SympifyError, SyntaxError, TypeError) as e:
        raise ValueError(f"failed to parse H: {e}")
    for sub in H.atoms(sympy.Function):
        head = type(sub).__name__
        if head not in ADMITTED_HEADS and not isinstance(sub, sympy.Symbol):
            raise ValueError(f"unknown head {head!r} in H")
    return H, syms


def _check_separability(H: Any, syms: dict, q_vars: list[str], p_vars: list[str]
                        ) -> str | None:
    """Return None if separable, else a reason string."""
    p_syms = [syms[v] for v in p_vars]
    q_syms = [syms[v] for v in q_vars]
    # ∂H/∂q must not depend on p
    for q in q_syms:
        dHdq = sympy.diff(H, q)
        for p in p_syms:
            if dHdq.has(p):
                return f"∂H/∂{q.name} depends on {p.name}"
    # ∂H/∂p must not depend on q
    for p in p_syms:
        dHdp = sympy.diff(H, p)
        for q in q_syms:
            if dHdp.has(q):
                return f"∂H/∂{p.name} depends on {q.name}"
    return None


def integrate_symplectic(payload: dict) -> dict:
    H_str = payload["H_str"]
    q_vars = payload["q_vars"]
    p_vars = payload["p_vars"]
    q0 = np.asarray(payload["q0"], dtype=np.float64)
    p0 = np.asarray(payload["p0"], dtype=np.float64)
    t0 = float(payload["t_span"]["t0"])
    tf = float(payload["t_span"]["tf"])
    n_steps = int(payload["n_steps"])
    options = payload.get("options", {}) or {}
    scheme = options.get("scheme", "verlet")
    atol = float(options.get("atol", 1e-9))

    if t0 == tf or n_steps == 0:
        return {
            "kind": "tagged",
            "tag": "integrate-ode-symplectic/degenerate-tspan",
            "payload": {"t0": t0, "tf": tf, "n_steps": n_steps},
        }
    if len(q_vars) != len(q0) or len(p_vars) != len(p0):
        return {
            "kind": "tool_error",
            "name": "DimensionMismatch",
            "message": f"len(q_vars)={len(q_vars)}, len(q0)={len(q0)}, "
                       f"len(p_vars)={len(p_vars)}, len(p0)={len(p0)}",
        }
    if len(q_vars) != len(p_vars):
        return {
            "kind": "tool_error",
            "name": "DimensionMismatch",
            "message": f"q and p must have equal dim",
        }

    try:
        H_expr, syms = _parse_hamiltonian(H_str, q_vars, p_vars)
    except ValueError as e:
        return {
            "kind": "tool_error",
            "name": "UnknownVocabulary" if "unknown head" in str(e).lower()
                    else "HamiltonianParseFailed",
            "message": str(e),
        }

    sep_reason = _check_separability(H_expr, syms, q_vars, p_vars)
    if sep_reason is not None:
        return {
            "kind": "tagged",
            "tag": "integrate-ode-symplectic/non-separable-hamiltonian",
            "payload": {"reason": sep_reason},
        }

    q_syms = [syms[v] for v in q_vars]
    p_syms = [syms[v] for v in p_vars]
    all_syms = q_syms + p_syms

    # Lambdify ∂H/∂q (= -p_dot) and ∂H/∂p (= q_dot), and H itself.
    grad_q = [sympy.lambdify(all_syms, sympy.diff(H_expr, q), modules="numpy") for q in q_syms]
    grad_p = [sympy.lambdify(all_syms, sympy.diff(H_expr, p), modules="numpy") for p in p_syms]
    H_eval = sympy.lambdify(all_syms, H_expr, modules="numpy")

    def force(q: np.ndarray, p: np.ndarray) -> np.ndarray:
        # F = -∂H/∂q (so dp/dt = F)
        args = list(q) + list(p)
        return np.array([-float(g(*args)) for g in grad_q])

    def velocity(q: np.ndarray, p: np.ndarray) -> np.ndarray:
        args = list(q) + list(p)
        return np.array([float(g(*args)) for g in grad_p])

    def energy(q: np.ndarray, p: np.ndarray) -> float:
        return float(H_eval(*list(q), *list(p)))

    h = (tf - t0) / n_steps

    def verlet_step(q, p, h):
        # Velocity Verlet: half-kick, drift, half-kick
        F = force(q, p)
        p_half = p + 0.5 * h * F
        v_half = velocity(q, p_half)  # for separable H: depends only on p_half
        q_new = q + h * v_half
        F_new = force(q_new, p_half)
        p_new = p_half + 0.5 * h * F_new
        return q_new, p_new

    def yoshida4_step(q, p, h):
        for w in YOSHIDA_W:
            q, p = verlet_step(q, p, w * h)
        return q, p

    step_fn = yoshida4_step if scheme == "yoshida-4" else verlet_step
    n_evals_per_step = (3 if scheme == "yoshida-4" else 1) * 2  # 2 force evals per Verlet sub-step

    q = q0.copy()
    p = p0.copy()
    q_traj = [q.tolist()]
    p_traj = [p.tolist()]
    t_values = [t0]
    energies = [energy(q, p)]
    n_evals = 0
    H0 = energies[0]

    for i in range(n_steps):
        try:
            q, p = step_fn(q, p, h)
        except (ZeroDivisionError, OverflowError) as e:
            return {
                "kind": "tagged",
                "tag": "integrate-ode-symplectic/non-finite-during-eval",
                "payload": {"at_t": t_values[-1], "at_q": q_traj[-1],
                            "at_p": p_traj[-1], "kind": "div-by-zero",
                            "error": str(e)},
            }
        if not (np.all(np.isfinite(q)) and np.all(np.isfinite(p))):
            return {
                "kind": "tagged",
                "tag": "integrate-ode-symplectic/non-finite-during-eval",
                "payload": {"at_t": t0 + (i + 1) * h, "at_q": q.tolist(),
                            "at_p": p.tolist(), "kind": "NaN"},
            }
        H_i = energy(q, p)
        if not math.isfinite(H_i):
            return {
                "kind": "tagged",
                "tag": "integrate-ode-symplectic/non-finite-during-eval",
                "payload": {"at_t": t0 + (i + 1) * h, "at_q": q.tolist(),
                            "at_p": p.tolist(), "kind": "non-finite-H"},
            }
        q_traj.append(q.tolist())
        p_traj.append(p.tolist())
        t_values.append(t0 + (i + 1) * h)
        energies.append(H_i)
        n_evals += n_evals_per_step

    # Compute energy drift diagnostics.
    H0_abs = max(abs(H0), atol)
    drifts = [abs(e - H0) / H0_abs for e in energies]
    drift_max = max(drifts)
    # Secular detection: fit drift = A·t + B, check |A|·(tf-t0) vs drift_max.
    ts_arr = np.asarray(t_values) - t0
    drifts_arr = np.asarray(drifts)
    if len(ts_arr) >= 4:
        A, B = np.polyfit(ts_arr, drifts_arr, 1)
        secular = abs(A) * (tf - t0) > 5.0 * max(drift_max, atol)
    else:
        secular = False

    return {
        "q_trajectory": q_traj,
        "p_trajectory": p_traj,
        "t_values": t_values,
        "energy": energies,
        "energy_drift_max": float(drift_max),
        "energy_drift_secular": bool(secular),
        "n_evals": n_evals,
        "n_steps": n_steps,
        "converged": True,
        "status": "success",
        "method": "yoshida-4" if scheme == "yoshida-4" else "velocity-verlet",
        "warnings": [],
    }


def main() -> None:
    payload = json.load(sys.stdin)
    result = integrate_symplectic(payload)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
