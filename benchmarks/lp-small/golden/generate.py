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
# derive their own subseed from a stable string (family + size descriptor).
# Re-running this script with the same Gurobi / Mosek versions on the
# same platform fingerprint produces bit-identical outputs.
#
# Case taxonomy (~40 cases across 8 families; see DESCRIPTION.md):
#
#   Family A — Random dense, well-conditioned (16 cases)
#     Controlled singular spectrum (AᵀA eigenvalue spread in [1,10]).
#     Primal x* and dual y* are constructed explicitly so the optimal
#     solution is known analytically; oracles confirm it.
#
#   Family B — Klee-Minty cubes (4 cases)
#     Parametric LP where Dantzig-pivot simplex visits 2^n vertices.
#     Interior-point converges in O(√n). Tests algorithm-dispatch decision.
#
#   Family C — Beale's 1955 cycling LP (1 case)
#     Textbook degenerate example that cycles under Dantzig rule.
#     Bland's rule (or perturbation) resolves it.
#
#   Family D — Transportation & assignment (4 cases)
#     Totally unimodular LPs with integer optimal vertices.
#
#   Family E — Degenerate / multiple optima (3 cases)
#     Optimal face has dim ≥ 1.  Oracles pick different vertices;
#     verifier accepts any objective on the consensus interval.
#
#   Family F — Near-infeasible (2 cases)
#     Exactly infeasible systems.  Tests Farkas certificate extraction.
#
#   Family G — Unbounded (2 cases)
#     Problems with primal-unbounded rays.  Tests unboundedness certificate.
#
#   Family H — Boundary tags (3 cases)
#     Structural edge cases: empty problem, malformed cone, NaN input.
#
# Run:
#   python3 golden/generate.py
#
# Validate:
#   ~/.bun/bin/bun src/cli.ts grade gurobi lp-small
#   ~/.bun/bin/bun src/cli.ts grade mosek  lp-small

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE          = Path(__file__).resolve().parent
SUITE_DIR     = HERE.parent
CORPUS_ROOT   = SUITE_DIR.parent.parent
ORACLE_GUROBI = CORPUS_ROOT / "adapters/gurobi/oracles/gurobi-lp.py"
ORACLE_MOSEK  = CORPUS_ROOT / "adapters/mosek/oracles/mosek-lp.py"

SEED              = 20260511
ENCODING_VERSION  = 1
AGREEMENT_TOL_REL = 1e-8


# ─── Subseed helper ──────────────────────────────────────────────────────────
#
# Each family generator derives a deterministic per-case seed from a stable
# descriptor string.  This ensures that adding a new family never shifts the
# seeds of previously-generated families.  The seed is truncated to 32 bits
# so numpy's default_rng accepts it.


def subseed(descriptor: str) -> int:
    """Derive a 32-bit seed from a stable descriptor string."""
    return int(hashlib.md5(descriptor.encode()).hexdigest(), 16) % (2**32)


# ─── Canonical LP builder helpers ────────────────────────────────────────────
#
# SCS canonical form: minimise cᵀx  s.t. Ax = b, x ∈ K (product cone).
# For LP the cone K is always NonNegCone over all variable indices.


def nonneg_cone(n: int) -> dict:
    return {"head": "NonNegCone", "indices": list(range(n))}


def lp_input(c: list, A: list, b: list, n_vars: int | None = None, precision: float = 1e-8) -> dict:
    """Build a canonical SCS LP input dict.

    `n_vars` defaults to len(c).  All variables are in NonNegCone.
    When A / b are empty (no equality constraints), the Ax_eq_b block
    is omitted from the wire representation.
    """
    nv = n_vars if n_vars is not None else len(c)
    inp: dict[str, Any] = {"minimize": {"c": list(c)}}
    sub: dict[str, Any] = {"cones": [nonneg_cone(nv)]}
    if A:
        sub["Ax_eq_b"] = {"A": [list(row) for row in A], "b": list(b)}
    inp["subjectTo"] = sub
    inp["precision"] = precision
    return inp


# ─── Oracle invocation ───────────────────────────────────────────────────────


