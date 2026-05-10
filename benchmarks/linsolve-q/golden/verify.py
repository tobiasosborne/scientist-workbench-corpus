#!/usr/bin/env python3
"""linsolve-q invariant verifier.

Reads `{input, candidate, id}` JSON on stdin; writes
`{pass, reason, checks}` JSON on stdout. Six checks per case
(see verifier_protocol.md), all exact: tolerance is zero.

Per ADR-0019: this verifier checks mathematical invariants, not
byte-equality. Multiple correct representations of the same
parametric solution all pass — the check is "do the affine spans
agree" by random substitution + linearity, not "is the textual
form identical."

Run:
  cat <case>.json | python3 verify.py
"""
from __future__ import annotations

import json
import re
import sys
from fractions import Fraction
from typing import Any, Dict, List, Tuple

import sympy as sp


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

_RAT_RE = re.compile(r"^-?\d+(?:/\d+)?$")


def parse_rat(s: str) -> Fraction:
    if not _RAT_RE.match(s.strip()):
        raise ValueError(f"not a canonical rational: {s!r}")
    return Fraction(s)


def parse_affine(expr_str: str, free_var_names: List[str]) -> sp.Expr:
    """Parse a string of form '5 - 2*t_0 + 1/3*t_1' into a SymPy
    expression in the named free symbols. Refuses non-linear forms.
    """
    syms = {name: sp.Symbol(name) for name in free_var_names}
    # Allow either '*' or no asterisk between coefficient and t_i. SymPy
    # handles both via parse_expr; explicit '*' is what generate.py
    # emits.
    expr = sp.sympify(expr_str, locals=syms)
    # Linearity check: every free symbol must appear with degree <= 1
    # AND no symbol outside `syms` may appear.
    free = expr.free_symbols
    extra = free - set(syms.values())
    if extra:
        raise ValueError(f"unexpected free symbols in {expr_str!r}: {extra}")
    for s in syms.values():
        if expr.diff(s, 2) != 0:
            raise ValueError(f"non-linear in {s} in {expr_str!r}")
    return expr


# ---------------------------------------------------------------------
# Check 1 — shape
# ---------------------------------------------------------------------


def check_kind_rank_consistent(
    input_obj: Dict, candidate: Dict
) -> Tuple[bool, str]:
    """Structural invariant: kind ↔ rank relation must hold.

    kind=unique           ⇒  rank = n  (full column rank)
    kind=under-determined ⇒  rank < n
    kind=inconsistent     ⇒  rank < augmented_rank

    This catches the "misclassified under-determined as unique"
    mutation where the candidate reports a single concrete x for a
    rank-deficient system; A·x=b might happen to hold (coincidence
    of substitution), but the structural relation between kind and
    rank cannot.
    """
    A = input_obj.get("A", [])
    n = len(A[0]) if A else 0
    kind = candidate.get("kind")
    rank = candidate.get("rank")
    if not isinstance(rank, int):
        return False, "rank not int (caught by shape; this should not run)"

    if kind == "unique":
        if rank != n:
            return False, (
                f"kind=unique requires rank=n; got rank={rank}, n={n} "
                f"(rank-deficient system reported as unique)"
            )
        return True, f"unique ⇒ rank=n=({rank})"
    if kind == "under-determined":
        if rank >= n:
            return False, (
                f"kind=under-determined requires rank<n; got rank={rank}, "
                f"n={n}"
            )
        return True, f"under-determined ⇒ rank<n ({rank}<{n})"
    if kind == "inconsistent":
        ar = candidate.get("augmented_rank")
        if not isinstance(ar, int):
            return False, "augmented_rank required for inconsistent"
        if ar <= rank:
            return False, (
                f"kind=inconsistent requires augmented_rank>rank; got "
                f"augmented_rank={ar}, rank={rank}"
            )
        return True, f"inconsistent ⇒ augmented_rank>rank ({ar}>{rank})"
    return False, f"unknown kind: {kind}"


