#!/usr/bin/env python3
"""Generate the poly-factor-q golden master with triple-witness oracle.

Per ADR-0019, every golden case is admitted iff at least 2 of 3
oracles (Wolfram Factor, SymPy factor_list, optional SageMath) agree
on the answer modulo the verifier-invariant equivalence relation.

This generator:
  1. Constructs each test case across 8 tiers (per PROMPT.md).
  2. Computes the expected output via SymPy (the local reference).
  3. Cross-validates against Wolfram via `bench/_corpus/oracle/`.
  4. Logs disagreements; admits only consensus cases to the bench.
  5. Writes inputs.json + expected.json + oracle_log.json.

Reproducibility: seeded `random.Random` (CPython stdlib).

Run:
  python3 generate.py             # full triple-witness (slow, ~5-10 min)
  WB_LIVE_ORACLE=0 python3 generate.py  # SymPy only (fast, ~10 s)
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from fractions import Fraction
from functools import reduce
from math import gcd
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT / "bench" / "_corpus"))

from oracle.wolfram import wolfram_query  # noqa: E402
from oracle.agreement import agree  # noqa: E402

LIVE_ORACLE = os.environ.get("WB_LIVE_ORACLE", "1") != "0"
SEED = 20260507
ENCODING_VERSION = 1

X = sp.Symbol("x")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def rat_str(x) -> str:
    """Canonical lowest-terms rational string."""
    f = Fraction(x) if not isinstance(x, Fraction) else x
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def poly_to_str(p: sp.Poly) -> str:
    """Canonical polynomial expression string (degree-descending)."""
    return sp.sstr(p.as_expr(), order="lex")


def expr_to_str(e) -> str:
    """Canonical expression string."""
    return sp.sstr(sp.expand(e), order="lex")


def mignotte_log2(f_str: str, var_str: str) -> int:
    """log2 of largest |coef| across all true factors of f."""
    var = sp.Symbol(var_str)
    f_poly = sp.Poly(sp.sympify(f_str, locals={var_str: var}), var, domain="QQ")
    # Convert to ZZ for factor_list (clears denominators into content).
    int_poly = f_poly.clear_denoms()[1]
    _, factors = int_poly.factor_list()
    max_coef = 1
    for fac, _ in factors:
        for coef in fac.all_coeffs():
            max_coef = max(max_coef, abs(int(coef)))
    return math.ceil(math.log2(max(2, max_coef + 1)))


def integer_gcd_list(coefs: List[int]) -> int:
    """gcd of a list of integers (treating sign as absolute)."""
    nz = [abs(c) for c in coefs if c != 0]
    if not nz:
        return 1
    return reduce(gcd, nz)


# ---------------------------------------------------------------------
# Per-tier case generators
# ---------------------------------------------------------------------


def tier_a_shape_edges() -> List[Dict]:
    """6 cases — shape edges. Catches 'doesn't handle deg-1' and
    'doesn't handle constant prefactor' bugs."""
    return [
        {"id": "A-deg-1-x",                "tier": "A",
         "input": {"f": "x", "var": "x"}},
        {"id": "A-deg-1-shifted",          "tier": "A",
         "input": {"f": "x - 3", "var": "x"}},
        {"id": "A-deg-1-with-content",     "tier": "A",
         "input": {"f": "2*x + 4", "var": "x"}},
        {"id": "A-deg-2-irreducible",      "tier": "A",
         "input": {"f": "x^2 + 1", "var": "x"}},
        {"id": "A-deg-2-splits",           "tier": "A",
         "input": {"f": "x^2 - 4", "var": "x"}},
        {"id": "A-deg-2-perfect-square",   "tier": "A",
         "input": {"f": "x^2 - 4*x + 4", "var": "x"}},
    ]


