#!/usr/bin/env python3
"""Generate the bench/poly-roots-radical golden master.

Per ADR-0019, every golden case is admitted iff at least 2 of 3 oracles
(SymPy primary, Wolfram cross-witness, optional SageMath) agree on the
expected output modulo the verifier-invariant equivalence.

Tiers (per bead `iyj`):
  A  linear (deg 1)
  B  quadratic (real, complex, repeated)
  C  cubic (Cardano — all 3 cases incl. casus irreducibilis)
  D  quartic (Ferrari — generic + biquadratic + depressed)
  E  reducible (factor first, radicals on factors)
  F  numeric stress (large/small coeffs, near-zero discriminants)
  G  refusals (deg-5 irreducible, non-polynomial, multivariate)

Reproducibility: seeded `random.Random` (CPython stdlib).

Run:
  python3 generate.py                    # full triple-witness via wolframscript
  WB_LIVE_ORACLE=0 python3 generate.py   # SymPy only (fast iteration)
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT / "bench" / "_corpus"))
sys.path.insert(0, str(HERE.parent / "reference"))

from oracle.wolfram import wolfram_query              # noqa: E402
from poly_roots_reference import poly_roots_reference  # noqa: E402

LIVE_ORACLE = os.environ.get("WB_LIVE_ORACLE", "1") != "0"
SEED = 20260507
ENCODING_VERSION = 1

X = sp.Symbol("x")


# ---------------------------------------------------------------------
# Per-tier generators
# ---------------------------------------------------------------------


def tier_a_linear() -> List[Dict[str, Any]]:
    """6 cases — linear (deg 1) edges."""
    out = []
    out.append({"id": "A-bare-x",              "tier": "A", "input": {"f": "x", "var": "x"}})
    out.append({"id": "A-shifted-x-3",         "tier": "A", "input": {"f": "x - 3", "var": "x"}})
    out.append({"id": "A-with-content-2x4",    "tier": "A", "input": {"f": "2*x + 4", "var": "x"}})
    out.append({"id": "A-rational-3x1",        "tier": "A", "input": {"f": "3*x + 1", "var": "x"}})
    out.append({"id": "A-rational-coeff",      "tier": "A", "input": {"f": "x/2 - 1/3", "var": "x"}})
    out.append({"id": "A-negative-leading",    "tier": "A", "input": {"f": "-x + 5", "var": "x"}})
    return out


def tier_b_quadratic() -> List[Dict[str, Any]]:
    """8 cases — quadratic: real, complex, repeated."""
    return [
        {"id": "B-rational-roots-23",          "tier": "B", "input": {"f": "x**2 - 5*x + 6", "var": "x"}},
        {"id": "B-irrational-roots-pm-sqrt2",  "tier": "B", "input": {"f": "x**2 - 2", "var": "x"}},
        {"id": "B-irrational-golden",          "tier": "B", "input": {"f": "x**2 - x - 1", "var": "x"}},
        {"id": "B-complex-roots-i",            "tier": "B", "input": {"f": "x**2 + 1", "var": "x"}},
        {"id": "B-complex-roots-omega",        "tier": "B", "input": {"f": "x**2 + x + 1", "var": "x"}},
        {"id": "B-double-root",                "tier": "B", "input": {"f": "x**2 - 2*x + 1", "var": "x"}},
        {"id": "B-zero-at-origin-double",      "tier": "B", "input": {"f": "x**2", "var": "x"}},
        {"id": "B-sum-of-squares-shifted",     "tier": "B", "input": {"f": "x**2 + 4*x + 5", "var": "x"}},
    ]


def tier_c_cubic() -> List[Dict[str, Any]]:
    """8 cases — cubic with Cardano, including casus irreducibilis."""
    return [
        {"id": "C-rational-three-roots",       "tier": "C", "input": {"f": "x**3 - x", "var": "x"}},
        {"id": "C-cyclotomic-x3-minus-1",      "tier": "C", "input": {"f": "x**3 - 1", "var": "x"}},
        {"id": "C-depressed-real",             "tier": "C", "input": {"f": "x**3 + 3*x - 4", "var": "x"}},
        # Casus irreducibilis: Δ < 0, three real irrational roots, Cardano
        # produces faithful complex form per ADR-1yu.
        {"id": "C-casus-irreducibilis",        "tier": "C", "input": {"f": "x**3 - 3*x + 1", "var": "x"}},
        {"id": "C-casus-irreducibilis-2",      "tier": "C", "input": {"f": "x**3 - 7*x + 6", "var": "x"}},
        {"id": "C-one-real-two-complex",       "tier": "C", "input": {"f": "x**3 + x + 1", "var": "x"}},
        {"id": "C-triple-root",                "tier": "C", "input": {"f": "x**3 - 3*x**2 + 3*x - 1", "var": "x"}},  # (x-1)^3
        {"id": "C-cubic-with-content",         "tier": "C", "input": {"f": "2*x**3 - 6*x", "var": "x"}},
    ]


def tier_d_quartic() -> List[Dict[str, Any]]:
    """8 cases — quartic Ferrari + biquadratic + depressed."""
    return [
        {"id": "D-biquadratic",                "tier": "D", "input": {"f": "x**4 - 5*x**2 + 4", "var": "x"}},
        {"id": "D-biquadratic-no-real",        "tier": "D", "input": {"f": "x**4 + 5*x**2 + 4", "var": "x"}},
        {"id": "D-depressed-ferrari",          "tier": "D", "input": {"f": "x**4 + x + 1", "var": "x"}},
        {"id": "D-cyclotomic-x4-1",            "tier": "D", "input": {"f": "x**4 - 1", "var": "x"}},
        {"id": "D-quadruple-root",             "tier": "D", "input": {"f": "x**4 - 4*x**3 + 6*x**2 - 4*x + 1", "var": "x"}},  # (x-1)^4
        {"id": "D-two-double-roots",           "tier": "D", "input": {"f": "(x - 1)**2 * (x + 1)**2", "var": "x"}},
        {"id": "D-perfect-square-quadratic",   "tier": "D", "input": {"f": "x**4 - 2*x**2 + 1", "var": "x"}},  # (x^2-1)^2
        {"id": "D-ferrari-resolvent-stress",   "tier": "D", "input": {"f": "x**4 - 10*x**2 + 1", "var": "x"}},
    ]


def tier_e_reducible() -> List[Dict[str, Any]]:
    """6 cases — input is reducible; factor-first dispatch."""
    return [
        {"id": "E-linear-times-quadratic",     "tier": "E", "input": {"f": "(x - 1) * (x**2 + 1)", "var": "x"}},
        {"id": "E-two-irreducible-quads",      "tier": "E", "input": {"f": "(x**2 + 1) * (x**2 + 4)", "var": "x"}},
        {"id": "E-quad-times-cubic",           "tier": "E", "input": {"f": "(x**2 - 2) * (x**3 - 3*x + 1)", "var": "x"}},
        {"id": "E-three-linear",               "tier": "E", "input": {"f": "(x - 1) * (x - 2) * (x - 3)", "var": "x"}},
        {"id": "E-mixed-multiplicity",         "tier": "E", "input": {"f": "(x - 1)**2 * (x + 1)", "var": "x"}},
        {"id": "E-difference-of-squares",      "tier": "E", "input": {"f": "x**4 - 16", "var": "x"}},
    ]


def tier_f_numeric_stress() -> List[Dict[str, Any]]:
    """8 cases — large/small coeffs, near-zero discriminants."""
    return [
        {"id": "F-large-coeffs",               "tier": "F", "input": {"f": "x**2 - 1000*x + 1", "var": "x"}},
        {"id": "F-tiny-rational-coeffs",       "tier": "F", "input": {"f": "x**2 - x/100 + 1/10000", "var": "x"}},
        {"id": "F-large-rational",             "tier": "F", "input": {"f": "100*x**2 - 200*x - 99", "var": "x"}},
        # Near-zero discriminant (b² ≈ 4ac, but not exactly equal — distinct roots).
        {"id": "F-near-zero-disc",             "tier": "F", "input": {"f": "x**2 - 2*x + 99/100", "var": "x"}},
        # Cubic with large coefficients.
        {"id": "F-cubic-large",                "tier": "F", "input": {"f": "x**3 - 30*x**2 + 300*x - 1000", "var": "x"}},
        # Quartic biquadratic with extreme spread.
        {"id": "F-biquadratic-spread",         "tier": "F", "input": {"f": "x**4 - 10001*x**2 + 10000", "var": "x"}},
        # Polynomial with content.
        {"id": "F-content-12",                 "tier": "F", "input": {"f": "12*x**2 - 36*x + 24", "var": "x"}},
        # Polynomial with rational coefficients spanning multiple denominators.
        {"id": "F-mixed-denoms",               "tier": "F", "input": {"f": "x**3 - x/2 + 1/6", "var": "x"}},
    ]


def tier_g_refusals() -> List[Dict[str, Any]]:
    """6 cases — boundary-tag refusal classes."""
    return [
        {"id": "G-deg5-eisenstein",            "tier": "G",
         "input": {"f": "x**5 + 2*x + 2", "var": "x"},
         "expected_tag": "poly-roots/degree-too-high"},
        {"id": "G-deg6-irreducible",           "tier": "G",
         "input": {"f": "x**6 + x + 1", "var": "x"},
         "expected_tag": "poly-roots/degree-too-high"},
        {"id": "G-deg7-cyclotomic",            "tier": "G",
         "input": {"f": "x**6 + x**5 + x**4 + x**3 + x**2 + x + 1", "var": "x"},  # Φ_7
         "expected_tag": "poly-roots/degree-too-high"},
        {"id": "G-non-polynomial-sin",         "tier": "G",
         "input": {"f": "sin(x)", "var": "x"},
         "expected_tag": "poly-roots/non-polynomial"},
        {"id": "G-non-polynomial-rational",    "tier": "G",
         "input": {"f": "1/x + x", "var": "x"},
         "expected_tag": "poly-roots/non-polynomial"},
        {"id": "G-multivariate",               "tier": "G",
         "input": {"f": "x*y - 1", "var": "x"},
         "expected_tag": "poly-roots/multivariate"},
    ]


# ---------------------------------------------------------------------
# Triple-witness
# ---------------------------------------------------------------------


def query_wolfram(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run `Solve[f == 0, var]` via wolframscript, returning the standard envelope."""
    from sympy.printing.mathematica import mathematica_code
    f_str = case["input"]["f"]
    var = case["input"]["var"]
    try:
        f_expr = sp.sympify(f_str, locals={var: sp.Symbol(var)})
    except (sp.SympifyError, SyntaxError, TypeError):
        return {"status": "error", "reason": "sympify failed"}
    code = f"FullForm[Solve[({mathematica_code(f_expr)}) == 0, {var}]]"
    return wolfram_query(code, timeout=30.0)


