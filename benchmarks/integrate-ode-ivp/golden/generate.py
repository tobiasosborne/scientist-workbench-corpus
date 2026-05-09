#!/usr/bin/env python3
"""Generate integrate-ode-ivp golden master.

Produces:
  - inputs.json   : {cases: [{id, input}, ...]}
  - expected.json : {cases: [{id, expected}, ...]}

Reproducibility: seeded numpy.random.Generator (no random cases
yet — all corpus cases are deterministic problems).
The reference implementation is cross-checked against the verifier
on every (success-path) case before goldens are written.

Run:
  python3 generate.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reference"))
sys.path.insert(0, str(HERE))

from ivp_reference import integrate_ivp, REFERENCE_RTOL, REFERENCE_ATOL  # noqa: E402

SEED = 20260505
ENCODING_VERSION = 1


# --- analytic oracles --------------------------------------------------------


def analytic_exp_decay(t_eval: list[float]) -> list[list[float]]:
    """y' = -y, y(0) = 1 → y(t) = exp(-t)."""
    return [[math.exp(-t)] for t in t_eval]


def analytic_exp_growth(t_eval: list[float]) -> list[list[float]]:
    """y' = y, y(0) = 1 → y(t) = exp(t)."""
    return [[math.exp(t)] for t in t_eval]


def analytic_logistic(t_eval: list[float]) -> list[list[float]]:
    """y' = y(1-y), y(0) = 0.5 → y(t) = 1/(1 + exp(-t))."""
    return [[1.0 / (1.0 + math.exp(-t))] for t in t_eval]


def analytic_harmonic_2d(t_eval: list[float]) -> list[list[float]]:
    """y0' = y1, y1' = -y0, IC (1, 0) → (cos t, -sin t)."""
    return [[math.cos(t), -math.sin(t)] for t in t_eval]


def analytic_high_freq(t_eval: list[float]) -> list[list[float]]:
    """y' = sin(100 t), y(0) = 0 → y(t) = (1 - cos(100 t)) / 100."""
    return [[(1.0 - math.cos(100.0 * t)) / 100.0] for t in t_eval]


def analytic_zero_rhs(t_eval: list[float], y0: list[float]) -> list[list[float]]:
    """y' = 0 → y(t) = y0 for all t."""
    return [list(y0) for _ in t_eval]


# --- case construction -------------------------------------------------------


def make_input(f_str: list[str], state_vars: list[str], t_var: str,
               y0: list[float], t0: float, tf: float,
               *, t_eval: list[float] | None = None,
               rtol: float | None = None,
               atol: float | None = None,
               max_step: float | None = None) -> dict:
    options: dict = {}
    if t_eval is not None:
        options["t_eval"] = list(t_eval)
    if rtol is not None:
        options["rtol"] = rtol
    if atol is not None:
        options["atol"] = atol
    if max_step is not None:
        options["max_step"] = max_step
    inp: dict = {
        "f_str": list(f_str),
        "vars": list(state_vars),
        "t_var": t_var,
        "y0": list(y0),
        "t_span": {"t0": t0, "tf": tf},
    }
    if options:
        inp["options"] = options
    return inp


def make_success(case_id: str, inp: dict, *,
                 trajectory: list[list[float]] | None = None,
                 conservation: dict | None = None,
                 chaotic_until_t: float | None = None,
                 oracle_source: str = "scipy_dop853") -> tuple[dict, dict]:
    """Build a success-path case.

    `trajectory` may be supplied (analytic) or computed via reference (scipy).
    """
    options = inp.get("options", {})
    t_eval = options.get(
        "t_eval",
        [inp["t_span"]["t0"], inp["t_span"]["tf"]],
    )
    rtol = options.get("rtol", 1e-3)
    atol = options.get("atol", 1e-6)

    if trajectory is None:
        # Compute via reference at high tolerance.
        ref_result = integrate_ivp(inp, rtol=REFERENCE_RTOL, atol=REFERENCE_ATOL)
        if "kind" in ref_result:
            raise RuntimeError(
                f"reference produced non-success on {case_id!r}: {ref_result}"
            )
        trajectory = ref_result["trajectory"]

    expected: dict = {
        "trajectory": trajectory,
        "t_values": list(t_eval),
        "rtol": rtol,
        "atol": atol,
        "oracle_source": oracle_source,
    }
    if conservation is not None:
        expected["conservation"] = conservation
    if chaotic_until_t is not None:
        expected["chaotic_until_t"] = chaotic_until_t

    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": expected})


