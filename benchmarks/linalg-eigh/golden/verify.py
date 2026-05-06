#!/usr/bin/env python3
"""linalg-eigh verifier — invariant-based, language-neutral.

stdin:
  {"input": {"A": [[float, ...], ...]},
   "candidate": {"Q": ..., "eigenvalues": [...],
                 "reconstruction_error": ..., "orthogonality_error": ...,
                 "condition_number": ..., "method": ..., "warnings": [...]}
                OR a tagged boundary {"kind": "tagged", "tag": "...", "payload": {...}},
   "id"?: str}

stdout:
  {"pass": bool, "reason": str, "checks": {<name>: {"pass": bool, "detail": str}}}

Runs 7 independent checks per case (see verifier_protocol.md):
  1. shape                       — Q n×n; eigenvalues length n; fields present
  2. finite_entries              — no NaN, no ±Inf
  3. eigenvalues_ascending       — λ[i] ≤ λ[i+1] within tol_struct
  4. Q_orthonormal               — ||QᵀQ − I||_F within tol_orth
  5. eigendecomp_residual        — ||A·Q − Q·diag(λ)||_F within tol_recon · ||A||_F
  6. self_reported_residual      — candidate's recon claim is honest
  7. self_reported_orthogonality — candidate's orth claim is honest

For tagged boundaries (non-symmetric-input, non-finite-input,
degenerate-shape), the verifier runs a single combined check:
"the boundary is justified by the input."

Tolerances: Higham 2002 §20.6 (symmetric eigh backward stability),
with 100× empirical safety factor.
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
SYMMETRY_TOL_FACTOR = 100.0 * EPS


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


def _is_symmetric(A: np.ndarray) -> bool:
    if A.shape[0] != A.shape[1]:
        return False
    if A.size == 0:
        return True
    max_abs = float(np.max(np.abs(A))) if A.size > 0 else 0.0
    if max_abs == 0.0:
        return True
    asym = float(np.max(np.abs(A - A.T)))
    return asym <= SYMMETRY_TOL_FACTOR * max_abs


# --- per-check implementations ----------------------------------------------


def check_shape(A: np.ndarray, candidate: dict) -> dict:
    n = A.shape[0]
    expected_Q = (n, n)

    required = {
        "Q", "eigenvalues",
        "reconstruction_error", "orthogonality_error",
        "condition_number", "method", "warnings",
    }
    missing = required - set(candidate.keys())
    if missing:
        return {"pass": False, "detail": f"missing fields: {sorted(missing)}"}

    if not isinstance(candidate["method"], str):
        return {"pass": False, "detail": "method must be a string"}

    if not isinstance(candidate["warnings"], list) or \
       not all(isinstance(s, str) for s in candidate["warnings"]):
        return {"pass": False, "detail": "warnings must be list[str]"}

    sQ = _shape_of(candidate["Q"])
    if sQ != expected_Q:
        return {"pass": False,
                "detail": f"Q shape {sQ}, expected {expected_Q}"}

    sEV = _vec_len(candidate["eigenvalues"])
    if sEV != n:
        return {"pass": False,
                "detail": f"eigenvalues length {sEV}, expected {n}"}

    return {"pass": True, "detail": f"Q {sQ}, eigenvalues length {sEV}"}


def check_finite(candidate: dict) -> dict:
    for field in ("Q", "eigenvalues"):
        if not _is_finite_array(candidate[field]):
            return {"pass": False, "detail": f"non-finite entry in {field}"}
    for field in ("reconstruction_error", "orthogonality_error", "condition_number"):
        v = candidate[field]
        if not isinstance(v, (int, float)) or not np.isfinite(v):
            return {"pass": False, "detail": f"non-finite or non-numeric {field}"}
    return {"pass": True, "detail": "all entries finite"}


def check_eigenvalues_ascending(eigenvalues: np.ndarray) -> dict:
    if len(eigenvalues) == 0:
        return {"pass": True, "detail": "empty eigenvalues"}
    lam_max = float(np.max(np.abs(eigenvalues)))
    tol_struct = SAFETY * EPS * max(lam_max, 1.0)
    for i in range(len(eigenvalues) - 1):
        if eigenvalues[i] > eigenvalues[i + 1] + tol_struct:
            return {"pass": False,
                    "detail": (f"eigenvalues[{i}]={eigenvalues[i]:.3e} > "
                               f"eigenvalues[{i+1}]={eigenvalues[i+1]:.3e} "
                               f"(non-ascending; gap "
                               f"{eigenvalues[i] - eigenvalues[i+1]:.3e} > tol={tol_struct:.3e})")}
    return {"pass": True,
            "detail": f"eigenvalues ascending: λ_min={eigenvalues[0]:.3e}, λ_max={eigenvalues[-1]:.3e}"}


def check_Q_orthonormal(A: np.ndarray, Q: np.ndarray) -> dict:
    n = A.shape[0]
    tol = SAFETY * EPS * n * np.sqrt(n)
    err = float(np.linalg.norm(Q.T @ Q - np.eye(n), ord="fro"))
    if err > tol:
        return {"pass": False,
                "detail": f"||QᵀQ − I||_F = {err:.3e} > tol = {tol:.3e}"}
    return {"pass": True, "detail": f"||QᵀQ − I||_F = {err:.3e} ≤ tol = {tol:.3e}"}


def check_eigendecomp_residual(A: np.ndarray, Q: np.ndarray,
                                eigenvalues: np.ndarray) -> dict:
    n = A.shape[0]
    A_norm = float(np.linalg.norm(A, ord="fro"))
    tol_factor = SAFETY * EPS * n * np.sqrt(n)

    Lambda = np.diag(eigenvalues)
    err = float(np.linalg.norm(A @ Q - Q @ Lambda, ord="fro"))

    if A_norm == 0.0:
        if err > 0.0:
            return {"pass": False,
                    "detail": f"||AQ − Qdiag(λ)||_F = {err:.3e} but A is zero"}
        return {"pass": True, "detail": "zero input, zero residual"}

    rel = err / A_norm
    if rel > tol_factor:
        return {"pass": False,
                "detail": f"||AQ − Qdiag(λ)||_F / ||A||_F = {rel:.3e} > tol = {tol_factor:.3e}"}
    return {"pass": True,
            "detail": f"||AQ − Qdiag(λ)||_F / ||A||_F = {rel:.3e} ≤ tol = {tol_factor:.3e}"}


def check_self_reported_residual(A: np.ndarray, Q: np.ndarray,
                                  eigenvalues: np.ndarray, reported: float) -> dict:
    A_norm = float(np.linalg.norm(A, ord="fro"))
    Lambda = np.diag(eigenvalues)
    recomputed = float(np.linalg.norm(A @ Q - Q @ Lambda, ord="fro")) / max(A_norm, 1.0)
    diff = abs(reported - recomputed)
    rhs = SELF_REPORT_REL_TOL * max(recomputed, EPS)
    if diff > rhs:
        return {"pass": False,
                "detail": (f"reported reconstruction_error={reported:.6e} "
                           f"vs verifier={recomputed:.6e}, "
                           f"|Δ|={diff:.3e} > tol={rhs:.3e}")}
    return {"pass": True,
            "detail": f"reported={reported:.6e}, verifier={recomputed:.6e}"}


def check_self_reported_orthogonality(Q: np.ndarray, reported: float) -> dict:
    n = Q.shape[1]
    recomputed = float(np.linalg.norm(Q.T @ Q - np.eye(n), ord="fro"))
    diff = abs(reported - recomputed)
    rhs = SELF_REPORT_REL_TOL * max(recomputed, EPS)
    if diff > rhs:
        return {"pass": False,
                "detail": (f"reported orthogonality_error={reported:.6e} "
                           f"vs verifier={recomputed:.6e}, "
                           f"|Δ|={diff:.3e} > tol={rhs:.3e}")}
    return {"pass": True,
            "detail": f"reported={reported:.6e}, verifier={recomputed:.6e}"}


# --- tagged-boundary handling ------------------------------------------------


def is_tagged(candidate: Any) -> bool:
    return isinstance(candidate, dict) and candidate.get("kind") == "tagged"


def verify_tagged(A: np.ndarray, candidate: dict, A_input: list) -> dict:
    """Validate that the boundary tag matches the input's actual category."""
    tag = candidate.get("tag", "")
    if tag == "linalg-eigh/non-finite-input":
        if not np.all(np.isfinite(A)):
            return {"pass": True, "reason": "non-finite-input correctly tagged",
                    "checks": {"boundary": {"pass": True, "detail": tag}}}
        return {"pass": False, "reason": "non-finite-input tagged but A is all-finite",
                "checks": {"boundary": {"pass": False, "detail": "misclassified"}}}
    if tag == "linalg-eigh/degenerate-shape":
        if A.shape[0] == 0 or A.shape[1] == 0:
            return {"pass": True, "reason": "degenerate-shape correctly tagged",
                    "checks": {"boundary": {"pass": True, "detail": tag}}}
        return {"pass": False, "reason": "degenerate-shape tagged but A non-empty",
                "checks": {"boundary": {"pass": False, "detail": "misclassified"}}}
    if tag == "linalg-eigh/non-symmetric-input":
        if not _is_symmetric(A):
            return {"pass": True, "reason": "non-symmetric-input correctly tagged",
                    "checks": {"boundary": {"pass": True, "detail": tag}}}
        return {"pass": False, "reason": "non-symmetric-input tagged but A is symmetric",
                "checks": {"boundary": {"pass": False, "detail": "misclassified"}}}
    return {"pass": False, "reason": f"unknown tag {tag!r}",
            "checks": {"boundary": {"pass": False, "detail": tag}}}


