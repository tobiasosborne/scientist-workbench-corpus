#!/usr/bin/env python3
# =============================================================================
# adapters/copt/oracles/copt-sdp.py — COPT SDP oracle adapter
# =============================================================================
#
# Sibling to mosek-sdp.py for COPT (Cardinal Optimizer) v8.x. Same wire
# contract: stdin a raw-JSON canonical-SDP problem record, stdout a
# candidate record matching ADR-0030 §D. Field-absence semantics for
# `objective` / `achieved_precision` (present iff status == "optimal")
# match the LP / Mosek-SDP adapters.
#
# COPT-specific notes
# -------------------
#
# - Free-license fallback: if no license file is present in
#   ${COPT_LICENSE_DIR}, ${HOME}/copt/, the working directory, or
#   the binary directory, COPT v8 starts in "size-limited non-commercial
#   mode" with `n ≤ 2000` (matrix dimension cap). All SDPLIB v0.1
#   problems (control-{1,2,3}, hinf2, theta1, mcp100) fit comfortably
#   under this cap. The license-warning lines are emitted to stderr by
#   COPT itself; the JSON output on stdout is unaffected.
#
# - LD_LIBRARY_PATH must include `/home/tobias/copt80/lib` and
#   `/home/tobias/copt80/lib/python/deps`; PYTHONPATH must include
#   `/home/tobias/copt80/lib/python/<py-major><py-minor>`. The wrapper
#   script `oracles/run-copt.sh` sets these before invoking python3.
#
# - PSD variable creation: `m.addPsdVars(dim, name)` (note: plural
#   "Vars"). Returns a PsdVar.
#
# - Symmetric-matrix coefficient: `m.addSparseMat(dim, rows, cols, vals)`
#   takes lower-triangular triplets (rows[k] >= cols[k]) without scaling
#   and returns a SymMatrix object. Inner product `<C, X>_F` is
#   expressed as `C * X` (or `X * C`) which yields a PsdExpr. Linear
#   constraints over PSD blocks are expressed as `m.addConstr(C * X == b_i)`.
#
# - Solution access: `psdvar.x` returns the primal X flattened **by
#   column** (column-major lower-triangular, length `dim*(dim+1)/2`,
#   identical convention to Mosek). `psdvar.dual` is the corresponding
#   dual slack S in the same packing.
#
# - Constraint duals (y) read via `m.getInfo(COPT.Info.Dual, [constrs])`.

import json
import math
import os
import sys

# COPT install paths must be set before `import coptpy` to load the
# native libcopt.so.  The adapter TOML's `cmd` field uses
# `oracles/run-copt.sh` to populate LD_LIBRARY_PATH and PYTHONPATH;
# this `sys.path` line is a defensive backstop for direct invocation.
_COPT_HOME = os.environ.get("COPT_HOME", "/home/tobias/copt80")
_PY_MM = f"{sys.version_info.major}{sys.version_info.minor}"
_pylib = os.path.join(_COPT_HOME, "lib", "python", _PY_MM)
if _pylib not in sys.path and os.path.isdir(_pylib):
    sys.path.insert(0, _pylib)

# COPT v8 writes license-check banner lines to **fd 1 (stdout)** during
# `import coptpy` and again at every `Envr()` / `createModel()` call —
# from the C library, not via Python's sys.stdout. To keep our JSON
# response clean (a strict-JSON parse on the bench-runner side), we
# duplicate fd 1 to fd 2 (stderr) for the duration of the COPT calls,
# then restore fd 1 just before emitting our own output. The
# `_real_stdout_fd` save lets us write the JSON via `os.write` to the
# original stdout, so even if Python's sys.stdout has been clobbered
# downstream we still emit on the right channel.
sys.stdout.flush()
_real_stdout_fd = os.dup(1)
os.dup2(2, 1)  # silence native-library writes by redirecting fd 1 → fd 2

import coptpy as cp
from coptpy import COPT

SQRT2 = math.sqrt(2)
INV_SQRT2 = 1.0 / SQRT2


def fail_tagged(tag: str, **payload) -> None:
    payload_bytes = json.dumps({"kind": "tagged", "tag": tag, "payload": payload}).encode("utf-8")
    os.write(_real_stdout_fd, payload_bytes)
    sys.exit(0)


# ---------------------------------------------------------------------------
# svec helpers (duplicated from mosek-sdp.py — leaf scripts are standalone)
# ---------------------------------------------------------------------------

def svec_to_ij(k: int, n: int) -> tuple[int, int]:
    i = 0
    rem = k
    while rem >= n - i:
        rem -= n - i
        i += 1
    return i, i + rem