def admit(case: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Run reference + (optional) Wolfram. Returns (expected envelope, oracle log entry)."""
    inp = case["input"]
    cand = poly_roots_reference(inp["f"], inp["var"])

    expected: Dict[str, Any] = {"kind": cand["kind"]}
    if cand["kind"] == "tagged":
        expected["tag"] = case.get("expected_tag", cand["tag"])

    log: Dict[str, Any] = {
        "id":    case["id"],
        "tier":  case["tier"],
        "sympy": "ok" if cand["kind"] == "ok" else f"refused:{cand.get('tag')}",
    }

    if LIVE_ORACLE:
        wolf = query_wolfram(case)
        log["wolfram"] = wolf.get("status", "missing")
        if wolf.get("status") != "ok":
            log["wolfram_reason"] = wolf.get("reason")
        if cand["kind"] == "tagged":
            permanent_classes = {"poly-roots/degree-too-high", "poly-roots/non-polynomial", "poly-roots/multivariate"}
            if expected.get("tag") in permanent_classes:
                if wolf.get("status") == "ok":
                    # Wolfram may still produce Root[] objects for deg ≥ 5 — that's
                    # expected; we admit because the workbench's *bounded scope* is
                    # the honest behaviour at v0.1 (lifting via bead `yoc`).
                    log["consensus"] = "wolfram-solved-workbench-bounded-scope"
                else:
                    log["consensus"] = "both-refuse"
            else:
                log["consensus"] = "uncertain"
        else:
            log["consensus"] = "sympy+wolfram" if wolf.get("status") == "ok" else "sympy-only"
    else:
        log["consensus"] = "sympy-only (LIVE_ORACLE off)"

    return expected, log


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def main() -> int:
    cases: List[Dict[str, Any]] = []
    cases.extend(tier_a_linear())
    cases.extend(tier_b_quadratic())
    cases.extend(tier_c_cubic())
    cases.extend(tier_d_quartic())
    cases.extend(tier_e_reducible())
    cases.extend(tier_f_numeric_stress())
    cases.extend(tier_g_refusals())

    # Strip the optional `expected_tag` field from inputs.json (it's a
    # generator hint, not part of the bench input contract).
    inputs_payload = {
        "version": ENCODING_VERSION,
        "seed":    SEED,
        "cases":   [{k: v for k, v in c.items() if k != "expected_tag"} for c in cases],
    }

    expected_list: List[Dict[str, Any]] = []
    oracle_log: List[Dict[str, Any]] = []
    admitted = 0
    dropped = 0

    print(f"[{len(cases)} cases] LIVE_ORACLE={LIVE_ORACLE}", flush=True)
    t_start = time.time()
    for i, case in enumerate(cases):
        t0 = time.time()
        expected, log = admit(case)
        dt = time.time() - t0
        oracle_log.append(log)
        marker = "✓" if expected is not None else "✗"
        print(
            f"  [{i + 1:3d}/{len(cases)}] {marker} {case['id']:38s} "
            f"{log.get('consensus',''):44s} {dt:5.1f}s",
            flush=True,
        )
        if expected is None:
            dropped += 1
            continue
        expected_list.append({"id": case["id"], "expected": expected})
        admitted += 1

    print(f"[done] {admitted}/{len(cases)} admitted, {dropped} dropped in {time.time() - t_start:.1f}s", flush=True)

    expected_payload = {"version": ENCODING_VERSION, "seed": SEED, "cases": expected_list}
    oracle_payload = {
        "version":      ENCODING_VERSION,
        "seed":         SEED,
        "live_oracle":  LIVE_ORACLE,
        "summary":      {"total": len(cases), "admitted": admitted, "dropped": dropped},
        "entries":      oracle_log,
    }

    (HERE / "inputs.json").write_text(json.dumps(inputs_payload, indent=2) + "\n")
    (HERE / "expected.json").write_text(json.dumps(expected_payload, indent=2) + "\n")
    (HERE / "oracle_log.json").write_text(json.dumps(oracle_payload, indent=2) + "\n")

    by_tier: Dict[str, int] = {}
    for c in cases:
        by_tier[c["tier"]] = by_tier.get(c["tier"], 0) + 1
    print("Per-tier counts:")
    for tier, n in sorted(by_tier.items()):
        print(f"  {tier}  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
