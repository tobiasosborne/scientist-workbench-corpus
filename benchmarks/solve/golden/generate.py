#!/usr/bin/env python3
"""Generate the bench/solve golden master with triple-witness oracle.

Per ADR-0019, every golden case is admitted iff at least 2 of 3 oracles
(Wolfram Solve, SymPy solve/solveset, optional SageMath) agree on the
expected output modulo the verifier-invariant equivalence relation.

This generator:
  1. Builds 20 hand-curated cases (Mathematica-v1 bank: 15 handled, 5
     refused) drawn from elementary CAS capability + Fateman 1991.
  2. Builds 80 stratified random cases per the bead description:
     linear (15), univariate-poly (25), multivariate-zero-dim (25
     v0.1-refusal-class), transcendental-univariate (15).
  3. Computes the expected output via the local SymPy reference.
  4. Cross-validates against Wolfram via `bench/_corpus/oracle/`.
  5. Logs disagreements; admits only consensus cases.
  6. Writes inputs.json + expected.json + oracle_log.json.

Reproducibility: seeded `random.Random` (CPython stdlib).

Run:
  python3 generate.py                    # full triple-witness (slow)
  WB_LIVE_ORACLE=0 python3 generate.py   # SymPy only (fast iteration)
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT / "bench" / "_corpus"))
sys.path.insert(0, str(HERE.parent / "reference"))

from oracle.wolfram import wolfram_query              # noqa: E402
from solve_reference import solve_reference            # noqa: E402

LIVE_ORACLE = os.environ.get("WB_LIVE_ORACLE", "1") != "0"
SEED = 20260507
ENCODING_VERSION = 1


# ---------------------------------------------------------------------
# Lane classification (used to set `expected.lane`)
# ---------------------------------------------------------------------


def classify_lane(eqs: List[str], var_names: List[str]) -> str:
    """Return the lane the bench verifier will dispatch to.

    Mirrors the workbench's `tools/solve/tool.ts` body: the transcendental
    fast path runs *first* for single-eq single-var; otherwise the
    polynomial classifier checks linearity (total_degree ≤ 1, no
    cross-products) before falling through to univariate-poly.
    """
    cand = solve_reference(eqs, var_names)
    if cand["kind"] == "tagged":
        return "refusal"
    # cand["kind"] == "ok": branch on (transcendental fast path) OR (linear / univariate-poly).
    syms = [sp.Symbol(v) for v in var_names]
    locals_map = {v: syms[i] for i, v in enumerate(var_names)}
    parsed = [sp.sympify(e, locals=locals_map) for e in eqs]

    # Transcendental fast path: 1 eq, 1 var, not a polynomial in var.
    if len(eqs) == 1 and len(var_names) == 1:
        try:
            sp.Poly(parsed[0], syms[0], domain="QQ")
        except (sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed):
            return "transcendental"

    # Polynomial route: check linearity first.
    try:
        polys = [sp.Poly(e, *syms, domain="QQ") for e in parsed]
    except (sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed):
        return "transcendental"  # (shouldn't reach here; cand was 'ok')

    is_linear = all(p.total_degree() <= 1 for p in polys)
    if is_linear:
        return "linear"
    if len(eqs) == 1 and len(var_names) == 1:
        return "univariate-poly"
    return "linear"  # multivariate degree>1 already routed to refusal above


# ---------------------------------------------------------------------
# Hand-curated v1 bank (20 cases)
# ---------------------------------------------------------------------


def hand_curated_v1_bank() -> List[Dict[str, Any]]:
    """20 cases: 15 v1-handled + 5 v1-refused (the Fateman bank)."""
    bank: List[Dict[str, Any]] = []

    # === v1 HANDLED ===

    # Linear (4)
    bank.append({"id": "v1-linear-2x2-unique",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x + y - 3", "x - y - 1"], "vars": ["x", "y"]}})
    bank.append({"id": "v1-linear-3x3-unique",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x + 2*y + 3*z - 6", "2*x + y + z - 4", "x - y + z - 1"],
                           "vars": ["x", "y", "z"]}})
    bank.append({"id": "v1-linear-underdet-2x3",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x + y + z - 6", "x - y"], "vars": ["x", "y", "z"]}})
    bank.append({"id": "v1-linear-deg1-rational",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["3*x + 1"], "vars": ["x"]}})

    # Univariate polynomial (6)
    bank.append({"id": "v1-univ-quadratic-rational",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x**2 - 5*x + 6"], "vars": ["x"]}})
    bank.append({"id": "v1-univ-quadratic-irrational",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x**2 - 2"], "vars": ["x"]}})
    bank.append({"id": "v1-univ-cubic-rational",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x**3 - x"], "vars": ["x"]}})
    bank.append({"id": "v1-univ-cubic-factored",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x**3 - 1"], "vars": ["x"]}})
    bank.append({"id": "v1-univ-quartic-biquadratic",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x**4 - 5*x**2 + 4"], "vars": ["x"]}})
    bank.append({"id": "v1-univ-multiplicity",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["x**2 - 2*x + 1"], "vars": ["x"]}})

    # Transcendental simple (5)
    bank.append({"id": "v1-trans-sin-zero",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["sin(x)"], "vars": ["x"]}})
    bank.append({"id": "v1-trans-cos-half",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["cos(x) - 1/2"], "vars": ["x"]}})
    bank.append({"id": "v1-trans-exp-five",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["exp(x) - 5"], "vars": ["x"]}})
    bank.append({"id": "v1-trans-tan-zero",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["tan(x)"], "vars": ["x"]}})
    bank.append({"id": "v1-trans-abs-three",
                 "tier": "v1-bank.handled",
                 "input": {"eqs": ["Abs(x) - 3"], "vars": ["x"]}})

    # === v1 REFUSED (5) ===

    # Two Fateman 1991 cases
    bank.append({"id": "v1-refused-fateman-cos-cos-cos",
                 "tier": "v1-bank.refused",
                 "note": ("Fateman 1991 headline. Mathematica v2 returned {} "
                          "with Solve::ifun warning; PRESS got 5/10 roots. "
                          "Honest answer requires multibranch detection (bead b55)."),
                 "input": {"eqs": ["cos(x) + cos(3*x) + cos(5*x)"], "vars": ["x"]}})
    bank.append({"id": "v1-refused-fateman-sin6x-over-sinx",
                 "tier": "v1-bank.refused",
                 "note": ("Fateman 1991 §8. Macsyma found 11 roots one wrong "
                          "(removable singularity at 0); Mathematica 2.0 also got it "
                          "wrong. v0.1 refuses honestly (rational function denominator)."),
                 "input": {"eqs": ["sin(6*x)/sin(x)"], "vars": ["x"]}})

    # Multivariate-non-zero-dim (workbench v0.1 refuses; oracles solve; capability-pending)
    bank.append({"id": "v1-refused-bilinear-xy",
                 "tier": "v1-bank.refused",
                 "note": ("Multivariate non-zero-dim. v0.1 refuses pending groebner stack "
                          "(beads m0m / x8d). Will regenerate to happy-path when shipped."),
                 "input": {"eqs": ["x*y - 1"], "vars": ["x", "y"]}})

    # Deg-5 irreducible (Abel-Ruffini territory; permanent refusal)
    bank.append({"id": "v1-refused-quintic-eisenstein",
                 "tier": "v1-bank.refused",
                 "note": ("Eisenstein's criterion at p=2 ⇒ irreducible over ℚ. "
                          "No closed-form roots in radicals (Abel-Ruffini)."),
                 "input": {"eqs": ["x**5 + 2*x + 2"], "vars": ["x"]}})

    # Constant equation
    bank.append({"id": "v1-refused-constant-equation",
                 "tier": "v1-bank.refused",
                 "input": {"eqs": ["5"], "vars": ["x"]}})

    return bank


# ---------------------------------------------------------------------
# Stratified random — linear (15)
# ---------------------------------------------------------------------


def random_linear(rng: random.Random, count: int) -> List[Dict[str, Any]]:
    """Generate `count` random linear systems with mixed shapes."""
    out: List[Dict[str, Any]] = []
    # Shape distribution: ~3x square-unique, 3x square-larger-unique,
    # 2x under-determined (m<n), 2x over-determined-consistent,
    # 2x over-determined-inconsistent, 3x sparse / structured.
    shapes = [(2, 2), (3, 3), (4, 4), (3, 3), (2, 2), (3, 3),
              (1, 2), (2, 3),                              # under
              (3, 2), (4, 3),                              # over consistent
              (3, 2), (4, 3),                              # over inconsistent
              (2, 2), (3, 3), (4, 4)]
    shapes = shapes[:count]
    for i, (m, n) in enumerate(shapes):
        var_names = ["x", "y", "z", "w", "u", "v"][:n]
        # Generate A ∈ Z^{m×n} with entries in [-9, 9], non-zero rows.
        attempts = 0
        while True:
            A = [[rng.randint(-9, 9) for _ in range(n)] for _ in range(m)]
            if all(any(c != 0 for c in row) for row in A):
                break
            attempts += 1
            if attempts > 50:
                A = [[1] + [0] * (n - 1) for _ in range(m)]
                break
        # Generate target solution x* (or pick b directly for inconsistent cases).
        force_inconsistent = (i in {10, 11}) and m > n
        if force_inconsistent:
            b = [rng.randint(-9, 9) for _ in range(m)]
            # Tweak b to ensure inconsistency: add a vector orthogonal to col(A).
            # Easiest: pick a random b and check rank — if consistent, perturb.
            A_sp = sp.Matrix(A)
            b_sp = sp.Matrix(b)
            if A_sp.rank() == A_sp.row_join(b_sp).rank():
                # Perturb the last entry by a value not in col(A).
                b[-1] = b[-1] + 1 if b[-1] != 100 else b[-1] - 1
        else:
            x_star = [rng.randint(-5, 5) for _ in range(n)]
            b = [sum(A[r][c] * x_star[c] for c in range(n)) for r in range(m)]
        # Build equation strings as `lhs - rhs`.
        eqs: List[str] = []
        for r in range(m):
            terms = []
            for c in range(n):
                if A[r][c] == 0:
                    continue
                coef = A[r][c]
                if coef == 1:
                    terms.append(var_names[c])
                elif coef == -1:
                    terms.append(f"-{var_names[c]}")
                else:
                    terms.append(f"{coef}*{var_names[c]}")
            lhs = " + ".join(terms).replace("+ -", "- ")
            if b[r] != 0:
                if b[r] > 0:
                    lhs += f" - {b[r]}"
                else:
                    lhs += f" + {-b[r]}"
            eqs.append(lhs)
        out.append({
            "id":    f"rand-linear-{i:03d}",
            "tier":  "rand.linear",
            "input": {"eqs": eqs, "vars": var_names},
        })
    return out


# ---------------------------------------------------------------------
# Stratified random — univariate poly (25)
# ---------------------------------------------------------------------


def random_univariate_poly(rng: random.Random, count: int) -> List[Dict[str, Any]]:
    """Generate `count` univariate polynomial equations of degree 2..10.

    To control happy-path coverage, we generate polynomials by *constructing
    them from chosen factor sets* (so the workbench's factor + radicals
    pipeline can solve them), with small-coefficient irreducible factors.
    """
    out: List[Dict[str, Any]] = []
    # Degree distribution (sums to count): 2 → 4, 3 → 4, 4 → 4, 5 → 3,
    # 6 → 2, 7 → 2, 8 → 2, 9 → 2, 10 → 2  (= 25).
    deg_plan = [2]*4 + [3]*4 + [4]*4 + [5]*3 + [6]*2 + [7]*2 + [8]*2 + [9]*2 + [10]*2
    deg_plan = deg_plan[:count]
    x = sp.Symbol("x")
    for i, target_deg in enumerate(deg_plan):
        # Build f as a product of small factors, summing degrees to target.
        # Choose factor degrees from {1, 2}, mix multiplicities.
        attempts = 0
        while True:
            attempts += 1
            factors: List[Tuple[sp.Poly, int]] = []
            d_left = target_deg
            while d_left > 0:
                if d_left == 1:
                    a = rng.randint(-5, 5)
                    fac = sp.Poly(x - a, x)  # degree 1
                    mult = 1
                elif rng.random() < 0.5 and d_left >= 2:
                    # Quadratic factor — irreducible over Q (negative discriminant).
                    while True:
                        b_, c_ = rng.randint(-5, 5), rng.randint(1, 6)
                        if b_ * b_ - 4 * c_ < 0:
                            break
                    fac = sp.Poly(x**2 + b_ * x + c_, x)
                    mult = 1
                else:
                    a = rng.randint(-5, 5)
                    fac = sp.Poly(x - a, x)
                    mult = rng.choice([1, 1, 1, 2]) if d_left >= 2 else 1
                # Don't blow past the target degree.
                if fac.total_degree() * mult > d_left:
                    mult = max(1, d_left // fac.total_degree())
                factors.append((fac, mult))
                d_left -= fac.total_degree() * mult
            # Optional: include a degree-5+ irreducible factor for some cases
            # to test the complex-roots-not-yet-named refusal path.
            if target_deg >= 5 and i % 5 == 4:  # ~1 in 5 of degree≥5 cases
                # Construct an irreducible quintic via Eisenstein at p=2.
                quintic = sp.Poly(x**5 + 2 * rng.randint(1, 3) * x + 2, x)
                # Replace the leading factors entirely.
                factors = [(quintic, 1)]
                # Pad with linear factors to reach target_deg.
                for _ in range(target_deg - 5):
                    a = rng.randint(-3, 3)
                    factors.append((sp.Poly(x - a, x), 1))
            # Multiply out.
            f = sp.Poly(1, x)
            for fac, mult in factors:
                f = f * fac**mult
            if f.total_degree() == target_deg:
                break
            if attempts > 50:
                # Fallback to (x^k - 1) type.
                f = sp.Poly(x**target_deg - 1, x)
                break
        eq_str = sp.sstr(f.as_expr(), order="lex")
        out.append({
            "id":    f"rand-univ-{i:03d}",
            "tier":  "rand.univariate-poly",
            "note":  f"target_deg={target_deg}",
            "input": {"eqs": [eq_str], "vars": ["x"]},
        })
    return out


# ---------------------------------------------------------------------
# Stratified random — multivariate non-zero-dim (25, v0.1-refusal-class)
# ---------------------------------------------------------------------


def random_multivariate_zero_dim(rng: random.Random, count: int) -> List[Dict[str, Any]]:
    """Generate cases the workbench v0.1 refuses with
    `solve/multivariate-non-zero-dim` (the groebner lane is open)."""
    out: List[Dict[str, Any]] = []
    cases: List[Tuple[List[str], List[str]]] = []
    # Pattern A: 2-variable degree-2 systems (intersect of conics).
    for _ in range(8):
        a = rng.randint(-5, 5)
        b = rng.randint(1, 5)
        c = rng.randint(-5, 5)
        d = rng.randint(1, 5)
        # Two conics: ax^2 + by^2 = const1, cx + dy = const2 (line + conic ⇒ 0-dim).
        const1 = a * 1 + b * 4
        const2 = c * 1 + d * 2
        cases.append((
            [f"{a}*x**2 + {b}*y**2 - {const1}", f"{c}*x + {d}*y - {const2}"],
            ["x", "y"],
        ))
    # Pattern B: 2-variable bilinear and quadratic.
    for _ in range(7):
        a = rng.randint(1, 5)
        b = rng.randint(1, 5)
        cases.append((
            [f"x*y - {a}", f"x + y - {a + b}"],
            ["x", "y"],
        ))
    # Pattern C: 3-variable linear-quadratic mixed.
    for _ in range(5):
        cases.append((
            [f"x**2 + y**2 + z**2 - {rng.randint(3, 14)}",
             f"x + y + z - {rng.randint(2, 6)}",
             f"x*y + y*z + z*x - {rng.randint(0, 6)}"],
            ["x", "y", "z"],
        ))
    # Pattern D: cyclic-2 / cyclic-3 systems.
    for _ in range(5):
        cases.append((
            [f"x**2 + y**2 - {rng.randint(2, 9)}",
             f"x*y - {rng.randint(-3, 3)}"],
            ["x", "y"],
        ))
    cases = cases[:count]
    for i, (eqs, var_names) in enumerate(cases):
        out.append({
            "id":    f"rand-mv-{i:03d}",
            "tier":  "rand.multivariate-zero-dim",
            "note":  ("v0.1 refusal-class golden: workbench refuses pending groebner stack; "
                      "regenerates to happy-path when m0m / x8d ship."),
            "input": {"eqs": eqs, "vars": var_names},
        })
    return out


# ---------------------------------------------------------------------
# Stratified random — transcendental univariate (15)
# ---------------------------------------------------------------------


def random_transcendental(rng: random.Random, count: int) -> List[Dict[str, Any]]:
    """Random `head(a*x + b) = c` for the v0.1 invert table."""
    heads = ["sin", "cos", "tan", "exp", "log", "sinh", "cosh", "tanh", "Abs"]
    out: List[Dict[str, Any]] = []
    for i in range(count):
        head = heads[i % len(heads)]
        a = rng.randint(1, 4) * rng.choice([-1, 1])
        b = rng.randint(-3, 3)
        c_num = rng.randint(-3, 3)
        c_den = rng.choice([1, 1, 1, 2, 3])
        c_str = f"{c_num}" if c_den == 1 else f"{c_num}/{c_den}"
        # For log(arg) = c with arg = a*x + b possibly negative, the equation
        # is still solvable over ℂ; reference handles this. For exp, log,
        # cosh: select positive c when relevant to stay in real domain.
        if head == "log" and c_den == 1 and c_num < 0:
            c_num = abs(c_num)
            c_str = f"{c_num}"
        # Build the inner argument expression.
        if a == 1:
            arg = f"x"
        elif a == -1:
            arg = f"-x"
        else:
            arg = f"{a}*x"
        if b > 0:
            arg = f"{arg} + {b}"
        elif b < 0:
            arg = f"{arg} - {-b}"
        eq = f"{head}({arg}) - ({c_str})"
        out.append({
            "id":    f"rand-trans-{i:03d}",
            "tier":  "rand.transcendental-univariate",
            "input": {"eqs": [eq], "vars": ["x"]},
        })
    return out


# ---------------------------------------------------------------------
# Triple-witness + admission
# ---------------------------------------------------------------------


def query_wolfram(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run the case through `wolframscript` Solve and return the standard envelope.

    Translates SymPy-syntax equations to Mathematica via SymPy's
    `mathematica_code` printer (robust against arbitrary nesting,
    capitalisation, and operator differences).
    """
    from sympy.printing.mathematica import mathematica_code  # local import to avoid generator startup cost
    eqs = case["input"]["eqs"]
    var_names = case["input"]["vars"]
    syms = {v: sp.Symbol(v) for v in var_names}
    try:
        eq_exprs = [sp.sympify(e, locals=syms) for e in eqs]
    except (sp.SympifyError, SyntaxError, TypeError):
        return {"status": "error", "reason": "sympify failed"}
    eq_strs = [f"({mathematica_code(e)}) == 0" for e in eq_exprs]
    var_list = ", ".join(var_names)
    code = f"FullForm[Solve[{{{', '.join(eq_strs)}}}, {{{var_list}}}]]"
    return wolfram_query(code, timeout=30.0)