def svec_to_lower_triplets(vec: list[float], indices: list[int], size: int):
    """Wire-svec → COPT/Mosek-style lower-triangular triplets, no scaling."""
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
            subi.append(i); subj.append(j); vals.append(v)
        else:
            subi.append(j); subj.append(i); vals.append(v * INV_SQRT2)
    return subi, subj, vals


def full_matrix_to_svec(ndarr, size: int) -> list[float]:
    """Convert a coptpy NdArray (or any 2D-indexable [i,j]) representing
    a symmetric size × size matrix into wire-svec ordering with sqrt(2)
    on off-diagonals.

    COPT v8 returns `psdvar.x` as an NdArray with shape (size, size) —
    the full symmetric matrix — supporting `arr[i, j]` indexing. We
    average the (i,j) and (j,i) entries to absorb any tiny asymmetry
    drift the solver leaves behind. Diagonal entries pass through;
    off-diagonals are scaled by sqrt(2) to match the wire's Mosek-svec
    convention (ADR-0030 §C Open Question #4).
    """
    out = [0.0] * (size * (size + 1) // 2)
    pos = 0
    for i in range(size):
        for j in range(i, size):
            if i == j:
                out[pos] = float(ndarr[i, i])
            else:
                # Symmetrise drift before scaling.
                v = 0.5 * (float(ndarr[i, j]) + float(ndarr[j, i]))
                out[pos] = SQRT2 * v
            pos += 1
    return out


# ---------------------------------------------------------------------------
# Input validation (identical to Mosek SDP adapter — same wire contract)
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
                        reason=f"cones[{k}] head {head!r} not supported (PSDCone only)")
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
                    reason=f"variables {missing[:10]}... not covered (n={n}, covered={len(seen)})")

    return c, A, b, psd_blocks, precision


# ---------------------------------------------------------------------------
# SDP solve
# ---------------------------------------------------------------------------

