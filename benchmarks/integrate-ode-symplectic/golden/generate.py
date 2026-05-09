#!/usr/bin/env python3
"""Generate integrate-ode-symplectic golden master.

Reference is hand-coded velocity Verlet / Yoshida-4 in Python with
sympy-derived ∂H/∂q and ∂H/∂p, run at the candidate's chosen `h`
(no high-precision oracle; symplectic methods are graded on conservation,
not pointwise accuracy against a tighter solver).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reference"))
sys.path.insert(0, str(HERE))
from symplectic_reference import integrate_symplectic  # noqa: E402

ENCODING_VERSION = 1


def make_input(H_str, q_vars, p_vars, q0, p0, t0, tf, n_steps, *,
               scheme=None, atol=None):
    options = {}
    if scheme is not None: options["scheme"] = scheme
    if atol is not None: options["atol"] = atol
    inp = {
        "H_str": H_str,
        "q_vars": list(q_vars), "p_vars": list(p_vars),
        "t_var": "t",
        "q0": list(q0), "p0": list(p0),
        "t_span": {"t0": t0, "tf": tf},
        "n_steps": n_steps,
    }
    if options: inp["options"] = options
    return inp


def make_success(case_id, inp, *, drift_constant=None, traj_tol_factor=None,
                 long_time=False, oracle="reference_verlet"):
    ref = integrate_symplectic(inp)
    if "kind" in ref:
        raise RuntimeError(f"reference produced non-success on {case_id!r}: {ref}")
    expected = {
        "q_trajectory": ref["q_trajectory"],
        "p_trajectory": ref["p_trajectory"],
        "t_values": ref["t_values"],
        "energy_drift_max_ref": ref["energy_drift_max"],
        "oracle_source": oracle,
    }
    if drift_constant is not None: expected["drift_constant"] = drift_constant
    if traj_tol_factor is not None: expected["traj_tol_factor"] = traj_tol_factor
    if long_time: expected["long_time"] = True
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": expected})


def make_tagged(case_id, inp, tag, payload):
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": {"kind": "tagged", "tag": tag, "payload": payload}})


def make_tool_error(case_id, inp, expected_class):
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": {"kind": "tool_error", "expected_class": expected_class}})


def build_corpus():
    cases = []

    # ── A. Shape edges (3) ─────────────────────────────────────────────────
    # 1-DOF harmonic at varying n_steps. H = (q²+p²)/2.

    for n in (10, 100, 1000):
        cases.append(make_success(f"A_harmonic_n{n}",
            make_input("(q^2 + p^2) / 2", ["q"], ["p"],
                       [1.0], [0.0], 0.0, 2.0 * math.pi, n)))

    # ── B. Canonical Hamiltonian systems (5) ──────────────────────────────

    # B_harmonic_1d: 1-DOF harmonic, exact periodic
    cases.append(make_success("B_harmonic_1d",
        make_input("(q^2 + p^2) / 2", ["q"], ["p"],
                   [1.0], [0.0], 0.0, 2.0 * math.pi, 200),
        drift_constant=2.0))

    # B_pendulum_small: small angle, near-harmonic
    cases.append(make_success("B_pendulum_small",
        make_input("p^2 / 2 + (1 - cos(q))",
                   ["q"], ["p"], [0.1], [0.0], 0.0, 4.0 * math.pi, 400),
        drift_constant=2.0))

    # B_pendulum_large: full nonlinearity
    cases.append(make_success("B_pendulum_large",
        make_input("p^2 / 2 + (1 - cos(q))",
                   ["q"], ["p"], [1.5], [0.0], 0.0, 4.0 * math.pi, 400),
        drift_constant=4.0))

    # B_kepler_e0: circular orbit, 2D
    # H = (px² + py²) / 2 - 1 / sqrt(qx² + qy²)
    cases.append(make_success("B_kepler_e0",
        make_input(
            "(px^2 + py^2) / 2 - 1 / sqrt(qx^2 + qy^2)",
            ["qx", "qy"], ["px", "py"],
            [1.0, 0.0], [0.0, 1.0],
            0.0, 2.0 * math.pi, 400),
        drift_constant=10.0))

    # B_double_pendulum: 2-DOF chaotic Hamiltonian (separable form
    # using a simplification: small-angle uncoupled). For full
    # double pendulum we need the canonical Hamiltonian; that's
    # non-separable in (q, p) and would tag. Use a simpler 2-DOF
    # separable system: two uncoupled pendulums.
    cases.append(make_success("B_two_pendulums_2d",
        make_input(
            "p1^2 / 2 + p2^2 / 2 + (1 - cos(q1)) + (1 - cos(q2))",
            ["q1", "q2"], ["p1", "p2"],
            [0.5, 0.3], [0.0, 0.1],
            0.0, 4.0 * math.pi, 400),
        drift_constant=4.0))

    # ── C. Long-time conservation (3) — the discriminator ─────────────────

    # C_kepler_e05_100: 100 orbital periods at e=0.5
    e_kep = 0.5
    qx0, py0 = 1.0 - e_kep, math.sqrt((1.0 + e_kep) / (1.0 - e_kep))
    cases.append(make_success("C_kepler_e05_100periods",
        make_input(
            "(px^2 + py^2) / 2 - 1 / sqrt(qx^2 + qy^2)",
            ["qx", "qy"], ["px", "py"],
            [qx0, 0.0], [0.0, py0],
            0.0, 100.0 * 2.0 * math.pi, 4000),
        drift_constant=20.0, long_time=True))

    # C_henon_heiles: 2-DOF, energy = 1/12 (regular regime — chaotic
    # at higher E).
    # H = (p1²+p2²)/2 + (q1²+q2²)/2 + q1²·q2 − q2³/3
    # This is NOT separable as T(p) + V(q) with V depending on q only?
    # Actually it IS: T = (p1²+p2²)/2 (depends only on p), V = (q1²+q2²)/2 + q1²·q2 - q2³/3 (depends only on q). Separable.
    cases.append(make_success("C_henon_heiles",
        make_input(
            "(p1^2 + p2^2) / 2 + (q1^2 + q2^2) / 2 + q1^2 * q2 - q2^3 / 3",
            ["q1", "q2"], ["p1", "p2"],
            [0.0, 0.1], [0.5, 0.0],
            0.0, 100.0, 4000),
        drift_constant=10.0, long_time=True))

    # C_harmonic_long: 1-DOF harmonic over many periods.
    cases.append(make_success("C_harmonic_long",
        make_input("(q^2 + p^2) / 2", ["q"], ["p"],
                   [1.0], [0.0], 0.0, 100.0 * 2.0 * math.pi, 10000),
        drift_constant=2.0, long_time=True))

    # ── E. Boundary cases (4) ──────────────────────────────────────────────

    # E_degenerate_tspan: t0 == tf
    cases.append(make_tagged("E_degenerate_tspan",
        make_input("(q^2 + p^2) / 2", ["q"], ["p"],
                   [1.0], [0.0], 0.0, 0.0, 10),
        "integrate-ode-symplectic/degenerate-tspan",
        {"t0": 0.0, "tf": 0.0, "n_steps": 10}))

    # E_zero_n_steps
    cases.append(make_tagged("E_zero_n_steps",
        make_input("(q^2 + p^2) / 2", ["q"], ["p"],
                   [1.0], [0.0], 0.0, 1.0, 0),
        "integrate-ode-symplectic/degenerate-tspan",
        {"t0": 0.0, "tf": 1.0, "n_steps": 0}))

    # E_non_separable: H = (q + p)² / 2 — q and p are coupled
    cases.append(make_tagged("E_non_separable",
        make_input("(q + p)^2 / 2", ["q"], ["p"],
                   [1.0], [0.0], 0.0, 1.0, 100),
        "integrate-ode-symplectic/non-separable-hamiltonian",
        {"reason": "..."}))

    # E_dim_mismatch: |q_vars| != |q0|
    cases.append(make_tool_error("E_dim_mismatch",
        make_input("(q^2 + p^2) / 2", ["q", "extra"], ["p"],
                   [1.0], [0.0], 0.0, 1.0, 10),
        "DimensionMismatch"))

    # ── F. Order check (2 cases) — same problem at h, h/2 ─────────────────

    # F_harmonic_h: harmonic at h = T/100
    cases.append(make_success("F_harmonic_h",
        make_input("(q^2 + p^2) / 2", ["q"], ["p"],
                   [1.0], [0.0], 0.0, 2.0 * math.pi, 100),
        drift_constant=2.0))

    # F_harmonic_h_half: same problem at h/2
    cases.append(make_success("F_harmonic_h_half",
        make_input("(q^2 + p^2) / 2", ["q"], ["p"],
                   [1.0], [0.0], 0.0, 2.0 * math.pi, 200),
        drift_constant=2.0))

    return cases


def main():
    cases = build_corpus()
    inputs_payload = {"encoding_version": ENCODING_VERSION,
                      "problem": "integrate-ode-symplectic",
                      "cases": [c[0] for c in cases]}
    expected_payload = {"encoding_version": ENCODING_VERSION,
                         "problem": "integrate-ode-symplectic",
                         "cases": [c[1] for c in cases]}
    (HERE / "inputs.json").write_text(json.dumps(inputs_payload) + "\n")
    (HERE / "expected.json").write_text(json.dumps(expected_payload) + "\n")
    print(f"wrote {len(cases)} cases to inputs.json and expected.json")


if __name__ == "__main__":
    main()
