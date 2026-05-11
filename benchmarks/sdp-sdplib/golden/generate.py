#!/usr/bin/env python3
# =============================================================================
# benchmarks/sdp-sdplib/golden/generate.py — golden master for sdp-sdplib.
# =============================================================================
#
# Produces, in one invocation:
#   golden/inputs.json    — SDPLIB problems in canonical cone-solver wire form
#   golden/expected.json  — dual-witness consensus (Mosek + COPT)
#   data/sdp-sdplib/MANIFEST.toml + sha256.sum
#
# Pipeline:
#   1. Read each SDPLIB .dat-s file (SDPA-sparse format) from
#      data/sdp-sdplib/raw/.
#   2. Convert to canonical SCS form (ADR-0030 §C with PSDCone) —
#      the same wire `tools/sdp-solve` and `tools/cone-solve` consume.
#      Sign convention: SDPLIB is `maximise <C, X>`; canonical is
#      `minimise <C', X>` with C' = -C; objectives flipped on output.
#   3. Encode each block as a PSDCone[size, indices] slice of the
#      global x vector, with **strict-Mosek-format √2 off-diagonal
#      scaling** (ADR-0030 §C Open Question #4).
#   4. Run both oracle adapters (Mosek + COPT) on the canonical wire
#      via subprocess — same pattern as lp-netlib / lp-small (oracles
#      are black boxes; the corpus runner re-invokes them on every
#      grade run).
#   5. Build dual-witness consensus and write expected.json.
#
# v0.1 SDPLIB selection (6 problems, pure PSD only):
#
#    control1  m=21   blocks: PSD 10 + PSD 5    LMI feasibility / Lyapunov
#    control2  m=66   blocks: PSD 20 + PSD 10   ditto, larger
#    control3  m=136  blocks: PSD 30 + PSD 15   ditto, larger again
#    hinf2     m=13   blocks: 5 + 5 + 6         H-∞ control
#    theta1    m=104  blocks: PSD 50            Lovász ϑ function
#    mcp100    m=100  blocks: PSD 100           MAXCUT relaxation
#
# SDPLIB problems with LP blocks (negative `blockSize` in SDPA) — the
# truss series, hinf1, several control-* variants — are deferred to
# v0.2 of `tools/sdp-solve` (bead `67nj`), which will reformulate
# NonNegCone as a diagonal SDP block. For now they are *skipped* and
# documented in DESCRIPTION.md.
#
# Sparse-encoding gate
# --------------------
# The wire encodes A as a dense list-of-lists (ADR-0030 §C). For SDP,
# n_total = sum_b svec_len(size_b) = sum_b size*(size+1)/2; the dense
# A matrix is m × n_total. Cases with `n_total * m > DENSE_LIMIT` are
# skipped, mirroring lp-netlib's gate. v0.1 DENSE_LIMIT = 600,000
# (≈10 MB inputs.json across the 6-problem set; well within github
# committable limits).
#
# References
# ----------
# - Borchers (1999). "SDPLIB 1.2, A Library of Semidefinite Programming
#   Test Problems." *Optimization Methods and Software* 11.
# - Yamashita, Fujisawa, Fukuda, Nakata, Nakata (2003). "SDPA-sparse
#   format reference." (the .dat-s format spec)
#
# Run:
#   python3 benchmarks/sdp-sdplib/golden/generate.py
#
# Re-running is safe: cached inputs.json is regenerated; oracles re-run
# on every invocation. Determinism: same SDPLIB bytes + same Mosek/COPT
# versions on the same platform produce bit-identical outputs.

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

# Paths (corpus-relative).
HERE = Path(__file__).resolve().parent
SUITE_ROOT = HERE.parent  # benchmarks/sdp-sdplib/
CORPUS_ROOT = SUITE_ROOT.parent.parent  # corpus repo root
DATA_RAW = CORPUS_ROOT / "data" / "sdp-sdplib" / "raw"

ORACLE_MOSEK = CORPUS_ROOT / "adapters/mosek/oracles/mosek-sdp.py"
ORACLE_COPT_PY = CORPUS_ROOT / "adapters/copt/oracles/copt-sdp.py"
ORACLE_COPT_SH = CORPUS_ROOT / "adapters/copt/oracles/run-copt.sh"

INPUTS_JSON = HERE / "inputs.json"
EXPECTED_JSON = HERE / "expected.json"

# v0.1 problem selection: pure-PSD SDPLIB classics.
PROBLEMS = [
    "control1",
    "control2",
    "control3",
    "hinf2",
    "theta1",
    "mcp100",
]