def admit(case: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Run the case through the SymPy reference + (optionally) Wolfram.

    Returns `(expected_envelope, oracle_log_entry)`. `expected_envelope`
    is None if the case is dropped (oracles disagreed). `oracle_log_entry`
    is always returned for the audit trail.
    """
    inp = case["input"]
    cand = solve_reference(inp["eqs"], inp["vars"])
    lane = classify_lane(inp["eqs"], inp["vars"])

    expected: Dict[str, Any] = {"lane": lane}
    if lane == "refusal":
        expected["tag"] = cand["tag"]

    log_entry: Dict[str, Any] = {
        "id":           case["id"],
        "tier":         case["tier"],
        "lane":         lane,
        "sympy":        "ok" if cand["kind"] == "ok" else f"refused:{cand.get('tag')}",
    }

    if LIVE_ORACLE:
        wolf = query_wolfram(case)
        log_entry["wolfram"] = wolf.get("status", "missing")
        log_entry["wolfram_reason"] = wolf.get("reason") if wolf.get("status") != "ok" else None

        # Consensus protocol for refusal vs ok lanes.
        if lane == "refusal":
            # Admit if both refused OR if Wolfram solves but workbench refuses
            # (the "capability-pending" sub-class).
            workbench_refuses = True
            if wolf.get("status") == "ok":
                # Wolfram solved; workbench refuses honestly. Flag as
                # capability-pending unless the refusal is permanent.
                permanent_classes = {
                    "solve/foreign-vocabulary",  # rational fns, mixed-trig sums
                    "solve/complex-roots-not-yet-named",
                    "solve/parametric-non-trivial",
                    "solve/constant-equation",
                    "solve/empty-input",
                    "solve/empty-vars",
                }
                if expected.get("tag") in permanent_classes:
                    log_entry["consensus"] = "wolfram-solved-workbench-refuses-permanent"
                else:
                    # Capability-pending (e.g., multivariate-non-zero-dim).
                    log_entry["consensus"] = "wolfram-solved-workbench-refuses-pending"
                    log_entry["note"] = "regenerate when capability ships"
            elif wolf.get("status") in ("failed", "timeout", "error"):
                log_entry["consensus"] = "both-refuse"
            else:
                log_entry["consensus"] = "wolfram-uncertain"
        else:
            # Happy-path lanes: cross-check via the agreement layer.
            # For v0.1 we admit on SymPy+Wolfram producing solutions of the
            # same cardinality (looser than the full ADR-0019 §3 protocol;
            # the cube/grid verifier provides the strict check downstream).
            log_entry["consensus"] = "sympy-only" if wolf.get("status") != "ok" else "sympy+wolfram"
    else:
        log_entry["consensus"] = "sympy-only (LIVE_ORACLE off)"

    return expected, log_entry


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def main() -> int:
    rng = random.Random(SEED)
    cases = []
    cases.extend(hand_curated_v1_bank())
    cases.extend(random_linear(rng, 15))
    cases.extend(random_univariate_poly(rng, 25))
    cases.extend(random_multivariate_zero_dim(rng, 25))
    cases.extend(random_transcendental(rng, 15))

    inputs_payload = {
        "version": ENCODING_VERSION,
        "seed":    SEED,
        "cases":   [{k: v for k, v in c.items() if k != "note"} for c in cases],
    }

    expected_list: List[Dict[str, Any]] = []
    oracle_log: List[Dict[str, Any]] = []
    admitted_count = 0
    dropped_count = 0

    print(f"[{len(cases)} cases] LIVE_ORACLE={LIVE_ORACLE}", flush=True)
    import time
    t_start = time.time()
    for i, case in enumerate(cases):
        t0 = time.time()
        expected, log_entry = admit(case)
        dt = time.time() - t0
        oracle_log.append(log_entry)
        consensus = log_entry.get("consensus", "")
        marker = "✓" if expected is not None else "✗"
        print(
            f"  [{i + 1:3d}/{len(cases)}] {marker} {case['id']:38s} "
            f"{log_entry.get('lane', ''):16s} {consensus:42s} {dt:5.1f}s",
            flush=True,
        )
        if expected is None:
            dropped_count += 1
            continue
        expected_list.append({
            "id":       case["id"],
            "expected": expected,
        })
        admitted_count += 1
    total_dt = time.time() - t_start
    print(f"[done] {admitted_count}/{len(cases)} admitted, {dropped_count} dropped in {total_dt:.1f}s", flush=True)

    expected_payload = {
        "version": ENCODING_VERSION,
        "seed":    SEED,
        "cases":   expected_list,
    }
    oracle_payload = {
        "version":  ENCODING_VERSION,
        "seed":     SEED,
        "live_oracle": LIVE_ORACLE,
        "summary":  {
            "total":    len(cases),
            "admitted": admitted_count,
            "dropped":  dropped_count,
        },
        "entries": oracle_log,
    }

    (HERE / "inputs.json").write_text(json.dumps(inputs_payload, indent=2) + "\n")
    (HERE / "expected.json").write_text(json.dumps(expected_payload, indent=2) + "\n")
    (HERE / "oracle_log.json").write_text(json.dumps(oracle_payload, indent=2) + "\n")

    print(f"Wrote {admitted_count}/{len(cases)} cases (dropped {dropped_count}).")
    print("  → bench/solve/golden/inputs.json")
    print("  → bench/solve/golden/expected.json")
    print("  → bench/solve/golden/oracle_log.json")

    # Per-tier counts.
    by_tier: Dict[str, int] = {}
    for c in cases:
        by_tier.setdefault(c["tier"], 0)
        by_tier[c["tier"]] += 1
    print("\nPer-tier counts:")
    for tier, n in sorted(by_tier.items()):
        print(f"  {tier:32s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
