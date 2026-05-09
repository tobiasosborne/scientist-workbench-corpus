#!/usr/bin/env python3
"""Generate linalg-qr golden master.

Produces:
  - inputs.json   : {cases: [{id, input}, ...]}
  - expected.json : {cases: [{id, expected}, ...]}

Reproducibility: seeded numpy.random.Generator, seed pinned below.
The reference implementation is cross-checked against the verifier
on every case before the goldens are written, so the bench cannot
ship a case the reference fails.

Run:
  python3 generate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reference"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "_corpus"))

from qr_reference import qr  # noqa: E402
from verify import verify  # noqa: E402
from mm_parser import HARWELL_BOEING_SUBSET, load_harwell_boeing  # noqa: E402

CORPUS_DIR = HERE.parent.parent / "_corpus" / "harwell-boeing"

SEED = 20260505
ENCODING_VERSION = 1


# --- test-matrix constructors ------------------------------------------------


def random_well_conditioned(rng: np.random.Generator, m: int, n: int) -> np.ndarray:
    """A = U · Σ · V^T with U, V random orthogonal, Σ_ii uniform in [0.5, 2]."""
    k = min(m, n)
    U = np.linalg.qr(rng.standard_normal((m, m)))[0]
    V = np.linalg.qr(rng.standard_normal((n, n)))[0]
    sigma = rng.uniform(0.5, 2.0, size=k)
    Sigma = np.zeros((m, n))
    Sigma[np.arange(k), np.arange(k)] = sigma
    return U @ Sigma @ V.T


def hilbert(n: int) -> np.ndarray:
    i, j = np.indices((n, n))
    return 1.0 / (i + j + 1)


def vandermonde(n: int) -> np.ndarray:
    """V[i, j] = x_i^j for x_i = i / (n − 1) on [0, 1] (uniform nodes)."""
    x = np.linspace(0.0, 1.0, n)
    return np.vander(x, n, increasing=True)


def wilkinson_plus(n: int) -> np.ndarray:
    """W^+_n: tridiagonal with diag = (|i − ⌊n/2⌋| or similar), 1 off."""
    if n % 2 == 0:
        raise ValueError("Wilkinson W^+_n is defined for odd n only")
    half = (n - 1) // 2
    diag = np.abs(np.arange(n) - half).astype(float)
    A = np.diag(diag)
    for i in range(n - 1):
        A[i, i + 1] = 1.0
        A[i + 1, i] = 1.0
    return A


def pei(n: int, alpha: float = 1.0) -> np.ndarray:
    """Pei matrix αI + ee^T."""
    return alpha * np.eye(n) + np.ones((n, n))


def frank(n: int) -> np.ndarray:
    """f_ij = n + 1 − max(i, j) for 1-indexed (i, j); lower-Hessenberg."""
    A = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            A[i, j] = n - max(i, j)
    return A


# --- case construction -------------------------------------------------------


def make_case(case_id: str, A_list: list, mode: str = "reduced") -> tuple[dict, dict]:
    A = np.asarray(A_list, dtype=np.float64)
    inp = {"A": A.tolist()}
    if mode != "reduced":
        inp["mode"] = mode
    expected = qr(A, mode)
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": expected})


def main() -> None:
    rng = np.random.default_rng(SEED)
    cases: list[tuple[dict, dict]] = []

    # ── A. Shape edges (10) ─────────────────────────────────────────────────

    cases.append(make_case("A_1x1", [[3.0]]))
    cases.append(make_case("A_2x1", [[1.0], [2.0]]))
    cases.append(make_case("A_1x2", [[1.0, 2.0]]))
    cases.append(make_case("A_2x2_identity", np.eye(2).tolist()))
    cases.append(make_case("A_2x2_zero", np.zeros((2, 2)).tolist()))
    cases.append(make_case("A_3x3_identity", np.eye(3).tolist()))
    cases.append(make_case("A_5x3_simple",
                           [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 10.0],
                            [1.0, 0.0, 1.0], [0.0, 1.0, 0.0]]))
    cases.append(make_case("A_3x5_simple",
                           [[1.0, 2.0, 3.0, 4.0, 5.0],
                            [2.0, 1.0, 0.0, -1.0, 1.0],
                            [3.0, 0.0, -1.0, 2.0, 1.0]]))
    cases.append(make_case("A_100x100_identity", np.eye(100).tolist()))
    cases.append(make_case("A_200x200_identity", np.eye(200).tolist()))

    # ── B. Random well-conditioned (8) ──────────────────────────────────────

    for n in (5, 10, 20, 50, 100, 200):
        A = random_well_conditioned(rng, n, n)
        cases.append(make_case(f"B_rand_{n}x{n}", A.tolist()))
    A = random_well_conditioned(rng, 50, 20)
    cases.append(make_case("B_rand_50x20", A.tolist()))
    A = random_well_conditioned(rng, 20, 50)
    cases.append(make_case("B_rand_20x50", A.tolist()))

    # ── C. Hilbert (7) ──────────────────────────────────────────────────────

    for n in (4, 6, 8, 10, 12, 20, 50):
        cases.append(make_case(f"C_hilbert_{n}", hilbert(n).tolist()))

    # ── D. Vandermonde (4) ─────────────────────────────────────────────────

    for n in (5, 10, 15, 20):
        cases.append(make_case(f"D_vandermonde_{n}", vandermonde(n).tolist()))

    # ── E. Wilkinson / Pei / Frank (5) ─────────────────────────────────────

    for n in (5, 11, 21):
        cases.append(make_case(f"E_wilkinson_{n}", wilkinson_plus(n).tolist()))
    cases.append(make_case("E_pei_10", pei(10).tolist()))
    cases.append(make_case("E_frank_12", frank(12).tolist()))

    # ── F. Rank-deficient (5) ──────────────────────────────────────────────

    u = rng.standard_normal(5)
    v = rng.standard_normal(5)
    cases.append(make_case("F_rank1_outer_5x5", np.outer(u, v).tolist()))

    A = np.eye(5)
    A[:, 2] = 0.0
    cases.append(make_case("F_identity_zerocol_5x5", A.tolist()))

    A = rng.standard_normal((8, 4))
    A[1, :] = A[0, :] + 1e-13 * rng.standard_normal(4)  # near-equal rows
    cases.append(make_case("F_near_equal_rows_8x4", A.tolist()))

    cases.append(make_case("F_zeros_6x6", np.zeros((6, 6)).tolist()))

    H = hilbert(8)
    H_padded = np.zeros((8, 9))
    H_padded[:, :8] = H
    cases.append(make_case("F_hilbert_zerocol_8x9", H_padded.tolist()))

    # ── G. Tall-and-skinny / short-and-fat (6) ─────────────────────────────

    for (m, n) in ((50, 3), (100, 5), (200, 10)):
        A = rng.standard_normal((m, n))
        cases.append(make_case(f"G_tall_{m}x{n}", A.tolist()))
    for (m, n) in ((3, 50), (5, 100), (10, 200)):
        A = rng.standard_normal((m, n))
        cases.append(make_case(f"G_fat_{m}x{n}", A.tolist()))

    # ── I. Industrial: NIST Matrix Market harwell-boeing dense subset ──────
    # Real test matrices from structural-engineering / fluid-dynamics codes
    # used by the wider numerical-computing community since the 1980s. See
    # bench/_corpus/mm_parser.py for the curated list and provenance.
    for name, expected_n, kind, source in HARWELL_BOEING_SUBSET:
        try:
            A = load_harwell_boeing(name, CORPUS_DIR)
        except FileNotFoundError as e:
            print(f"  WARN  Tier I skipped {name}: {e}", file=sys.stderr)
            continue
        if A.shape[0] != expected_n:
            print(f"  WARN  Tier I {name} expected {expected_n}, got {A.shape[0]}",
                  file=sys.stderr)
        cases.append(make_case(f"I_{name}", A.tolist()))

    # ── J. Stress: large random (post-ADR-0016 uncapped regime) ───────────
    # The bench previously stopped at n=200 (the ADR-0014 cap). With the
    # cap lifted (ADR-0016), exercise n=500 and n=1000 to validate that the
    # tools actually run there and emit the expected scale warnings.
    A = random_well_conditioned(rng, 500, 500)
    cases.append(make_case("J_stress_500x500", A.tolist()))
    A = random_well_conditioned(rng, 1000, 1000)
    cases.append(make_case("J_stress_1000x1000", A.tolist()))

    # ── H. Complete-mode (4) ───────────────────────────────────────────────

    cases.append(make_case("H_complete_5x3",
                           [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 10.0],
                            [1.0, 0.0, 1.0], [0.0, 1.0, 0.0]],
                           mode="complete"))
    A = random_well_conditioned(rng, 20, 8)
    cases.append(make_case("H_complete_rand_20x8", A.tolist(), mode="complete"))
    cases.append(make_case("H_complete_hilbert_10", hilbert(10).tolist(),
                           mode="complete"))
    A = rng.standard_normal((100, 5))
    cases.append(make_case("H_complete_tall_100x5", A.tolist(), mode="complete"))

    # ── Cross-check: reference passes verifier on every case ────────────────

    print(f"cross-checking reference against verifier on {len(cases)} cases...")
    fail_count = 0
    for inp, exp in cases:
        result = verify({"input": inp["input"], "candidate": exp["expected"], "id": inp["id"]})
        if not result["pass"]:
            fail_count += 1
            print(f"  FAIL  {inp['id']}: {result['reason']}")
    if fail_count:
        print(f"\n{fail_count} reference cases failed verifier — refusing to write goldens")
        sys.exit(1)
    print(f"  all {len(cases)} reference cases pass — writing goldens\n")

    inputs_payload = {
        "encoding_version": ENCODING_VERSION,
        "seed": SEED,
        "problem": "linalg-qr",
        "cases": [c[0] for c in cases],
    }
    expected_payload = {
        "encoding_version": ENCODING_VERSION,
        "seed": SEED,
        "problem": "linalg-qr",
        "cases": [c[1] for c in cases],
    }

    (HERE / "inputs.json").write_text(json.dumps(inputs_payload) + "\n")
    (HERE / "expected.json").write_text(json.dumps(expected_payload) + "\n")

    print(f"wrote {len(cases)} cases to inputs.json and expected.json")


if __name__ == "__main__":
    main()