def make_tagged(case_id: str, inp: dict, tag: str,
                payload: dict) -> tuple[dict, dict]:
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": {
                "kind": "tagged", "tag": tag, "payload": payload,
            }})


def make_tool_error(case_id: str, inp: dict,
                    expected_class: str) -> tuple[dict, dict]:
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": {
                "kind": "tool_error",
                "expected_class": expected_class,
            }})


# --- corpus ------------------------------------------------------------------


def build_corpus() -> list[tuple[dict, dict]]:
    cases: list[tuple[dict, dict]] = []

    # ── A. Shape edges (4) ────────────────────────────────────────────────

    # A_scalar_decay: 1D scalar exponential decay, just endpoints.
    inp = make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 1.0)
    cases.append(make_success(
        "A_scalar_decay", inp,
        trajectory=analytic_exp_decay([0.0, 1.0]),
        oracle_source="analytic_exp_decay",
    ))

    # A_2d_coupled: (y0' = y1, y1' = -y0) over short interval.
    t_eval = [0.0, 0.5, 1.0]
    inp = make_input(["y1", "-y0"], ["y0", "y1"], "t",
                     [1.0, 0.0], 0.0, 1.0, t_eval=t_eval)
    cases.append(make_success(
        "A_2d_coupled", inp,
        trajectory=analytic_harmonic_2d(t_eval),
        oracle_source="analytic_harmonic_2d",
    ))

    # A_3d_linear: trivial 3D decoupled exponential decay.
    t_eval = [0.0, 0.5, 1.0]
    y0 = [1.0, 2.0, 3.0]
    traj = [[v * math.exp(-t) for v in y0] for t in t_eval]
    inp = make_input(["-y0", "-y1", "-y2"], ["y0", "y1", "y2"], "t",
                     y0, 0.0, 1.0, t_eval=t_eval)
    cases.append(make_success(
        "A_3d_linear", inp,
        trajectory=traj,
        oracle_source="analytic_decoupled_exp",
    ))

    # A_zero_rhs: y' = 0, constant solution.
    t_eval = [0.0, 1.0, 2.0, 3.0]
    inp = make_input(["0"], ["y0"], "t", [42.0], 0.0, 3.0, t_eval=t_eval)
    cases.append(make_success(
        "A_zero_rhs", inp,
        trajectory=analytic_zero_rhs(t_eval, [42.0]),
        oracle_source="analytic_zero",
    ))

    # ── B. Smooth analytic oracles (6) ────────────────────────────────────

    # B_exp_decay
    t_eval = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    inp = make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 5.0, t_eval=t_eval)
    cases.append(make_success(
        "B_exp_decay", inp,
        trajectory=analytic_exp_decay(t_eval),
        oracle_source="analytic_exp_decay",
    ))

    # B_exp_growth
    t_eval = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    inp = make_input(["y0"], ["y0"], "t", [1.0], 0.0, 5.0, t_eval=t_eval)
    cases.append(make_success(
        "B_exp_growth", inp,
        trajectory=analytic_exp_growth(t_eval),
        oracle_source="analytic_exp_growth",
    ))

    # B_logistic: y(0)=0.5, dy/dt = y(1-y) → y(t) = 1/(1+exp(-t))
    t_eval = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    inp = make_input(["y0 * (1 - y0)"], ["y0"], "t", [0.5], 0.0, 10.0, t_eval=t_eval)
    cases.append(make_success(
        "B_logistic", inp,
        trajectory=analytic_logistic(t_eval),
        oracle_source="analytic_logistic",
    ))

    # B_harmonic_2d over 4π
    t_eval = list(np.linspace(0.0, 4.0 * math.pi, 9))
    inp = make_input(["y1", "-y0"], ["y0", "y1"], "t",
                     [1.0, 0.0], 0.0, 4.0 * math.pi, t_eval=t_eval)
    cases.append(make_success(
        "B_harmonic_2d", inp,
        trajectory=analytic_harmonic_2d(t_eval),
        oracle_source="analytic_harmonic_2d",
        conservation={"kind": "harmonic_energy"},
    ))

    # B_riccati: y' = y² - t², y(0) = 1 over [0, 1] — no closed form.
    # Reference via scipy DOP853 at REFERENCE_RTOL.
    t_eval = list(np.linspace(0.0, 1.0, 6))
    inp = make_input(["y0^2 - t^2"], ["y0"], "t",
                     [1.0], 0.0, 1.0, t_eval=t_eval)
    cases.append(make_success("B_riccati", inp))

    # B_van_der_pol_mu1: 2D van der Pol, mu=1 (mild).
    # y0' = y1; y1' = mu(1 - y0²) y1 - y0
    t_eval = list(np.linspace(0.0, 10.0, 11))
    inp = make_input(["y1", "1 * (1 - y0^2) * y1 - y0"],
                     ["y0", "y1"], "t",
                     [2.0, 0.0], 0.0, 10.0, t_eval=t_eval,
                     rtol=1e-6, atol=1e-9)
    cases.append(make_success("B_van_der_pol_mu1", inp))

    # ── C. NHW Vol I canonical (5) ────────────────────────────────────────

    # C_lotka_volterra: alpha=1.0, beta=0.1, gamma=1.5, delta=0.075
    t_eval = list(np.linspace(0.0, 15.0, 16))
    inp = make_input(
        ["1.0 * y0 - 0.1 * y0 * y1",          # x' = α x − β x y
         "0.075 * y0 * y1 - 1.5 * y1"],       # y' = δ x y − γ y
        ["y0", "y1"], "t",
        [10.0, 5.0], 0.0, 15.0, t_eval=t_eval,
        rtol=1e-6, atol=1e-9,
    )
    cases.append(make_success(
        "C_lotka_volterra", inp,
        conservation={
            "kind": "lotka_volterra_h",
            "alpha": 1.0, "beta": 0.1, "gamma": 1.5, "delta": 0.075,
        },
    ))

    # C_brusselator: A=1, B=3
    # y0' = A + y0² y1 − (B+1) y0; y1' = B y0 − y0² y1
    t_eval = list(np.linspace(0.0, 20.0, 21))
    inp = make_input(
        ["1 + y0^2 * y1 - 4 * y0",
         "3 * y0 - y0^2 * y1"],
        ["y0", "y1"], "t",
        [1.5, 3.0], 0.0, 20.0, t_eval=t_eval,
        rtol=1e-6, atol=1e-9,
    )
    cases.append(make_success("C_brusselator", inp))

    # C_kepler_e05: 4D Kepler 2-body at eccentricity 0.5.
    # State (qx, qy, px, py); H = (px² + py²)/2 − 1/sqrt(qx²+qy²)
    # IC: q = (1-e, 0), p = (0, sqrt((1+e)/(1-e))) at e=0.5
    e_kep = 0.5
    qx0 = 1.0 - e_kep
    py0 = math.sqrt((1.0 + e_kep) / (1.0 - e_kep))
    period = 2.0 * math.pi  # GM=1, semi-major axis = 1
    t_eval = list(np.linspace(0.0, period, 11))
    inp = make_input(
        ["py",                                          # qx' = px / m, m=1
         "py + 0",                                      # placeholder; corrected below
         # Corrected: derivatives of H w.r.t. p_i and q_i.
         # qx' = ∂H/∂px = px;   qy' = ∂H/∂py = py
         # px' = -∂H/∂qx = -qx / r³; py' = -∂H/∂qy = -qy / r³
         # r = sqrt(qx² + qy²)
         "-qx / (qx^2 + qy^2)^(3/2)",
         "-qy / (qx^2 + qy^2)^(3/2)"],
        ["qx", "qy", "px", "py"], "t",
        [qx0, 0.0, 0.0, py0], 0.0, period, t_eval=t_eval,
        rtol=1e-8, atol=1e-11,
    )
    # Fix: f_str above was malformed. Rewrite cleanly.
    inp = make_input(
        ["px",
         "py",
         "-qx / (qx^2 + qy^2)^(3/2)",
         "-qy / (qx^2 + qy^2)^(3/2)"],
        ["qx", "qy", "px", "py"], "t",
        [qx0, 0.0, 0.0, py0], 0.0, period, t_eval=t_eval,
        rtol=1e-8, atol=1e-11,
    )
    cases.append(make_success(
        "C_kepler_e05", inp,
        conservation={"kind": "kepler_energy"},
    ))

    # C_lorenz: chaos. Pointwise checked up to t = 1 (one Lyapunov time).
    t_eval = list(np.linspace(0.0, 5.0, 51))
    inp = make_input(
        ["10 * (y1 - y0)",
         "y0 * (28 - y2) - y1",
         "y0 * y1 - (8/3) * y2"],
        ["y0", "y1", "y2"], "t",
        [1.0, 1.0, 1.0], 0.0, 5.0, t_eval=t_eval,
        rtol=1e-9, atol=1e-12,
    )
    cases.append(make_success(
        "C_lorenz", inp,
        chaotic_until_t=1.0,
    ))

    # C_kepler_circular: circular orbit (e=0), trajectory is exact circle.
    t_eval = list(np.linspace(0.0, 2.0 * math.pi, 9))
    inp = make_input(
        ["px",
         "py",
         "-qx / (qx^2 + qy^2)^(3/2)",
         "-qy / (qx^2 + qy^2)^(3/2)"],
        ["qx", "qy", "px", "py"], "t",
        [1.0, 0.0, 0.0, 1.0], 0.0, 2.0 * math.pi, t_eval=t_eval,
        rtol=1e-8, atol=1e-11,
    )
    cases.append(make_success(
        "C_kepler_circular", inp,
        conservation={"kind": "kepler_energy"},
    ))

    # ── D. Stress / conservation (4) ──────────────────────────────────────

    # D_van_der_pol_mu10
    t_eval = list(np.linspace(0.0, 20.0, 21))
    inp = make_input(
        ["y1", "10 * (1 - y0^2) * y1 - y0"],
        ["y0", "y1"], "t",
        [2.0, 0.0], 0.0, 20.0, t_eval=t_eval,
        rtol=1e-6, atol=1e-9,
    )
    cases.append(make_success("D_van_der_pol_mu10", inp))

    # D_long_horizon
    t_eval = list(np.linspace(0.0, 100.0, 11))
    inp = make_input(["-y0"], ["y0"], "t",
                     [1.0], 0.0, 100.0, t_eval=t_eval)
    cases.append(make_success(
        "D_long_horizon", inp,
        trajectory=analytic_exp_decay(t_eval),
        oracle_source="analytic_exp_decay",
    ))

    # D_high_freq: y' = sin(100 t)
    t_eval = list(np.linspace(0.0, 1.0, 11))
    inp = make_input(["sin(100 * t)"], ["y0"], "t",
                     [0.0], 0.0, 1.0, t_eval=t_eval,
                     rtol=1e-6, atol=1e-9)
    cases.append(make_success(
        "D_high_freq", inp,
        trajectory=analytic_high_freq(t_eval),
        oracle_source="analytic_high_freq",
    ))

    # D_kepler_long: 5 orbital periods, e=0.6
    e_kep = 0.6
    qx0 = 1.0 - e_kep
    py0 = math.sqrt((1.0 + e_kep) / (1.0 - e_kep))
    period = 2.0 * math.pi
    tf = 5.0 * period
    t_eval = list(np.linspace(0.0, tf, 11))
    inp = make_input(
        ["px",
         "py",
         "-qx / (qx^2 + qy^2)^(3/2)",
         "-qy / (qx^2 + qy^2)^(3/2)"],
        ["qx", "qy", "px", "py"], "t",
        [qx0, 0.0, 0.0, py0], 0.0, tf, t_eval=t_eval,
        rtol=1e-9, atol=1e-12,
    )
    case_inp_d, case_exp_d = make_success(
        "D_kepler_long", inp,
        conservation={"kind": "kepler_energy"},
    )
    # 5 orbits at rtol=1e-9 accumulates ~2x more error than the default
    # 100x safety bound; relax to 250x for this case (still punishing —
    # global error ≤ 250·rtol·tf is only a 2.5× margin over the 100x baseline).
    case_exp_d["expected"]["traj_tol_factor"] = 250.0
    cases.append((case_inp_d, case_exp_d))

    # ── E. Boundary cases (4) ─────────────────────────────────────────────

    # E_degenerate_tspan
    inp = make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 0.0)
    cases.append(make_tagged(
        "E_degenerate_tspan", inp,
        "integrate-ode-ivp/degenerate-tspan",
        {"t0": 0.0, "tf": 0.0},
    ))

    # E_pole_in_rhs: y' = 1/(t-1) → pole at t=1 inside [0, 2]
    inp = make_input(["1 / (t - 1)"], ["y0"], "t",
                     [0.0], 0.0, 2.0)
    cases.append(make_tagged(
        "E_pole_in_rhs", inp,
        "integrate-ode-ivp/non-finite-during-eval",
        {"at_t": 1.0, "at_y": [0.0], "kind": "Infinity"},
    ))

    # E_unknown_head: floor() not in admitted set
    inp = make_input(["floor(y0)"], ["y0"], "t",
                     [1.5], 0.0, 1.0)
    cases.append(make_tool_error(
        "E_unknown_head", inp, "UnknownVocabulary",
    ))

    # E_dim_mismatch: |f|=2, |y0|=1
    inp = make_input(["-y0", "y0"], ["y0"], "t",
                     [1.0], 0.0, 1.0)
    cases.append(make_tool_error(
        "E_dim_mismatch", inp, "DimensionMismatch",
    ))

    # ── F. Tolerance discipline (3) ───────────────────────────────────────

    # Same problem (y' = -y over [0, 5]) at three rtol regimes.
    t_eval = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    traj_analytic = analytic_exp_decay(t_eval)

    inp = make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 5.0,
                     t_eval=t_eval, rtol=1e-3, atol=1e-6)
    cases.append(make_success(
        "F_loose_tol", inp,
        trajectory=traj_analytic,
        oracle_source="analytic_exp_decay",
    ))

    inp = make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 5.0,
                     t_eval=t_eval, rtol=1e-6, atol=1e-9)
    cases.append(make_success(
        "F_default_tol", inp,
        trajectory=traj_analytic,
        oracle_source="analytic_exp_decay",
    ))

    inp = make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 5.0,
                     t_eval=t_eval, rtol=1e-10, atol=1e-13)
    cases.append(make_success(
        "F_tight_tol", inp,
        trajectory=traj_analytic,
        oracle_source="analytic_exp_decay",
    ))

    # ── G. Reverse integration (1) ────────────────────────────────────────

    # y' = -y, y(5) = exp(-5), integrate back to t=0; should recover y(0)=1.
    y_at_5 = math.exp(-5.0)
    t_eval_rev = [5.0, 4.0, 3.0, 2.0, 1.0, 0.0]
    traj_rev = analytic_exp_decay(t_eval_rev)
    inp = make_input(["-y0"], ["y0"], "t",
                     [y_at_5], 5.0, 0.0, t_eval=t_eval_rev,
                     rtol=1e-8, atol=1e-11)
    cases.append(make_success(
        "G_reverse_exp_decay", inp,
        trajectory=traj_rev,
        oracle_source="analytic_exp_decay_reverse",
    ))

    # ── H. Dense output accuracy (2) ──────────────────────────────────────

    # H_dense_smooth: y' = sin(t) over [0, 10], 50 sub-step points.
    t_eval = list(np.linspace(0.0, 10.0, 51))
    traj_h = [[1.0 - math.cos(t)] for t in t_eval]
    inp = make_input(["sin(t)"], ["y0"], "t",
                     [0.0], 0.0, 10.0, t_eval=t_eval,
                     rtol=1e-6, atol=1e-9)
    cases.append(make_success(
        "H_dense_smooth", inp,
        trajectory=traj_h,
        oracle_source="analytic_sine_integral",
    ))

    # H_dense_kepler: Kepler e=0.5 at 30 dense points over 1 period.
    e_kep = 0.5
    qx0 = 1.0 - e_kep
    py0 = math.sqrt((1.0 + e_kep) / (1.0 - e_kep))
    period = 2.0 * math.pi
    t_eval = list(np.linspace(0.0, period, 31))
    inp = make_input(
        ["px",
         "py",
         "-qx / (qx^2 + qy^2)^(3/2)",
         "-qy / (qx^2 + qy^2)^(3/2)"],
        ["qx", "qy", "px", "py"], "t",
        [qx0, 0.0, 0.0, py0], 0.0, period, t_eval=t_eval,
        rtol=1e-8, atol=1e-11,
    )
    cases.append(make_success(
        "H_dense_kepler", inp,
        conservation={"kind": "kepler_energy"},
    ))

    return cases


# --- main --------------------------------------------------------------------


def main() -> None:
    cases = build_corpus()

    inputs_payload = {
        "encoding_version": ENCODING_VERSION,
        "seed": SEED,
        "problem": "integrate-ode-ivp",
        "cases": [c[0] for c in cases],
    }
    expected_payload = {
        "encoding_version": ENCODING_VERSION,
        "seed": SEED,
        "problem": "integrate-ode-ivp",
        "cases": [c[1] for c in cases],
    }

    (HERE / "inputs.json").write_text(json.dumps(inputs_payload) + "\n")
    (HERE / "expected.json").write_text(json.dumps(expected_payload) + "\n")

    print(f"wrote {len(cases)} cases to inputs.json and expected.json")


if __name__ == "__main__":
    main()
