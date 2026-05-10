#!/usr/bin/env python3
"""Generate `inputs.json` + `expected.json` for the alg-num-arith bench.

Each case has:
  id        — A-elem-01 / B-nested-03 / etc.
  tier      — A | B | C | D | E
  input     — {a: <Root[]>, b: <Root[]>?, op: str}
  expected  — {kind: "ok"} (arithmetic happy-path; verifier
              re-derives the reference output from the SymPy oracle)
            | {kind: "boolean", value: bool} (eq cases — fixed expected)
            | {kind: "tagged", tag: <class>} (refusal)

Deterministic output: every byte byte-equal across runs given the
same SymPy version. (Per-version SymPy drift is unlikely on simple
algebraic inputs but recorded if observed.)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

# Resolve sibling reference module
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BENCH_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(BENCH_ROOT, "reference"))

from alg_num_arith_reference import (  # noqa: E402
    CLS_INV_OF_ZERO,
    CLS_DIV_BY_ZERO,
    oracle_compute,
)


# ---------------------------------------------------------------------
# Wire-encoded Root[] helpers
# ---------------------------------------------------------------------


def root_val(coefs_low_to_high: List[int], k: int) -> Dict[str, Any]:
    return {
        "kind": "expression",
        "head": "Root",
        "args": [
            {
                "kind": "expression",
                "head": "Polynomial",
                "args": [
                    {"kind": "integer", "value": str(c)}
                    for c in coefs_low_to_high
                ],
            },
            {"kind": "integer", "value": str(k)},
        ],
    }


# Common Root values
ZERO     = root_val([0, 1], 0)             # Root[x, 0] = 0
ONE      = root_val([-1, 1], 0)            # Root[x − 1, 0] = 1
NEG_ONE  = root_val([1, 1], 0)             # Root[x + 1, 0] = −1
SQRT2    = root_val([-2, 0, 1], 1)         # +√2
NEG_SQRT2 = root_val([-2, 0, 1], 0)        # −√2
SQRT3    = root_val([-3, 0, 1], 1)         # +√3
NEG_SQRT3 = root_val([-3, 0, 1], 0)        # −√3
SQRT5    = root_val([-5, 0, 1], 1)         # +√5
SQRT6    = root_val([-6, 0, 1], 1)         # +√6


# `Root[x⁴ − 4x² + 1, k=3]` is √(2 + √3) (one of the four real roots
# of the quartic; k=3 is the largest, ≈ 1.93). Verified by:
#   sp.Poly(x**4 - 4*x**2 + 1, x).real_roots()
# = [-sqrt(2 + sqrt(3)), -sqrt(2 - sqrt(3)), sqrt(2 - sqrt(3)),
#    sqrt(2 + sqrt(3))]
SQRT_2_PLUS_SQRT3  = root_val([1, 0, -4, 0, 1], 3)
SQRT_2_MINUS_SQRT3 = root_val([1, 0, -4, 0, 1], 2)
NEG_SQRT_2_MINUS_SQRT3 = root_val([1, 0, -4, 0, 1], 1)
NEG_SQRT_2_PLUS_SQRT3  = root_val([1, 0, -4, 0, 1], 0)


# Lehmer's L(x) = x⁵ + x⁴ − 4x³ − 3x² + 3x + 1
# minimal polynomial of 2cos(2π/11); 5 real roots.
LEHMER_K0 = root_val([1, 3, -3, -4, 1, 1], 0)
LEHMER_K1 = root_val([1, 3, -3, -4, 1, 1], 1)
LEHMER_K2 = root_val([1, 3, -3, -4, 1, 1], 2)


# ---------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------


def _ok() -> Dict[str, Any]:
    return {"kind": "ok"}


def _bool_expected(b: bool) -> Dict[str, Any]:
    return {"kind": "boolean", "value": b}


def _refusal(tag: str) -> Dict[str, Any]:
    return {"kind": "tagged", "tag": tag}


def build_cases() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    def add(case_id: str, tier: str, op: str,
            a: Dict[str, Any], b: Optional[Dict[str, Any]],
            expected: Dict[str, Any]) -> None:
        inp: Dict[str, Any] = {"a": a, "op": op}
        if b is not None:
            inp["b"] = b
        cases.append({
            "id": case_id,
            "tier": tier,
            "input": inp,
            "expected": expected,
        })

    # ----- Tier A — elementary ------------------------------------------------
    add("A-elem-01-add-sqrt2-sqrt3", "A", "add", SQRT2, SQRT3, _ok())
    add("A-elem-02-mul-sqrt2-sqrt3", "A", "mul", SQRT2, SQRT3, _ok())
    add("A-elem-03-mul-sqrt2-sqrt2-rational-degenerate", "A", "mul", SQRT2, SQRT2, _ok())
    add("A-elem-04-add-1-sqrt2", "A", "add", ONE, SQRT2, _ok())
    add("A-elem-05-sub-sqrt3-sqrt2", "A", "sub", SQRT3, SQRT2, _ok())
    add("A-elem-06-neg-sqrt2", "A", "neg", SQRT2, None, _ok())
    add("A-elem-07-inv-sqrt2-non-monic-canonical", "A", "inv", SQRT2, None, _ok())
    add("A-elem-08-div-sqrt6-sqrt2", "A", "div", SQRT6, SQRT2, _ok())
    add("A-elem-09-div-sqrt2-sqrt2-yields-1", "A", "div", SQRT2, SQRT2, _ok())
    add("A-elem-10-add-zero-sqrt2", "A", "add", ZERO, SQRT2, _ok())

    # ----- Tier B — nested radicals ------------------------------------------
    # √(2 + √3) + √(2 − √3) = √6 (a classical denesting identity)
    add("B-nested-01-add-nested-conjugate-sum", "B", "add",
        SQRT_2_PLUS_SQRT3, SQRT_2_MINUS_SQRT3, _ok())
    # √(2 + √3) · √(2 − √3) = 1 (product of conjugates of x⁴ − 4x² + 1)
    add("B-nested-02-mul-nested-conjugate-product", "B", "mul",
        SQRT_2_PLUS_SQRT3, SQRT_2_MINUS_SQRT3, _ok())
    # √(2 + √3) − √(2 − √3) = √2 (denesting via difference)
    add("B-nested-03-sub-nested-difference", "B", "sub",
        SQRT_2_PLUS_SQRT3, SQRT_2_MINUS_SQRT3, _ok())
    add("B-nested-04-neg-nested", "B", "neg",
        SQRT_2_PLUS_SQRT3, None, _ok())
    # `B-nested-05-inv-nested` exercises the palindromic-minpoly path:
    # `inv(√(2+√3))` reverses the coefficients of x⁴−4x²+1 (palindromic
    # → reciprocal is itself), then canonicalises. This was the canary
    # case for the term-order bug in `algNumInv`'s `reverseCoefficients`
    # helper — fixed in worklog 066. Result: 1/√(2+√3) = √(2−√3) =
    # Root[x⁴−4x²+1, k=2].
    add("B-nested-05-inv-nested", "B", "inv",
        SQRT_2_PLUS_SQRT3, None, _ok())

    # ----- Tier C — high-degree minpolys --------------------------------------
    add("C-high-01-add-lehmer-k0-k1", "C", "add", LEHMER_K0, LEHMER_K1, _ok())
    add("C-high-02-mul-lehmer-k0-k1", "C", "mul", LEHMER_K0, LEHMER_K1, _ok())
    add("C-high-03-add-lehmer-sqrt2", "C", "add", LEHMER_K2, SQRT2, _ok())
    add("C-high-04-mul-lehmer-sqrt3", "C", "mul", LEHMER_K1, SQRT3, _ok())
    add("C-high-05-neg-lehmer-k0", "C", "neg", LEHMER_K0, None, _ok())

    # ----- Tier D — conjugate-distinguishing ---------------------------------
    # Same minpoly, different k. Sum of conjugates = trace; product = norm.
    # +√2 + (−√2) = 0 (rational case; trace = 0)
    add("D-conj-01-add-sqrt2-conjugates-trace-0", "D", "add", SQRT2, NEG_SQRT2, _ok())
    # +√2 · (−√2) = −2 (norm)
    add("D-conj-02-mul-sqrt2-conjugates-norm", "D", "mul", SQRT2, NEG_SQRT2, _ok())
    # √(2+√3) + √(2−√3) = √6 (k=3 + k=2 of x⁴-4x²+1)
    add("D-conj-03-add-quartic-conjugates-trace-shape", "D", "add",
        SQRT_2_PLUS_SQRT3, NEG_SQRT_2_MINUS_SQRT3, _ok())  # k=3 + k=1 = 0 (different)
    # Lehmer k=0 + k=4 (other end of conjugate set)
    add("D-conj-04-add-lehmer-k0-neg-conjugate", "D", "add", LEHMER_K0, NEG_SQRT2, _ok())
    add("D-conj-05-mul-lehmer-k1-k2", "D", "mul", LEHMER_K1, LEHMER_K2, _ok())

    # ----- Tier E — equality stress ------------------------------------------
    # (1) Reflexivity
    add("E-eq-01-reflexive-sqrt2", "E", "eq", SQRT2, SQRT2, _bool_expected(True))
    # (2) Cross-minpoly distinguish
    add("E-eq-02-cross-minpoly-sqrt2-sqrt3-false", "E", "eq", SQRT2, SQRT3, _bool_expected(False))
    # (3) Same-minpoly index-distinguish
    add("E-eq-03-same-minpoly-different-k-false", "E", "eq", SQRT2, NEG_SQRT2, _bool_expected(False))
    # (4) Rational degenerate: 0 == 0, 1 == 1, 1 == −1
    add("E-eq-04-rational-zero-true", "E", "eq", ZERO, ZERO, _bool_expected(True))
    add("E-eq-05-rational-one-vs-negone-false", "E", "eq", ONE, NEG_ONE, _bool_expected(False))

    # ----- Refusal cases -----------------------------------------------------
    add("F-refuse-01-inv-of-zero", "F", "inv", ZERO, None, _refusal(CLS_INV_OF_ZERO))
    add("F-refuse-02-div-by-zero", "F", "div", SQRT2, ZERO, _refusal(CLS_DIV_BY_ZERO))

    return cases


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def main() -> None:
    cases = build_cases()
    inputs = {"cases": [{"id": c["id"], "tier": c["tier"], "input": c["input"]}
                        for c in cases]}
    expected = {"cases": [{"id": c["id"], "expected": c["expected"]}
                          for c in cases]}

    out_dir = THIS_DIR
    with open(os.path.join(out_dir, "inputs.json"), "w") as f:
        json.dump(inputs, f, indent=2, sort_keys=True)
        f.write("\n")
    with open(os.path.join(out_dir, "expected.json"), "w") as f:
        json.dump(expected, f, indent=2, sort_keys=True)
        f.write("\n")

    # Sanity-validate every case via the oracle. This catches misshapen
    # inputs at generation time rather than at verify time.
    n_ok = n_eq = n_refuse = n_failed = 0
    for c in cases:
        inp = c["input"]
        op = inp["op"]
        a = inp["a"]
        b = inp.get("b")
        try:
            oracle_compute(a, b, op)
            if c["expected"]["kind"] == "ok":
                n_ok += 1
            elif c["expected"]["kind"] == "boolean":
                n_eq += 1
            elif c["expected"]["kind"] == "tagged":
                n_refuse += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {c['id']}: oracle failed — {e}", file=sys.stderr)
            n_failed += 1

    print(f"generated {len(cases)} cases:")
    print(f"  arithmetic-happy: {n_ok}")
    print(f"  eq:               {n_eq}")
    print(f"  refusal:          {n_refuse}")
    print(f"  oracle-failed:    {n_failed}")
    if n_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