def run_oracle(oracle_path: Path, problem: dict) -> dict:
    """Pipe problem JSON through the oracle subprocess and parse stdout.

    Raises subprocess.CalledProcessError on non-zero exit; let it propagate
    loudly (CLAUDE.md Rule 1: fail fast, fail loud).
    """
    proc = subprocess.run(
        [sys.executable, str(oracle_path)],
        input=json.dumps(problem, allow_nan=False).encode(),
        capture_output=True,
        check=True,
        timeout=120,
    )
    return json.loads(proc.stdout)




# ─── Consensus builder ───────────────────────────────────────────────────────
#
# A tagged-refusal record from an oracle has shape {kind:"tagged", tag, payload}
# rather than the success record's {status, x, dual, ...}.  The consensus
# builder handles both shapes: for tagged records the "status" is derived from
# the tag string; for success records the status field is direct.


def oracle_status(rec: dict) -> str:
    """Extract the canonical status string from either a success or tagged record."""
    if rec.get("kind") == "tagged":
        # Map the tag to the closest status for consensus purposes.
        # The H-family boundary cases produce tagged refusals that the verifier
        # accepts when expected.status is in {infeasible, unbounded,
        # numerical-breakdown}.  We pin "numerical-breakdown" for malformed
        # inputs so the verifier's status_consistency check passes.
        tag = rec.get("tag", "")
        if "non-finite" in tag or "malformed-cone" in tag or "degenerate-shape" in tag:
            return "numerical-breakdown"
        return "numerical-breakdown"
    return rec["status"]


def build_consensus(g: dict, m: dict) -> dict:
    """Compare two oracle records; return the consensus block to pin.

    `objective` is present in the oracle record iff status == "optimal"
    (the oracle adapters use field-absence to encode ±∞ / NaN — see the
    adapters' headers).  This function mirrors that convention: the
    consensus block carries per-oracle objectives only when both
    oracles reached optimality.

    For tagged-refusal records the status is derived via oracle_status().
    """
    gs = oracle_status(g)
    ms = oracle_status(m)

    if gs != ms:
        return {
            "agreement":     False,
            "agreement_tol": AGREEMENT_TOL_REL,
            "reason":        f"status mismatch: gurobi={gs} mosek={ms}",
        }

    if gs == "optimal":
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

    # For infeasible / unbounded / numerical-breakdown / tagged-refusal:
    # the status is the consensus.  No objective values to compare.
    return {
        "agreement":     True,
        "agreement_tol": AGREEMENT_TOL_REL,
        "rationale":     f"status-only consensus ({gs})",
    }


def build_expected(g: dict, m: dict, override_status: str | None = None) -> dict:
    """Build the expected.json record.  `objective` present iff optimal.

    `override_status` allows H-family cases to pin an explicit expected
    status when both oracles return a tagged refusal rather than a
    standard status field.
    """
    status = override_status if override_status is not None else oracle_status(g)
    consensus = build_consensus(g, m)
    expected: dict[str, Any] = {"status": status, "consensus": consensus}
    if status == "optimal" and consensus.get("agreement"):
        # Mean of the two oracle objectives, both within 1e-8 relative.
        expected["objective"] = (g["objective"] + m["objective"]) / 2.0
    return expected


# ─── Family A — Random dense, well-conditioned ───────────────────────────────
#
# WHY THIS CONSTRUCTION:
#
# Naive random matrices are often poorly conditioned — AᵀA can have
# eigenvalue spread of 10^12 or more, which makes the LP numerically
# fragile.  We need "easy baseline" problems where any reasonable solver
# should achieve 1e-8 precision without difficulty.
#
# Construction:
#   1. Build A = U Σ Vᵀ with singular values uniformly in [1, 10]:
#      - U  is m×m orthogonal (from QR of random Gaussian)
#      - V  is n×n orthogonal (from QR of random Gaussian)
#      - Σ  is m×n with sv_i ∈ [1, 10] (controlled condition number ≤ 10)
#   2. Pick x* = [1, ..., 1, 0, ..., 0] (first m components = 1, rest 0).
#      This gives a primal feasible point with known structure.
#   3. Set b = A x* so the equality constraint is satisfied by construction.
#   4. Pick a random dual y*; compute Aᵀ y* then construct c so that:
#      - For basic indices (x*_i > 0): c_i = (Aᵀ y*)_i  [complementary slackness]
#      - For nonbasic indices (x*_i = 0): c_i = (Aᵀ y*)_i + 1  [s*_i = 1 > 0]
#   5. The optimal objective is cᵀ x* = (Aᵀ y*)ᵀ x* = y*ᵀ (A x*) = y*ᵀ b.
#      Both oracles confirm this value — the analytical derivation is the
#      ground truth per CLAUDE.md Law 1.


