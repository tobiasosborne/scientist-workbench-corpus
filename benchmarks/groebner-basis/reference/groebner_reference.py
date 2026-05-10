"""SymPy-backed reference implementation of `groebner-basis`.

`groebner_reference(polys, vars_, order)` returns a candidate dict matching
the bench's wire format (the same expression-string shape that
`run-candidate.ts` emits when it converts the tool's value-protocol output):

    {
      "kind":     "ok",
      "basis":    [<expression-string>, ...],
      "order":    "lex" | "degrevlex",
      "vars":     [<symbol>, ...],
      "n_pairs":  <int>,
      "warnings": [],
    }

or a tagged record for boundary inputs:

    {
      "kind":    "tagged",
      "tag":     "groebner-basis/<class>",
      "payload": {"detail": "<reason>"}
    }

Why a SymPy reference and not a Buchberger-from-scratch reproduction?
---------------------------------------------------------------------
Per ADR-0019 §1, the bench accepts ANY correct Gröbner basis (the four
mathematical invariants — shape, ideal containment in both directions, and
S-polynomial reduction-to-zero — uniquely characterise GB-ness modulo
ordering of the basis list and the equivalence "G is a GB of <F>"). The
reference therefore does not need to implement Buchberger's algorithm
itself; it only needs to be a TRUSTWORTHY oracle for "compute *some*
correct Gröbner basis of the input ideal under the requested order."
SymPy's `groebner()` is that oracle (built on Buchberger + sugar +
Gebauer-Möller in `sympy.polys.groebnertools`).

The reference is consumed in two places:

  1. `generate.py` calls it (as the SymPy half of the dual-witness oracle)
     to produce `expected.json`.
  2. `test_mutations.py` perturbs it (drop a basis element, add a
     non-ideal element, etc.) and confirms `verify.py` flags every
     perturbation RED.

Both consumers depend on the reference returning byte-identical output
for byte-identical input — this means the canonical-ordering pass below
(monic leading coefficient, list sorted by leading monomial) is
load-bearing. SymPy's `groebner()` already returns the reduced GB; we
re-emit each basis polynomial as a SymPy expression string in the
canonical degree-descending lex order (which matches the bench's
`expected.json` format).

Refusal classes are implemented mechanically, mirroring the tool surface
the Phase 3 implementation will expose:

  groebner-basis/empty-input    polys is empty
  groebner-basis/empty-vars     vars is empty
  groebner-basis/parametric     a polynomial mentions a free symbol not in vars
  groebner-basis/non-polynomial a polynomial uses a non-arithmetic head
                                (sin, cos, log, sqrt, abs, ...)

The detection here is intentionally simple — sympy.sympify the
expression, then walk free_symbols and the expression atom set. This is
the v0.1 contract. The Phase 3 tool will implement the same detection
on Value protocol heads.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Tuple

import sympy as sp

# Heads that are NOT in the closed +,-,*,/,^ vocabulary. Presence of any of
# these in an input expression triggers `groebner-basis/non-polynomial`.
_NON_POLYNOMIAL_HEADS = (
    sp.sin, sp.cos, sp.tan, sp.cot, sp.sec, sp.csc,
    sp.asin, sp.acos, sp.atan, sp.acot, sp.asec, sp.acsc,
    sp.sinh, sp.cosh, sp.tanh, sp.coth,
    sp.exp, sp.log, sp.Abs,
    sp.Function,
)


def _is_non_polynomial_head(expr: sp.Expr) -> bool:
    """True iff expr's tree contains any non-arithmetic head.

    sqrt(x) becomes Pow(x, 1/2) under sympify — we catch that as a
    Pow with a non-integer exponent (the parametric-rational-power
    case). Foreign function calls f(x) are caught via AppliedUndef.
    """
    for sub in sp.preorder_traversal(expr):
        # Pow with a non-integer exponent is non-polynomial.
        if isinstance(sub, sp.Pow):
            exp = sub.exp
            if not (exp.is_Integer and exp >= 0):
                return True
        # Trig / log / exp / Abs are never polynomial.
        if isinstance(sub, _NON_POLYNOMIAL_HEADS):
            return True
        # Applied undefined functions like f(x).
        if isinstance(sub, sp.core.function.AppliedUndef):
            return True
    return False


def groebner_reference(
    polys: List[str],
    vars_: List[str],
    order: str,
) -> Dict[str, Any]:
    """Compute a Gröbner basis or emit a tagged refusal.

    Inputs are wire-format strings (polynomial expressions + variable
    names + monomial order); output is the bench's canonical wire format
    (see module docstring).
    """
    # ── refusal: empty inputs ───────────────────────────────────────────
    if not vars_:
        return {
            "kind":    "tagged",
            "tag":     "groebner-basis/empty-vars",
            "payload": {"detail": "vars list is empty; need at least one variable"},
        }
    if not polys:
        return {
            "kind":    "tagged",
            "tag":     "groebner-basis/empty-input",
            "payload": {"detail": "polys list is empty; ideal is the zero ideal"},
        }

    if order not in ("lex", "degrevlex"):
        raise ValueError(f"unsupported order: {order!r} (must be 'lex' or 'degrevlex')")
    sympy_order = "grevlex" if order == "degrevlex" else order

    sym_objs = [sp.Symbol(v) for v in vars_]
    var_set = set(sym_objs)

    # Parse each polynomial. Catch parametric and non-polynomial early.
    parsed_exprs: List[sp.Expr] = []
    for p_str in polys:
        try:
            expr = sp.sympify(p_str, locals={v: s for v, s in zip(vars_, sym_objs)})
        except (sp.SympifyError, SyntaxError, TypeError) as exc:
            return {
                "kind":    "tagged",
                "tag":     "groebner-basis/non-polynomial",
                "payload": {"detail": f"sympify failed for {p_str!r}: {exc}"},
            }
        if _is_non_polynomial_head(expr):
            return {
                "kind":    "tagged",
                "tag":     "groebner-basis/non-polynomial",
                "payload": {"detail": f"polynomial {p_str!r} uses non-polynomial head"},
            }
        free = expr.free_symbols
        foreign = free - var_set
        if foreign:
            return {
                "kind":    "tagged",
                "tag":     "groebner-basis/parametric",
                "payload": {
                    "detail": (
                        f"polynomial {p_str!r} mentions symbol(s) not in vars: "
                        f"{sorted(str(s) for s in foreign)}"
                    ),
                },
            }
        parsed_exprs.append(expr)

    # Filter out the zero polynomial (ideals are unchanged by 0 generators).
    nonzero = [e for e in parsed_exprs if e != 0]
    if not nonzero:
        # Whole input was zero — generate the zero ideal. Reduced GB is [].
        return {
            "kind":     "ok",
            "basis":    [],
            "order":    order,
            "vars":     vars_,
            "n_pairs":  0,
            "warnings": ["all input polynomials are zero; ideal is (0)"],
        }

    # Compute the reduced GB.
    G = sp.groebner(nonzero, *sym_objs, order=sympy_order)
    basis = list(G)

    # Re-render each basis element as a SymPy expression string. We expand
    # so the output is in canonical degree-descending lex order on the
    # internal expression representation (deterministic for the SymPy
    # version we pin in CI). Each element is forced monic by factoring
    # out the leading coefficient; SymPy's `groebner` already returns the
    # reduced GB in monic form, but we re-monicise defensively to remove
    # any version drift.
    rendered: List[str] = []
    for g in basis:
        gp = sp.Poly(g, *sym_objs, domain="QQ")
        if gp.is_zero:
            continue
        # Force monic.
        lc = gp.LC()
        if lc != 1:
            gp = gp.monic()
        # Canonical string emission. We use SymPy's default sstr which renders
        # a*b**2 etc. — matches the format poly-factor-q uses.
        rendered.append(sp.sstr(gp.as_expr(), order="lex"))

    # Sort the basis by (leading-monomial, expression-string) so the
    # output order is reproducible across SymPy versions. Note: this
    # sort is *for diff-friendliness only* — the verifier accepts any
    # ordering of the basis list.
    def _sort_key(s: str) -> Tuple[Tuple[int, ...], str]:
        e = sp.sympify(s, locals={v: sv for v, sv in zip(vars_, sym_objs)})
        ep = sp.Poly(e, *sym_objs, domain="QQ")
        # Use the leading monomial under the requested order.
        try:
            lm = ep.LM(order=sympy_order)
            # LM returns a Monomial-like object; its exponents are accessible.
            exps = tuple(int(x) for x in lm.exponents) if hasattr(lm, "exponents") else \
                tuple(int(x) for x in lm.monom)
        except Exception:
            exps = (0,) * len(sym_objs)
        return (exps, s)

    rendered.sort(key=_sort_key)

    # n_pairs is a metric not an invariant — the SymPy reference doesn't
    # expose internal pair counters, so report 0 as a sentinel. The Phase 3
    # tool will populate this from its Buchberger loop.
    return {
        "kind":     "ok",
        "basis":    rendered,
        "order":    order,
        "vars":     vars_,
        "n_pairs":  0,
        "warnings": [],
    }
