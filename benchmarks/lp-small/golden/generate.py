#!/usr/bin/env python3
# =============================================================================
# benchmarks/lp-small/golden/generate.py — golden master for lp-small.
# =============================================================================
#
# Produces:
#   - inputs.json   : { encoding_version, seed, problem, cases: [{id, input,
#                       meta}, ...] }
#   - expected.json : { cases: [{id, expected}, ...] }
#
# Each case carries a canonical SCS-form LP in `input` (ADR-0030 §C,
# raw-JSON projection) and per-case provenance / family classification
# in `meta`.  The `expected` record carries the dual-witness consensus
# objective + agreement flag plus the consensus `status`.
#
# Reproducibility: SEED is the master rng seed; per-case generators
# derive their own subseed from family-name + size.  Re-running this
# script with the same Gurobi / Mosek versions on the same platform
# fingerprint produces bit-identical outputs.
#
# Tracer scope (v0.1 of this file):
#   - 3 cases, one per certificate path (optimal / infeasible / unbounded).
#   - The full 8-family / 40-case taxonomy from DESCRIPTION.md lands
#     incrementally; the tracer's job is to close the validate→grade loop.
#
# Run:
#   python3 golden/generate.py
#
# Then re-pin the manifest hashes:
#   python3 -c "import hashlib; …" or `bun src/cli.ts validate` (which
#   does not currently compare hashes — pinning is informational v0.1).

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE          = Path(__file__).resolve().parent
SUITE_DIR     = HERE.parent
CORPUS_ROOT   = SUITE_DIR.parent.parent
ORACLE_GUROBI = CORPUS_ROOT / "adapters/gurobi/oracles/gurobi-lp.py"
ORACLE_MOSEK  = CORPUS_ROOT / "adapters/mosek/oracles/mosek-lp.py"

SEED              = 20260511
ENCODING_VERSION  = 1
AGREEMENT_TOL_REL = 1e-8


# ─── case builders ──────────────────────────────────────────────────────────


def case_optimal_minimal() -> dict:
    """A_minimal_optimal — 2 vars, 1 equality, both ≥ 0.  Optimum (1, 0)."""
    return {
        "id": "A_minimal_optimal",
        "input": {
            "minimize": {"c": [1.0, 2.0]},
            "subjectTo": {
                "Ax_eq_b": {"A": [[1.0, 1.0]], "b": [1.0]},
                "cones":   [{"head": "NonNegCone", "indices": [0, 1]}],
            },
            "precision": 1e-8,
        },
        "meta": {
            "family": "A_random_dense",
            "expected_status": "optimal",
            "comment": "tracer — minimal LP for closing the validate→grade loop",
        },
    }


def case_infeasible_simple() -> dict:
    """F_infeasible_simple — x = 1 and x = 2 simultaneously."""
    return {
        "id": "F_infeasible_simple",
        "input": {
            "minimize": {"c": [0.0]},
            "subjectTo": {
                "Ax_eq_b": {"A": [[1.0], [1.0]], "b": [1.0, 2.0]},
                "cones":   [{"head": "NonNegCone", "indices": [0]}],
            },
            "precision": 1e-8,
        },
        "meta": {
            "family": "F_near_infeasible",
            "expected_status": "infeasible",
            "comment": "tracer — exactly-infeasible LP (two contradicting equalities)",
        },
    }


def case_unbounded_simple() -> dict:
    """G_unbounded_simple — min −x s.t. x ≥ 0."""
    return {
        "id": "G_unbounded_simple",
        "input": {
            "minimize": {"c": [-1.0]},
            "subjectTo": {"cones": [{"head": "NonNegCone", "indices": [0]}]},
            "precision": 1e-8,
        },
        "meta": {
            "family": "G_unbounded",
            "expected_status": "unbounded",
            "comment": "tracer — primal-unbounded LP (no equality constraints)",
        },
    }


def all_tracer_cases() -> list[dict]:
    return [
        case_optimal_minimal(),
        case_infeasible_simple(),
        case_unbounded_simple(),
    ]