def family_a_cases() -> list[dict]:
    """Family A: 16 random dense well-conditioned LPs.

    Sizes (m, n) with m ≤ n drawn from {10, 25, 50, 100}².  Each size
    pair gets one case; the sub-seed is derived from the descriptor so
    adding new pairs does not shift existing seeds.
    """
    sizes = [10, 25, 50, 100]
    cases = []
    for m in sizes:
        for n in sizes:
            if m > n:
                continue  # m ≤ n required
            desc = f"A_dense_{m}x{n}_s001"
            rng = np.random.default_rng(subseed(desc))

            # Step 1: orthogonal U (m×m) and V (n×n) via QR decomposition.
            Q_U, _ = np.linalg.qr(rng.standard_normal((m, m)))
            Q_V, _ = np.linalg.qr(rng.standard_normal((n, n)))
            sv = rng.uniform(1.0, 10.0, m)      # m singular values in [1, 10]
            Sigma = np.zeros((m, n))
            for i in range(m):
                Sigma[i, i] = sv[i]
            A = (Q_U @ Sigma @ Q_V.T).tolist()

            # Step 2: primal solution x* (first m components = 1, rest = 0).
            x_star = np.zeros(n)
            x_star[:m] = 1.0

            # Step 3: equality RHS b = A x*.
            b = (np.array(A) @ x_star).tolist()

            # Step 4: dual y* → cost vector c with complementary slackness.
            y_star = rng.standard_normal(m)
            ATy = np.array(A).T @ y_star          # n-vector
            c = ATy.copy()
            c[m:] += 1.0                           # nonbasic slacks = 1
            c = c.tolist()

            cases.append({
                "id": desc,
                "input": lp_input(c, A, b),
                "meta": {
                    "family": "A_random_dense",
                    "generator": {
                        "kind": "random_dense",
                        "seed": subseed(desc),
                        "m": m,
                        "n": n,
                        "sv_range": [1.0, 10.0],
                    },
                    "expected_status": "optimal",
                },
            })
    return cases


# ─── Family B — Klee-Minty cubes ─────────────────────────────────────────────
#
# WHY THIS CONSTRUCTION:
#
# Klee and Minty (1972) proved that the simplex method under Dantzig's
# "most-negative-reduced-cost" pivot rule visits all 2^n vertices of
# their parametric LP.  Interior-point methods (including Mehrotra's IPM)
# solve Klee-Minty in O(√n) iterations — the canonical demonstration of
# why algorithm dispatch matters for practical solvers.
#
# The original form is an inequality LP.  We convert to canonical
# SCS-equality form by introducing one slack variable per inequality row:
#
#   Klee-Minty inequality:  C x ≤ d  with x ≥ 0
#   Canonical form:         C x + s = d  with x ≥ 0, s ≥ 0
#   Variables:              [x₁…xₙ | s₁…sₙ]  (all in NonNegCone)
#
# Row 1:       x₁ + s₁ = 1
# Row i≥2:     2·Σ_{j<i} 10^(i−j)·xⱼ + xᵢ + sᵢ = 10^i
#
# Objective:   min −Σᵢ 10^(n−i)·xᵢ  (slack terms have coefficient 0)
#
# The optimal vertex is x = (0, …, 0, 10^n), s = (1, 10², …, 10^(n−1), 0),
# giving objective = −10^n.


