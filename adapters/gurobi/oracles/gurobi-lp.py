#!/usr/bin/env python3
# =============================================================================
# adapters/gurobi/oracles/gurobi-lp.py — Gurobi LP oracle adapter
# =============================================================================
#
# Wire contract:
#   stdin:  one JSON object in canonical SCS form (ADR-0030 §C, raw-JSON
#           projection — see benchmarks/lp-netlib/DESCRIPTION.md):
#
#               { "minimize":  { "c": [...] },
#                 "subjectTo": { "Ax_eq_b": { "A": [[...]], "b": [...] },
#                                "cones":   [{ "head": "NonNegCone",
#                                              "indices": [...] }, ...] },
#                 "precision": 1e-8 }
#
#   stdout: one JSON object in candidate-record form (ADR-0030 §D):
#
#               { "status":            "optimal" | "infeasible" | "unbounded"
#                                       | "iter-cap" | "numerical-breakdown",
#                 "x":                 [...],
#                 "dual":              [...],   # always present, may be []
#                 "slack":             [...],   # always present, may be []
#                 "objective":         <float>,
#                 "achieved_precision": <float>,
#                 "iterations":        <int>,
#                 "method":            "gurobi-lp",
#                 "condition_estimate": <float>,    # 0.0 when unavailable
#                 "warnings":          [...] }
#
#           Or, on a structural refusal (malformed cone, non-finite input,
#           degenerate shape), a tagged envelope:
#
#               { "kind": "tagged",
#                 "tag":  "cone-solve/malformed-cone" | ... ,
#                 "payload": { ... } }
#
# Design notes:
#
#   - Gurobi is the *oracle*, not the candidate.  This adapter's job is to
#     produce ground-truth answers in the same wire shape the candidate
#     would produce, so the verifier can grade Gurobi just like it grades
#     scientist-workbench's lp-solve.  Re-running the oracle every grade
#     surfaces version drift, platform variance, and multi-optimum
#     disagreement.
#
#   - "Cones" today are all NonNegCone for LP.  When this oracle is reused
#     for QP / SOCP / mixed-conic in subsequent suites the cone dispatch
#     extends here — kept structural from day one.
#
#   - The achieved_precision returned is the larger of (Gurobi's reported
#     OptimalityTol, the recomputed primal/dual/complementarity residual).
#     Gurobi internally hits much tighter tolerances than its reported
#     OptimalityTol; we surface the *recomputed* residual so the
#     self_reported_precision verifier check has something honest to bite
#     against.  CLAUDE.md Rule 8.
#
#   - condition_estimate: Gurobi exposes KappaExact (the basis-matrix
#     condition number) when LP simplex completes.  For IPM-completed
#     problems Gurobi returns -1; we report 0.0 in that case (the
#     candidate side will compute its own; the oracle is just providing a
#     hint when available).
#
# Reference: Gurobi 13 API documentation, "Attributes" section, and the
# Wright 1997 §2.4 KKT formulation.

import json
import math
import sys

import gurobipy as gp
from gurobipy import GRB


def fail_tagged(tag: str, **payload) -> None:
    """Emit a tagged-refusal envelope and exit cleanly."""
    json.dump({"kind": "tagged", "tag": tag, "payload": payload}, sys.stdout)
    sys.stdout.flush()
    sys.exit(0)


def validate_input(prob: dict) -> tuple[list[float], list[list[float]], list[float], list[dict], float]:
    """Return (c, A, b, cones, precision) or raise via fail_tagged()."""
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

    # Structural checks.
    if m != len(b):
        fail_tagged("cone-solve/degenerate-shape", reason=f"A has {m} rows, b has {len(b)}")
    for i, row in enumerate(A):
        if len(row) != n:
            fail_tagged("cone-solve/degenerate-shape", reason=f"A row {i} has {len(row)} cols, expected {n}")

    # Finite-entry check.
    def all_finite(xs):
        return all(isinstance(v, (int, float)) and math.isfinite(v) for v in xs)
    if not all_finite(c) or not all_finite(b) or not all(all_finite(row) for row in A):
        fail_tagged("cone-solve/non-finite-input", reason="NaN or Inf in c, A, or b")

    # Cone validity.
    if not isinstance(cones, list):
        fail_tagged("cone-solve/malformed-cone", reason=f"cones is not a list: {type(cones).__name__}")
    for k, cone in enumerate(cones):
        if not isinstance(cone, dict) or "head" not in cone:
            fail_tagged("cone-solve/malformed-cone", reason=f"cones[{k}] missing head")
        head = cone["head"]
        idx = cone.get("indices", [])
        if not isinstance(idx, list) or any(not isinstance(i, int) or i < 0 or i >= n for i in idx):
            fail_tagged("cone-solve/malformed-cone", reason=f"cones[{k}] indices out of range or non-integer (n={n})")
        if head not in ("NonNegCone",):  # LP-only oracle for now
            fail_tagged("cone-solve/malformed-cone", reason=f"cones[{k}] head {head!r} not supported by lp oracle")

    return c, A, b, cones, precision