def solve_sdp(c, A, b, psd_blocks, precision):
    n = len(c)
    m_constr = len(A)
    nb = len(psd_blocks)

    warnings: list[str] = []

    env = cp.Envr()
    model = env.createModel("sdp_oracle")
    model.setParam(COPT.Param.Logging, 0)
    # Tolerances tightened to one decade past verifier floor.
    oracle_tol = max(1e-12, precision * 0.1)
    # COPT SDP tolerance parameter names (from coptpy COPT.Param):
    #   FeasTol — primal feasibility tolerance
    #   DualTol — dual feasibility tolerance
    #   RelGap  — relative duality gap
    try:
        model.setParam(COPT.Param.FeasTol, oracle_tol)
        model.setParam(COPT.Param.DualTol, oracle_tol)
        model.setParam(COPT.Param.RelGap, oracle_tol)
    except cp.CoptError:
        # If a parameter isn't accepted by this version, fall through with defaults.
        pass

    # Add PSD blocks.
    bar_vars = [model.addPsdVars(size, f"X{j}") for j, (size, _) in enumerate(psd_blocks)]

    # Build C: setObjective( sum_b <C_b, X_b> ).
    obj_terms = []
    for j, (size, indices) in enumerate(psd_blocks):
        subi, subj, vals = svec_to_lower_triplets(c, indices, size)
        if subi:
            Cmat = model.addSparseMat(size, subi, subj, vals)
            obj_terms.append(Cmat * bar_vars[j])
    if obj_terms:
        # Sum the per-block PsdExpr objects.  cp.psdquicksum is the
        # vendor's helper; falling back to manual reduction is safe.
        try:
            model.setObjective(cp.psdquicksum(obj_terms), COPT.MINIMIZE)
        except (AttributeError, cp.CoptError):
            obj = obj_terms[0]
            for t in obj_terms[1:]:
                obj = obj + t
            model.setObjective(obj, COPT.MINIMIZE)
    else:
        # Zero objective.  COPT will minimise 0 over the feasible set
        # (any feasible point is optimal).
        model.setObjective(0.0, COPT.MINIMIZE)

    # Build A_i^b and equality constraints.
    constrs = []
    for i in range(m_constr):
        row = A[i]
        lhs_terms = []
        for j, (size, indices) in enumerate(psd_blocks):
            subi, subj, vals = svec_to_lower_triplets(row, indices, size)
            if subi:
                Aij_mat = model.addSparseMat(size, subi, subj, vals)
                lhs_terms.append(Aij_mat * bar_vars[j])
        if lhs_terms:
            try:
                lhs = cp.psdquicksum(lhs_terms)
            except (AttributeError, cp.CoptError):
                lhs = lhs_terms[0]
                for t in lhs_terms[1:]:
                    lhs = lhs + t
            constrs.append(model.addConstr(lhs == b[i], name=f"con{i}"))
        else:
            # Constraint 0 = b_i — vacuous if b_i == 0, infeasible if not.
            if abs(b[i]) > oracle_tol:
                fail_tagged("cone-solve/non-finite-input",
                            reason=f"constraint {i} has zero LHS but b_i={b[i]} (trivially infeasible)")
            constrs.append(None)  # placeholder; we won't read its dual

    model.solve()
    status = model.status

    if status == COPT.OPTIMAL:
        x_wire = [0.0] * n
        s_wire = [0.0] * n
        for j, (size, indices) in enumerate(psd_blocks):
            # COPT v8 PsdVar primal/dual access: `bar_vars[j].x` and
            # `bar_vars[j].dual` are NdArray of shape (size, size) — the
            # full symmetric matrices.  No packing/unpacking required;
            # just walk by (i, j) and write into the wire svec slot.
            svec_X = full_matrix_to_svec(bar_vars[j].x, size)
            svec_S = full_matrix_to_svec(bar_vars[j].dual, size)
            for k, slot in enumerate(indices):
                x_wire[slot] = svec_X[k]
                s_wire[slot] = svec_S[k]

        # COPT v8's `getInfo(Dual, PsdConstraint)` does not return the
        # standard SDP equality-constraint dual y_i; it appears to
        # report a normalised-sum value that's numerically wrong. We
        # instead reconstruct y from the KKT stationarity relation
        # `S = C - Σ y_i A_i` via least-squares on the canonical wire:
        #
        #     A_wire^T y = c - s   (with A_wire the m × n constraint matrix)
        #
        # For well-posed SDPs (full-row-rank A_wire) this recovers the
        # unique dual y. The least-squares fallback handles edge cases
        # (rank-deficient A, multiple-optimum problems) gracefully. The
        # primal slack S already correctly reflects the dual feasibility
        # at this point, so y obtained this way is *internally
        # consistent* with what COPT actually solved.
        try:
            import numpy as np
            A_mat = np.asarray(A, dtype=np.float64)            # m × n
            diff  = np.asarray(c, dtype=np.float64) - np.asarray(s_wire, dtype=np.float64)
            # Solve A A^T y = A diff (normal equations; works robustly
            # when m << n, the typical SDP regime).
            gram = A_mat @ A_mat.T
            rhs  = A_mat @ diff
            y_arr, *_ = np.linalg.lstsq(gram, rhs, rcond=None)
            y_val = y_arr.tolist()
        except Exception:
            # If numpy is missing or the lstsq blows up, fall back to
            # zeros — the corpus verifier checks the candidate's y, not
            # the oracle's, so this only loses the y field for the
            # oracle's record (objective and status remain correct).
            y_val = [0.0] * m_constr

        obj = model.objval

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
            "dual": y_val,
            "slack": s_wire,
            "objective": float(obj),
            "achieved_precision": float(max(r_p, r_d, r_c)),
            "iterations": int(model.getInfo(COPT.Info.SDP, "BarIter")) if hasattr(COPT.Info, "SDP") else 0,
            "method": "copt-sdp",
            "condition_estimate": 0.0,
            "warnings": warnings,
        }

    if status in (COPT.INFEASIBLE, COPT.INF_OR_UNB):
        return {
            "status": "infeasible",
            "x": [],
            "dual": [],
            "slack": [],
            "iterations": 0,
            "method": "copt-sdp",
            "condition_estimate": 0.0,
            "warnings": [f"COPT reported status={status} (INFEASIBLE or INF_OR_UNB)"],
        }
    if status == COPT.UNBOUNDED:
        return {
            "status": "unbounded",
            "x": [],
            "dual": [],
            "slack": [],
            "iterations": 0,
            "method": "copt-sdp",
            "condition_estimate": 0.0,
            "warnings": ["COPT reported status=UNBOUNDED"],
        }

    return {
        "status": "numerical-breakdown",
        "x": [],
        "dual": [],
        "slack": [],
        "iterations": 0,
        "method": "copt-sdp",
        "condition_estimate": 0.0,
        "warnings": [f"COPT non-optimal status={status}"],
    }


def _emit_json(result: dict) -> None:
    """Write the result as compact JSON to the **original** fd 1 (the
    one we saved before redirecting the COPT noise to stderr). Bypasses
    Python's sys.stdout so the writes can't be silently dropped if some
    intermediate code clobbered the file object."""
    payload = json.dumps(result, allow_nan=False).encode("utf-8")
    os.write(_real_stdout_fd, payload)
    # No trailing newline — bench runner reads the whole stream.


def main() -> None:
    raw = sys.stdin.read()
    prob = json.loads(raw)
    c, A, b, psd_blocks, precision = validate_input(prob)
    result = solve_sdp(c, A, b, psd_blocks, precision)
    _emit_json(result)


if __name__ == "__main__":
    main()