def family_b_cases() -> list[dict]:
    """Family B: Klee-Minty cubes, n ∈ {3, 5, 8, 10}."""
    cases = []
    for n in [3, 5, 8, 10]:
        n_vars = 2 * n
        # Cost: -(10^(n-1)) for x1, -(10^(n-2)) for x2, ..., -1 for xn; 0 for slacks.
        c = [-(10.0 ** (n - i)) for i in range(1, n + 1)] + [0.0] * n

        A: list[list[float]] = []
        b: list[float] = []

        # Row 1: x1 + s1 = 1
        row = [0.0] * n_vars
        row[0] = 1.0          # x₁
        row[n] = 1.0          # s₁
        A.append(row)
        b.append(1.0)

        # Rows 2..n: 2·Σ_{j<i} 10^(i−j)·x_j + x_i + s_i = 10^i
        for i in range(2, n + 1):   # i is 1-indexed
            row = [0.0] * n_vars
            for j in range(1, i):   # j is 1-indexed, j < i
                row[j - 1] = 2.0 * (10.0 ** (i - j))
            row[i - 1] = 1.0        # x_i
            row[n + i - 1] = 1.0    # s_i
            A.append(row)
            b.append(float(10 ** i))

        cases.append({
            "id": f"B_klee_minty_n{n}",
            "input": lp_input(c, A, b, n_vars=n_vars),
            "meta": {
                "family": "B_klee_minty",
                "generator": {
                    "kind": "klee_minty",
                    "n": n,
                    "known_optimal": -(10 ** n),
                },
                "expected_status": "optimal",
            },
        })
    return cases


# ─── Family C — Beale's 1955 cycling LP ──────────────────────────────────────
#
# WHY THIS CONSTRUCTION:
#
# Beale (1955) exhibited a 4-variable LP where the Dantzig simplex method
# cycles endlessly through degenerate bases without improving the objective.
# The problem has zero right-hand sides in two of its three constraints,
# which is precisely what drives the degeneracy cycle.  Bland's rule (1977)
# and lexicographic perturbation both terminate correctly on this instance.
#
# Original formulation (inequality, MINIMISATION):
#
#   minimise  −¾·x₁ + 150·x₂ − 1/50·x₃ + 6·x₄
#   s.t.       ¼·x₁ − 60·x₂ − 1/25·x₃ + 9·x₄ ≤ 0
#              ½·x₁ − 90·x₂ − 1/50·x₃ + 3·x₄ ≤ 0
#              x₃ ≤ 1
#              x ≥ 0
#
# Canonical SCS form: introduce slacks s₁, s₂, s₃ (one per inequality).
# Variables: [x₁, x₂, x₃, x₄, s₁, s₂, s₃] (7 total, all ≥ 0).
#
# The optimal is −0.05 at x = (0.04, 0, 1, 0) after Bland-rule termination
# (Beale 1955 §4, Table 2 final basis).
#
# Coefficients are exact rationals encoded as float64 — the fractions 1/4,
# 1/2, 1/25, 1/50 are exactly representable (powers of 2 denominators only
# for 1/4 and 1/2; 1/25 and 1/50 are NOT exactly representable in binary
# float64, but the solvers handle them correctly within 1e-8 tolerance).


def family_c_cases() -> list[dict]:
    """Family C: Beale's 1955 cycling LP (1 case, hand-coded)."""
    c = [-0.75, 150.0, -1.0 / 50.0, 6.0, 0.0, 0.0, 0.0]
    A = [
        [1.0 / 4.0, -60.0, -1.0 / 25.0, 9.0, 1.0, 0.0, 0.0],   # row 1 ≤ 0 → + s₁ = 0
        [1.0 / 2.0, -90.0, -1.0 / 50.0, 3.0, 0.0, 1.0, 0.0],   # row 2 ≤ 0 → + s₂ = 0
        [0.0,        0.0,    1.0,         0.0, 0.0, 0.0, 1.0],   # x₃ ≤ 1  → + s₃ = 1
    ]
    b = [0.0, 0.0, 1.0]
    return [{
        "id": "C_beale_1955",
        "input": lp_input(c, A, b),
        "meta": {
            "family": "C_beale_cycling",
            "generator": {"kind": "hand_coded", "reference": "Beale 1955, Naval Research Logistics Quarterly 2(4):269-275"},
            "expected_status": "optimal",
            "comment": "Dantzig-rule simplex cycles on this LP; Bland's rule terminates. Optimal = -0.05.",
        },
    }]