def solve_lp(c: list[float], A: list[list[float]], b: list[float],
             nonneg_indices: set[int], precision: float) -> dict:
    """Build and solve the canonical-form LP via Gurobi; return candidate record."""
    n = len(c)
    m = len(A)

    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 0)
    env.setParam("FeasibilityTol", min(precision, 1e-6))     # Gurobi's floor
    env.setParam("OptimalityTol", min(precision, 1e-6))
    env.setParam("InfUnbdInfo", 1)                            # produce certificates
    env.start()

    model = gp.Model(env=env)

    # Variables: lb = 0 for indices in any NonNegCone, lb = -inf otherwise.
    # (For LP suites today every index is in NonNegCone; the dispatch
    # leaves room for free-variable cones later without restructure.)
    xs = []
    for j in range(n):
        lb = 0.0 if j in nonneg_indices else -GRB.INFINITY
        v = model.addVar(lb=lb, ub=GRB.INFINITY, obj=c[j], name=f"x{j}")
        xs.append(v)
    model.ModelSense = GRB.MINIMIZE
    model.update()

    # Equality constraints.
    constrs = []
    for i in range(m):
        expr = gp.quicksum(A[i][j] * xs[j] for j in range(n) if A[i][j] != 0.0)
        c_i = model.addConstr(expr == b[i], name=f"r{i}")
        constrs.append(c_i)
    model.update()

    model.optimize()
    status_code = model.Status

    def safe_attr(thing, name, default):
        try:
            return thing.getAttr(name)
        except (gp.GurobiError, AttributeError):
            return default

    n_iter = int(safe_attr(model, "IterCount", 0))
    method = "gurobi-lp"
    warnings: list[str] = []

    if status_code == GRB.OPTIMAL:
        x_val = [v.X for v in xs]
        # Gurobi's "Pi" attribute is the equality dual (multiplier on Ax=b).
        y_val = [c_i.Pi for c_i in constrs]
        # Compute s = c − A^T y directly rather than reading Gurobi's RC.
        # The two are equal at the optimum but the formula-based version
        # matches Mosek's oracle exactly (which can't read its own snx on
        # basic solutions) and removes any per-vendor sign-convention
        # subtlety from the wire surface.
        n = len(c)
        m = len(A)
        s_val = [c[j] - sum(A[i][j] * y_val[i] for i in range(m)) for j in range(n)]
        obj   = float(model.ObjVal)
        # Recompute residuals to fill achieved_precision honestly.
        r_p = max((abs(sum(A[i][j] * x_val[j] for j in range(n)) - b[i]) for i in range(m)), default=0.0)
        r_d = max((abs(sum(A[i][j] * y_val[i] for i in range(m)) + s_val[j] - c[j]) for j in range(n)), default=0.0)
        r_c = abs(sum(x_val[j] * s_val[j] for j in range(n)))
        achieved = max(r_p, r_d, r_c)
        # Condition estimate from simplex basis when available.
        cond = safe_attr(model, "KappaExact", 0.0) or 0.0
        if cond < 0:
            cond = 0.0
        return {
            "status": "optimal",
            "x": x_val, "dual": y_val, "slack": s_val,
            "objective": obj, "achieved_precision": achieved,
            "iterations": n_iter, "method": method,
            "condition_estimate": float(cond), "warnings": warnings,
        }

    # Non-optimal terminations: omit fields that would carry ±Inf / NaN.
    # JSON.parse rejects raw Infinity tokens (the spec disallows them), and
    # widening `objective: number` to `number | "Infinity"` is exactly the
    # kind of type-noise a TS expert refuses to type into.  Field-absence
    # semantics gives the verifier a cleaner predicate: "candidate has
    # `objective` ⟺ candidate claims optimality".

    if status_code == GRB.INFEASIBLE:
        # FarkasDual: y with A^T y ≤ 0 and b^T y > 0 (Gurobi's sign convention).
        y_farkas = [safe_attr(c_i, "FarkasDual", 0.0) for c_i in constrs]
        return {
            "status":             "infeasible",
            "x":                  [],
            "dual":               y_farkas,
            "slack":              [],
            "iterations":         n_iter,
            "method":             method,
            "condition_estimate": 0.0,
            "warnings":           ["primal infeasibility certificate via FarkasDual"],
        }

    if status_code == GRB.UNBOUNDED:
        # UnbdRay: d with A d = 0, d ≥ 0, c^T d < 0.
        d_ray = [safe_attr(v, "UnbdRay", 0.0) for v in xs]
        return {
            "status":             "unbounded",
            "x":                  d_ray,
            "dual":               [],
            "slack":              [],
            "iterations":         n_iter,
            "method":             method,
            "condition_estimate": 0.0,
            "warnings":           ["unbounded direction via UnbdRay"],
        }

    if status_code == GRB.ITERATION_LIMIT or status_code == GRB.TIME_LIMIT:
        return {
            "status":             "iter-cap",
            "x":                  [],
            "dual":               [],
            "slack":              [],
            "iterations":         n_iter,
            "method":             method,
            "condition_estimate": 0.0,
            "warnings":           [f"gurobi status {status_code}"],
        }

    # Numerical issues, suboptimal, inf-or-unbd, etc.
    return {
        "status":             "numerical-breakdown",
        "x":                  [],
        "dual":               [],
        "slack":              [],
        "iterations":         n_iter,
        "method":             method,
        "condition_estimate": 0.0,
        "warnings":           [f"gurobi status {status_code} (unmapped)"],
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
    # ValueError at serialization time, which is what we want — special
    # floats are an internal-only intermediate and must be coerced to
    # field-absence before the wire emits.
    json.dump(result, sys.stdout, allow_nan=False)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
