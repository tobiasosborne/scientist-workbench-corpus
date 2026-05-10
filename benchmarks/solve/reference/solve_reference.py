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
  • multivariate-poly — SymPy `solve(eqs, vars, dict=True)` for zero-dim ideals;
                        mirrors the workbench's Buchberger + FGLM + shape-lemma
                        substrate (ADR-0029, bead `x8d`).  Refuses on positive-dim
                        (`multivariate-non-zero-dim`) and on systems whose closed-form
                        solutions contain `I` (`complex-roots-not-yet-named`).
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
CLS_SHAPE_LEMMA_FAILURE       = "solve/shape-lemma-failure"
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

    # Multivariate polynomial system — try the zero-dim Gröbner lane.
    return _solve_multivariate(parsed_eqs, var_names)


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


def _extract_head_with_coeff(
    term: sp.Expr, x: sp.Symbol,
) -> Optional[Tuple[str, sp.Expr, sp.Expr]]:
    """If `term` is `c · head(arg)` with `c` a constant in ℚ, `head` in
    INVERT_TABLE, and `x ∈ free_symbols(arg)`, return `(head_name, arg, c)`.
    Otherwise return `None`.

    Crucially, this admits the `c = -1` form that SymPy emits when it
    unfolds odd-function identities at sympify time:
      sympify("tan(-2*x + 2)") → -tan(2*x - 2)
      sympify("sin(-4*x + 1)") → -sin(4*x - 1)
    Those unfolds also fire for `sinh`, `tanh`, `asin`, `atan`, `asinh`,
    `atanh` and bare `-x`-as-arg.  Without this case the reference would
    refuse the same equations the workbench correctly solves via Sub-shape
    D in `decomposeLinearInVar` (bead `scientist-workbench-3g9x`).
    """
    # Plain `head(arg)`.
    if term.is_Function and term.func.__name__.lower() in INVERT_TABLE:
        if x in term.args[0].free_symbols:
            return (term.func.__name__.lower(), term.args[0], sp.Integer(1))
        return None
    # `c · head(arg)`: split the Mul into x-free coefficients and an
    # x-bearing rest.  Accept iff there is exactly one head-call in the
    # rest and no other x-bearing factor.
    if isinstance(term, sp.Mul):
        coef_factors: List[sp.Expr] = []
        rest_factors: List[sp.Expr] = []
        for f in term.args:
            if x in f.free_symbols:
                rest_factors.append(f)
            else:
                coef_factors.append(f)
        if len(rest_factors) != 1:
            return None
        head_call = rest_factors[0]
        if not (head_call.is_Function
                and head_call.func.__name__.lower() in INVERT_TABLE):
            return None
        coef = sp.Mul(*coef_factors) if coef_factors else sp.Integer(1)
        if coef == 0:
            return None
        return (head_call.func.__name__.lower(), head_call.args[0], coef)
    return None


def _decompose_head_eq_const(eq: sp.Expr, x: sp.Symbol) -> Optional[Tuple[str, sp.Expr, sp.Expr]]:
    """Pattern-match eq into `head(g(x)) - c` form.

    Returns `(head_name, g(x), c)` on a match.  The forms handled
    (mirroring `packages/solve/src/transcendental.ts`):

      head(arg)
      head(arg) - c
      c - head(arg)
      head(arg) + (-c)         (n-ary +)
      c · head(arg) + …        (e.g. SymPy's `-tan(...)` from odd-fn unfold;
                                 see `_extract_head_with_coeff`)

    `head` ∈ {sin,cos,tan,exp,log,sinh,cosh,tanh,abs}.
    """
    # Bare head form: eq = head(arg).
    if eq.is_Function and eq.func.__name__.lower() in INVERT_TABLE:
        return (eq.func.__name__.lower(), eq.args[0], sp.Integer(0))

    # Bare `c · head(arg)` (no constant residue).  Equation: c·H = 0 ⇒ H = 0.
    bare_match = _extract_head_with_coeff(eq, x)
    if bare_match is not None:
        head_name, arg, _coef = bare_match
        return (head_name, arg, sp.Integer(0))

    # Linear combination: walk top-level Add args.  Looking for exactly one
    # head-bearing term (possibly with a leading rational coefficient) plus
    # constants (no other head-applications, no x outside the head).
    if isinstance(eq, sp.Add):
        head_match: Optional[Tuple[str, sp.Expr, sp.Expr]] = None
        const_residue: sp.Expr = sp.Integer(0)
        for term in eq.args:
            m = _extract_head_with_coeff(term, x)
            if m is not None:
                if head_match is not None:
                    return None  # two head-bearing terms
                head_match = m
                continue
            if x in term.free_symbols:
                return None  # additional x-bearing term that isn't the head
            const_residue = const_residue + term
        if head_match is None:
            return None
        head_name, arg, coef = head_match
        # coef · head(arg) + const_residue = 0  ⇒  head(arg) = −const_residue / coef.
        return (head_name, arg, sp.simplify(-const_residue / coef))

    # Mul like 2*sin(x) without any constant residue is the bare form
    # handled above.  Anything else falls through.
    return None