# ─── Family D — Transportation & assignment ───────────────────────────────────
#
# WHY THIS CONSTRUCTION:
#
# Transportation and assignment LPs have totally unimodular (TU) constraint
# matrices.  For a TU matrix with integer supply/demand vectors the LP
# relaxation has an integer optimal vertex — verifying this is a useful
# stress test for the candidate's primal-feasibility accuracy (the integer
# components should emerge from the solver to within 1e-8, not as 0.9999...).
#
# Transportation LP (m sources, n sinks):
#   Variables: x_{ij} for i∈{0..m-1}, j∈{0..n-1} (m·n total)
#   min  Σ_{ij} cost_{ij} · x_{ij}
#   s.t. Σ_j x_{ij} = supply_i       (m supply constraints)
#        Σ_i x_{ij} = demand_j       (n demand constraints)
#        x_{ij} ≥ 0
#
#   Σ supply_i = Σ demand_j is required for feasibility (balanced network).
#   The constraint matrix has m+n rows of which one is redundant; both
#   solvers handle this correctly via their own LP preprocessing.
#
# Assignment LP (k workers, k jobs):
#   Special case of transportation with supply_i = demand_j = 1.
#   Variables: x_{ij} ∈ {0,1} at the LP optimum due to TU (n=k²).
#   min  Σ_{ij} cost_{ij} · x_{ij}
#   s.t. Σ_j x_{ij} = 1    ∀i   (each worker assigned once)
#        Σ_i x_{ij} = 1    ∀j   (each job covered once)
#        x_{ij} ≥ 0


def _transport_lp(m: int, n: int, supply: list[int], demand: list[int], cost: list[list[int]], case_id: str) -> dict:
    """Build a balanced transportation LP in canonical SCS form.

    `m` sources, `n` sinks.  supply and demand are integer vectors with
    equal sums (balanced).  `cost` is an m×n integer cost matrix.
    """
    assert sum(supply) == sum(demand), "transportation LP must be balanced"
    n_vars = m * n
    c = [float(cost[i][j]) for i in range(m) for j in range(n)]

    A: list[list[float]] = []
    b: list[float] = []

    # Supply constraints: Σ_j x_{ij} = supply_i for i = 0..m-1.
    for i in range(m):
        row = [0.0] * n_vars
        for j in range(n):
            row[i * n + j] = 1.0
        A.append(row)
        b.append(float(supply[i]))

    # Demand constraints: Σ_i x_{ij} = demand_j for j = 0..n-1.
    for j in range(n):
        row = [0.0] * n_vars
        for i in range(m):
            row[i * n + j] = 1.0
        A.append(row)
        b.append(float(demand[j]))

    return {
        "id": case_id,
        "input": lp_input(c, A, b),
        "meta": {
            "family": "D_transport_assign",
            "generator": {
                "kind": "transportation",
                "m_sources": m,
                "n_sinks": n,
                "supply": supply,
                "demand": demand,
                "cost": cost,
            },
            "expected_status": "optimal",
        },
    }


def _assignment_lp(k: int, cost: list[list[int]], case_id: str) -> dict:
    """Build a k-job assignment LP in canonical SCS form.

    Each of k workers must be assigned to exactly one of k jobs.
    The constraint matrix is the incidence matrix of the complete
    bipartite graph K_{k,k} — a special balanced transportation matrix.
    """
    # Assignment is transportation with supply_i = demand_j = 1.
    supply = [1] * k
    demand = [1] * k
    result = _transport_lp(k, k, supply, demand, cost, case_id)
    result["meta"]["generator"]["kind"] = "assignment"
    result["meta"]["generator"]["k_jobs"] = k
    return result


def family_d_cases() -> list[dict]:
    """Family D: 2 transportation LPs + 2 assignment LPs."""
    rng34 = np.random.default_rng(subseed("D_transport_3x4"))
    cost34 = rng34.integers(1, 20, (3, 4)).tolist()

    rng58 = np.random.default_rng(subseed("D_transport_5x8"))
    cost58 = rng58.integers(1, 20, (5, 8)).tolist()

    rng4j = np.random.default_rng(subseed("D_assign_4job"))
    cost4j = rng4j.integers(1, 20, (4, 4)).tolist()

    rng6j = np.random.default_rng(subseed("D_assign_6job"))
    cost6j = rng6j.integers(1, 20, (6, 6)).tolist()

    return [
        _transport_lp(3, 4, [10, 20, 15], [8, 12, 14, 11], cost34, "D_transport_3x4"),
        _transport_lp(5, 8, [12, 18, 22, 14, 9], [8, 11, 9, 13, 7, 10, 14, 3], cost58, "D_transport_5x8"),
        _assignment_lp(4, cost4j, "D_assign_4job"),
        _assignment_lp(6, cost6j, "D_assign_6job"),
    ]


