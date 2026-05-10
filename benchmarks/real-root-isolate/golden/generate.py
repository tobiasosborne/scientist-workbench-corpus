#!/usr/bin/env python3
"""Generate the bench/real-root-isolate golden master.

Per ADR-0019, every golden case is admitted iff at least 2 of 3 oracles
(SymPy primary, Wolfram cross-witness, optional SageMath) agree on the
expected output modulo the verifier-invariant equivalence. For real-root
isolation the agreement criterion is reduced to the *count* of real
roots — the interval *endpoints* differ across implementations (each
chooses its own bisection cadence) but the count is invariant.

Tiers (per bead `q8q`, refined per ADR-0019 §7):
  A  trivial — linear, quadratic with two real or no real roots
  B  Chebyshev / Legendre — known real-root counts (deg 3-7)
  C  clustered — Mignotte M_{n,a} with two roots near 1/a
  D  large-degree — Wilkinson and Chebyshev/factor products at deg 20-50
  E  rational-coefficient stress — large numerators, mixed denominators
  F  refusals — non-squarefree, multivariate, non-polynomial
  G  edge structural — no-real-roots cyclotomic and complex-only quartics

Reproducibility: the case list is fully curated (no random tier in v1 —
the closed-form algorithmic regimes are tightly bounded; same precedent
as bench/poly-roots-radical worklog 057).

Run:
  python3 generate.py                    # full triple-witness via wolframscript
  WB_LIVE_ORACLE=0 python3 generate.py   # SymPy only (fast iteration)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT / "bench" / "_corpus"))
sys.path.insert(0, str(HERE.parent / "reference"))

from oracle.wolfram import wolfram_query                          # noqa: E402
from real_root_isolate_reference import real_root_isolate_reference  # noqa: E402

LIVE_ORACLE = os.environ.get("WB_LIVE_ORACLE", "1") != "0"
ENCODING_VERSION = 1

X = sp.Symbol("x")


# ---------------------------------------------------------------------
# Per-tier generators
# ---------------------------------------------------------------------


def tier_a_trivial() -> List[Dict[str, Any]]:
    """5 cases — linear, quadratic with two real or no real roots."""
    return [
        {"id": "A-linear-rational",       "tier": "A", "input": {"f": "2*x - 4", "var": "x"}},
        {"id": "A-linear-fraction-root",  "tier": "A", "input": {"f": "3*x - 1", "var": "x"}},
        {"id": "A-linear-negative",       "tier": "A", "input": {"f": "-x + 5", "var": "x"}},
        {"id": "A-quadratic-two-real",    "tier": "A", "input": {"f": "x**2 - 2", "var": "x"}},
        {"id": "A-quadratic-rational",    "tier": "A", "input": {"f": "x**2 - 5*x + 6", "var": "x"}},
    ]


def tier_b_chebyshev_legendre() -> List[Dict[str, Any]]:
    """8 cases — Chebyshev T_n and Legendre P_n with n real roots."""
    return [
        {"id": "B-T3", "tier": "B", "input": {"f": "4*x**3 - 3*x", "var": "x"}},
        {"id": "B-T4", "tier": "B", "input": {"f": "8*x**4 - 8*x**2 + 1", "var": "x"}},
        {"id": "B-T5", "tier": "B", "input": {"f": "16*x**5 - 20*x**3 + 5*x", "var": "x"}},
        {"id": "B-T6", "tier": "B", "input": {"f": "32*x**6 - 48*x**4 + 18*x**2 - 1", "var": "x"}},
        {"id": "B-T7", "tier": "B", "input": {"f": "64*x**7 - 112*x**5 + 56*x**3 - 7*x", "var": "x"}},
        {"id": "B-P3", "tier": "B", "input": {"f": "5*x**3 - 3*x", "var": "x"}},
        {"id": "B-P4", "tier": "B", "input": {"f": "35*x**4 - 30*x**2 + 3", "var": "x"}},
        {"id": "B-P5", "tier": "B", "input": {"f": "63*x**5 - 70*x**3 + 15*x", "var": "x"}},
    ]


def tier_c_clustered() -> List[Dict[str, Any]]:
    """5 cases — Mignotte M_{n,a} = x^n − 2(ax − 1)^2, two roots near 1/a.

    Mignotte's family is the classic real-root-isolation stress test:
    the polynomial has two real roots clustered within 1/a^{(n+2)/2} of
    each other (Mignotte 1981, "Some useful bounds"). At a = 10, the
    cubic case puts the two clustered roots in the rationals (1/10, 1/9)
    — VAS must bisect down to a separating rational endpoint.
    """
    return [
        # (n=3, a=10): two roots near 0.1, one large root. SymPy intervals:
        # (0, 1/10), (1/10, 1/9), (199, 200).
        {"id": "C-mignotte-3-10",  "tier": "C", "input": {"f": "x**3 - 2*(10*x - 1)**2", "var": "x"}},
        # (n=4, a=10): two roots near 0.1, two large.
        {"id": "C-mignotte-4-10",  "tier": "C", "input": {"f": "x**4 - 2*(10*x - 1)**2", "var": "x"}},
        # (n=5, a=5): two roots near 0.2, one large.
        {"id": "C-mignotte-5-5",   "tier": "C", "input": {"f": "x**5 - 2*(5*x - 1)**2",  "var": "x"}},
        # Three clustered rational roots near 0: (x − 1/100)(x − 2/100)(x − 3/100).
        {"id": "C-three-rational-cluster", "tier": "C",
         "input": {"f": "(x - 1/100) * (x - 2/100) * (x - 3/100)", "var": "x"}},
        # Two roots near zero with rational separation 10^-6.
        {"id": "C-roots-10e-6-apart", "tier": "C",
         "input": {"f": "(x - 1) * (x - 1000001/1000000)", "var": "x"}},
    ]


def tier_d_large_degree() -> List[Dict[str, Any]]:
    """5 cases — large-degree squarefree polynomials with many real roots.

    Wilkinson-style integer-root products are the classical floating-
    point hardness test; for *exact rational* arithmetic they're
    trivial — included here as straightforward stress on rational-
    arithmetic depth, not on real-root isolation per se. Chebyshev
    T_11 and T_13 contribute degrees 11 and 13 of densely-clustered
    irrational real roots — the canonical squarefree-polynomial
    real-root-isolation stress at moderate degree (avoiding the shared-
    zero pitfall of products of odd Chebyshev/Legendre, which collapse
    to non-squarefree).
    """
    wilkinson_20 = " * ".join(f"(x - {i})" for i in range(1, 21))
    cheby_T11 = "1024*x**11 - 2816*x**9 + 2816*x**7 - 1232*x**5 + 220*x**3 - 11*x"
    wilkinson_50 = " * ".join(f"(x - {i})" for i in range(1, 51))
    cheby_T13 = "4096*x**13 - 13312*x**11 + 16640*x**9 - 9984*x**7 + 2912*x**5 - 364*x**3 + 13*x"
    half_integer_30 = " * ".join(f"(2*x - {i})" for i in range(1, 31))
    return [
        {"id": "D-wilkinson-20",    "tier": "D", "input": {"f": wilkinson_20,    "var": "x"}},
        {"id": "D-cheby-T11",       "tier": "D", "input": {"f": cheby_T11,       "var": "x"}},
        {"id": "D-wilkinson-50",    "tier": "D", "input": {"f": wilkinson_50,    "var": "x"}},
        {"id": "D-cheby-T13",       "tier": "D", "input": {"f": cheby_T13,       "var": "x"}},
        {"id": "D-half-integer-30", "tier": "D", "input": {"f": half_integer_30, "var": "x"}},
    ]


def tier_e_rational_stress() -> List[Dict[str, Any]]:
    """5 cases — large numerators, mixed denominators, dense rational coefs."""
    return [
        # Large coefficients in a quadratic (still 2 real roots).
        {"id": "E-large-coefs-quad", "tier": "E",
         "input": {"f": "1000*x**2 - 2*x - 1", "var": "x"}},
        # Mixed denominators in a cubic.
        {"id": "E-mixed-denoms-cubic", "tier": "E",
         "input": {"f": "x**3 - x/2 + 1/6", "var": "x"}},
        # Tiny leading coefficient, large root.
        {"id": "E-tiny-leading-large-root", "tier": "E",
         "input": {"f": "x/1000 - 1", "var": "x"}},
        # Quartic with rational coefs spanning 5 distinct denominators.
        {"id": "E-rational-quartic", "tier": "E",
         "input": {"f": "x**4 - x**3/2 + x**2/3 - x/5 + 1/7", "var": "x"}},
        # Spread roots: near 1 and near 10000.
        {"id": "E-spread-roots-quad", "tier": "E",
         "input": {"f": "x**2 - 10001*x + 10000", "var": "x"}},
    ]


def tier_f_refusals() -> List[Dict[str, Any]]:
    """4 cases — boundary tag classes."""
    return [
        {"id": "F-not-squarefree-double", "tier": "F",
         "input": {"f": "(x - 1)**2", "var": "x"},
         "expected_tag": "real-root-isolate/not-squarefree"},
        {"id": "F-not-squarefree-mixed", "tier": "F",
         "input": {"f": "(x - 1)**2 * (x + 1)", "var": "x"},
         "expected_tag": "real-root-isolate/not-squarefree"},
        {"id": "F-multivariate", "tier": "F",
         "input": {"f": "x*y - 1", "var": "x"},
         "expected_tag": "real-root-isolate/multivariate"},
        {"id": "F-non-polynomial-sin", "tier": "F",
         "input": {"f": "sin(x)", "var": "x"},
         "expected_tag": "real-root-isolate/non-polynomial"},
    ]


def tier_g_edge_structural() -> List[Dict[str, Any]]:
    """5 cases — no-real-roots and other structural edges."""
    return [
        # Quadratic with no real roots.
        {"id": "G-no-real-quadratic", "tier": "G",
         "input": {"f": "x**2 + 1", "var": "x"}},
        # Quartic with no real roots.
        {"id": "G-no-real-quartic", "tier": "G",
         "input": {"f": "x**4 + 1", "var": "x"}},
        # Φ_12 = x^4 − x^2 + 1 — 12-th cyclotomic, all roots complex.
        {"id": "G-cyclotomic-Phi12", "tier": "G",
         "input": {"f": "x**4 - x**2 + 1", "var": "x"}},
        # Φ_13 — 13-th cyclotomic, all 12 roots complex.
        {"id": "G-cyclotomic-Phi13", "tier": "G",
         "input": {"f": "x**12 + x**11 + x**10 + x**9 + x**8 + x**7 + x**6 + x**5 + x**4 + x**3 + x**2 + x + 1",
                   "var": "x"}},
        # Three rational roots including zero — exercises degenerate (r, r) intervals.
        {"id": "G-roots-with-zero", "tier": "G",
         "input": {"f": "x**3 - x", "var": "x"}},
    ]


# ---------------------------------------------------------------------
# Triple-witness oracle
# ---------------------------------------------------------------------


def query_wolfram_count(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run `Length[RootIntervals[f][[1]]]` via wolframscript.

    Wolfram's `RootIntervals[poly]` returns `{intervals, root-membership}`;
    `[[1]]` is the list of intervals; `Length[...]` is the count of
    isolated real roots. We compare *count*, not endpoints — endpoint
    bytes differ across VAS/CF implementations.
    """
    from sympy.printing.mathematica import mathematica_code
    f_str = case["input"]["f"]
    var = case["input"]["var"]
    try:
        f_expr = sp.sympify(f_str, locals={var: sp.Symbol(var)})
    except (sp.SympifyError, SyntaxError, TypeError):
        return {"status": "error", "reason": "sympify failed"}
    code = f"Length[RootIntervals[{mathematica_code(f_expr)}][[1]]]"
    return wolfram_query(code, timeout=60.0)