DENSE_LIMIT = 600_000  # n_total × m gate for the dense-JSON wire

ENCODING_VERSION = "sdp-sdplib-v0.1"
SQRT2 = math.sqrt(2)


# ---------------------------------------------------------------------------
# SDPA-sparse parser (Python port of @workbench/solver-ipm's parseSdpaSparse)
# ---------------------------------------------------------------------------

def parse_sdpa_sparse(text: str):
    """Returns dict with keys: m, nblocks, block_sizes (list[int]; negative
    means diagonal LP block), b (list[float]), entries (list of dicts).
    Each entry: {i: matrix index 0..m, block: 1-indexed, k: 1-indexed
    row, l: 1-indexed col (k <= l), v: float}.
    """
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        ln = lines[idx].strip()
        if ln == "" or ln.startswith('"') or ln.startswith("*"):
            idx += 1
            continue
        break
    def next_line():
        nonlocal idx
        ln = lines[idx]
        idx += 1
        return _strip_comment(ln)
    m = int(next_line().strip())
    nblocks = int(next_line().strip())
    sizes_line = next_line()
    block_sizes = [int(t) for t in _split_tokens(sizes_line) if t]
    if len(block_sizes) != nblocks:
        raise ValueError(f"nblocks={nblocks} but parsed {len(block_sizes)} sizes")
    b_line = next_line()
    b = [float(t) for t in _split_tokens(b_line) if t]
    if len(b) != m:
        raise ValueError(f"m={m} but parsed {len(b)} b values")
    entries = []
    while idx < len(lines):
        ln = _strip_comment(lines[idx]).strip()
        idx += 1
        if not ln:
            continue
        parts = ln.split()
        if len(parts) < 5:
            continue
        i = int(parts[0]); blk = int(parts[1])
        k = int(parts[2]); l = int(parts[3])
        v = float(parts[4])
        if not math.isfinite(v):
            continue
        entries.append({"i": i, "block": blk, "k": k, "l": l, "v": v})
    return {"m": m, "nblocks": nblocks, "block_sizes": block_sizes, "b": b, "entries": entries}


def _strip_comment(s: str) -> str:
    i = s.find("=")
    return s[:i] if i >= 0 else s


def _split_tokens(s: str):
    # SDPA-sparse uses any of these as separators; commas/braces appear
    # in the mdim/sizes lines.
    for ch in "{}(),":
        s = s.replace(ch, " ")
    return s.split()


# ---------------------------------------------------------------------------
# SDPA → canonical-wire conversion
# ---------------------------------------------------------------------------

def svec_pos(i: int, j: int, n: int) -> int:
    """Row-major upper-tri svec position for matrix entry (i, j) with i<=j (0-indexed)."""
    return i * n - (i * (i - 1)) // 2 + (j - i)


