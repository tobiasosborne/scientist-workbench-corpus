#!/usr/bin/env python3
"""SymPy-backed reference implementation of `tools/solve`.

The reference produces the workbench's output envelope shape per
ADR-0017: either `{kind: 'ok', vars, solutions, completeness, warnings}`
for happy-path or `{kind: 'tagged', tag, payload}` for boundary
refusals. Solutions follow the flat shape (multiplicity ⇒ repetition);
branches are parameterised by `t_0, t_1, …` symbols matching the
workbench's bareiss linear lane and `tryTranscendentalInvert` table.

Per ADR-0019, this reference is the bench's primary oracle. It is
*not* the workbench candidate; it is the SymPy-side mirror of the
same dispatch logic, used to:

  1. Generate `expected.json` for `bench/solve/golden/`.
  2. Cross-validate against Wolfram via the triple-witness protocol.
  3. Provide a known-good baseline for the mutation-prove harness
     (`test_mutations.py` perturbs *this* output to demonstrate RED).

Lanes (mirroring `packages/solve` + `tools/solve/tool.ts`):

  • linear            — SymPy `linsolve(A, b)` ⇒ unique / underdetermined / inconsistent
  • univariate-poly   — `Poly.factor_list()` per ℚ-irreducible; per-degree closed-form roots
  • transcendental    — pattern-match `head(a·x + b) = c`; emit branched bindings
  • refusal           — classify and emit `tagged "solve/<class>"`

The output JSON-shape is:

  {
    "kind": "ok",
    "vars": ["x", "y"],
    "solutions": [
      {
        "bindings": [{"var": "x", "value": "<sympy-str>"}],
        "branches": ["t_0"]
      }
    ],
    "completeness": "complete" | "finite-rep-of-infinite",
    "warnings": []
  }

  // or
  {
    "kind": "tagged",
    "tag":  "solve/<class>",
    "payload": {"detail": "..."}
  }
"""
from __future__ import annotations

import sys
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

import sympy as sp


# ---------------------------------------------------------------------
# Refusal class roster
# ---------------------------------------------------------------------

CLS_COMPLEX_NOT_YET            = "solve/complex-roots-not-yet-named"
CLS_MULTIVARIATE_NON_ZERO_DIM = "solve/multivariate-non-zero-dim"
CLS_PARAMETRIC_NON_TRIVIAL    = "solve/parametric-non-trivial"
CLS_FOREIGN_VOCABULARY        = "solve/foreign-vocabulary"
CLS_CONSTANT_EQUATION         = "solve/constant-equation"
CLS_EMPTY_INPUT               = "solve/empty-input"
CLS_EMPTY_VARS                = "solve/empty-vars"
CLS_TRANSCENDENTAL_MULTIBRANCH = "solve/transcendental-multibranch"


# ---------------------------------------------------------------------
# Transcendental invert table — mirrors
# packages/solve/src/transcendental.ts
# ---------------------------------------------------------------------

# Each entry is: head_str -> {inverse: sympy expr (function of c), branches: list[branch_template]}
# A branch template is a string like "{inv} + 2*pi*t_0" with `{inv}` substituted at emit time.
# Multiple branches mean the head has multiple pre-images per period (sin/cos).

INVERT_TABLE: Dict[str, Dict[str, Any]] = {
    "exp": {
        "inverse": lambda c: sp.log(c),
        "branches": [("{inv}", [])],  # complete
    },
    "log": {
        "inverse": lambda c: sp.exp(c),
        "branches": [("{inv}", [])],
    },
    "sinh": {
        "inverse": lambda c: sp.asinh(c),
        "branches": [("{inv}", [])],
    },
    "tanh": {
        "inverse": lambda c: sp.atanh(c),
        "branches": [("{inv}", [])],
    },
    "cosh": {
        "inverse": lambda c: sp.acosh(c),
        "branches": [("{inv}", []), ("-({inv})", [])],  # complete (cosh is even)
    },
    "abs": {
        "inverse": lambda c: c,
        "branches": [("({inv})", []), ("-({inv})", [])],  # complete
    },
    "sin": {
        "inverse": lambda c: sp.asin(c),
        "branches": [
            ("{inv} + 2*pi*t_0", ["t_0"]),
            ("pi - ({inv}) + 2*pi*t_1", ["t_1"]),
        ],
    },
    "cos": {
        "inverse": lambda c: sp.acos(c),
        "branches": [
            ("{inv} + 2*pi*t_0", ["t_0"]),
            ("-({inv}) + 2*pi*t_1", ["t_1"]),
        ],
    },
    "tan": {
        "inverse": lambda c: sp.atan(c),
        "branches": [("{inv} + pi*t_0", ["t_0"])],
    },
}

