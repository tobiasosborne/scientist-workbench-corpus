#!/usr/bin/env python3
"""linalg-qr verifier — invariant-based, language-neutral.

stdin:
  {"input": {"A": [[float, ...], ...], "mode"?: "reduced" | "complete"},
   "candidate": {"Q": ..., "R": ..., "mode": ..., "diagonal_R": ...,
                 "reconstruction_error": ..., "orthogonality_error": ...,
                 "method": ..., "warnings": [...]},
   "id"?: str}

stdout:
  {"pass": bool, "reason": str, "checks": {<name>: {"pass": bool, "detail": str}}}

Runs 7 independent checks per case (see verifier_protocol.md):
  1. shape                          — Q, R dimensions match (m, n, mode)
  2. finite_entries                 — no NaN, no ±Inf
  3. R_upper_triangular             — sub-diagonal entries within tol_struct
  4. Q_orthonormal                  — ||QᵀQ − I|| within tol_orth
  5. factorisation_residual         — ||QR − A||_F within tol_recon · ||A||_F
  6. self_reported_residual         — candidate's recon claim is honest
  7. self_reported_orthogonality    — candidate's orth claim is honest

These 7 invariants are necessary AND sufficient for a valid QR
factorisation. (An earlier version included a singular_values_match
check; dropped because |diag(R)| is not equal to singular values of A
in general — they coincide only when A is itself upper triangular.
The factorisation residual + R_upper_triangular + Q_orthonormal trio
is already tight: any (Q, R) that passes all three is a valid QR.)

Tolerances derive from Higham 2002, Thm 19.4 (backward stability of
Householder QR), with a 100× empirical safety factor justified
by SciPy LAPACK clearing each bound by ≥1 order of magnitude.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

import numpy as np

EPS = 2.220446049250313e-16
SAFETY = 100.0
SELF_REPORT_REL_TOL = 1e-6


# --- helpers -----------------------------------------------------------------


def _is_finite_array(x: Any) -> bool:
    arr = np.asarray(x, dtype=float)
    return bool(np.all(np.isfinite(arr)))


def _shape_of(mat: Any) -> tuple[int, int] | None:
    if not isinstance(mat, list):
        return None
    if not mat:
        return (0, 0)
    if not all(isinstance(row, list) for row in mat):
        return None
    n_cols = len(mat[0])
    if not all(len(row) == n_cols for row in mat):
        return None
    if not all(isinstance(v, (int, float)) for row in mat for v in row):
        return None
    return (len(mat), n_cols)


# --- per-check implementations ----------------------------------------------


def check_shape(A: np.ndarray, candidate: dict, mode: str) -> dict:
    m, n = A.shape
    expected_Q = (m, min(m, n)) if mode == "reduced" else (m, m)
    expected_R = (min(m, n), n) if mode == "reduced" else (m, n)

    required = {
        "Q", "R", "mode", "diagonal_R",
        "reconstruction_error", "orthogonality_error",
        "method", "warnings",
    }
    missing = required - set(candidate.keys())
    if missing:
        return {"pass": False, "detail": f"missing fields: {sorted(missing)}"}

    if candidate["mode"] != mode:
        return {"pass": False,
                "detail": f"mode mismatch: candidate={candidate['mode']!r}, expected={mode!r}"}

    if candidate["method"] != "householder":
        return {"pass": False,
                "detail": f"method must be 'householder'; got {candidate['method']!r}"}

    if not isinstance(candidate["warnings"], list) or \
       not all(isinstance(s, str) for s in candidate["warnings"]):
        return {"pass": False, "detail": "warnings must be list[str]"}

    sQ = _shape_of(candidate["Q"])
    if sQ != expected_Q:
        return {"pass": False,
                "detail": f"Q shape {sQ}, expected {expected_Q} for mode={mode!r}"}

    sR = _shape_of(candidate["R"])
    if sR != expected_R:
        return {"pass": False,
                "detail": f"R shape {sR}, expected {expected_R} for mode={mode!r}"}

    diag_len = min(sR)
    if not isinstance(candidate["diagonal_R"], list) or \
       len(candidate["diagonal_R"]) != diag_len or \
       not all(isinstance(v, (int, float)) for v in candidate["diagonal_R"]):
        return {"pass": False,
                "detail": f"diagonal_R must be list[float] of length {diag_len}"}

    return {"pass": True, "detail": f"Q {sQ}, R {sR}, mode {mode!r}"}


def check_finite(candidate: dict) -> dict:
    for field in ("Q", "R", "diagonal_R"):
        if not _is_finite_array(candidate[field]):
            return {"pass": False, "detail": f"non-finite entry in {field}"}
    for field in ("reconstruction_error", "orthogonality_error"):
        v = candidate[field]
        if not isinstance(v, (int, float)) or not np.isfinite(v):
            return {"pass": False, "detail": f"non-finite or non-numeric {field}"}
    return {"pass": True, "detail": "all entries finite"}


def check_R_upper_triangular(A: np.ndarray, R: np.ndarray) -> dict:
    m, n = A.shape
    A_norm = max(float(np.linalg.norm(A, ord="fro")), 1.0)
    tol = SAFETY * EPS * max(m, n) * A_norm

    rR, cR = R.shape

    # Sub-diagonal of the min(m,n) × min(m,n) leading block must be ~0.
    k = min(rR, cR)
    for i in range(k):
        for j in range(i):
            if abs(R[i, j]) > tol:
                return {"pass": False,
                        "detail": f"R[{i},{j}]={R[i,j]:.3e} > tol={tol:.3e}"}

    # For mode="complete" and m > n, the bottom (m − n) rows of R must be ~0.
    if rR > cR:
        for i in range(cR, rR):
            for j in range(cR):
                if abs(R[i, j]) > tol:
                    return {"pass": False,
                            "detail": f"R[{i},{j}]={R[i,j]:.3e} > tol={tol:.3e} (bottom block)"}

    return {"pass": True, "detail": f"sub-diagonal max < tol={tol:.3e}"}


def check_Q_orthonormal(A: np.ndarray, Q: np.ndarray) -> dict:
    m, _ = A.shape
    k = Q.shape[1]
    tol = SAFETY * EPS * m * np.sqrt(k)
    QtQ = Q.T @ Q
    err = float(np.linalg.norm(QtQ - np.eye(k), ord="fro"))
    if err > tol:
        return {"pass": False,
                "detail": f"||QᵀQ − I||_F = {err:.3e} > tol = {tol:.3e}"}
    return {"pass": True, "detail": f"||QᵀQ − I||_F = {err:.3e} ≤ tol = {tol:.3e}"}


def check_factorisation_residual(A: np.ndarray, Q: np.ndarray, R: np.ndarray) -> dict:
    m, n = A.shape
    A_norm = float(np.linalg.norm(A, ord="fro"))
    tol_factor = SAFETY * EPS * max(m, n) * np.sqrt(min(m, n))
    err = float(np.linalg.norm(Q @ R - A, ord="fro"))

    if A_norm == 0.0:
        # Zero input: require exact zero residual.
        if err > 0.0:
            return {"pass": False,
                    "detail": f"||QR − A||_F = {err:.3e} but A is zero"}
        return {"pass": True, "detail": "zero input, zero residual"}

    rel = err / A_norm
    if rel > tol_factor:
        return {"pass": False,
                "detail": f"||QR − A||_F / ||A||_F = {rel:.3e} > tol = {tol_factor:.3e}"}
    return {"pass": True,
            "detail": f"||QR − A||_F / ||A||_F = {rel:.3e} ≤ tol = {tol_factor:.3e}"}


def check_self_reported_residual(A: np.ndarray, Q: np.ndarray, R: np.ndarray,
                                  reported: float) -> dict:
    A_norm = float(np.linalg.norm(A, ord="fro"))
    recomputed = float(np.linalg.norm(Q @ R - A, ord="fro")) / max(A_norm, 1.0)
    diff = abs(reported - recomputed)
    rhs = SELF_REPORT_REL_TOL * max(recomputed, EPS)
    if diff > rhs:
        return {"pass": False,
                "detail": (f"reported reconstruction_error={reported:.6e} "
                           f"vs verifier={recomputed:.6e}, "
                           f"|Δ|={diff:.3e} > tol={rhs:.3e}")}
    return {"pass": True,
            "detail": f"reported={reported:.6e}, verifier={recomputed:.6e} (within {SELF_REPORT_REL_TOL} rel)"}


def check_self_reported_orthogonality(Q: np.ndarray, reported: float) -> dict:
    k = Q.shape[1]
    recomputed = float(np.linalg.norm(Q.T @ Q - np.eye(k), ord="fro"))
    diff = abs(reported - recomputed)
    rhs = SELF_REPORT_REL_TOL * max(recomputed, EPS)
    if diff > rhs:
        return {"pass": False,
                "detail": (f"reported orthogonality_error={reported:.6e} "
                           f"vs verifier={recomputed:.6e}, "
                           f"|Δ|={diff:.3e} > tol={rhs:.3e}")}
    return {"pass": True,
            "detail": f"reported={reported:.6e}, verifier={recomputed:.6e} (within {SELF_REPORT_REL_TOL} rel)"}


# --- top-level verify --------------------------------------------------------


def verify(payload: dict) -> dict:
    inp = payload["input"]
    candidate = payload["candidate"]
    mode = inp.get("mode", "reduced")
    if mode not in ("reduced", "complete"):
        return {"pass": False, "reason": f"unknown mode {mode!r}", "checks": {}}

    if not isinstance(candidate, dict):
        return {"pass": False, "reason": "candidate must be a JSON object", "checks": {}}

    A = np.asarray(inp["A"], dtype=np.float64)
    if A.ndim != 2:
        return {"pass": False, "reason": f"A must be 2-D; got ndim={A.ndim}", "checks": {}}

    checks: dict[str, dict] = {}

    # 1. shape (must come first; downstream checks dereference fields)
    checks["shape"] = check_shape(A, candidate, mode)
    if not checks["shape"]["pass"]:
        return _wrap(checks)

    # 2. finite_entries (must come before any numerical checks)
    checks["finite_entries"] = check_finite(candidate)
    if not checks["finite_entries"]["pass"]:
        return _wrap(checks)

    Q = np.asarray(candidate["Q"], dtype=np.float64)
    R = np.asarray(candidate["R"], dtype=np.float64)

    # 3. R_upper_triangular
    checks["R_upper_triangular"] = check_R_upper_triangular(A, R)

    # 4. Q_orthonormal
    checks["Q_orthonormal"] = check_Q_orthonormal(A, Q)

    # 5. factorisation_residual
    checks["factorisation_residual"] = check_factorisation_residual(A, Q, R)

    # 6. self_reported_residual
    checks["self_reported_residual"] = check_self_reported_residual(
        A, Q, R, float(candidate["reconstruction_error"]))

    # 7. self_reported_orthogonality
    checks["self_reported_orthogonality"] = check_self_reported_orthogonality(
        Q, float(candidate["orthogonality_error"]))

    return _wrap(checks)


def _wrap(checks: dict) -> dict:
    overall = all(c["pass"] for c in checks.values())
    if overall:
        return {"pass": True, "reason": "all invariants hold", "checks": checks}
    first_fail = next(k for k, v in checks.items() if not v["pass"])
    return {"pass": False,
            "reason": f"failed: {first_fail} — {checks[first_fail]['detail']}",
            "checks": checks}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        result = verify(payload)
    except Exception as e:
        sys.stderr.write(traceback.format_exc())
        sys.stderr.write("\n")
        result = {
            "pass": False,
            "reason": f"verifier crashed: {type(e).__name__}: {e}",
            "checks": {},
        }
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