# ---------------------------------------------------------------------
# Multivariate-poly lane (zero-dim Gröbner)
# ---------------------------------------------------------------------


def _lm_dict(p: sp.Poly) -> Dict[sp.Symbol, int]:
    """Return the leading monomial of `p` as a `{generator: exponent}`
    dict, omitting zero-exponent entries.  An empty dict means the
    leading term is constant (i.e. `p` is itself a non-zero ground
    element)."""
    lm_exp = p.LM().exponents
    gens = p.gens
    return {g: e for g, e in zip(gens, lm_exp) if e != 0}


def _is_zero_dim(polys: List[sp.Poly], syms: List[sp.Symbol]) -> bool:
    """Macaulay's basis theorem (CLO Ch.5 §3 Theorem 6): a zero-dim
    ideal's GB has, for each variable `x_i`, some element whose
    leading monomial is a pure power `x_i^{d_i}` with `d_i ≥ 1`.

    The test is ordering-independent (CLO Ch.2 §4 Proposition 4) — we
    can run it on the lex GB and trust the answer.
    """
    for v in syms:
        if not any(_lm_dict(p) == {v: _lm_dict(p).get(v, 0)} and v in _lm_dict(p)
                   for p in polys):
            return False
    return True


def _is_lex_gb_shape_form(polys: List[sp.Poly], syms: List[sp.Symbol]) -> bool:
    """Return True iff `polys` (the reduced lex GB under `syms[0] > …
    > syms[-1]`) is in shape position per Becker-Mora-Marinari-
    Traverso 1994.

    Shape position requires exactly one element with LM a pure power
    of the last variable `syms[-1]`, and every other element with LM
    a single power of one of the non-last variables (each non-last
    variable appearing as a leading monomial exactly once).  The lex
    GB must therefore have `len(syms)` elements total.
    """
    n = len(syms)
    if n == 0 or len(polys) != n:
        return False
    last = syms[-1]
    others = set(syms[:-1])
    saw_g_n = 0
    seen_other_lm: set = set()
    for p in polys:
        lm_dict = _lm_dict(p)
        if not lm_dict:
            return False
        if set(lm_dict.keys()) == {last}:
            if lm_dict[last] < 1:
                return False
            saw_g_n += 1
            continue
        if len(lm_dict) != 1:
            return False
        (var,), (exp_val,) = list(lm_dict.keys()), list(lm_dict.values())
        if var not in others or exp_val != 1:
            return False
        if var in seen_other_lm:
            return False
        seen_other_lm.add(var)
    return saw_g_n == 1 and seen_other_lm == others