# --- top-level verify --------------------------------------------------------


def verify(payload: dict) -> dict:
    inp = payload["input"]
    candidate = payload["candidate"]

    if not isinstance(candidate, dict):
        return {"pass": False, "reason": "candidate must be a JSON object", "checks": {}}

    A = np.asarray(inp["A"], dtype=np.float64)
    if A.ndim != 2:
        return {"pass": False, "reason": f"A must be 2-D; got ndim={A.ndim}", "checks": {}}

    # Tagged boundary path.
    if is_tagged(candidate):
        return verify_tagged(A, candidate, inp["A"])

    # Success path.
    if A.shape[0] != A.shape[1]:
        return {"pass": False, "reason": f"A must be square; got shape {A.shape}", "checks": {}}

    checks: dict[str, dict] = {}

    # 1. shape
    checks["shape"] = check_shape(A, candidate)
    if not checks["shape"]["pass"]:
        return _wrap(checks)

    # 2. finite_entries
    checks["finite_entries"] = check_finite(candidate)
    if not checks["finite_entries"]["pass"]:
        return _wrap(checks)

    Q = np.asarray(candidate["Q"], dtype=np.float64)
    eigenvalues = np.asarray(candidate["eigenvalues"], dtype=np.float64)

    # 3. eigenvalues_ascending
    checks["eigenvalues_ascending"] = check_eigenvalues_ascending(eigenvalues)

    # 4. Q_orthonormal
    checks["Q_orthonormal"] = check_Q_orthonormal(A, Q)

    # 5. eigendecomp_residual
    checks["eigendecomp_residual"] = check_eigendecomp_residual(A, Q, eigenvalues)

    # 6. self_reported_residual
    checks["self_reported_residual"] = check_self_reported_residual(
        A, Q, eigenvalues, float(candidate["reconstruction_error"]))

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
