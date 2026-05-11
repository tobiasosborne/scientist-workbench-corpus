#!/usr/bin/env python3
# =============================================================================
# adapters/mosek/oracles/mosek-sdp.py — Mosek SDP oracle adapter
# =============================================================================
#
# Sibling to mosek-lp.py for the SDP cone-solver tier (ADR-0030 §C with
# PSDCone). Same wire contract: stdin a raw-JSON canonical-SDP problem
# record, stdout a candidate record matching ADR-0030 §D. Field-absence
# semantics for `objective` / `achieved_precision` (present iff
# status == "optimal") match the LP adapter.
#
# Wire input (raw JSON projection of ADR-0030 §C, PSDCone-only here):
#
#   {
#     "minimize":  {"c": [...]},                       length n
#     "subjectTo": {
#       "Ax_eq_b": {"A": [[...], ...], "b": [...]},    A: m × n, b: m
#       "cones":   [{"head": "PSDCone", "size": k, "indices": [...]}, ...]
#     },
#     "precision": 1e-8
#   }
#
# Each PSDCone[size, indices] declares that the slice `x[indices]` is
# the **svec** (symmetric vectorisation) of an `size × size` PSD matrix,
# with **strict-Mosek-format √2 off-diagonal scaling** (ADR-0030 §C
# Open Question #4):
#
#   svec(M)[k(i,i)] = M[i,i]                  (diagonal)
#   svec(M)[k(i,j)] = sqrt(2) * M[i,j]   (i < j)   (upper off-diagonal)
#
# Position ordering: row-major upper-tri — (0,0), (0,1), ..., (0,n-1),
# (1,1), (1,2), ..., (n-1,n-1). Length n*(n+1)/2.
#
# The √2 scaling makes <C, X>_F = svec(C)^T svec(X) exactly. To recover
# the symmetric matrix from the wire vector, divide off-diagonals by
# sqrt(2). To pack a matrix back to the wire, multiply off-diagonals by
# sqrt(2). This is what the helpers in this file do.
#
# Mosek-specific notes
# --------------------
#
# - Mosek's symmetric-matrix storage convention is **column-major lower-
#   triangular** (no scaling): for an n × n symmetric matrix M, the
#   barvar storage is `[M[0,0], M[1,0], ..., M[n-1,0], M[1,1], M[2,1],
#   ..., M[n-1,1], ..., M[n-1,n-1]]`. Length n*(n+1)/2.  The
#   wire-to-Mosek conversion drops the √2 scaling and re-orders into
#   col-major lower-tri triplets; the Mosek-to-wire conversion does the
#   inverse.
#
# - Mosek SDP exposes only the interior solution (`mosek.soltype.itr`).
#   No basic solution exists for SDPs in general.
#
# - Convergence tolerances: `intpnt_co_tol_pfeas`, `intpnt_co_tol_dfeas`,
#   `intpnt_co_tol_rel_gap`. We tighten all three to one decade past
#   the verifier's 1e-8 thresholds (1e-9) so the candidate's
#   self-reported precision check has a tight oracle target.
#
# - Constraint duals (y) are read via `task.gety(soltype, output_buf)`.
#   No vendor-specific sign-convention quirks for the equality form
#   `Ax = b` we use here — same as the LP adapter.
#
# - Achieved precision: recompute residuals from the returned matrices
#   in the same wire convention as the verifier, so the
#   self-reported-precision check has an honest target. Match the LP
#   adapter's pattern.

import json
import math
import sys

import mosek

SQRT2 = math.sqrt(2)
INV_SQRT2 = 1.0 / SQRT2


# ---------------------------------------------------------------------------
# Refusal envelope (mirrors LP oracle conventions — ADR-0003 boundary tags)
# ---------------------------------------------------------------------------

def fail_tagged(tag: str, **payload) -> None:
    json.dump({"kind": "tagged", "tag": tag, "payload": payload}, sys.stdout)
    sys.stdout.flush()
    sys.exit(0)