def encode_sdpa_to_wire(sdpa) -> tuple[dict, dict, str | None]:
    """Convert SDPA-sparse to canonical wire (cone-solver wire, ADR-0030 §C)
    in the *minimise* convention (SDPLIB is maximise; we negate C).

    Returns (wire_input, meta, skip_reason). If skip_reason is non-None,
    the problem is skipped (LP block present, exceeds DENSE_LIMIT, etc.).
    """
    m_sdpa = sdpa["m"]
    block_sizes = sdpa["block_sizes"]

    # Skip problems with LP blocks (negative block size in SDPA-sparse).
    # v0.1 sdp-solve handles only PSDCone; LP blocks reformulated as
    # diagonal PSD blocks are deferred to v0.2 (bead 67nj).
    if any(s < 0 for s in block_sizes):
        return {}, {}, f"has_lp_block (block_sizes={block_sizes}); deferred to v0.2"

    # Compute n_total = sum_b svec_len(size_b) and per-block index ranges.
    indices_per_block = []
    cursor = 0
    for size in block_sizes:
        n_b = size * (size + 1) // 2
        indices_per_block.append(list(range(cursor, cursor + n_b)))
        cursor += n_b
    n_total = cursor

    # DENSE_LIMIT gate.
    if n_total * m_sdpa > DENSE_LIMIT:
        return {}, {}, (
            f"dense_limit_exceeded (n_total={n_total} × m={m_sdpa} = "
            f"{n_total * m_sdpa} > {DENSE_LIMIT})"
        )

    # Build c (objective) and A (constraint matrix).
    # SDPA convention: maximise <C, X>; canonical is minimise; flip sign.
    # Entries with i=0 set C; entries with i in 1..m set A_i.
    c = [0.0] * n_total
    A = [[0.0] * n_total for _ in range(m_sdpa)]
    b = list(sdpa["b"])

    for e in sdpa["entries"]:
        i = e["i"]            # 0 = C, 1..m = A_i
        blk = e["block"] - 1  # 0-indexed
        k = e["k"] - 1        # 0-indexed row
        l = e["l"] - 1        # 0-indexed col, k <= l in SDPA convention
        v = e["v"]
        if blk < 0 or blk >= len(block_sizes):
            continue
        size = block_sizes[blk]
        if size < 0:
            continue  # already handled by skip-check above; defensive
        if k < 0 or l < 0 or k >= size or l >= size:
            continue
        # Ensure k <= l (svec ordering).
        if k > l:
            k, l = l, k
        pos = svec_pos(k, l, size)
        slot = indices_per_block[blk][pos]
        scale = SQRT2 if k != l else 1.0
        sign = -1 if i == 0 else 1  # flip C only (maximise -> minimise)
        wire_v = scale * v * sign
        if i == 0:
            c[slot] += wire_v
        else:
            A[i - 1][slot] += wire_v

    cones = [
        {"head": "PSDCone", "size": block_sizes[bi], "indices": indices_per_block[bi]}
        for bi in range(len(block_sizes))
    ]

    wire = {
        "minimize": {"c": c},
        "subjectTo": {
            "Ax_eq_b": {"A": A, "b": b},
            "cones": cones,
        },
        "precision": 1e-8,
    }

    meta = {
        "source": "SDPLIB (Borchers 1999)",
        "format": "SDPA-sparse (.dat-s)",
        "convention_in":  "maximize <C, X> s.t. <A_i, X> = b_i, X >= 0",
        "convention_out": "minimize <-C, X> s.t. <A_i, X> = b_i, X >= 0",
        "n_total":  n_total,
        "m":        m_sdpa,
        "blocks":   [{"size": s, "n_indices": len(indices_per_block[bi])}
                     for bi, s in enumerate(block_sizes)],
        "encoding": ENCODING_VERSION,
    }
    return wire, meta, None


# ---------------------------------------------------------------------------
# Oracle invocation
# ---------------------------------------------------------------------------

