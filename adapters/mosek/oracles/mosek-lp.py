#!/usr/bin/env python3
# =============================================================================
# adapters/mosek/oracles/mosek-lp.py — Mosek LP oracle adapter
# =============================================================================
#
# Mirrors adapters/gurobi/oracles/gurobi-lp.py for Mosek.  See that file's
# header for the wire contract — both adapters produce identically-shaped
# candidate records and the verifier doesn't distinguish them.
#
# Mosek-specific notes:
#
#   - We use Mosek's high-level "Task" API (mosek.fusion was deferred because
#     fusion's exception model is heavier and the LP surface is small enough
#     that the Task API reads cleaner).
#
#   - Mosek's dual sign convention: for `min c^T x s.t. A x = b, x ≥ 0`,
#     `Task.gety()` returns the equality multiplier y such that the
#     stationarity equation is `A^T y + s = c` with `s ≥ 0` — same as
#     Wright 1997 §2.4 and what the verifier expects.  Mosek's "snx"
#     (variable reduced cost) is `s` directly.
#
#   - achieved_precision policy: same as gurobi-lp — recompute residuals
#     from (x, y, s) and report the max, so the verifier's
#     self_reported_precision check has an honest target.
#
#   - condition_estimate: Mosek does not expose a basis-matrix condition
#     number directly.  We return 0.0; the verifier treats 0.0 as
#     "unavailable" (it is bounded below by every legitimate condition
#     number ≥ 1, so 0.0 cannot collide with a real estimate).
#
#   - Infeasibility / unboundedness certificates: Mosek surfaces these
#     via solution status codes (PRIM_INFEAS_CER, DUAL_INFEAS_CER) plus
#     the corresponding solution vectors.  For infeasibility the
#     y vector is the Farkas certificate; for unboundedness the x vector
#     is the unbounded ray.

import json
import math
import sys

import mosek

# Shared validation lives in the gurobi-lp.py file as well — duplicated
# verbatim here rather than imported, because each oracle adapter is a
# standalone subprocess and we want zero cross-adapter Python coupling.
# Senior-engineer principle: avoid hidden import-graph dependencies for
# leaf scripts that the corpus runner treats as black boxes.


def fail_tagged(tag: str, **payload) -> None:
    json.dump({"kind": "tagged", "tag": tag, "payload": payload}, sys.stdout)
    sys.stdout.flush()
    sys.exit(0)


def validate_input(prob: dict):
    try:
        c = prob["minimize"]["c"]
        cones = prob["subjectTo"]["cones"]
        precision = float(prob.get("precision", 1e-8))
    except (KeyError, TypeError, ValueError) as e:
        fail_tagged("cone-solve/degenerate-shape", reason=f"missing required field: {e}")

    Ax = prob["subjectTo"].get("Ax_eq_b")
    A = Ax["A"] if Ax else []
    b = Ax["b"] if Ax else []
    n = len(c)
    m = len(A)

    if m != len(b):
        fail_tagged("cone-solve/degenerate-shape", reason=f"A has {m} rows, b has {len(b)}")
    for i, row in enumerate(A):
        if len(row) != n:
            fail_tagged("cone-solve/degenerate-shape", reason=f"A row {i} has {len(row)} cols, expected {n}")

    def all_finite(xs):
        return all(isinstance(v, (int, float)) and math.isfinite(v) for v in xs)
    if not all_finite(c) or not all_finite(b) or not all(all_finite(row) for row in A):
        fail_tagged("cone-solve/non-finite-input", reason="NaN or Inf in c, A, or b")

    if not isinstance(cones, list):
        fail_tagged("cone-solve/malformed-cone", reason=f"cones is not a list: {type(cones).__name__}")
    for k, cone in enumerate(cones):
        if not isinstance(cone, dict) or "head" not in cone:
            fail_tagged("cone-solve/malformed-cone", reason=f"cones[{k}] missing head")
        head = cone["head"]
        idx = cone.get("indices", [])
        if not isinstance(idx, list) or any(not isinstance(i, int) or i < 0 or i >= n for i in idx):
            fail_tagged("cone-solve/malformed-cone", reason=f"cones[{k}] indices out of range or non-integer (n={n})")
        if head not in ("NonNegCone",):
            fail_tagged("cone-solve/malformed-cone", reason=f"cones[{k}] head {head!r} not supported by lp oracle")

    return c, A, b, cones, precision