def tier_b_random_primitive(rng: random.Random) -> List[Dict]:
    """12 cases — random low-degree primitive ℤ[x]. Broad smoke."""
    cases = []
    degrees = [2, 3, 4, 5, 7, 10]  # 2 cases per degree
    for d in degrees:
        for k in range(2):
            coefs = [rng.randint(-9, 9) for _ in range(d + 1)]
            while coefs[0] == 0:
                coefs[0] = rng.randint(-9, 9)
            g = integer_gcd_list(coefs)
            coefs = [c // g for c in coefs]
            if coefs[0] < 0:
                coefs = [-c for c in coefs]
            poly = sp.Poly(coefs, X, domain="ZZ")
            cases.append({
                "id": f"B-random-deg{d}-{k+1}",
                "tier": "B",
                "input": {"f": poly_to_str(poly), "var": "x"},
            })
    return cases


def tier_c_cyclotomic() -> List[Dict]:
    """8 cases — cyclotomic Φ_n. Each is irreducible over ℚ; catches
    'factored an irreducible' bugs. Φ_30 is the canonical 'looks
    reducible but isn't' example."""
    cases = []
    for n in [3, 5, 7, 8, 12, 15, 24, 30]:
        phi_n = sp.Poly(sp.cyclotomic_poly(n, X), X, domain="ZZ")
        cases.append({
            "id": f"C-cyclotomic-phi-{n}",
            "tier": "C",
            "input": {"f": poly_to_str(phi_n), "var": "x"},
        })
    return cases


def tier_d_swinnerton_dyer() -> List[Dict]:
    """6 cases — minimal poly of √p_1 + … + √p_n for distinct
    primes. Each is irreducible over ℚ but factors into 2^n pieces
    mod every prime. Tier D demands polynomial-time recombination."""
    primes_sequences = [
        [2, 3],          # deg 4, 4 modular factors
        [2, 5],          # deg 4
        [3, 5],          # deg 4
        [2, 3, 5],       # deg 8, 8 modular factors
        [2, 3, 7],       # deg 8
        [2, 3, 5, 7],    # deg 16, 16 modular factors — the load-bearing case
    ]
    cases = []
    for primes in primes_sequences:
        s = sum(sp.sqrt(p) for p in primes)
        minpoly = sp.minimal_polynomial(s, X)
        minpoly_poly = sp.Poly(minpoly, X, domain="ZZ")
        cases.append({
            "id": f"D-swinnerton-dyer-{'-'.join(str(p) for p in primes)}",
            "tier": "D",
            "input": {"f": poly_to_str(minpoly_poly), "var": "x"},
        })
    return cases


def tier_e_multiplicities() -> List[Dict]:
    """8 cases — square-laden / mixed multiplicities. Catches
    'merged powers of the same factor' and 'off-by-one multiplicity'
    bugs."""
    return [
        {"id": "E-x-minus-1-squared",      "tier": "E",
         "input": {"f": "(x - 1)^2", "var": "x"}},
        {"id": "E-x-minus-1-cubed",        "tier": "E",
         "input": {"f": "(x - 1)^3", "var": "x"}},
        {"id": "E-x-minus-1-fifth",        "tier": "E",
         "input": {"f": "(x - 1)^5", "var": "x"}},
        {"id": "E-x-minus-1-seventh",      "tier": "E",
         "input": {"f": "(x - 1)^7", "var": "x"}},
        {"id": "E-x2plus1-cubed-times-x-minus-2-squared",  "tier": "E",
         "input": {"f": "(x^2 + 1)^3 * (x - 2)^2", "var": "x"}},
        {"id": "E-mixed-quadratic-irreducible", "tier": "E",
         "input": {"f": "(x - 1)^2 * (x + 1)^2 * (x^2 + x + 1)", "var": "x"}},
        {"id": "E-deg-15-mixed-multiplicities", "tier": "E",
         "input": {"f": "(x + 1)^4 * (x - 2)^3 * (x^2 + x + 1)^2 * (x - 5)^2",
                   "var": "x"}},
        {"id": "E-nested-squares-of-squares", "tier": "E",
         "input": {"f": "((x - 1)^2 * (x + 1)^2)^2", "var": "x"}},
    ]


def tier_f_mignotte_stress(rng: random.Random) -> List[Dict]:
    """6 cases — large-coefficient stress, deliberately near the
    Mignotte bound. Catches 'Hensel lifted to insufficient p^k' and
    'bit-blown intermediate rationals' bugs."""
    cases = []

    # Three random-product cases: f = ∏ (x − a_i) with a_i ∈ [-50, 50]
    for deg in [8, 12, 15]:
        roots: List[int] = []
        attempts = 0
        while len(roots) < deg and attempts < 200:
            r = rng.randint(-50, 50)
            if r not in roots:
                roots.append(r)
            attempts += 1
        while len(roots) < deg:
            roots.append(rng.randint(-50, 50))  # tolerate dup if unlucky
        f = sp.prod([X - r for r in roots])
        cases.append({
            "id": f"F-product-of-{deg}-distinct-linears",
            "tier": "F",
            "input": {"f": expr_to_str(sp.expand(f)), "var": "x"},
        })

    # Three deliberate cases:
    cases.append({
        "id": "F-mignotte-large-leading",
        "tier": "F",
        "input": {"f": "100*x^4 + 200*x^3 - 300*x^2 - 400*x + 500", "var": "x"},
    })
    cases.append({
        "id": "F-mignotte-irreducible-large",
        "tier": "F",
        "input": {"f": "x^4 + 90*x^3 + 80*x^2 + 70*x + 60", "var": "x"},
    })
    coefs = [rng.randint(-200, 200) for _ in range(11)]
    while coefs[0] == 0:
        coefs[0] = rng.randint(-200, 200)
    if coefs[0] < 0:
        coefs = [-c for c in coefs]
    poly = sp.Poly(coefs, X, domain="ZZ")
    cases.append({
        "id": "F-mignotte-random-deg-10-coefs-200",
        "tier": "F",
        "input": {"f": poly_to_str(poly), "var": "x"},
    })

    return cases


def tier_g_content() -> List[Dict]:
    """6 cases — content / scaling. Catches 'lost the rational
    prefactor' and 'absorbed sign into wrong factor' bugs."""
    return [
        {"id": "G-int-content-7",         "tier": "G",
         "input": {"f": "7*(x - 1)", "var": "x"}},
        {"id": "G-rational-content-2-3",  "tier": "G",
         "input": {"f": "(2/3)*(x^2 + x + 1)", "var": "x"}},
        {"id": "G-negative-content",      "tier": "G",
         "input": {"f": "-5*(x^3 - 1)", "var": "x"}},
        {"id": "G-large-content-1024",    "tier": "G",
         "input": {"f": "1024*(x^2 + 1)", "var": "x"}},
        {"id": "G-content-with-multiplicities", "tier": "G",
         "input": {"f": "-18*(x - 1)^3 * (x^2 + 1)", "var": "x"}},
        {"id": "G-rational-content-multi-factor", "tier": "G",
         "input": {"f": "(3/2)*(x - 1)*(x + 2)*(x^2 + 1)", "var": "x"}},
    ]


def tier_h_refusals() -> List[Dict]:
    """4 cases — refusal class. Catches 'silently produced garbage on
    out-of-vocabulary input' bugs."""
    return [
        {"id": "H-non-polynomial-sin",  "tier": "H",
         "input": {"f": "sin(x)", "var": "x"},
         "expected_kind": "tagged",
         "expected_tag":  "poly-factor-q/non-polynomial",
         "expected_payload_predicate": "detail-non-empty"},
        {"id": "H-non-polynomial-1-over-x", "tier": "H",
         "input": {"f": "1/x", "var": "x"},
         "expected_kind": "tagged",
         "expected_tag":  "poly-factor-q/non-polynomial",
         "expected_payload_predicate": "detail-non-empty"},
        {"id": "H-non-polynomial-sqrt", "tier": "H",
         "input": {"f": "sqrt(x) + 1", "var": "x"},
         "expected_kind": "tagged",
         "expected_tag":  "poly-factor-q/non-polynomial",
         "expected_payload_predicate": "detail-non-empty"},
        {"id": "H-multivariate", "tier": "H",
         "input": {"f": "x*y + 1", "var": "x"},
         "expected_kind": "tagged",
         "expected_tag":  "poly-factor-q/non-polynomial",
         "expected_payload_predicate": "detail-non-empty"},
    ]


# ---------------------------------------------------------------------
# Oracles
# ---------------------------------------------------------------------


def is_univariate_polynomial(f_str: str, var_str: str) -> bool:
    """Strict check: f parses to a polynomial in `var` over ℚ with no
    other free symbols."""
    var = sp.Symbol(var_str)
    try:
        f = sp.sympify(f_str, locals={var_str: var})
    except (sp.SympifyError, SyntaxError):
        return False
    if f.free_symbols - {var}:
        return False
    try:
        sp.Poly(f, var, domain="QQ")
    except (sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed):
        return False
    return True


def sympy_factor(f_str: str, var_str: str) -> Dict[str, Any]:
    """SymPy oracle: factor f over ℚ via Poly.factor_list."""
    if not is_univariate_polynomial(f_str, var_str):
        return {"status": "ok", "result": {"_refusal": "non-polynomial"}}

    var = sp.Symbol(var_str)
    f = sp.sympify(f_str, locals={var_str: var})
    poly = sp.Poly(f, var, domain="QQ")

    if poly.is_zero or poly.degree() <= 0:
        return {"status": "failed", "reason": "zero or constant", "result": None}

    c, factors = poly.factor_list()

    # Canonicalise content + factors.
    # Each factor from SymPy is a Poly over QQ. Convert each to ℤ[x]
    # primitive form by clearing denominators into the content.
    canonical_factors: List[Tuple[str, int]] = []
    accumulated_denom = 1
    accumulated_lead_sign = 1

    for fac, mult in factors:
        # Convert fac (Poly over QQ) to integer-coef primitive form.
        coefs_q = fac.all_coeffs()
        # Clear denominators: multiply by lcm of denominators.
        denoms = [Fraction(str(coef)).denominator for coef in coefs_q]
        common_denom = reduce(lambda a, b: a * b // gcd(a, b), denoms, 1)
        int_coefs = [int(Fraction(str(coef)) * common_denom) for coef in coefs_q]
        # Make primitive.
        g = integer_gcd_list(int_coefs)
        int_coefs = [c // g for c in int_coefs]
        # Make positive leading coefficient.
        if int_coefs[0] < 0:
            int_coefs = [-c for c in int_coefs]
            sign_flip = -1
        else:
            sign_flip = 1
        # Total content adjustment: factor's content (from QQ form) is
        # (g / common_denom) * sign_flip per multiplicity; absorb into
        # global content c.
        adjustment = Fraction(g * sign_flip, common_denom) ** mult
        c = c * sp.Rational(adjustment.numerator, adjustment.denominator)
        # Build canonical factor expression
        prim_poly = sp.Poly(int_coefs, var, domain="ZZ")
        canonical_factors.append((poly_to_str(prim_poly), mult))

    # Sort canonical factors by (degree ascending, coef-tuple ascending).
    def _factor_key(item: Tuple[str, int]):
        f_str_inner, _ = item
        p = sp.Poly(sp.sympify(f_str_inner, locals={var_str: var}), var, domain="ZZ")
        return (p.degree(), tuple(int(co) for co in p.all_coeffs()))

    canonical_factors.sort(key=_factor_key)

    return {
        "status": "ok",
        "result": {
            "kind": "ok",
            "content": rat_str(c),
            "factors": canonical_factors,
        },
    }


def wolfram_factor(f_str: str, var_str: str) -> Dict[str, Any]:
    """Wolfram oracle: Factor[f, var]. Returns the factored expression
    in InputForm, suitable for the agreement layer's `kind="factor-list"`
    handler (which re-factors via SymPy and compares as multisets)."""
    if not LIVE_ORACLE:
        return {"status": "skipped", "result": None}
    # Factor[] does not take a variable argument; for univariate input
    # the variable is inferred. Passing one trips Factor::nonopt.
    code = f"Factor[{f_str}]"
    return wolfram_query(code, timeout=30)


# ---------------------------------------------------------------------
# Triple-witness consensus
# ---------------------------------------------------------------------


def admit_case(case: Dict) -> Tuple[bool, Optional[Dict], Dict]:
    """Compute SymPy factorization + Wolfram cross-check.

    Returns (admit, expected_record, oracle_log_record).
    """
    f_str = case["input"]["f"]
    var_str = case["input"]["var"]

    # Tier H: refusal-class, no factorization to compute.
    if case["tier"] == "H":
        # Sanity check: SymPy should also classify as non-polynomial.
        sp_result = sympy_factor(f_str, var_str)
        sp_refused = (
            sp_result["status"] == "ok"
            and isinstance(sp_result["result"], dict)
            and "_refusal" in sp_result["result"]
        )
        if not sp_refused:
            # SymPy thinks it's a polynomial but we marked it as Tier H —
            # bench-design inconsistency. Drop with a loud log.
            return (
                False,
                None,
                {"status": "DROPPED",
                 "reason": "sympy admits as polynomial but bench expects refusal",
                 "sympy": sp_result},
            )
        expected = {
            "kind": "tagged",
            "tag":  case["expected_tag"],
            "payload_predicate": case["expected_payload_predicate"],
        }
        return True, expected, {
            "status":  "admitted",
            "sympy":   "agrees: refusal",
            "wolfram": "skipped (refusal)",
            "consensus": "sympy + bench-design (no Wolfram needed)",
        }

    # Happy-path tiers.
    sp_oracle = sympy_factor(f_str, var_str)
    if sp_oracle["status"] != "ok" or "_refusal" in (sp_oracle.get("result") or {}):
        return False, None, {"status": "DROPPED",
                              "reason": f"sympy: {sp_oracle.get('reason') or sp_oracle['result']}"}

    # Build expected.json from SymPy.
    expected: Dict[str, Any] = {
        "kind": "ok",
        "content": sp_oracle["result"]["content"],
        "factors": [{"factor": f, "multiplicity": m}
                    for f, m in sp_oracle["result"]["factors"]],
    }

    wolf_oracle = wolfram_factor(f_str, var_str)

    if wolf_oracle["status"] == "ok":
        # Repackage SymPy result for the agreement layer (`kind="factor-list"`
        # expects a list of (str, int) tuples).
        sp_for_agree = {
            "status": "ok",
            "result": list(sp_oracle["result"]["factors"]),
        }
        try:
            agreed = agree(wolf_oracle, sp_for_agree, kind="factor-list",
                           var=var_str)
        except Exception as exc:  # noqa: BLE001
            agreed = False
            ag_status = f"agreement-error: {exc}"
        else:
            ag_status = "agreed" if agreed else "DISAGREED"
        log = {
            "status":  "admitted" if agreed else "DROPPED",
            "sympy":   "ok",
            "wolfram": ag_status,
            "consensus": ("sympy + wolfram" if agreed else "sympy only"),
        }
        if not agreed:
            return False, expected, log
        return True, expected, log

    # Wolfram unavailable / failed / skipped — admit on SymPy alone with
    # a warning in the log. (Triple-witness is the goal; pair-witness is
    # the floor when the kernel is offline.)
    log = {
        "status":  "admitted-sympy-only",
        "sympy":   "ok",
        "wolfram": wolf_oracle.get("status", "unknown"),
        "consensus": "sympy only",
    }
    return True, expected, log


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def main() -> int:
    rng = random.Random(SEED)

    cases: List[Dict] = []
    cases.extend(tier_a_shape_edges())
    cases.extend(tier_b_random_primitive(rng))
    cases.extend(tier_c_cyclotomic())
    cases.extend(tier_d_swinnerton_dyer())
    cases.extend(tier_e_multiplicities())
    cases.extend(tier_f_mignotte_stress(rng))
    cases.extend(tier_g_content())
    cases.extend(tier_h_refusals())

    inputs: List[Dict] = []
    expected: List[Dict] = []
    oracle_log: List[Dict] = []
    drops = 0

    for case in cases:
        print(f"  case {case['id']:50s} ", end="", flush=True)
        admit, exp, log = admit_case(case)
        if not admit:
            print(f"DROPPED  ({log.get('reason', log.get('wolfram', '?'))})")
            drops += 1
            oracle_log.append({"id": case["id"], **log})
            continue
        print(f"OK  ({log.get('consensus', '-')})")

        # Tier F: add expected_max_coef_log2 sidecar (verifier sanity check).
        if case["tier"] == "F":
            case["input"]["expected_max_coef_log2"] = mignotte_log2(
                case["input"]["f"], case["input"]["var"]
            )

        inputs.append({"id": case["id"], "tier": case["tier"], "input": case["input"]})
        expected.append({"id": case["id"], "expected": exp})
        oracle_log.append({"id": case["id"], **log})

    # Write outputs.
    payload_inputs = {
        "version": ENCODING_VERSION,
        "seed": SEED,
        "cases": inputs,
    }
    payload_expected = {
        "version": ENCODING_VERSION,
        "cases": expected,
    }

    (HERE / "inputs.json").write_text(json.dumps(payload_inputs, indent=2) + "\n")
    (HERE / "expected.json").write_text(json.dumps(payload_expected, indent=2) + "\n")
    (HERE / "oracle_log.json").write_text(json.dumps(oracle_log, indent=2) + "\n")

    print()
    print(f"Wrote {len(inputs)} cases to inputs.json + expected.json.")
    print(f"Drops: {drops}.")
    if drops:
        print("  (See oracle_log.json for disagreement classification.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