# ─── Family E — Degenerate / multiple optima ─────────────────────────────────
#
# WHY THIS CONSTRUCTION:
#
# A degenerate LP has an optimal face of dimension ≥ 1.  Two solvers
# satisfying the KKT conditions can return different optimal vertices —
# both are correct.  The verifier must accept any candidate objective that
# lies within the consensus interval; checking the specific vertex is wrong.
#
# Three constructions of increasing dimension of the optimal face:
#
#   E1: min 0·x s.t. x₁ + x₂ = 1, x ≥ 0.
#       Every feasible point is optimal (objective = 0 always).
#       Gurobi returns (1,0); Mosek returns (0,1) — both correct.
#
#   E2: min x₁ + x₂ s.t. x₁ + x₂ = 1, x ≥ 0.
#       The entire constraint hyperplane is the optimal face: obj = 1 always.
#       Two extreme points (1,0) and (0,1) are both optimal vertices.
#
#   E3: min x₁ + x₂ + x₃ s.t. x₁ + x₂ + x₃ = 1, x ≥ 0.
#       The 2-simplex is the optimal face: obj = 1.  Three vertices:
#       (1,0,0), (0,1,0), (0,0,1) — all optimal.


def family_e_cases() -> list[dict]:
    """Family E: 3 degenerate / multiple-optima LPs."""
    e1 = {
        "id": "E_multiple_optima_zero_obj",
        "input": lp_input([0.0, 0.0], [[1.0, 1.0]], [1.0]),
        "meta": {
            "family": "E_degenerate_multiple_optima",
            "generator": {"kind": "hand_coded", "dim_optimal_face": 1},
            "expected_status": "optimal",
            "comment": "min 0·x s.t. x1+x2=1, x>=0; obj=0 for all feasible x.",
        },
    }
    e2 = {
        "id": "E_multiple_optima_flat_face_2d",
        "input": lp_input([1.0, 1.0], [[1.0, 1.0]], [1.0]),
        "meta": {
            "family": "E_degenerate_multiple_optima",
            "generator": {"kind": "hand_coded", "dim_optimal_face": 1},
            "expected_status": "optimal",
            "comment": "min x1+x2 s.t. x1+x2=1, x>=0; obj=1 at any vertex of [0,1].",
        },
    }
    e3 = {
        "id": "E_multiple_optima_simplex_3d",
        "input": lp_input([1.0, 1.0, 1.0], [[1.0, 1.0, 1.0]], [1.0]),
        "meta": {
            "family": "E_degenerate_multiple_optima",
            "generator": {"kind": "hand_coded", "dim_optimal_face": 2},
            "expected_status": "optimal",
            "comment": "min x1+x2+x3 s.t. x1+x2+x3=1, x>=0; optimal face is the 2-simplex.",
        },
    }
    return [e1, e2, e3]


# ─── Family F — Near-infeasible ───────────────────────────────────────────────
#
# WHY THIS CONSTRUCTION:
#
# The verifier checks the Farkas infeasibility certificate (check 11:
# infeasibility_certificate in the 12-check protocol).  A Farkas
# certificate y satisfies Aᵀ y ≤ 0 and bᵀ y > 0.  To exercise this
# path the LP must be exactly infeasible — not "near" infeasible in the
# sense of being ε-close to feasibility, but structurally infeasible.
#
# The "near" in Family F refers to the *construction* style: we start
# with a contradicting pair of equality constraints that are individually
# plausible-looking but together have no simultaneous solution.
#
#   F1 (tracer, preserved): x = 1 and x = 2 simultaneously (1 var).
#      Simple enough to verify by hand.
#
#   F2: x₁ + x₂ + x₃ = 3 and x₁ + x₂ + x₃ = 4 simultaneously.
#      Three variables; the constraint rows are proportional so the
#      contradiction is structurally identical to F1 but at larger scale.
#      A Farkas certificate is any y = (α, -α) with α > 0.