# Heads in the table whose preimage is finite over R (the cube
# completeness check applies only to the periodic ones).
COMPLETENESS_FINITE: set[str] = {"exp", "log", "sinh", "tanh", "cosh", "abs"}


# ---------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------


def solve_reference(eqs: List[str], var_names: List[str]) -> Dict[str, Any]:
    """Reference solver mirroring `tools/solve`'s output shape.

    `eqs` are Python/SymPy parseable expression strings, each implicitly
    `eqs[i] == 0`. `var_names` are the unknowns to solve for.

    Returns a JSON-serialisable dict envelope.
    """
    if not eqs:
        return _refuse(CLS_EMPTY_INPUT, "no equations provided")
    if not var_names:
        return _refuse(CLS_EMPTY_VARS, "no variables to solve for")

    syms = {v: sp.Symbol(v) for v in var_names}
    parsed_eqs: List[sp.Expr] = []
    for i, eq_str in enumerate(eqs):
        try:
            e = sp.sympify(eq_str, locals=syms)
        except (sp.SympifyError, SyntaxError, TypeError) as exc:
            return _refuse(
                CLS_FOREIGN_VOCABULARY,
                f"equation #{i + 1} did not parse: {exc}",
            )
        parsed_eqs.append(e)

    # Single-equation single-variable transcendental fast path.
    if len(parsed_eqs) == 1 and len(var_names) == 1:
        trans = _try_transcendental_invert(parsed_eqs[0], var_names[0])
        if trans is not None:
            return trans

    # Classify by equation/variable shape. Two refusal classes resolved here:
    #   parametric-non-trivial (extra free symbol in eqs not in vars)
    #   foreign-vocabulary     (a non-polynomial atom in eqs that wasn't
    #                            caught by the transcendental fast path)
    classification = _classify(parsed_eqs, var_names)
    if classification["kind"] == "refusal":
        return _refuse(classification["tag"], classification["detail"])

    if classification["kind"] == "linear":
        return _solve_linear(classification["A"], classification["b"], var_names)

    if classification["kind"] == "univariate-poly":
        return _solve_univariate_poly(parsed_eqs[0], var_names[0])

    # Multivariate non-zero-dim or higher-than-zero-dim — refuse for v0.1.
    return _refuse(
        CLS_MULTIVARIATE_NON_ZERO_DIM,
        classification.get("detail", "multivariate input not in zero-dim lane"),
    )


# ---------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------


