#!/usr/bin/env python3
"""Reference SVD via SciPy LAPACK DGESDD.

Bench drop-in baseline. Reads the same JSON I/O contract as the
candidate, computes SVD via SciPy (LAPACK divide-and-conquer
backend), emits the candidate-shaped output. Used to:

  1. Sanity-check the verifier itself.
  2. Provide an oracle for human inspection of `expected.json`.

Usage:
  python3 svd_reference.py < input.json
"""

from __future__ import annotations

import json
import sys

import numpy as np
import scipy.linalg

EPS = 2.220446049250313e-16


def svd(A: np.ndarray, mode: str) -> dict:
    """Reduced or complete SVD via LAPACK DGESDD.

    SciPy's `full_matrices=False` corresponds to our `"reduced"`;
    `full_matrices=True` to `"complete"`.
    """
    if mode == "reduced":
        U, S, Vt = scipy.linalg.svd(A, full_matrices=False)
    elif mode == "complete":
        U, S, Vt = scipy.linalg.svd(A, full_matrices=True)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    A_norm = float(np.linalg.norm(A, ord="fro"))
    Sigma = np.zeros((U.shape[1], Vt.shape[0]))
    for i in range(len(S)):
        Sigma[i, i] = S[i]
    recon = float(np.linalg.norm(U @ Sigma @ Vt - A, ord="fro")) / max(A_norm, 1.0)
    orth_U = float(np.linalg.norm(U.T @ U - np.eye(U.shape[1]), ord="fro"))
    orth_Vt = float(np.linalg.norm(Vt @ Vt.T - np.eye(Vt.shape[0]), ord="fro"))

    m, n = A.shape
    s_max = float(S[0]) if len(S) > 0 else 0.0
    s_min = float(S[-1]) if len(S) > 0 else 0.0
    if s_min > 0:
        cond = s_max / s_min
    else:
        cond = s_max / max(EPS * s_max, EPS)
    rtol = max(m, n) * EPS * s_max
    rank = int(np.sum(S > rtol))

    return {
        "U": U.tolist(),
        "S": S.tolist(),
        "Vt": Vt.tolist(),
        "mode": mode,
        "reconstruction_error": recon,
        "orthogonality_error_U": orth_U,
        "orthogonality_error_Vt": orth_Vt,
        "condition_number": cond,
        "rank_estimate": rank,
        "method": "golub-reinsch",
        "warnings": [],
    }


def main() -> None:
    payload = json.load(sys.stdin)
    A = np.asarray(payload["A"], dtype=np.float64)
    mode = payload.get("mode", "reduced")
    if A.ndim != 2:
        raise ValueError(f"A must be 2-D; got shape {A.shape}")
    result = svd(A, mode)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