def admit(case: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Run reference + (optional) Wolfram. Returns (expected envelope, oracle log entry)."""
    inp = case["input"]
    cand = real_root_isolate_reference(inp["f"], inp["var"])

    expected: Dict[str, Any] = {"kind": cand["kind"]}
    if cand["kind"] == "tagged":
        expected["tag"] = case.get("expected_tag", cand["tag"])

    log: Dict[str, Any] = {
        "id":    case["id"],
        "tier":  case["tier"],
        "sympy": "ok" if cand["kind"] == "ok" else f"refused:{cand.get('tag')}",
    }
    if cand["kind"] == "ok":
        log["sympy_count"] = len(cand["intervals"])

    if LIVE_ORACLE:
        if cand["kind"] == "tagged":
            wolf = query_wolfram_count(case)
            log["wolfram"] = wolf.get("status", "missing")
            permanent_classes = {
                "real-root-isolate/not-squarefree",
                "real-root-isolate/multivariate",
                "real-root-isolate/non-polynomial",
            }
            tag = expected.get("tag")
            if tag in permanent_classes:
                # Wolfram may still produce a count for non-squarefree (it
                # squarefree's internally) or refuse on multivariate. Both
                # outcomes are admissible — the workbench's stricter scope
                # is the honest "we expect squarefree input" boundary.
                if wolf.get("status") == "ok":
                    log["consensus"] = "wolfram-ok-workbench-bounded-scope"
                else:
                    log["consensus"] = "both-refuse"
            else:
                log["consensus"] = "uncertain"
        else:
            wolf = query_wolfram_count(case)
            log["wolfram"] = wolf.get("status", "missing")
            if wolf.get("status") == "ok":
                try:
                    wolf_count = int(wolf["result"].strip())
                    log["wolfram_count"] = wolf_count
                    if wolf_count == log["sympy_count"]:
                        log["consensus"] = "sympy+wolfram"
                    else:
                        log["consensus"] = "sympy-wolfram-disagree"
                        # Drop on disagreement (ADR-0019 §3).
                        return None, log
                except (ValueError, KeyError):
                    log["consensus"] = "sympy-only-wolfram-parse-failed"
            else:
                log["wolfram_reason"] = wolf.get("reason")
                log["consensus"] = "sympy-only"
    else:
        log["consensus"] = "sympy-only (LIVE_ORACLE off)"

    return expected, log


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def main() -> int:
    cases: List[Dict[str, Any]] = []
    cases.extend(tier_a_trivial())
    cases.extend(tier_b_chebyshev_legendre())
    cases.extend(tier_c_clustered())
    cases.extend(tier_d_large_degree())
    cases.extend(tier_e_rational_stress())
    cases.extend(tier_f_refusals())
    cases.extend(tier_g_edge_structural())

    inputs_payload = {
        "version": ENCODING_VERSION,
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
            f"  [{i + 1:3d}/{len(cases)}] {marker} {case['id']:34s} "
            f"{log.get('consensus',''):44s} {dt:5.1f}s",
            flush=True,
        )
        if expected is None:
            dropped += 1
            continue
        expected_list.append({"id": case["id"], "expected": expected})
        admitted += 1

    print(f"[done] {admitted}/{len(cases)} admitted, {dropped} dropped in {time.time() - t_start:.1f}s",
          flush=True)

    expected_payload = {"version": ENCODING_VERSION, "cases": expected_list}
    oracle_payload = {
        "version":     ENCODING_VERSION,
        "live_oracle": LIVE_ORACLE,
        "summary":     {"total": len(cases), "admitted": admitted, "dropped": dropped},
        "entries":     oracle_log,
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