def _classify(eqs: List[sp.Expr], var_names: List[str]) -> Dict[str, Any]:
    """Classify the input into one of: linear, univariate-poly, multivariate, refusal."""
    syms = [sp.Symbol(v) for v in var_names]
    sym_set = set(syms)

    # Check for foreign vocabulary: any atom that isn't a number, an unknown
    # in `vars`, or a `**` power with integer exponent — and isn't covered by
    # the transcendental fast path (which we've already tried). Exception:
    # a symbol not in `vars` becomes parametric-non-trivial.
    for i, eq in enumerate(eqs):
        free = eq.free_symbols
        extra = free - sym_set
        if extra:
            return {
                "kind": "refusal",
                "tag": CLS_PARAMETRIC_NON_TRIVIAL,
                "detail": f"equation #{i + 1} has free symbol(s) {sorted(map(str, extra))} not in vars",
            }

    # Check that each equation reduces to a polynomial in `vars` over ℚ.
    polys: List[sp.Poly] = []
    for i, eq in enumerate(eqs):
        # Constant equation — no variables in the expression.
        if all(s not in eq.free_symbols for s in syms):
            simplified = sp.simplify(eq)
            if simplified == 0:
                # Trivially satisfied — degenerate case, treated as
                # parametric-non-trivial since there's nothing to solve.
                return {
                    "kind": "refusal",
                    "tag": CLS_CONSTANT_EQUATION,
                    "detail": f"equation #{i + 1} reduces to 0 = 0 (trivially satisfied)",
                }
            return {
                "kind": "refusal",
                "tag": CLS_CONSTANT_EQUATION,
                "detail": f"equation #{i + 1} reduces to constant {simplified} ≠ 0",
            }
        try:
            p = sp.Poly(eq, *syms, domain="QQ")
        except (sp.PolynomialError, sp.GeneratorsError, sp.CoercionFailed) as exc:
            # Atoms not polynomial in vars over Q — sin(x), 1/x, sqrt(x), etc.
            return {
                "kind": "refusal",
                "tag": CLS_FOREIGN_VOCABULARY,
                "detail": f"equation #{i + 1} is not a polynomial in vars over ℚ: {type(exc).__name__}: {exc}",
            }
        polys.append(p)

    # Check linearity — total degree ≤ 1 in vars and no cross-products.
    is_linear = True
    for p in polys:
        if p.total_degree() > 1:
            is_linear = False
            break
    if is_linear:
        # Build (A, b) via SymPy's `linear_eq_to_matrix`.
        A, b = sp.linear_eq_to_matrix(eqs, *syms)
        return {"kind": "linear", "A": A, "b": b}

    # Univariate polynomial: one equation, one variable, degree ≥ 1.
    if len(eqs) == 1 and len(var_names) == 1 and polys[0].total_degree() >= 1:
        return {"kind": "univariate-poly"}

    # Multivariate (non-linear) — refuse for v0.1.
    return {
        "kind": "multivariate",
        "detail": (
            f"{len(eqs)} equation(s) in {len(var_names)} variable(s) with "
            f"total degree {max(p.total_degree() for p in polys)}; "
            "multivariate-zero-dim lane (groebner) is not yet implemented in v0.1"
        ),
    }


# ---------------------------------------------------------------------
# Linear lane
# ---------------------------------------------------------------------


def _solve_linear(A: sp.Matrix, b: sp.Matrix, var_names: List[str]) -> Dict[str, Any]:
    """Solve `A·x = b` over ℚ, returning the workbench shape."""
    syms = [sp.Symbol(v) for v in var_names]

    # Use sympy.linsolve for the most uniform output. Returns a FiniteSet.
    try:
        sol_set = sp.linsolve((A, b), *syms)
    except (ValueError, TypeError) as exc:
        return _refuse(
            CLS_FOREIGN_VOCABULARY,
            f"linsolve raised {type(exc).__name__}: {exc}",
        )

    if not sol_set:
        # Empty FiniteSet ⇒ inconsistent.
        return _ok(var_names, solutions=[], completeness="complete")

    # FiniteSet has exactly one tuple (the parametric-or-unique solution).
    (sol_tuple,) = list(sol_set)

    # Identify free parameters. linsolve uses one of two conventions:
    #   (a) The parameter appears as one of the original `vars` itself —
    #       e.g., for `x + y - 3` in (x, y), the result is (3 - y, y),
    #       i.e. y is its own value. We rename those to t_0, t_1, ...
    #   (b) sympy auto-generates `tau0`, `tau1`, ... for cases where the
    #       parameter dimension exceeds the var count. We rename those too.
    branch_syms: List[sp.Symbol] = []
    rename_map: Dict[sp.Symbol, sp.Symbol] = {}

    # (a) Self-mapped vars first (in declared order, so t_0 corresponds
    # to the first self-mapped var).
    for s, val in zip(syms, sol_tuple):
        if val == s:
            new = sp.Symbol(f"t_{len(branch_syms)}")
            rename_map[s] = new
            branch_syms.append(new)

    # (b) Any other free symbols (sympy-generated tau symbols).
    extra_free = set().union(*(s.free_symbols for s in sol_tuple)) - set(syms) - set(rename_map.values())
    for fs in sorted(extra_free, key=str):
        new = sp.Symbol(f"t_{len(branch_syms)}")
        rename_map[fs] = new
        branch_syms.append(new)

    bindings: List[Dict[str, str]] = []
    for s, val in zip(syms, sol_tuple):
        new_val = sp.simplify(val.subs(rename_map))
        bindings.append({"var": str(s), "value": _expr_to_str(new_val)})

    branches = [str(s) for s in branch_syms]
    completeness = "complete" if not branches else "finite-rep-of-infinite"

    return _ok(
        var_names,
        solutions=[{"bindings": bindings, "branches": branches}],
        completeness=completeness,
    )