def solve_lp(c, A, b, nonneg_indices: set[int], precision: float) -> dict:
    n = len(c)
    m = len(A)
    warnings: list[str] = []

    with mosek.Env() as env, env.Task(0, 0) as task:
        task.appendvars(n)
        task.appendcons(m)

        for j in range(n):
            task.putvarbound(j,
                             mosek.boundkey.lo if j in nonneg_indices else mosek.boundkey.fr,
                             0.0,
                             +1.0e30)
            task.putcj(j, c[j])

        # Constraints: A_i x = b_i (equality, both bounds = b_i).
        for i in range(m):
            row = A[i]
            nz_idx = [j for j in range(n) if row[j] != 0.0]
            nz_val = [row[j] for j in nz_idx]
            task.putarow(i, nz_idx, nz_val)
            task.putconbound(i, mosek.boundkey.fx, b[i], b[i])

        task.putobjsense(mosek.objsense.minimize)

        # Tighten Mosek's simplex feasibility tolerances to match the
        # verifier's 1e-8 thresholds with one decade of margin.
        #
        # basis_tol_s — dual-variable feasibility (s ≥ 0 check in verifier):
        #   Mosek default ~1e-7; verifier threshold 1e-8; set 1e-9.
        # basis_tol_x — primal-variable feasibility (x ≥ 0 check in verifier):
        #   Mosek default ~1e-8; can be loose enough to allow x < -1e-8;
        #   tighten to 1e-9 so the primal_nonneg check also passes.
        #
        # Both tolerances govern the simplex basis acceptance criterion only;
        # they do not affect the interior-point fallback path.
        task.putdouparam(mosek.dparam.basis_tol_s, 1e-9)
        task.putdouparam(mosek.dparam.basis_tol_x, 1e-9)

        task.optimize()

        soltype = mosek.soltype.bas      # prefer basic solution; fall back to interior
        try:
            sol_status = task.getsolsta(soltype)
        except mosek.Error:
            soltype = mosek.soltype.itr
            sol_status = task.getsolsta(soltype)

        prob_status = task.getprosta(soltype)
        n_iter = task.getintinf(mosek.iinfitem.sim_primal_iter) \
                 + task.getintinf(mosek.iinfitem.sim_dual_iter) \
                 + task.getintinf(mosek.iinfitem.intpnt_iter)

        def get_vec(getter, length):
            out = [0.0] * length
            getter(soltype, out)
            return out

        if sol_status == mosek.solsta.optimal:
            x_val = get_vec(task.getxx, n)
            y_val = get_vec(task.gety, m)
            # Compute s = c − A^T y directly rather than asking Mosek for it.
            # The basic solution does not expose `snx`; the interior solution
            # does but with potentially different sign conventions per bound
            # kind.  Computing from the stationarity equation removes both
            # uncertainties and matches the formula the verifier expects.
            s_val = [c[j] - sum(A[i][j] * y_val[i] for i in range(m)) for j in range(n)]
            obj   = task.getprimalobj(soltype)
            r_p = max((abs(sum(A[i][j] * x_val[j] for j in range(n)) - b[i]) for i in range(m)), default=0.0)
            r_d = max((abs(sum(A[i][j] * y_val[i] for i in range(m)) + s_val[j] - c[j]) for j in range(n)), default=0.0)
            r_c = abs(sum(x_val[j] * s_val[j] for j in range(n)))
            achieved = max(r_p, r_d, r_c)
            return {
                "status": "optimal",
                "x": x_val, "dual": y_val, "slack": s_val,
                "objective": float(obj), "achieved_precision": float(achieved),
                "iterations": int(n_iter), "method": "mosek-lp",
                "condition_estimate": 0.0, "warnings": warnings,
            }

        # Non-optimal terminations: omit `objective` and `achieved_precision`
        # rather than carrying ±Inf / NaN through raw JSON.  See gurobi-lp.py
        # for the rationale (JSON.parse rejects Infinity tokens; field-absence
        # is the TS-natural way to encode "no finite objective").

        if prob_status == mosek.prosta.prim_infeas \
           or sol_status == mosek.solsta.prim_infeas_cer:
            y_farkas = get_vec(task.gety, m)
            return {
                "status":             "infeasible",
                "x":                  [],
                "dual":               y_farkas,
                "slack":              [],
                "iterations":         int(n_iter),
                "method":             "mosek-lp",
                "condition_estimate": 0.0,
                "warnings":           ["primal infeasibility certificate via mosek.solsta.prim_infeas_cer"],
            }

        if prob_status == mosek.prosta.dual_infeas \
           or sol_status == mosek.solsta.dual_infeas_cer:
            d_ray = get_vec(task.getxx, n)
            return {
                "status":             "unbounded",
                "x":                  d_ray,
                "dual":               [],
                "slack":              [],
                "iterations":         int(n_iter),
                "method":             "mosek-lp",
                "condition_estimate": 0.0,
                "warnings":           ["unbounded direction via mosek.solsta.dual_infeas_cer"],
            }

        # Generic non-success.
        return {
            "status":             "numerical-breakdown",
            "x":                  [],
            "dual":               [],
            "slack":              [],
            "iterations":         int(n_iter),
            "method":             "mosek-lp",
            "condition_estimate": 0.0,
            "warnings":           [f"mosek solsta={sol_status} prosta={prob_status}"],
        }


def main() -> None:
    raw = sys.stdin.read()
    prob = json.loads(raw)
    c, A, b, cones, precision = validate_input(prob)
    nonneg_indices: set[int] = set()
    for cone in cones:
        if cone["head"] == "NonNegCone":
            nonneg_indices.update(cone.get("indices", []))
    result = solve_lp(c, A, b, nonneg_indices, precision)
    # Strict JSON: allow_nan=False makes any leaked Infinity / NaN a
    # ValueError at serialization time.  See gurobi-lp.py for rationale.
    json.dump(result, sys.stdout, allow_nan=False)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