# ---------------------------------------------------------------------------
# svec ↔ symmetric matrix
# ---------------------------------------------------------------------------

def svec_to_ij(k: int, n: int) -> tuple[int, int]:
    """Inverse of row-major upper-tri svec ordering (i <= j)."""
    i = 0
    rem = k
    while rem >= n - i:
        rem -= n - i
        i += 1
    return i, i + rem


def svec_to_lower_triplets(vec: list[float], indices: list[int], size: int):
    """Convert wire-svec entries (slot indices into vec) into Mosek
    lower-triangular sparse triplets (subi, subj, val) with subi >= subj.

    Diagonal entries pass through; off-diagonals are unscaled by 1/sqrt(2)
    because the wire carries svec(M)[k(i,j)] = sqrt(2) * M[i,j].
    Triplets with value exactly 0.0 are omitted (Mosek treats unset as 0).
    """
    subi, subj, vals = [], [], []
    expected = size * (size + 1) // 2
    if len(indices) != expected:
        raise ValueError(f"PSDCone size={size} expects {expected} indices, got {len(indices)}")
    for k, slot in enumerate(indices):
        i, j = svec_to_ij(k, size)
        v = vec[slot]
        if v == 0.0:
            continue
        if i == j:
            subi.append(i)
            subj.append(j)
            vals.append(v)
        else:
            # Mosek lower-tri: subi > subj.
            subi.append(j)
            subj.append(i)
            vals.append(v * INV_SQRT2)
    return subi, subj, vals