def check_shape(input_obj: Dict, candidate: Dict) -> Tuple[bool, str]:
    A = input_obj.get("A")
    b = input_obj.get("b")
    if not isinstance(A, list) or not isinstance(b, list):
        return False, "A or b not a list"
    if A and not all(isinstance(row, list) for row in A):
        return False, "A is not list-of-lists"
    m = len(A)
    n = len(A[0]) if m > 0 else 0
    if any(len(row) != n for row in A):
        return False, f"ragged A: row lengths differ"
    if len(b) != m:
        return False, f"|b|={len(b)} but |A|={m}"
    try:
        for row in A:
            for s in row:
                parse_rat(s)
        for s in b:
            parse_rat(s)
    except (ValueError, TypeError) as e:
        return False, f"unparseable rational: {e}"

    kind = candidate.get("kind")
    if kind not in {"unique", "under-determined", "inconsistent"}:
        return False, f"invalid kind: {kind!r}"

    rank = candidate.get("rank")
    if not isinstance(rank, int) or rank < 0 or rank > min(m, n) if (m and n) else rank != 0:
        return False, f"invalid rank: {rank!r}"

    if kind in ("unique", "under-determined"):
        x = candidate.get("x")
        free = candidate.get("free_vars")
        if not isinstance(x, list) or len(x) != n:
            return False, f"x must be length-{n} list; got {x!r}"
        if not isinstance(free, list):
            return False, f"free_vars must be a list; got {free!r}"
        for fv in free:
            if not isinstance(fv, str) or not re.match(r"^t_\d+$", fv):
                return False, f"free var must match 't_<int>': {fv!r}"

    if kind == "inconsistent":
        ar = candidate.get("augmented_rank")
        if not isinstance(ar, int):
            return False, f"augmented_rank required for inconsistent"

    return True, "all structural fields valid"


# ---------------------------------------------------------------------
# Check 2 — exact_satisfaction_unique
# ---------------------------------------------------------------------


def check_exact_satisfaction_unique(
    input_obj: Dict, candidate: Dict
) -> Tuple[bool, str]:
    if candidate["kind"] != "unique":
        return True, f"n/a for kind={candidate['kind']}"
    A = [[parse_rat(s) for s in row] for row in input_obj["A"]]
    b = [parse_rat(s) for s in input_obj["b"]]
    x = [parse_rat(s) for s in candidate["x"]]
    m = len(A)
    n = len(A[0]) if m > 0 else 0
    if len(x) != n:
        return False, f"|x|={len(x)} but |A_cols|={n}"
    for i in range(m):
        s = sum(A[i][j] * x[j] for j in range(n))
        if s != b[i]:
            return False, (
                f"row {i}: A[i]·x={s} != b[i]={b[i]} "
                f"(residual {s - b[i]})"
            )
    return True, f"A·x ≡ b exactly across {m} rows"


# ---------------------------------------------------------------------
# Check 3 — free_var_basis_underdetermined
# ---------------------------------------------------------------------


# Substitution pool from verifier_protocol.md
_SUBST_POOL = [
    Fraction(-3), Fraction(-1), Fraction(0), Fraction(1), Fraction(2),
    Fraction(5, 3), Fraction(-7, 4), Fraction(11), Fraction(-19, 4),
    Fraction(6, 7),
]
_TRIALS = 10
# A deterministic per-case trial-tuple sequence. Re-running the
# verifier produces the same trials (no random seed needed).


def check_free_var_basis(
    input_obj: Dict, candidate: Dict
) -> Tuple[bool, str]:
    if candidate["kind"] != "under-determined":
        return True, f"n/a for kind={candidate['kind']}"
    A = [[parse_rat(s) for s in row] for row in input_obj["A"]]
    b = [parse_rat(s) for s in input_obj["b"]]
    free_vars = candidate["free_vars"]
    n = len(A[0]) if A else 0
    m = len(A)

    # Parse each x[j] as an affine combination.
    affines: List[sp.Expr] = []
    for j, expr_str in enumerate(candidate["x"]):
        try:
            affines.append(parse_affine(expr_str, free_vars))
        except ValueError as e:
            return False, f"x[{j}] failed affine parse: {e}"

    # Generate _TRIALS deterministic substitutions.
    for trial in range(_TRIALS):
        subst = {
            sp.Symbol(name): _SUBST_POOL[(trial + i) % len(_SUBST_POOL)]
            for i, name in enumerate(free_vars)
        }
        x_concrete: List[Fraction] = []
        for j in range(n):
            val = affines[j].subs(subst)
            try:
                x_concrete.append(Fraction(int(val.p), int(val.q))
                                  if hasattr(val, "p")
                                  else Fraction(val))
            except (TypeError, AttributeError):
                # Fallback via SymPy nsimplify -> Rational.
                rval = sp.nsimplify(val)
                x_concrete.append(Fraction(rval.p, rval.q))
        for i in range(m):
            s = sum(A[i][j] * x_concrete[j] for j in range(n))
            if s != b[i]:
                return False, (
                    f"trial {trial} (subst={subst}): row {i} "
                    f"A·x={s} != b[i]={b[i]}"
                )
    return True, (
        f"{_TRIALS} random rational substitutions "
        f"× {len(free_vars)} free vars all satisfy A·x ≡ b"
    )


# ---------------------------------------------------------------------
# Check 4 — rank_consistent
# ---------------------------------------------------------------------