# ---------------------------------------------------------------------
# Univariate polynomial lane
# ---------------------------------------------------------------------


def _solve_univariate_poly(eq: sp.Expr, var: str) -> Dict[str, Any]:
    """Solve a single univariate polynomial equation over ℚ."""
    x = sp.Symbol(var)
    p = sp.Poly(eq, x, domain="QQ")
    if p.total_degree() == 0:
        const = sp.simplify(eq)
        if const == 0:
            return _refuse(CLS_CONSTANT_EQUATION, "equation reduces to 0 = 0")
        return _refuse(CLS_CONSTANT_EQUATION, f"equation reduces to {const} ≠ 0")

    # Factor over ℚ, dispatch each irreducible factor by degree.
    coeff, factors = p.factor_list()
    bindings: List[Dict[str, Any]] = []  # accumulate per-root, multiplicity-aware
    for fac, mult in factors:
        d = fac.total_degree()
        if d >= 5:
            # Workbench v0.2 (yoc): all-real ⇒ Root[poly, k] solutions
            # (one per real root × multiplicity); mixed-real-complex ⇒
            # refuse with `complex-roots-not-yet-named` (alg-num v0.1
            # names real algebraic numbers only). Reference oracle for
            # the all-real Root[]-emit path requires a Root[] canonical
            # formatter; mirroring the refusal half here covers the
            # bench's existing G-tier mixed-real-complex cases.
            real_count = len(sp.Poly(fac, x, domain="QQ").real_roots(multiple=True))
            return _refuse(
                CLS_COMPLEX_NOT_YET,
                f"factor of degree {d} has {d - real_count} complex root(s); "
                f"alg-num v0.1 names real algebraic numbers only",
            )
        # Get the closed-form roots of fac using sympy's Poly.all_roots.
        roots = sp.Poly(fac, x, domain="QQ").all_roots(multiple=True)
        # roots is length d (with multiplicity already 1 per factor); but
        # we need to multiply by `mult` to account for the factor's
        # multiplicity in p.
        for root in roots:
            for _ in range(mult):
                bindings.append({"var": var, "value": _expr_to_str(root)})

    solutions = [{"bindings": [b], "branches": []} for b in bindings]
    return _ok([var], solutions=solutions, completeness="complete")


# ---------------------------------------------------------------------
# Transcendental lane
# ---------------------------------------------------------------------


