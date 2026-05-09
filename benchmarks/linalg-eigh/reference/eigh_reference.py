#!/usr/bin/env python3
"""Reference symmetric eigh via SciPy LAPACK DSYEVD.

Reads JSON I/O contract; emits candidate-shaped output. Used to:
  1. Sanity-check the verifier itself.
  2. Provide an oracle for human inspection of expected.json.

Usage:
  python3 eigh_reference.py < input.json
"""

from __future__ import annotations

import json
import sys

import numpy as np
import scipy.linalg

EPS = 2.220446049250313e-16


def eigh(A: np.ndarray) -> dict:
    """Symmetric eigh via LAPACK DSYEVD."""
    # eigvalsh returns ascending; eigh returns ascending eigenvalues
    # and corresponding orthogonal eigenvectors as columns of Q.
    eigenvalues, Q = scipy.linalg.eigh(A)

    # Self-report diagnostics.
    A_norm = float(np.linalg.norm(A, ord="fro"))
    n = A.shape[0]
    Lambda = np.diag(eigenvalues)
    recon = float(np.linalg.norm(A @ Q - Q @ Lambda, ord="fro")) / max(A_norm, 1.0)
    orth = float(np.linalg.norm(Q.T @ Q - np.eye(n), ord="fro"))

    # Condition number on |λ| (spectral condition for symmetric A).
    abs_lam = np.abs(eigenvalues)
    lam_max = float(abs_lam.max()) if n > 0 else 0.0
    lam_min = float(abs_lam.min()) if n > 0 else 0.0
    if lam_min > 0:
        cond = lam_max / lam_min
    else:
        cond = lam_max / max(EPS * lam_max, EPS)

    return {
        "Q": Q.tolist(),
        "eigenvalues": eigenvalues.tolist(),
        "reconstruction_error": recon,
        "orthogonality_error": orth,
        "condition_number": cond,
        "method": "tridiag-qr",  # SciPy's DSYEVD path
        "warnings": [],
    }


def main() -> None:
    payload = json.load(sys.stdin)
    A = np.asarray(payload["A"], dtype=np.float64)
    if A.ndim != 2:
        raise ValueError(f"A must be 2-D; got shape {A.shape}")
    if A.shape[0] != A.shape[1]:
        raise ValueError(f"A must be square; got shape {A.shape}")

    # Boundary: non-finite input → tagged.
    if not np.all(np.isfinite(A)):
        # Find first non-finite coordinate.
        for i in range(A.shape[0]):
            for j in range(A.shape[1]):
                v = A[i, j]
                if not np.isfinite(v):
                    val = "NaN" if np.isnan(v) else ("Infinity" if v > 0 else "-Infinity")
                    result = {
                        "kind": "tagged",
                        "tag": "linalg-eigh/non-finite-input",
                        "payload": {"row": i, "col": j, "value": val},
                    }
                    json.dump(result, sys.stdout)
                    sys.stdout.write("\n")
                    return

    # Boundary: non-symmetric input → tagged. Threshold: max|A − Aᵀ| >
    # 100·EPS·max|A| matches the verifier's _is_symmetric.
    SYMMETRY_TOL_FACTOR = 100.0 * 2.220446049250313e-16
    n = A.shape[0]
    if n > 0:
        max_abs = float(np.max(np.abs(A))) if A.size > 0 else 0.0
        if max_abs > 0:
            asym_mat = np.abs(A - A.T)
            asym = float(np.max(asym_mat))
            if asym > SYMMETRY_TOL_FACTOR * max_abs:
                idx = np.unravel_index(np.argmax(asym_mat), asym_mat.shape)
                i, j = int(idx[0]), int(idx[1])
                result = {
                    "kind": "tagged",
                    "tag": "linalg-eigh/non-symmetric-input",
                    "payload": {
                        "row": i,
                        "col": j,
                        "value": str(float(A[i, j] - A[j, i])),
                        "max_asymmetry": str(asym),
                    },
                }
                json.dump(result, sys.stdout)
                sys.stdout.write("\n")
                return

    # Boundary: degenerate-shape n = 0 (rare in raw JSON but possible).
    if n == 0:
        result = {
            "kind": "tagged",
            "tag": "linalg-eigh/degenerate-shape",
            "payload": {"m": 0, "n": 0},
        }
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
        return

    result = eigh(A)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
