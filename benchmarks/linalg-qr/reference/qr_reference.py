#!/usr/bin/env python3
"""Reference Householder QR via SciPy LAPACK DGEQRF.

This is the bench's drop-in baseline: it reads the same JSON I/O
contract as the candidate, computes QR via SciPy (which calls into
LAPACK), and emits the candidate-shaped output. Used to:

  1. Sanity-check the verifier itself (a known-good implementation
     should pass every check on every case).
  2. Provide an oracle for human inspection of `expected.json`.

Usage:
  python3 qr_reference.py < input.json

Reads:  {"A": [[...], ...], "mode": "reduced" | "complete"}
Writes: {"Q": ..., "R": ..., "mode": ..., "diagonal_R": [...],
         "reconstruction_error": ..., "orthogonality_error": ...,
         "method": "householder", "warnings": []}
"""

from __future__ import annotations

import json
import sys

import numpy as np
import scipy.linalg


def qr(A: np.ndarray, mode: str) -> dict:
    """Reduced or complete QR via LAPACK DGEQRF.

    SciPy's `mode="economic"` corresponds to our `"reduced"`;
    `mode="full"` to our `"complete"`.
    """
    if mode == "reduced":
        Q, R = scipy.linalg.qr(A, mode="economic")
    elif mode == "complete":
        Q, R = scipy.linalg.qr(A, mode="full")
    else:
        raise ValueError(f"unknown mode {mode!r}")

    # Self-report diagnostics (the agent-honest output).
    A_norm = float(np.linalg.norm(A, ord="fro"))
    recon = float(np.linalg.norm(Q @ R - A, ord="fro")) / max(A_norm, 1.0)
    k = Q.shape[1]
    orth = float(np.linalg.norm(Q.T @ Q - np.eye(k), ord="fro"))

    diag_len = min(R.shape)
    diag_R = [float(R[i, i]) for i in range(diag_len)]

    return {
        "Q": Q.tolist(),
        "R": R.tolist(),
        "mode": mode,
        "diagonal_R": diag_R,
        "reconstruction_error": recon,
        "orthogonality_error": orth,
        "method": "householder",
        "warnings": [],
    }


def main() -> None:
    payload = json.load(sys.stdin)
    A = np.asarray(payload["A"], dtype=np.float64)
    mode = payload.get("mode", "reduced")
    if A.ndim != 2:
        raise ValueError(f"A must be 2-D; got shape {A.shape}")
    result = qr(A, mode)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