def _try_transcendental_invert(eq: sp.Expr, var: str) -> Optional[Dict[str, Any]]:
    """Pattern-match `head(linear in x) = c` and emit branched solutions.

    Returns the workbench-shaped envelope on a match, or None if the
    pattern doesn't apply (caller falls through to polynomial classifier).
    """
    x = sp.Symbol(var)

    # Decompose eq = expr_with_head - constant or constant - expr_with_head.
    # We try to identify a single applied-function term whose argument is
    # linear in x, and a constant residue.
    match = _decompose_head_eq_const(eq, x)
    if match is None:
        return None

    head_name, arg_expr, const_expr = match
    if head_name not in INVERT_TABLE:
        return None

    # arg_expr must be linear in x: arg_expr = a*x + b for some a, b in Q.
    arg_poly = sp.Poly(arg_expr, x, domain="QQ")
    if arg_poly.total_degree() != 1:
        return None
    coefs = arg_poly.all_coeffs()  # [a, b] for a*x + b
    a = coefs[0]
    b = coefs[1] if len(coefs) > 1 else sp.Integer(0)

    # Compute inverse(c) symbolically.
    inv_value = INVERT_TABLE[head_name]["inverse"](const_expr)

    # Emit one Solution per branch in the head's invert table.
    solutions: List[Dict[str, Any]] = []
    has_branches = False
    for branch_template, branch_syms in INVERT_TABLE[head_name]["branches"]:
        # Substitute `{inv}` into the template, parse as SymPy.
        # Then solve a*x + b = <substituted-template> for x:
        #   x = (<template> - b) / a
        rhs_str = branch_template.replace("{inv}", f"({_expr_to_str(inv_value)})")
        local_syms = {bs: sp.Symbol(bs) for bs in branch_syms}
        local_syms[var] = x
        rhs = sp.sympify(rhs_str, locals=local_syms)
        x_value = sp.simplify((rhs - b) / a)
        if branch_syms:
            has_branches = True
        solutions.append({
            "bindings": [{"var": var, "value": _expr_to_str(x_value)}],
            "branches": list(branch_syms),
        })

    completeness = "finite-rep-of-infinite" if has_branches else "complete"
    return _ok([var], solutions=solutions, completeness=completeness)


def _decompose_head_eq_const(eq: sp.Expr, x: sp.Symbol) -> Optional[Tuple[str, sp.Expr, sp.Expr]]:
    """Pattern-match eq into `head(g(x)) - c` form.

    Returns `(head_name, g(x), c)` on a match. The five literal forms
    handled (mirroring `packages/solve/src/transcendental.ts`):

      head(arg)
      head(arg) - c
      c - head(arg)
      head(arg) + (-c)         (n-ary +)
      a*head(arg) - c          ⇒ no, we don't divide; only coefficient 1

    `head` ∈ {sin,cos,tan,exp,log,sinh,cosh,tanh,abs}.
    """
    # Bare head form: eq = head(arg).
    if eq.is_Function and eq.func.__name__.lower() in INVERT_TABLE:
        return (eq.func.__name__.lower(), eq.args[0], sp.Integer(0))

    # Linear combination: walk top-level Add args. Looking for exactly one
    # head(arg) plus constants (no other head-applications, no x outside
    # the head).
    if isinstance(eq, sp.Add):
        head_term: Optional[sp.Expr] = None
        const_residue: sp.Expr = sp.Integer(0)
        for term in eq.args:
            if (term.is_Function and term.func.__name__.lower() in INVERT_TABLE
                    and x in term.args[0].free_symbols):
                if head_term is not None:
                    return None  # two heads; pattern doesn't apply
                head_term = term
                continue
            if x in term.free_symbols:
                # An additional x-bearing term that isn't the single head.
                return None
            const_residue = const_residue + term
        if head_term is None:
            return None
        # head_term + const_residue = 0  ⇒  head_term = -const_residue
        return (head_term.func.__name__.lower(), head_term.args[0], -const_residue)

    # Mul like 2*sin(x): not handled by v0.1 invert.
    return None


# ---------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------


def _ok(var_names: List[str], *, solutions: List[Dict[str, Any]], completeness: str) -> Dict[str, Any]:
    return {
        "kind": "ok",
        "vars": list(var_names),
        "solutions": solutions,
        "completeness": completeness,
        "warnings": [],
    }


def _refuse(tag: str, detail: str) -> Dict[str, Any]:
    return {
        "kind": "tagged",
        "tag":   tag,
        "payload": {"detail": detail},
    }


def _expr_to_str(e: sp.Expr) -> str:
    """Stringify a SymPy expression in a parser-stable form."""
    return sp.sstr(e)


# ---------------------------------------------------------------------
# CLI entry point (for ad-hoc testing)
# ---------------------------------------------------------------------


def main() -> int:
    import json
    raw = sys.stdin.read()
    case = json.loads(raw)
    out = solve_reference(case["eqs"], case["vars"])
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