def lower_tri_colmajor_to_svec(packed: list[float], size: int) -> list[float]:
    """Convert Mosek's column-major lower-triangular packed vector into
    a wire-svec vector (row-major upper-tri with sqrt(2) on off-diag).

    Mosek packing: index 0..size-1 is column 0 (rows 0..size-1); index
    size..2*size-2 is column 1 (rows 1..size-1); etc.
    """
    # Build a full size × size matrix first.
    M = [[0.0] * size for _ in range(size)]
    pos = 0
    for col in range(size):
        for row in range(col, size):
            v = packed[pos]
            pos += 1
            M[row][col] = v
            if row != col:
                M[col][row] = v
    # Then convert to wire svec (row-major upper-tri, sqrt(2) on off-diag).
    out = [0.0] * (size * (size + 1) // 2)
    pos = 0
    for i in range(size):
        for j in range(i, size):
            if i == j:
                out[pos] = M[i][j]
            else:
                out[pos] = SQRT2 * M[i][j]
            pos += 1
    return out


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

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
    m_constr = len(A)

    if m_constr != len(b):
        fail_tagged("cone-solve/degenerate-shape",
                    reason=f"A has {m_constr} rows, b has {len(b)}")
    for i, row in enumerate(A):
        if len(row) != n:
            fail_tagged("cone-solve/degenerate-shape",
                        reason=f"A row {i} has {len(row)} cols, expected {n}")

    def all_finite(xs):
        return all(isinstance(v, (int, float)) and math.isfinite(v) for v in xs)
    if not all_finite(c) or not all_finite(b) or not all(all_finite(row) for row in A):
        fail_tagged("cone-solve/non-finite-input", reason="NaN or Inf in c, A, or b")

    if not isinstance(cones, list):
        fail_tagged("cone-solve/malformed-cone",
                    reason=f"cones is not a list: {type(cones).__name__}")

    psd_blocks = []
    seen = set()
    for k, cone in enumerate(cones):
        if not isinstance(cone, dict) or "head" not in cone:
            fail_tagged("cone-solve/malformed-cone", reason=f"cones[{k}] missing head")
        head = cone["head"]
        if head != "PSDCone":
            fail_tagged("cone-solve/malformed-cone",
                        reason=f"cones[{k}] head {head!r} not supported by sdp oracle (PSDCone only)")
        size = cone.get("size")
        idx = cone.get("indices", [])
        if not isinstance(size, int) or size <= 0:
            fail_tagged("cone-solve/malformed-cone",
                        reason=f"cones[{k}] PSDCone size must be positive integer, got {size!r}")
        expected_idx = size * (size + 1) // 2
        if not isinstance(idx, list) or len(idx) != expected_idx:
            fail_tagged("cone-solve/malformed-cone",
                        reason=f"cones[{k}] PSDCone size={size} expects {expected_idx} indices, got {len(idx) if isinstance(idx, list) else type(idx)}")
        if any((not isinstance(i, int)) or i < 0 or i >= n for i in idx):
            fail_tagged("cone-solve/malformed-cone",
                        reason=f"cones[{k}] indices out of range or non-integer (n={n})")
        for i in idx:
            if i in seen:
                fail_tagged("cone-solve/malformed-cone",
                            reason=f"cones[{k}] index {i} collides with previous cone")
            seen.add(i)
        psd_blocks.append((size, idx))
    if seen != set(range(n)):
        missing = sorted(set(range(n)) - seen)
        fail_tagged("cone-solve/malformed-cone",
                    reason=f"variables {missing[:10]}... not covered by any cone (n={n}, covered={len(seen)})")

    return c, A, b, psd_blocks, precision


# ---------------------------------------------------------------------------
# SDP solve
# ---------------------------------------------------------------------------

def solve_sdp(c: list[float], A: list[list[float]], b: list[float],
              psd_blocks: list[tuple[int, list[int]]],
              precision: float) -> dict:
    n = len(c)
    m_constr = len(A)
    nb = len(psd_blocks)
    block_sizes = [bs for bs, _ in psd_blocks]

    warnings: list[str] = []

    with mosek.Env() as env, env.Task(0, 0) as task:
        # Add semidefinite-matrix variables and equality constraints.
        task.appendbarvars(block_sizes)
        task.appendcons(m_constr)
        # No scalar variables in the pure-PSD case (lifted away by the
        # cone-solver wire — every wire variable lives in some PSDCone).

        # Build C: for each block, sparse-symmat of svec(C_b).
        for j, (size, indices) in enumerate(psd_blocks):
            subi, subj, vals = svec_to_lower_triplets(c, indices, size)
            if subi:
                cmat_idx = task.appendsparsesymmat(size, subi, subj, vals)
                task.putbarcj(j, [cmat_idx], [1.0])
            # else: zero objective contribution from this block; nothing to set.

        # Build A_i^b: for each (constraint, block) pair, sparse-symmat.
        for i in range(m_constr):
            row = A[i]
            for j, (size, indices) in enumerate(psd_blocks):
                subi, subj, vals = svec_to_lower_triplets(row, indices, size)
                if subi:
                    a_mat_idx = task.appendsparsesymmat(size, subi, subj, vals)
                    task.putbaraij(i, j, [a_mat_idx], [1.0])
            # Equality bound: b_i = b_i.
            task.putconbound(i, mosek.boundkey.fx, b[i], b[i])

        task.putobjsense(mosek.objsense.minimize)

        # Tighten interior-point tolerances to one decade past the
        # verifier's 1e-8 floor. The verifier's `self_reported_precision`
        # check expects oracles to reach precision strictly better than
        # the candidate's own claim; one decade is the standard margin.
        oracle_tol = max(1e-12, precision * 0.1)
        task.putdouparam(mosek.dparam.intpnt_co_tol_pfeas, oracle_tol)
        task.putdouparam(mosek.dparam.intpnt_co_tol_dfeas, oracle_tol)
        task.putdouparam(mosek.dparam.intpnt_co_tol_rel_gap, oracle_tol)

        task.optimize()

        soltype = mosek.soltype.itr  # SDP only has interior solution
        sol_status = task.getsolsta(soltype)
        prob_status = task.getprosta(soltype)
        n_iter = task.getintinf(mosek.iinfitem.intpnt_iter)

        if sol_status == mosek.solsta.optimal:
            x_wire = [0.0] * n
            s_wire = [0.0] * n
            for j, (size, indices) in enumerate(psd_blocks):
                # Mosek returns the barvar in column-major lower-triangular.
                packed_X = [0.0] * (size * (size + 1) // 2)
                packed_S = [0.0] * (size * (size + 1) // 2)
                task.getbarxj(soltype, j, packed_X)
                task.getbarsj(soltype, j, packed_S)
                svec_X = lower_tri_colmajor_to_svec(packed_X, size)
                svec_S = lower_tri_colmajor_to_svec(packed_S, size)
                for k, slot in enumerate(indices):
                    x_wire[slot] = svec_X[k]
                    s_wire[slot] = svec_S[k]

            y_val = [0.0] * m_constr
            task.gety(soltype, y_val)

            obj = task.getprimalobj(soltype)

            # Honest residual recomputation, matching verifier formulas:
            #   r_p_i = | sum_j A[i][j] * x_wire[j] - b_i |  (pure svec dot product)
            #   r_d_j = | sum_i A[i][j] * y_val[i] + s_wire[j] - c[j] |
            #   r_c   = | sum_j x_wire[j] * s_wire[j] |
            r_p = 0.0
            for i in range(m_constr):
                s = sum(A[i][j] * x_wire[j] for j in range(n)) - b[i]
                if abs(s) > r_p:
                    r_p = abs(s)
            r_d = 0.0
            for j in range(n):
                s = sum(A[i][j] * y_val[i] for i in range(m_constr)) + s_wire[j] - c[j]
                if abs(s) > r_d:
                    r_d = abs(s)
            r_c = abs(sum(x_wire[j] * s_wire[j] for j in range(n)))

            return {
                "status": "optimal",
                "x": x_wire,
                "dual": list(y_val),
                "slack": s_wire,
                "objective": float(obj),
                "achieved_precision": float(max(r_p, r_d, r_c)),
                "iterations": int(n_iter),
                "method": "mosek-sdp",
                "condition_estimate": 0.0,
                "warnings": warnings,
            }

        # Non-optimal terminations: omit `objective` and `achieved_precision`
        # (field-absence semantics, matching LP oracle).
        if prob_status == mosek.prosta.prim_infeas \
           or sol_status == mosek.solsta.prim_infeas_cer:
            y_val = [0.0] * m_constr
            try:
                task.gety(soltype, y_val)
            except mosek.Error:
                pass
            return {
                "status": "infeasible",
                "x": [],
                "dual": y_val,
                "slack": [],
                "iterations": int(n_iter),
                "method": "mosek-sdp",
                "condition_estimate": 0.0,
                "warnings": ["primal infeasibility certificate via mosek.solsta.prim_infeas_cer"],
            }

        if prob_status == mosek.prosta.dual_infeas \
           or sol_status == mosek.solsta.dual_infeas_cer:
            return {
                "status": "unbounded",
                "x": [],
                "dual": [],
                "slack": [],
                "iterations": int(n_iter),
                "method": "mosek-sdp",
                "condition_estimate": 0.0,
                "warnings": ["unbounded direction via mosek.solsta.dual_infeas_cer"],
            }

        return {
            "status": "numerical-breakdown",
            "x": [],
            "dual": [],
            "slack": [],
            "iterations": int(n_iter),
            "method": "mosek-sdp",
            "condition_estimate": 0.0,
            "warnings": [f"mosek solsta={sol_status} prosta={prob_status}"],
        }


def main() -> None:
    raw = sys.stdin.read()
    prob = json.loads(raw)
    c, A, b, psd_blocks, precision = validate_input(prob)
    result = solve_sdp(c, A, b, psd_blocks, precision)
    json.dump(result, sys.stdout, allow_nan=False)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
