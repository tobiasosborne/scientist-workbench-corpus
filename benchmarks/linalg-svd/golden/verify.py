#!/usr/bin/env python3
"""linalg-svd verifier — invariant-based, language-neutral.

stdin:
  {"input": {"A": [[float, ...], ...], "mode"?: "reduced" | "complete"},
   "candidate": {"U": ..., "S": ..., "Vt": ..., "mode": ...,
                 "reconstruction_error": ..., "orthogonality_error_U": ...,
                 "orthogonality_error_Vt": ..., "condition_number": ...,
                 "rank_estimate": ..., "method": ..., "warnings": [...]},
   "id"?: str}

stdout:
  {"pass": bool, "reason": str, "checks": {<name>: {"pass": bool, "detail": str}}}

Runs 8 independent checks per case (see verifier_protocol.md):
  1. shape                        — U, S, Vt dimensions match (m, n, mode)
  2. finite_entries               — no NaN, no ±Inf in factors or scalars
  3. S_nonneg_descending          — singular values ≥ 0 and non-increasing
  4. U_orthonormal                — ||UᵀU − I|| within tol_orth
  5. Vt_orthonormal               — ||Vt·Vtᵀ − I|| within tol_orth
  6. factorisation_residual       — ||U·diag(S)·Vt − A||_F within tol_recon
  7. self_reported_residual       — candidate's recon claim is honest
  8. self_reported_orthogonality  — candidate's orth claims (U + Vt) honest

Tolerances: Higham 2002 §20.3 / Demmel-Kahan 1990, with 100× empirical
safety factor (SciPy LAPACK clears each bound by ≥1 order of magnitude).
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


def _vec_len(v: Any) -> int | None:
    if not isinstance(v, list):
        return None
    if not all(isinstance(x, (int, float)) for x in v):
        return None
    return len(v)


# --- per-check implementations ----------------------------------------------


def check_shape(A: np.ndarray, candidate: dict, mode: str) -> dict:
    m, n = A.shape
    k = min(m, n)
    expected_U = (m, k) if mode == "reduced" else (m, m)
    expected_Vt = (k, n) if mode == "reduced" else (n, n)
    expected_S_len = k

    required = {
        "U", "S", "Vt", "mode",
        "reconstruction_error", "orthogonality_error_U",
        "orthogonality_error_Vt", "condition_number",
        "rank_estimate", "method", "warnings",
    }
    missing = required - set(candidate.keys())
    if missing:
        return {"pass": False, "detail": f"missing fields: {sorted(missing)}"}

    if candidate["mode"] != mode:
        return {"pass": False,
                "detail": f"mode mismatch: candidate={candidate['mode']!r}, expected={mode!r}"}

    if not isinstance(candidate["method"], str):
        return {"pass": False, "detail": "method must be a string"}

    if not isinstance(candidate["warnings"], list) or \
       not all(isinstance(s, str) for s in candidate["warnings"]):
        return {"pass": False, "detail": "warnings must be list[str]"}

    if not isinstance(candidate["rank_estimate"], int) or \
       isinstance(candidate["rank_estimate"], bool) or \
       candidate["rank_estimate"] < 0:
        return {"pass": False, "detail": "rank_estimate must be a non-negative int"}

    sU = _shape_of(candidate["U"])
    if sU != expected_U:
        return {"pass": False,
                "detail": f"U shape {sU}, expected {expected_U} for mode={mode!r}"}

    sS = _vec_len(candidate["S"])
    if sS != expected_S_len:
        return {"pass": False,
                "detail": f"S length {sS}, expected {expected_S_len}"}

    sVt = _shape_of(candidate["Vt"])
    if sVt != expected_Vt:
        return {"pass": False,
                "detail": f"Vt shape {sVt}, expected {expected_Vt} for mode={mode!r}"}

    return {"pass": True, "detail": f"U {sU}, S {sS}, Vt {sVt}, mode {mode!r}"}


def check_finite(candidate: dict) -> dict:
    for field in ("U", "S", "Vt"):
        if not _is_finite_array(candidate[field]):
            return {"pass": False, "detail": f"non-finite entry in {field}"}
    for field in ("reconstruction_error", "orthogonality_error_U",
                  "orthogonality_error_Vt", "condition_number"):
        v = candidate[field]
        if not isinstance(v, (int, float)) or not np.isfinite(v):
            return {"pass": False, "detail": f"non-finite or non-numeric {field}"}
    return {"pass": True, "detail": "all entries finite"}


def check_S_nonneg_descending(S: np.ndarray) -> dict:
    if len(S) == 0:
        return {"pass": True, "detail": "empty S"}
    s_max = float(S[0]) if len(S) > 0 else 0.0
    tol_struct = SAFETY * EPS * max(s_max, 1.0)
    for i in range(len(S)):
        if S[i] < -tol_struct:
            return {"pass": False,
                    "detail": f"S[{i}]={S[i]:.3e} < -tol={-tol_struct:.3e} (negative singular value)"}
    for i in range(len(S) - 1):
        if S[i] + tol_struct < S[i + 1]:
            return {"pass": False,
                    "detail": (f"S[{i}]={S[i]:.3e} < S[{i+1}]={S[i+1]:.3e} "
                                f"(non-monotone, gap {S[i+1]-S[i]:.3e} > tol={tol_struct:.3e})")}
    return {"pass": True,
            "detail": f"S[0]={S[0]:.3e}, S[k-1]={S[-1]:.3e}, monotone within tol={tol_struct:.3e}"}


def check_U_orthonormal(A: np.ndarray, U: np.ndarray) -> dict:
    m, _ = A.shape
    q = U.shape[1]
    tol = SAFETY * EPS * m * np.sqrt(q)
    err = float(np.linalg.norm(U.T @ U - np.eye(q), ord="fro"))
    if err > tol:
        return {"pass": False,
                "detail": f"||UᵀU − I||_F = {err:.3e} > tol = {tol:.3e}"}
    return {"pass": True, "detail": f"||UᵀU − I||_F = {err:.3e} ≤ tol = {tol:.3e}"}


def check_Vt_orthonormal(A: np.ndarray, Vt: np.ndarray) -> dict:
    m, _ = A.shape
    q = Vt.shape[0]
    tol = SAFETY * EPS * m * np.sqrt(q)
    err = float(np.linalg.norm(Vt @ Vt.T - np.eye(q), ord="fro"))
    if err > tol:
        return {"pass": False,
                "detail": f"||Vt·Vtᵀ − I||_F = {err:.3e} > tol = {tol:.3e}"}
    return {"pass": True, "detail": f"||Vt·Vtᵀ − I||_F = {err:.3e} ≤ tol = {tol:.3e}"}


def check_factorisation_residual(A: np.ndarray, U: np.ndarray, S: np.ndarray,
                                  Vt: np.ndarray) -> dict:
    m, n = A.shape
    A_norm = float(np.linalg.norm(A, ord="fro"))
    tol_factor = SAFETY * EPS * max(m, n) * np.sqrt(min(m, n))

    Sigma = np.zeros((U.shape[1], Vt.shape[0]))
    for i in range(len(S)):
        Sigma[i, i] = S[i]
    err = float(np.linalg.norm(U @ Sigma @ Vt - A, ord="fro"))

    if A_norm == 0.0:
        if err > 0.0:
            return {"pass": False,
                    "detail": f"||USVt − A||_F = {err:.3e} but A is zero"}
        return {"pass": True, "detail": "zero input, zero residual"}

    rel = err / A_norm
    if rel > tol_factor:
        return {"pass": False,
                "detail": f"||USVt − A||_F / ||A||_F = {rel:.3e} > tol = {tol_factor:.3e}"}
    return {"pass": True,
            "detail": f"||USVt − A||_F / ||A||_F = {rel:.3e} ≤ tol = {tol_factor:.3e}"}


def check_self_reported_residual(A: np.ndarray, U: np.ndarray, S: np.ndarray,
                                  Vt: np.ndarray, reported: float) -> dict:
    A_norm = float(np.linalg.norm(A, ord="fro"))
    Sigma = np.zeros((U.shape[1], Vt.shape[0]))
    for i in range(len(S)):
        Sigma[i, i] = S[i]
    recomputed = float(np.linalg.norm(U @ Sigma @ Vt - A, ord="fro")) / max(A_norm, 1.0)
    diff = abs(reported - recomputed)
    rhs = SELF_REPORT_REL_TOL * max(recomputed, EPS)
    if diff > rhs:
        return {"pass": False,
                "detail": (f"reported reconstruction_error={reported:.6e} "
                           f"vs verifier={recomputed:.6e}, "
                           f"|Δ|={diff:.3e} > tol={rhs:.3e}")}
    return {"pass": True,
            "detail": f"reported={reported:.6e}, verifier={recomputed:.6e}"}


def check_self_reported_orthogonality(U: np.ndarray, Vt: np.ndarray,
                                       reported_U: float, reported_Vt: float) -> dict:
    qU = U.shape[1]
    qV = Vt.shape[0]
    rU = float(np.linalg.norm(U.T @ U - np.eye(qU), ord="fro"))
    rV = float(np.linalg.norm(Vt @ Vt.T - np.eye(qV), ord="fro"))

    diffU = abs(reported_U - rU)
    diffV = abs(reported_Vt - rV)
    rhsU = SELF_REPORT_REL_TOL * max(rU, EPS)
    rhsV = SELF_REPORT_REL_TOL * max(rV, EPS)
    if diffU > rhsU:
        return {"pass": False,
                "detail": (f"orthogonality_error_U: reported={reported_U:.6e} "
                           f"vs verifier={rU:.6e}, |Δ|={diffU:.3e} > tol={rhsU:.3e}")}
    if diffV > rhsV:
        return {"pass": False,
                "detail": (f"orthogonality_error_Vt: reported={reported_Vt:.6e} "
                           f"vs verifier={rV:.6e}, |Δ|={diffV:.3e} > tol={rhsV:.3e}")}
    return {"pass": True,
            "detail": f"U: reported={reported_U:.3e}, verifier={rU:.3e}; Vt: reported={reported_Vt:.3e}, verifier={rV:.3e}"}


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

    # 1. shape (must come first)
    checks["shape"] = check_shape(A, candidate, mode)
    if not checks["shape"]["pass"]:
        return _wrap(checks)

    # 2. finite_entries
    checks["finite_entries"] = check_finite(candidate)
    if not checks["finite_entries"]["pass"]:
        return _wrap(checks)

    U = np.asarray(candidate["U"], dtype=np.float64)
    S = np.asarray(candidate["S"], dtype=np.float64)
    Vt = np.asarray(candidate["Vt"], dtype=np.float64)

    # 3. S_nonneg_descending
    checks["S_nonneg_descending"] = check_S_nonneg_descending(S)

    # 4. U_orthonormal
    checks["U_orthonormal"] = check_U_orthonormal(A, U)

    # 5. Vt_orthonormal
    checks["Vt_orthonormal"] = check_Vt_orthonormal(A, Vt)

    # 6. factorisation_residual
    checks["factorisation_residual"] = check_factorisation_residual(A, U, S, Vt)

    # 7. self_reported_residual
    checks["self_reported_residual"] = check_self_reported_residual(
        A, U, S, Vt, float(candidate["reconstruction_error"]))

    # 8. self_reported_orthogonality
    checks["self_reported_orthogonality"] = check_self_reported_orthogonality(
        U, Vt, float(candidate["orthogonality_error_U"]),
        float(candidate["orthogonality_error_Vt"]))

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