def _solve_multivariate(eqs: List[sp.Expr], var_names: List[str]) -> Dict[str, Any]:
    """Solve a multivariate polynomial system over ℚ via SymPy.

    Mirrors the workbench's `solveGroebner` substrate (ADR-0029, bead
    `scientist-workbench-x8d`): Buchberger → zero-dim test → FGLM
    (DRL → lex) → shape-lemma extraction → per-factor radicals.

    SymPy provides the same end-to-end behaviour through `sp.solve(eqs,
    *syms, dict=True)`.  Internally SymPy dispatches a Gröbner-equipped
    elimination strategy; the contract for our purposes is just the
    output shape:

      • Returns a list of `{sym: value}` dicts on zero-dim with closed-
        form solutions over an algebraic extension of ℚ;
      • Returns `[]` on inconsistent zero-dim systems (ideal contains 1);
      • Returns parametric forms (values with free symbols outside `vars`)
        on positive-dim;
      • Raises `NotImplementedError` / `PolynomialError` on systems
        outside its closed-form repertoire.

    We classify the result and emit one of three verdicts:

      • Positive-dim, NotImplementedError, or any value with free symbols
        outside `vars` ⇒ refuse `solve/multivariate-non-zero-dim`.
      • Zero-dim with any complex root ⇒ refuse
        `solve/complex-roots-not-yet-named` (alg-num v0.1 names real
        algebraic numbers only; matches the workbench's deg ≥ 5
        complex-mixed refusal class).
      • Zero-dim, all real (or empty for inconsistent systems) ⇒ emit
        happy-path solutions.

    Per ADR-0029 the workbench's `shape-lemma-failure` refusal class
    fires when the lex GB does not have the structured shape form.
    Generic random zero-dim ideals over ℚ[x_1, …, x_n] virtually always
    admit the shape form, so we don't pre-detect that case here; if the
    workbench refuses on shape-lemma-failure where this reference says
    happy-path, the verifier will surface the disagreement at grade
    time.
    """
    syms = [sp.Symbol(v) for v in var_names]
    sym_set = set(syms)

    # Phase 1: lex Gröbner basis — shared workspace for the inconsistent /
    # zero-dim / shape-lemma gates.  This mirrors `solveGroebner`'s
    # control flow in `@workbench/groebner` (ADR-0029, bead `x8d`):
    # Buchberger → constant-1 short-circuit → Macaulay zero-dim test →
    # FGLM (DRL → lex) → shape-lemma extraction.  We compute the lex GB
    # directly via SymPy's `groebner(order='lex')`, which under the hood
    # runs Buchberger followed by interreduction; it also fuses the
    # FGLM step (the result is the unique reduced lex GB).
    try:
        gb = sp.groebner(eqs, *syms, order="lex")
    except (NotImplementedError, sp.PolynomialError, sp.GeneratorsError) as exc:
        return _refuse(
            CLS_MULTIVARIATE_NON_ZERO_DIM,
            f"sp.groebner raised {type(exc).__name__}: {exc}",
        )
    polys = list(gb.polys)

    # Phase 2: inconsistent-system short-circuit.  If the lex GB is
    # `{1}` (or any non-zero constant), the ideal contains 1, the
    # variety is empty, and the canonical "no solutions" output is
    # `kind: ok, solutions: [], completeness: complete` (ADR-0017's
    # empty-list-with-complete encoding for inconsistent zero-dim
    # systems).  This mirrors `extractShapeSolutions`'s constant-1 arm
    # at `packages/groebner/src/shape-extract.ts` (the early-return
    # path before `detectShapePosition`).
    if len(polys) == 1 and polys[0].is_ground and not polys[0].is_zero:
        return _ok(var_names, solutions=[], completeness="complete")

    # Phase 3: zero-dimensionality test (Macaulay; CLO Ch.5 §3
    # Theorem 6).  The workbench's `isZeroDimensional` short-circuits
    # any positive-dim system with `multivariate-non-zero-dim` before
    # FGLM is even attempted.  Here we run the same pure-power LM
    # check on the lex GB; the test is ordering-independent so the
    # answer matches the workbench's DRL-based test bit-for-bit on
    # zero-dim ideals.
    if not _is_zero_dim(polys, syms):
        return _refuse(
            CLS_MULTIVARIATE_NON_ZERO_DIM,
            "lex GB lacks a pure-power leading monomial for some variable; "
            "ideal is positive-dimensional (Macaulay; CLO Ch.5 §3 Theorem 6)",
        )

    # Phase 4: shape-lemma gate (Becker-Mora-Marinari-Traverso 1994).
    # Per ADR-0029 / RESEARCH-NOTE-x8d Q2 the workbench refuses with
    # `solve/shape-lemma-failure` when the lex GB is not in the
    # structured form `{g_n(x_n), x_{n-1} − h_{n-1}(x_n), …, x_1 −
    # h_1(x_n)}`; no fixed-shift retry is performed — the v0.1 honest
    # path is refusal.  Mirror that decision here so refusal-tag
    # matching stays exact end-to-end.
    if not _is_lex_gb_shape_form(polys, syms):
        return _refuse(
            CLS_SHAPE_LEMMA_FAILURE,
            "lex GB is not in shape position; per ADR-0029 Q2 v0.1 refuses without retry",
        )

    # Phase 5: closed-form solutions for the shape-form lex GB.  At
    # this point the system is zero-dim with shape structure, so
    # `sp.solve` returns a finite list of dicts in closed form.
    try:
        sol_list = sp.solve(eqs, *syms, dict=True)
    except (NotImplementedError, sp.PolynomialError, sp.GeneratorsError) as exc:
        return _refuse(
            CLS_MULTIVARIATE_NON_ZERO_DIM,
            f"sp.solve raised {type(exc).__name__}: {exc}",
        )

    # SymPy occasionally returns a list of *tuples* instead of dicts when
    # the variable count and equation count match its internal heuristic.
    # Normalise to dicts.
    normalised: List[Dict[sp.Symbol, sp.Expr]] = []
    for sol in sol_list:
        if isinstance(sol, dict):
            normalised.append(sol)
        elif isinstance(sol, tuple) and len(sol) == len(syms):
            normalised.append(dict(zip(syms, sol)))
        else:
            return _refuse(
                CLS_MULTIVARIATE_NON_ZERO_DIM,
                f"sp.solve returned unrecognised solution form: {type(sol).__name__}",
            )

    # Detect positive-dim and complex roots in a single pass.
    for sol in normalised:
        # Every requested variable must be bound.
        if any(s not in sol for s in syms):
            return _refuse(
                CLS_MULTIVARIATE_NON_ZERO_DIM,
                "sp.solve returned underdetermined solution (positive-dimensional)",
            )
        for s in syms:
            val = sol[s]
            extra = val.free_symbols - sym_set
            if extra:
                return _refuse(
                    CLS_MULTIVARIATE_NON_ZERO_DIM,
                    f"sp.solve returned parametric solution; extra free symbol(s) "
                    f"{sorted(map(str, extra))} not in vars",
                )
            if val.has(sp.I):
                return _refuse(
                    CLS_COMPLEX_NOT_YET,
                    "zero-dim system has complex root(s); "
                    "alg-num v0.1 names real algebraic numbers only",
                )

    # All real (or empty), zero-dim. Emit happy-path solutions.
    solutions: List[Dict[str, Any]] = []
    for sol in normalised:
        bindings = [
            {"var": str(s), "value": _expr_to_str(sp.simplify(sol[s]))}
            for s in syms
        ]
        solutions.append({"bindings": bindings, "branches": []})
    return _ok(var_names, solutions=solutions, completeness="complete")


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