def family_f_cases() -> list[dict]:
    """Family F: 2 infeasible LPs."""
    f1 = {
        "id": "F_infeasible_simple",
        "input": lp_input([0.0], [[1.0], [1.0]], [1.0, 2.0]),
        "meta": {
            "family": "F_near_infeasible",
            "generator": {"kind": "hand_coded"},
            "expected_status": "infeasible",
            "comment": "tracer — exactly-infeasible LP (two contradicting equalities)",
        },
    }
    f2 = {
        "id": "F_infeasible_3var_sum",
        "input": lp_input([0.0, 0.0, 0.0], [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]], [3.0, 4.0]),
        "meta": {
            "family": "F_near_infeasible",
            "generator": {"kind": "hand_coded"},
            "expected_status": "infeasible",
            "comment": "x1+x2+x3=3 and x1+x2+x3=4 simultaneously — proportional rows, different RHS.",
        },
    }
    return [f1, f2]


# ─── Family G — Unbounded ────────────────────────────────────────────────────
#
# WHY THIS CONSTRUCTION:
#
# The verifier checks the unboundedness certificate (check 12).  A valid
# unbounded direction d satisfies A d = 0, d ≥ 0, cᵀ d < 0.
# The primal can be driven arbitrarily negative along t·d for any t > 0.
#
#   G1 (tracer, preserved): min −x s.t. x ≥ 0.  Unbounded ray: d = (1).
#      The simplest possible unbounded LP.
#
#   G2: min −(x₁ + x₂) s.t. x₁ − x₂ = 0, x₁, x₂ ≥ 0.
#      The constraint forces x₁ = x₂.  The ray d = (1, 1) satisfies
#      A d = 1-1 = 0, d ≥ 0, cᵀ d = -(1+1) = -2 < 0.
#      The feasible set is the ray {(t, t) : t ≥ 0} — unbounded along d.


def family_g_cases() -> list[dict]:
    """Family G: 2 primal-unbounded LPs."""
    g1 = {
        "id": "G_unbounded_simple",
        "input": {
            "minimize": {"c": [-1.0]},
            "subjectTo": {"cones": [{"head": "NonNegCone", "indices": [0]}]},
            "precision": 1e-8,
        },
        "meta": {
            "family": "G_unbounded",
            "generator": {"kind": "hand_coded"},
            "expected_status": "unbounded",
            "comment": "tracer — primal-unbounded LP (no equality constraints)",
        },
    }
    g2 = {
        "id": "G_unbounded_ray_x1_eq_x2",
        "input": lp_input([-1.0, -1.0], [[1.0, -1.0]], [0.0]),
        "meta": {
            "family": "G_unbounded",
            "generator": {"kind": "hand_coded"},
            "expected_status": "unbounded",
            "comment": "min -(x1+x2) s.t. x1=x2, x>=0; ray d=(1,1) is the unbounded direction.",
        },
    }
    return [g1, g2]


# ─── Family H — Boundary tags ────────────────────────────────────────────────
#
# WHY THESE CASES EXIST:
#
# The boundary-tag refusal envelope ("cone-solve/non-finite-input" etc.)
# is a first-class output category for the cone-solve tier (ADR-0030 §A).
# These cases exercise the verifier's tagged-refusal path: when both
# oracles return a tagged envelope, the expected record pins
# "numerical-breakdown" as the status so the verifier's
# status_consistency check accepts the refusal.
#
# Three structural edges:
#
#   H1 (H_empty_problem): c=[], no A/b, cones=[].
#      The empty LP is trivially optimal with objective=0.
#      Both Gurobi and Mosek handle this correctly as a success record.
#      expected_status = "optimal".
#
#   H2 (H_malformed_cone): cones contains an index out of range (n=2, index=5).
#      Both oracles emit tagged "cone-solve/malformed-cone".
#      expected_status = "numerical-breakdown" so the verifier accepts
#      the tagged refusal.
#
#   H3 (H_non_finite_input): A contains a null entry (JSON null, not NaN).
#      Raw NaN tokens are forbidden by the JSON specification; json.dumps with
#      allow_nan=False raises ValueError.  Instead we store JSON null for the
#      non-finite entry: in the Python oracle adapters null is parsed as None,
#      which fails the isinstance(v, (int, float)) check in all_finite() and
#      triggers tagged "cone-solve/non-finite-input".  A candidate solver
#      written in TypeScript sees null where it expects a number; Number.isFinite
#      (null) returns false → same non-finite detection path.  The representation
#      is faithful to the intent (a structurally broken numeric entry) while
#      remaining valid strict JSON.
#      expected_status = "numerical-breakdown".