# ─── oracle invocation ──────────────────────────────────────────────────────


def run_oracle(oracle_path: Path, problem: dict) -> dict:
    """Pipe problem JSON through the oracle subprocess and parse stdout."""
    proc = subprocess.run(
        [sys.executable, str(oracle_path)],
        input=json.dumps(problem).encode(),
        capture_output=True,
        check=True,
        timeout=120,
    )
    return json.loads(proc.stdout)


def build_consensus(g: dict, m: dict) -> dict:
    """Compare two oracle records; return the consensus block to pin.

    `objective` is present in the oracle record iff status == "optimal"
    (the oracle adapters use field-absence to encode ±∞ / NaN — see the
    adapters' headers).  This function mirrors that convention: the
    consensus block carries the per-oracle objective only when both
    oracles reached optimality.
    """
    if g["status"] != m["status"]:
        return {
            "agreement":     False,
            "agreement_tol": AGREEMENT_TOL_REL,
            "reason":        f"status mismatch: gurobi={g['status']} mosek={m['status']}",
        }

    if g["status"] == "optimal":
        og = g["objective"]
        om = m["objective"]
        diff = abs(og - om)
        scale = max(1.0, abs(og), abs(om))
        agree = diff <= AGREEMENT_TOL_REL * scale
        return {
            "objective_gurobi": og,
            "objective_mosek":  om,
            "agreement":        agree,
            "agreement_tol":    AGREEMENT_TOL_REL,
            "rel_diff":         diff / scale,
        }

    # For infeasible / unbounded the *status* is the consensus.  No
    # objective values to compare (oracles omit `objective` on these
    # paths).  Agreement = the two statuses match.
    return {
        "agreement":     True,
        "agreement_tol": AGREEMENT_TOL_REL,
        "rationale":     f"status-only consensus ({g['status']})",
    }


def build_expected(g: dict, m: dict) -> dict:
    """Build the expected.json record.  `objective` present iff optimal."""
    consensus = build_consensus(g, m)
    expected: dict[str, Any] = {"status": g["status"], "consensus": consensus}
    if g["status"] == "optimal" and consensus.get("agreement"):
        # Mean of the two oracle objectives, both within 1e-8 relative.
        expected["objective"] = (g["objective"] + m["objective"]) / 2.0
    return expected


# ─── main ───────────────────────────────────────────────────────────────────


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    cases = all_tracer_cases()
    inputs_doc: dict[str, Any] = {
        "encoding_version": ENCODING_VERSION,
        "seed":             SEED,
        "problem":          "lp-small — small linear programs (n ≤ 100) for the resource-limited regime",
        "cases":            [{"id": c["id"], "input": c["input"], "meta": c["meta"]} for c in cases],
    }
    expected_cases: list[dict] = []
    for c in cases:
        g = run_oracle(ORACLE_GUROBI, c["input"])
        m = run_oracle(ORACLE_MOSEK,  c["input"])
        expected = build_expected(g, m)
        expected_cases.append({"id": c["id"], "expected": expected})
        agree = expected["consensus"].get("agreement", False)
        print(f"  {c['id']:<30}  gurobi={g['status']:<12}  mosek={m['status']:<12}  agree={agree}", file=sys.stderr)

    expected_doc: dict[str, Any] = {"cases": expected_cases}

    inputs_path   = HERE / "inputs.json"
    expected_path = HERE / "expected.json"
    inputs_path.write_text(json.dumps(inputs_doc, indent=2, allow_nan=False) + "\n")
    expected_path.write_text(json.dumps(expected_doc, indent=2, allow_nan=False) + "\n")

    print("\nWrote:", file=sys.stderr)
    print(f"  {inputs_path}    sha256={sha256_of(inputs_path)}", file=sys.stderr)
    print(f"  {expected_path}  sha256={sha256_of(expected_path)}", file=sys.stderr)
    print(f"\nCases: {len(cases)}", file=sys.stderr)


if __name__ == "__main__":
    main()
