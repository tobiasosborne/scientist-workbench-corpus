#!/usr/bin/env python3
"""Generate linalg-eigh golden master.

Produces:
  - inputs.json   : {cases: [{id, input}, ...]}
  - expected.json : {cases: [{id, expected}, ...]}

Reproducibility: seeded numpy.random.Generator.
The reference implementation is cross-checked against the verifier
on every (success-path) case before goldens are written.

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

from eigh_reference import eigh  # noqa: E402
from verify import verify  # noqa: E402
from mm_parser import HARWELL_BOEING_SUBSET, load_harwell_boeing  # noqa: E402

CORPUS_DIR = HERE.parent.parent / "_corpus" / "harwell-boeing"

SEED = 20260505
ENCODING_VERSION = 1


def random_symmetric(rng: np.random.Generator, n: int,
                     eigvals: np.ndarray | None = None) -> np.ndarray:
    """A = Q · diag(λ) · Qᵀ for random orthogonal Q and given eigenvalues."""
    Q, _ = np.linalg.qr(rng.standard_normal((n, n)))
    if eigvals is None:
        eigvals = rng.uniform(0.5, 2.0, size=n)
    return Q @ np.diag(eigvals) @ Q.T


def hilbert(n: int) -> np.ndarray:
    i, j = np.indices((n, n))
    return 1.0 / (i + j + 1)


def wilkinson_plus(n: int) -> np.ndarray:
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
    return alpha * np.eye(n) + np.ones((n, n))


def make_case(case_id: str, A_list: list,
              expected_override: dict | None = None) -> tuple[dict, dict]:
    A = np.asarray(A_list, dtype=np.float64)
    inp = {"A": A.tolist()}
    if expected_override is not None:
        return ({"id": case_id, "input": inp},
                {"id": case_id, "expected": expected_override})
    expected = eigh(A)
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": expected})


def make_tagged_case(case_id: str, A_list: list, tag: str,
                     payload: dict) -> tuple[dict, dict]:
    """For boundary cases — the 'expected' is a tagged value."""
    inp = {"A": A_list}
    expected = {"kind": "tagged", "tag": tag, "payload": payload}
    return ({"id": case_id, "input": inp},
            {"id": case_id, "expected": expected})


def main() -> None:
    rng = np.random.default_rng(SEED)
    cases: list[tuple[dict, dict]] = []

    # ── A. Shape edges (8) ─────────────────────────────────────────────────

    cases.append(make_case("A_1x1", [[3.0]]))
    cases.append(make_case("A_2x2_identity", np.eye(2).tolist()))
    cases.append(make_case("A_2x2_zero", np.zeros((2, 2)).tolist()))
    cases.append(make_case("A_3x3_identity", np.eye(3).tolist()))
    cases.append(make_case("A_5x5_identity", np.eye(5).tolist()))
    cases.append(make_case("A_simple_3x3",
                           [[2.0, 1.0, 0.0], [1.0, 2.0, 1.0], [0.0, 1.0, 2.0]]))
    cases.append(make_case("A_100x100_identity", np.eye(100).tolist()))
    cases.append(make_case("A_200x200_identity", np.eye(200).tolist()))

    # ── B. Random symmetric well-conditioned (7) ───────────────────────────

    for n in (5, 10, 20, 50, 100, 200):
        A = random_symmetric(rng, n)
        cases.append(make_case(f"B_rand_sym_{n}", A.tolist()))
    A = random_symmetric(rng, 30)
    cases.append(make_case("B_rand_sym_30", A.tolist()))

    # ── C. Hilbert (7) ─────────────────────────────────────────────────────

    for n in (4, 6, 8, 10, 12, 20, 50):
        cases.append(make_case(f"C_hilbert_{n}", hilbert(n).tolist()))

    # ── D'. Random symmetric ill-conditioned (4) ──────────────────────────
    # λ_i = 10^((i-1)/(n-1) · 14), giving κ ≈ 10¹⁴

    for n in (5, 10, 20, 50):
        eigvals = 10 ** np.linspace(0, 14, n)
        A = random_symmetric(rng, n, eigvals)
        cases.append(make_case(f"D_illcond_{n}", A.tolist()))

    # ── E. Wilkinson / Pei (4) ────────────────────────────────────────────

    for n in (5, 11, 21):
        cases.append(make_case(f"E_wilkinson_{n}", wilkinson_plus(n).tolist()))
    cases.append(make_case("E_pei_10", pei(10).tolist()))

    # ── F. Rank-deficient symmetric (5) ───────────────────────────────────

    u = rng.standard_normal(5)
    cases.append(make_case("F_rank1_outer_5x5", np.outer(u, u).tolist()))

    A = np.eye(5)
    A[2, 2] = 0.0
    cases.append(make_case("F_identity_zerodiag_5x5", A.tolist()))

    cases.append(make_case("F_zeros_6x6", np.zeros((6, 6)).tolist()))

    # Near-degenerate (two close eigenvalues)
    eigvals = np.array([1.0, 1.0 + 1e-13, 2.0, 3.0, 4.0])
    A = random_symmetric(rng, 5, eigvals)
    cases.append(make_case("F_near_degenerate_5x5", A.tolist()))

    # Repeated eigenvalues (non-unique Q)
    A = np.diag([1.0, 1.0, 1.0, 2.0, 2.0])
    cases.append(make_case("F_repeated_eigvals_5x5", A.tolist()))

    # ── H. Eigenvalue-spectrum stress (4) ─────────────────────────────────

    eigvals = np.array([1.0, 1.0 + 1e-10, 1.0 + 2e-10, 1.0 + 3e-10, 1.0 + 4e-10])
    A = random_symmetric(rng, 5, eigvals)
    cases.append(make_case("H_clustered_5x5", A.tolist()))

    eigvals = np.array([1e-8, 1.0, 1e8])
    A = random_symmetric(rng, 3, eigvals)
    cases.append(make_case("H_separated_extremes_3x3", A.tolist()))

    A = np.eye(5)  # all eigenvalues = 1
    cases.append(make_case("H_all_same_5x5", A.tolist()))

    eigvals = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    A = random_symmetric(rng, 5, eigvals)
    cases.append(make_case("H_alternating_signs_5x5", A.tolist()))

    # ── I. Industrial (NIST harwell-boeing — SPD structural matrices) ─────

    for name, expected_n, kind, source in HARWELL_BOEING_SUBSET:
        try:
            A = load_harwell_boeing(name, CORPUS_DIR)
        except FileNotFoundError as e:
            print(f"  WARN  Tier I skipped {name}: {e}", file=sys.stderr)
            continue
        # All bcsstk* are symmetric by construction (mass/stiffness matrices).
        # Belt-and-braces symmetrise to dodge LSB asymmetry from the parser.
        A = (A + A.T) / 2.0
        cases.append(make_case(f"I_{name}", A.tolist()))

    # ── J. Stress (post-ADR-0016 uncapped regime) ─────────────────────────

    A = random_symmetric(rng, 500)
    cases.append(make_case("J_stress_500x500", A.tolist()))

    # ── K. Boundary (3 tagged cases) ──────────────────────────────────────
    # These don't go through `eigh()` — the expected output is a tagged
    # boundary value. The tool under test must produce the same tag.

    A_asym = [[1.0, 2.0], [3.0, 1.0]]  # max asymmetry: |2 − 3| = 1
    cases.append(make_tagged_case(
        "K_non_symmetric_2x2", A_asym,
        "linalg-eigh/non-symmetric-input",
        {"row": 0, "col": 1, "value": "1.0", "max_asymmetry": "1.0"},
    ))

    A_inf = [[1.0, 2.0], [2.0, float("inf")]]
    # JSON can't carry Inf; we encode as a string boundary the input parser
    # would have rejected. The bench harness encodes Inf via the JSON
    # number system though; let's substitute a very large representable
    # number for the wire, matching what the tool would receive in JSON.
    # Actually — JSON does not support Inf. Skip this case from the
    # generator side; the tool's non-finite-input path is exercised by
    # tools/linalg-eigh/goldens/ instead (those use the canonical Value
    # encoding which does represent Inf via float64 hex bits).

    # (degenerate-shape n=0 isn't easily representable in raw JSON
    # — `[]` is 1D ndim=1 in numpy, not (0, 0). The tool's own goldens
    # under tools/linalg-eigh/goldens/ cover this path via canonical
    # Value encoding which can carry the 2-D-empty shape cleanly.)

    # ── Cross-check: reference passes verifier on every success-path case ──

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
        "problem": "linalg-eigh",
        "cases": [c[0] for c in cases],
    }
    expected_payload = {
        "encoding_version": ENCODING_VERSION,
        "seed": SEED,
        "problem": "linalg-eigh",
        "cases": [c[1] for c in cases],
    }

    (HERE / "inputs.json").write_text(json.dumps(inputs_payload) + "\n")
    (HERE / "expected.json").write_text(json.dumps(expected_payload) + "\n")

    print(f"wrote {len(cases)} cases to inputs.json and expected.json")


if __name__ == "__main__":
    main()