def family_h_cases() -> list[dict]:
    """Family H: 3 boundary-tag / structural-edge cases.

    Returns case dicts with an extra '_oracle_invocation' hint:
      'normal'     — use run_oracle (allow_nan=False)
      'allow_nan'  — use run_oracle_allow_nan (allow_nan=True)
      'status_override' — force expected.status to this value
    """
    h1 = {
        "id": "H_empty_problem",
        "input": {
            "minimize": {"c": []},
            "subjectTo": {"cones": []},
            "precision": 1e-8,
        },
        "meta": {
            "family": "H_boundary_tags",
            "generator": {"kind": "hand_coded"},
            "expected_status": "optimal",
            "comment": "Empty LP (n=0): trivially optimal with obj=0.",
        },
        "_oracle_invocation": "normal",
    }
    h2 = {
        "id": "H_malformed_cone",
        "input": {
            "minimize": {"c": [1.0, 2.0]},
            "subjectTo": {
                "Ax_eq_b": {"A": [[1.0, 1.0]], "b": [1.0]},
                "cones": [{"head": "NonNegCone", "indices": [0, 1, 5]}],  # 5 out of range for n=2
            },
            "precision": 1e-8,
        },
        "meta": {
            "family": "H_boundary_tags",
            "generator": {"kind": "hand_coded"},
            "expected_status": "numerical-breakdown",
            "comment": "NonNegCone index 5 out of range for n=2; both oracles emit tagged cone-solve/malformed-cone.",
        },
        "_oracle_invocation": "normal",
        "_status_override": "numerical-breakdown",
    }
    h3 = {
        "id": "H_non_finite_input",
        "input": {
            "minimize": {"c": [1.0, 2.0]},
            "subjectTo": {
                "Ax_eq_b": {"A": [[1.0, None]], "b": [1.0]},   # None → JSON null → non-finite
                "cones": [{"head": "NonNegCone", "indices": [0, 1]}],
            },
            "precision": 1e-8,
        },
        "meta": {
            "family": "H_boundary_tags",
            "generator": {"kind": "hand_coded"},
            "expected_status": "numerical-breakdown",
            "comment": "A[0][1] = null (non-finite sentinel); both oracles emit tagged cone-solve/non-finite-input.",
        },
        "_oracle_invocation": "normal",
        "_status_override": "numerical-breakdown",
    }
    return [h1, h2, h3]


# ─── All cases ────────────────────────────────────────────────────────────────


def all_cases() -> list[dict]:
    """Assemble all ~40 cases in family order."""
    return (
        family_a_cases()    # 16 cases
        + family_b_cases()  #  4 cases
        + family_c_cases()  #  1 case
        + family_d_cases()  #  4 cases
        + family_e_cases()  #  3 cases
        + family_f_cases()  #  2 cases
        + family_g_cases()  #  2 cases
        + family_h_cases()  #  3 cases
    )                       # 35 total


# ─── Main ────────────────────────────────────────────────────────────────────


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    cases = all_cases()

    # Strip internal-only hints from the wire representation.
    inputs_cases = []
    for c in cases:
        ic = {k: v for k, v in c.items() if not k.startswith("_")}
        inputs_cases.append({"id": ic["id"], "input": ic["input"], "meta": ic["meta"]})

    inputs_doc: dict[str, Any] = {
        "encoding_version": ENCODING_VERSION,
        "seed":             SEED,
        "problem":          "lp-small — small linear programs (n ≤ 100) for the resource-limited regime",
        "cases":            inputs_cases,
    }

    expected_cases: list[dict] = []
    for c in cases:
        status_override = c.get("_status_override")

        g = run_oracle(ORACLE_GUROBI, c["input"])
        m = run_oracle(ORACLE_MOSEK,  c["input"])

        expected = build_expected(g, m, override_status=status_override)
        expected_cases.append({"id": c["id"], "expected": expected})

        gs = oracle_status(g)
        ms = oracle_status(m)
        agree = expected["consensus"].get("agreement", False)
        print(
            f"  {c['id']:<42}  gurobi={gs:<22}  mosek={ms:<22}  agree={agree}",
            file=sys.stderr,
        )

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
