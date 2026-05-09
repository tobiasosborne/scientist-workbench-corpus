#!/usr/bin/env python3
"""Generate integrate-ode-stiff golden master.

Produces inputs.json + expected.json. Reproducibility: seeded numpy
generator (no random cases yet — corpus is deterministic).

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
from stiff_reference import integrate_stiff, REFERENCE_RTOL, REFERENCE_ATOL  # noqa: E402

SEED = 20260505
ENCODING_VERSION = 1


def make_input(f_str, vars, t_var, y0, t0, tf, *,
               t_eval=None, rtol=None, atol=None, max_step=None,
               method=None, jacobian=None):
    options = {}
    if t_eval is not None: options["t_eval"] = list(t_eval)
    if rtol is not None: options["rtol"] = rtol
    if atol is not None: options["atol"] = atol
    if max_step is not None: options["max_step"] = max_step
    if method is not None: options["method"] = method
    if jacobian is not None: options["jacobian"] = jacobian
    inp = {
        "f_str": list(f_str), "vars": list(vars), "t_var": t_var,
        "y0": list(y0), "t_span": {"t0": t0, "tf": tf},
    }
    if options: inp["options"] = options
    return inp


def make_success(case_id, inp, *, conservation=None, traj_tol_factor=None):
    options = inp.get("options", {})
    t_eval = options.get("t_eval", [inp["t_span"]["t0"], inp["t_span"]["tf"]])
    rtol = options.get("rtol", 1e-3)
    atol = options.get("atol", 1e-6)
    ref = integrate_stiff(inp, rtol=REFERENCE_RTOL, atol=REFERENCE_ATOL)
    if "kind" in ref:
        raise RuntimeError(f"reference produced non-success on {case_id!r}: {ref}")
    expected = {
        "trajectory": ref["trajectory"], "t_values": list(t_eval),
        "rtol": rtol, "atol": atol, "oracle_source": "scipy_radau_1e-13",
    }
    if conservation is not None: expected["conservation"] = conservation
    if traj_tol_factor is not None: expected["traj_tol_factor"] = traj_tol_factor
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

    # A_scalar_mild_decay: trivial non-stiff baseline through the stiff machinery
    t_eval = [0.0, 1.0]
    cases.append(make_success("A_scalar_mild_decay",
        make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 1.0, t_eval=t_eval)))

    # A_scalar_stiff_1000: y' = -1000 y; stable explicit step ~1/1000
    t_eval = list(np.linspace(0.0, 0.1, 6))
    cases.append(make_success("A_scalar_stiff_1000",
        make_input(["-1000 * y0"], ["y0"], "t", [1.0], 0.0, 0.1,
                   t_eval=t_eval, rtol=1e-6, atol=1e-9)))

    # A_2d_linear_stiff: eigenvalues (-1, -1000)
    t_eval = list(np.linspace(0.0, 0.5, 6))
    cases.append(make_success("A_2d_linear_stiff",
        make_input(["-y0", "-1000 * y1"], ["y0", "y1"], "t",
                   [1.0, 1.0], 0.0, 0.5, t_eval=t_eval, rtol=1e-6, atol=1e-9)))

    # ── B. Mild-stiff (3) ──────────────────────────────────────────────────

    # B_van_der_pol_mu1: mild
    t_eval = list(np.linspace(0.0, 10.0, 11))
    cases.append(make_success("B_van_der_pol_mu1",
        make_input(["y1", "1 * (1 - y0^2) * y1 - y0"], ["y0", "y1"], "t",
                   [2.0, 0.0], 0.0, 10.0, t_eval=t_eval, rtol=1e-6, atol=1e-9)))

    # B_exp_decay_long: y' = -y over [0, 1000]
    t_eval = list(np.linspace(0.0, 1000.0, 11))
    cases.append(make_success("B_exp_decay_long",
        make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 1000.0,
                   t_eval=t_eval, rtol=1e-6, atol=1e-9)))

    # B_linear_decay_2d: 2D linear stiff
    t_eval = list(np.linspace(0.0, 5.0, 6))
    cases.append(make_success("B_linear_decay_2d",
        make_input(["-y0 - y1", "y0 - 100 * y1"], ["y0", "y1"], "t",
                   [1.0, 1.0], 0.0, 5.0, t_eval=t_eval, rtol=1e-6, atol=1e-9)))

    # ── C. Stiffness sweep (van der Pol μ ∈ {100, 1000, 10000}) (3) ────────
    # Skip μ=1e6 — scipy Radau at REFERENCE_RTOL takes minutes; not worth
    # the bench-runtime cost. The 10⁴ case is enough to exercise the stiff
    # machinery.

    for mu in (100, 1000, 10000):
        t_eval = list(np.linspace(0.0, 2.0 * mu, 11))
        cases.append(make_success(f"C_vdp_mu_{mu}",
            make_input(["y1", f"{mu} * (1 - y0^2) * y1 - y0"],
                       ["y0", "y1"], "t",
                       [2.0, 0.0], 0.0, 2.0 * mu, t_eval=t_eval,
                       rtol=1e-6, atol=1e-9)))

    # ── D. NHW Vol II canonical (3) ────────────────────────────────────────
    # Trim to Robertson, HIRES, Oregonator. E5 and Pollu have heavier setup
    # and longer reference runtime; can be added in a later expansion of the
    # bench.

    # D_robertson: 3-species, very stiff over [0, 1e10] (truncated from 1e11
    # for reference-runtime budget).
    t_eval = [0.0, 1.0, 100.0, 1e4, 1e6, 1e8, 1e10]
    cases.append(make_success("D_robertson",
        make_input(
            ["-0.04 * y0 + 1e4 * y1 * y2",
             "0.04 * y0 - 1e4 * y1 * y2 - 3e7 * y1^2",
             "3e7 * y1^2"],
            ["y0", "y1", "y2"], "t",
            [1.0, 0.0, 0.0], 0.0, 1e10, t_eval=t_eval,
            rtol=1e-6, atol=1e-10),
        traj_tol_factor=10000.0,  # span 10^10 with rtol 1e-6 — relax bound
    ))

    # D_oregonator: Belousov-Zhabotinsky 3-component
    t_eval = list(np.linspace(0.0, 360.0, 11))
    cases.append(make_success("D_oregonator",
        make_input(
            ["77.27 * (y1 + y0 * (1 - 8.375e-6 * y0 - y1))",
             "(y2 - (1 + y0) * y1) / 77.27",
             "0.161 * (y0 - y2)"],
            ["y0", "y1", "y2"], "t",
            [1.0, 2.0, 3.0], 0.0, 360.0, t_eval=t_eval,
            rtol=1e-6, atol=1e-9)))

    # D_hires: Schäfer 1975 photomorphogenesis, 8 components.
    t_eval = list(np.linspace(0.0, 321.8122, 11))
    cases.append(make_success("D_hires",
        make_input(
            ["-1.71 * y0 + 0.43 * y1 + 8.32 * y2 + 0.0007",
             "1.71 * y0 - 8.75 * y1",
             "-10.03 * y2 + 0.43 * y3 + 0.035 * y4",
             "8.32 * y1 + 1.71 * y2 - 1.12 * y3",
             "-1.745 * y4 + 0.43 * y5 + 0.43 * y6",
             "-280 * y5 * y7 + 0.69 * y3 + 1.71 * y4 - 0.43 * y5 + 0.69 * y6",
             "280 * y5 * y7 - 1.81 * y6",
             "-280 * y5 * y7 + 1.81 * y6"],
            ["y0", "y1", "y2", "y3", "y4", "y5", "y6", "y7"], "t",
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0057], 0.0, 321.8122,
            t_eval=t_eval, rtol=1e-6, atol=1e-9)))

    # ── E. Boundary cases (4) ──────────────────────────────────────────────

    # E_degenerate_tspan
    cases.append(make_tagged("E_degenerate_tspan",
        make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 0.0),
        "integrate-ode-stiff/degenerate-tspan",
        {"t0": 0.0, "tf": 0.0}))

    # E_method_bdf: options.method = 'bdf' → method-not-implemented
    cases.append(make_tagged("E_method_bdf",
        make_input(["-y0"], ["y0"], "t", [1.0], 0.0, 1.0, method="bdf"),
        "integrate-ode-stiff/method-not-implemented",
        {"method": "bdf"}))

    # E_unknown_head: floor() not in admitted set
    cases.append(make_tool_error("E_unknown_head",
        make_input(["floor(y0)"], ["y0"], "t", [1.5], 0.0, 1.0),
        "UnknownVocabulary"))

    # E_dim_mismatch
    cases.append(make_tool_error("E_dim_mismatch",
        make_input(["-y0", "y0"], ["y0"], "t", [1.0], 0.0, 1.0),
        "DimensionMismatch"))

    # ── F. Tolerance discipline (3) — Robertson at three rtol regimes ─────
    # Same problem template as D_robertson, three rtol values.

    for rt, atl, tag in [(1e-3, 1e-6, "loose"),
                          (1e-6, 1e-10, "default"),
                          (1e-9, 1e-13, "tight")]:
        t_eval_f = [0.0, 1.0, 100.0, 1e4, 1e6, 1e8, 1e10]
        cases.append(make_success(f"F_robertson_{tag}",
            make_input(
                ["-0.04 * y0 + 1e4 * y1 * y2",
                 "0.04 * y0 - 1e4 * y1 * y2 - 3e7 * y1^2",
                 "3e7 * y1^2"],
                ["y0", "y1", "y2"], "t",
                [1.0, 0.0, 0.0], 0.0, 1e10, t_eval=t_eval_f,
                rtol=rt, atol=atl),
            traj_tol_factor=10000.0,
        ))

    return cases


def main():
    cases = build_corpus()
    inputs_payload = {"encoding_version": ENCODING_VERSION, "seed": SEED,
                      "problem": "integrate-ode-stiff",
                      "cases": [c[0] for c in cases]}
    expected_payload = {"encoding_version": ENCODING_VERSION, "seed": SEED,
                         "problem": "integrate-ode-stiff",
                         "cases": [c[1] for c in cases]}
    (HERE / "inputs.json").write_text(json.dumps(inputs_payload) + "\n")
    (HERE / "expected.json").write_text(json.dumps(expected_payload) + "\n")
    print(f"wrote {len(cases)} cases to inputs.json and expected.json")


if __name__ == "__main__":
    main()