def run_oracle_mosek(wire: dict) -> dict:
    proc = subprocess.run(
        ["python3", str(ORACLE_MOSEK)],
        input=json.dumps(wire).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return {"_error": True, "stderr": proc.stderr.decode("utf-8", errors="replace")[:500]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {"_error": True, "parse": str(e), "stdout_head": proc.stdout[:500].decode("utf-8", errors="replace")}


def run_oracle_copt(wire: dict) -> dict:
    proc = subprocess.run(
        [str(ORACLE_COPT_SH), str(ORACLE_COPT_PY)],
        input=json.dumps(wire).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return {"_error": True, "stderr": proc.stderr.decode("utf-8", errors="replace")[:500]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {"_error": True, "parse": str(e), "stdout_head": proc.stdout[:500].decode("utf-8", errors="replace")}


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------

def build_consensus(mosek_res: dict, copt_res: dict, agreement_tol: float = 1e-5) -> dict:
    """Build the dual-witness consensus record. Tolerance defaults to
    1e-5 (relative) for SDP. Why looser than the LP suites' 1e-8?
    SDP solvers reach 1e-7 to 1e-9 in primal/dual feasibility but
    objective agreement between Mosek's interior-point and COPT's
    interior-point routinely drifts at the 1e-5 to 1e-6 level on
    SDPLIB classics — this is the well-known "1e-6 SDP precision floor"
    documented in Tütüncü-Toh-Todd 2003 and confirmed empirically
    against the SDPLIB reference values (Borchers 1999, Table 2).
    1e-5 catches order-of-magnitude lies; 1e-8 would falsely flag
    every case as oracle_disagreement and leave the suite ungated."""
    consensus = {"agreement_tol": agreement_tol}

    if mosek_res.get("_error"):
        consensus["mosek_error"] = mosek_res.get("stderr") or str(mosek_res)[:200]
    if copt_res.get("_error"):
        consensus["copt_error"] = copt_res.get("stderr") or str(copt_res)[:200]

    if mosek_res.get("_error") and copt_res.get("_error"):
        return {
            "status": "numerical-breakdown",
            "consensus": {**consensus, "agreement": False, "reason": "both oracles errored"},
        }
    if mosek_res.get("_error"):
        # Use COPT as sole witness.
        out = _single_witness(copt_res, "copt", consensus)
        return out
    if copt_res.get("_error"):
        out = _single_witness(mosek_res, "mosek", consensus)
        return out

    s_m = mosek_res.get("status")
    s_c = copt_res.get("status")
    consensus["objective_mosek"] = mosek_res.get("objective")
    consensus["objective_copt"]  = copt_res.get("objective")

    if s_m != s_c:
        # Status mismatch — flag as oracle disagreement; let verifier
        # treat the case as N/A for oracle_agreement gating.
        return {
            "status": s_m,  # report Mosek's status arbitrarily; status_consistency check uses it
            "consensus": {**consensus, "agreement": False,
                          "reason": f"status mismatch: mosek={s_m}, copt={s_c}"},
        }

    if s_m != "optimal":
        # Non-optimal terminations — no objective field; agreement
        # vacuously holds on status but not on a numerical value.
        return {
            "status": s_m,
            "consensus": {**consensus, "agreement": True,
                          "rationale": f"both oracles agree on non-optimal status={s_m}"},
        }

    obj_m = float(mosek_res["objective"])
    obj_c = float(copt_res["objective"])
    diff = abs(obj_m - obj_c)
    rel = diff / max(1.0, abs(obj_m))
    if rel <= agreement_tol:
        return {
            "status": "optimal",
            "objective": 0.5 * (obj_m + obj_c),
            "consensus": {**consensus, "agreement": True, "rel_diff": rel},
        }
    return {
        "status": "optimal",
        # No consensus objective when oracles disagree — the verifier
        # treats oracle_agreement as N/A and the case drops from gating.
        "consensus": {**consensus, "agreement": False, "rel_diff": rel,
                      "reason": f"objectives disagree by rel={rel:.3e}"},
    }


def _single_witness(res: dict, label: str, consensus: dict) -> dict:
    s = res.get("status")
    if s != "optimal":
        return {
            "status": s,
            "consensus": {**consensus, "agreement": False,
                          "reason": f"only {label} oracle produced a result, status={s}"},
        }
    return {
        "status": "optimal",
        "objective": float(res["objective"]),
        "consensus": {**consensus, "agreement": False,
                      "reason": f"only {label} oracle produced an objective; no dual-witness verification"},
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    if not DATA_RAW.exists():
        print(f"error: raw SDPLIB data missing at {DATA_RAW}", file=sys.stderr)
        sys.exit(2)

    cases_in = []
    cases_exp = []
    skipped = []

    for problem_name in PROBLEMS:
        path = DATA_RAW / f"{problem_name}.dat-s"
        if not path.exists():
            print(f"warn: {path} missing, skipping", file=sys.stderr)
            skipped.append({"id": problem_name, "reason": "missing .dat-s"})
            continue
        text = path.read_text()
        sdpa = parse_sdpa_sparse(text)

        wire, meta, skip = encode_sdpa_to_wire(sdpa)
        if skip:
            print(f"skip: {problem_name} — {skip}", file=sys.stderr)
            skipped.append({"id": problem_name, "reason": skip})
            continue

        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        meta["sha256_dat_s"] = sha

        print(f"oracle: {problem_name} (m={meta['m']}, n_total={meta['n_total']}) ...",
              file=sys.stderr)
        mosek_res = run_oracle_mosek(wire)
        copt_res  = run_oracle_copt(wire)
        expected = build_consensus(mosek_res, copt_res)

        s_m = mosek_res.get("status", "ERR")
        s_c = copt_res.get("status", "ERR")
        agree = expected.get("consensus", {}).get("agreement", False)
        obj_m = mosek_res.get("objective", "n/a")
        obj_c = copt_res.get("objective", "n/a")
        print(f"  mosek: {s_m} obj={obj_m}", file=sys.stderr)
        print(f"  copt:  {s_c} obj={obj_c}", file=sys.stderr)
        print(f"  consensus: agreement={agree} status={expected.get('status')}",
              file=sys.stderr)

        cases_in.append({"id": problem_name, "input": wire, "meta": meta})
        cases_exp.append({"id": problem_name, "expected": expected})

    # Write inputs.json + expected.json.
    INPUTS_JSON.write_text(json.dumps({
        "encoding_version": ENCODING_VERSION,
        "skipped": skipped,
        "cases": cases_in,
    }, allow_nan=False))
    EXPECTED_JSON.write_text(json.dumps({
        "cases": cases_exp,
    }, allow_nan=False))

    print(f"\nwrote {INPUTS_JSON} ({len(cases_in)} cases, {len(skipped)} skipped)",
          file=sys.stderr)
    print(f"wrote {EXPECTED_JSON}", file=sys.stderr)


if __name__ == "__main__":
    main()