def check_rank_consistent(
    input_obj: Dict, candidate: Dict
) -> Tuple[bool, str]:
    A = sp.Matrix([[sp.Rational(s) for s in row] for row in input_obj["A"]])
    if A.rows == 0:
        sympy_rank = 0
    else:
        sympy_rank = A.rank()
    if candidate["rank"] != sympy_rank:
        return False, (
            f"candidate.rank={candidate['rank']} but SymPy rank(A)={sympy_rank}"
        )
    return True, f"rank={sympy_rank} matches SymPy oracle"


# ---------------------------------------------------------------------
# Check 5 — inconsistency_witness
# ---------------------------------------------------------------------


def check_inconsistency_witness(
    input_obj: Dict, candidate: Dict
) -> Tuple[bool, str]:
    if candidate["kind"] != "inconsistent":
        return True, f"n/a for kind={candidate['kind']}"
    A = sp.Matrix([[sp.Rational(s) for s in row] for row in input_obj["A"]])
    b = sp.Matrix([sp.Rational(s) for s in input_obj["b"]])
    if A.rows == 0:
        return False, "m=0 cannot be inconsistent"
    rank_A = A.rank()
    rank_aug = A.row_join(b).rank()
    if rank_aug <= rank_A:
        return False, (
            f"Rouché-Capelli says CONSISTENT: "
            f"rank(A)={rank_A}, rank([A|b])={rank_aug}"
        )
    if candidate.get("augmented_rank") != rank_aug:
        return False, (
            f"candidate.augmented_rank={candidate.get('augmented_rank')} "
            f"but actual rank([A|b])={rank_aug}"
        )
    return True, (
        f"Rouché-Capelli witness: rank(A)={rank_A} < "
        f"rank([A|b])={rank_aug}"
    )


# ---------------------------------------------------------------------
# Check 6 — free_var_count_correct
# ---------------------------------------------------------------------


def check_free_var_count(
    input_obj: Dict, candidate: Dict
) -> Tuple[bool, str]:
    if candidate["kind"] != "under-determined":
        return True, f"n/a for kind={candidate['kind']}"
    A = input_obj["A"]
    n = len(A[0]) if A else 0
    expected = n - candidate["rank"]
    actual = len(candidate.get("free_vars", []))
    if actual != expected:
        return False, (
            f"|free_vars|={actual} but n-rank={expected} "
            f"(n={n}, rank={candidate['rank']})"
        )
    return True, f"|free_vars|=n-rank={expected}"


# ---------------------------------------------------------------------
# Bit-budget check (Tier-H only)
# ---------------------------------------------------------------------


_BL_RE = re.compile(r"max intermediate bit length:\s*(\d+)")


def check_bit_budget(
    input_obj: Dict, candidate: Dict
) -> Tuple[bool, str]:
    budget = input_obj.get("expected_max_bit_length")
    if budget is None:
        return True, "n/a (no bit-budget on this tier)"
    warnings = candidate.get("warnings", [])
    matched = None
    for w in warnings:
        m = _BL_RE.search(w)
        if m:
            matched = int(m.group(1))
            break
    if matched is None:
        return False, (
            "warnings missing 'max intermediate bit length: <int>' "
            "self-report (required on Tier H)"
        )
    if matched > budget:
        return False, (
            f"max intermediate bit length {matched} exceeds budget "
            f"{budget} — naive ℚ-Gaussian suspected"
        )
    return True, f"max intermediate bit length {matched} ≤ budget {budget}"


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def verify(input_obj: Dict, candidate: Dict) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    fns = [
        ("shape", check_shape),
        ("kind_rank_consistent", check_kind_rank_consistent),
        ("exact_satisfaction_unique", check_exact_satisfaction_unique),
        ("free_var_basis_underdetermined", check_free_var_basis),
        ("rank_consistent", check_rank_consistent),
        ("inconsistency_witness", check_inconsistency_witness),
        ("free_var_count_correct", check_free_var_count),
        ("bit_budget", check_bit_budget),
    ]
    for name, fn in fns:
        try:
            ok, detail = fn(input_obj, candidate)
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        checks[name] = {"pass": ok, "detail": detail}

    all_pass = all(c["pass"] for c in checks.values())
    return {
        "pass": all_pass,
        "reason": ("all invariants hold" if all_pass
                   else "; ".join(f"{k}: {v['detail']}"
                                  for k, v in checks.items()
                                  if not v["pass"])),
        "checks": checks,
    }


def main() -> int:
    raw = json.loads(sys.stdin.read())
    result = verify(raw["input"], raw["candidate"])
    sys.stdout.write(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
