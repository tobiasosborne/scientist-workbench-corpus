#!/usr/bin/env python3
# =============================================================================
# benchmarks/lp-netlib/golden/generate.py — golden master for lp-netlib.
# =============================================================================
#
# Produces, in one invocation:
#
#   golden/inputs.json   — NETLIB LP problems in canonical SCS form
#   golden/expected.json — dual-witness consensus (Gurobi + Mosek)
#   data/lp-netlib/raw/  — decoded MPS files (cached; skipped if sha256 matches)
#   data/lp-netlib/MANIFEST.toml — per-file provenance ledger
#   data/lp-netlib/sha256.sum    — sha256 ledger
#
# ─── Design narrative ────────────────────────────────────────────────────────
#
# The NETLIB LP collection (nominally 114 problems, public domain since 1985)
# is the canonical LP benchmark battery used in every serious LP solver
# evaluation since Bixby, Boyd, and Indovina (1992).  Problems are stored in
# NETLIB's proprietary "compressed MPS" format, decoded by the `emps` C program
# (netlib.org/lp/data/emps.c).  This script:
#
#   1. Downloads each problem's raw emps-encoded bytes from netlib.org.
#   2. Decodes to standard MPS via the compiled emps binary.
#   3. Reads each MPS via Gurobi (for parsing only, not solving).
#   4. Extracts the general-form LP: c, per-constraint (sense, rhs, sparse row),
#      per-variable (lb, ub).
#   5. Reduces to canonical SCS form (Vanderbei §2.5–2.7):
#        - Equality rows pass through as-is.
#        - Inequality rows get a slack variable (s ≥ 0), converting to equality.
#        - Bounded variables (finite lo) get shifted: x' = x - lo, x' ≥ 0.
#        - Upper-bounded variables introduce a slack: x' + t = hi - lo, t ≥ 0.
#        - Free variables split: x = x+ - x-, both ≥ 0.
#   6. Records the reduction in meta (var_map, slack_intro, free_split,
#      bound_slack) so a downstream consumer can invert the candidate's x back
#      to original NETLIB variable names.
#   7. Runs both oracle adapters (Gurobi and Mosek) on the *canonical* problem
#      via subprocess — exactly as lp-small/golden/generate.py does.
#   8. Builds the dual-witness consensus and writes inputs.json / expected.json.
#
# ─── Dense matrix size gate ──────────────────────────────────────────────────
#
# The canonical wire format (ADR-0030 §C) encodes A as a dense list-of-lists
# (list[list[float]]).  This is fine for problems with n_can × m_can ≤ ~200K
# entries (each case ~3MB JSON or less), but many NETLIB problems are large
# and sparse:
#
#   - 80bau3b:  n_can≈15545, m_can≈5746 → 89M entries (1.3GB JSON)
#   - fit2p:    n_can≈21025, m_can≈10500 → 220M entries (3.3GB JSON)
#   - ken-18:   n_can≈309K,  m_can≈260K → impractical
#
# Cases that exceed DENSE_LIMIT entries are *skipped* — they don't appear in
# the committed inputs.json at all.  Per ADR-0030 §"Open questions #5"
# (sparse matrix wire format) sparse support is deferred to v0.2.  Until
# then this suite covers the small-to-medium NETLIB problems only.  The
# raw .mps files for excluded cases stay in `data/lp-netlib/raw/` for
# local regeneration when the gate widens.
#
# Threshold counts (from pre-scan of all 109 downloadable problems):
#   n_can*m_can ≤   25K:  10 problems (~ 1.6MB JSON)
#   n_can*m_can ≤  100K:  21 problems (~12.2MB JSON)   ← current gate
#   n_can*m_can ≤  200K:  28 problems (~29.4MB JSON)
#   n_can*m_can ≤  500K:  37 problems (~71.4MB JSON)
#   n_can*m_can ≤ 2,000K: 62 problems (~478MB JSON; rejects GitHub's 100MB limit)
#
# DENSE_LIMIT = 100,000 is the sweet spot for the committed subset:
# ~12MB inputs.json, 21 canonical NETLIB problems, including all the
# famous tractable classics (afiro, adlittle, sc50a/b, share2b, blend,
# recipe, beaconfd, israel, lotfi, scsd1, sc205, brandy, bore3d, boeing2,
# forplan, ...).
#
# The Kennington problems (ken-*, cre-*, osa-*, pds-*), 80bau3b, fit2p,
# fit2d, pilots, qaps — the large NETLIB problems — sit outside this gate
# until the v0.2 sparse wire format lands.  Their .mps files remain
# downloaded under data/lp-netlib/raw/ for that future use.
#
# ─── Why Gurobi for parsing ──────────────────────────────────────────────────
#
# MPS is nominally standard but has decades of vendor-specific dialect drift
# (RANGES section semantics differ between Gurobi, CPLEX, and scipy; BOUNDS
# types LO/UP/FX/FR/MI/BV; RANGES sign conventions).  Gurobi has the most
# battle-tested MPS reader and presents a clean abstract model API
# (getVars(), getConstrs(), getRow()) that is independent of MPS dialect.
# This is NOT a solve — we set OutputFlag=0 and never call model.optimize().
#
# ─── Why subprocess for oracle calls ────────────────────────────────────────
#
# The oracle adapters are designed as subprocess-piped black boxes so the
# grader can invoke them identically on both generation and grade runs.
# Re-running the oracles on grade catches version drift and platform variance
# that a single generation snapshot would miss.
#
# Reference: Vanderbei, "Linear Programming: Foundations and Extensions",
# 4th ed. (Springer, 2014), §§2.5–2.7.
#
# Run:
#   python3 golden/generate.py
#
# Re-running is safe: cached downloads are verified by sha256; the reduction
# and oracle calls are recomputed (idempotent with same Gurobi/Mosek versions).

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# ─── paths ───────────────────────────────────────────────────────────────────

HERE          = Path(__file__).resolve().parent
SUITE_DIR     = HERE.parent
CORPUS_ROOT   = SUITE_DIR.parent.parent
ORACLE_GUROBI = CORPUS_ROOT / "adapters/gurobi/oracles/gurobi-lp.py"
ORACLE_MOSEK  = CORPUS_ROOT / "adapters/mosek/oracles/mosek-lp.py"
DATA_DIR      = CORPUS_ROOT / "data/lp-netlib"
RAW_DIR       = DATA_DIR / "raw"
EMPS_C_URL    = "https://netlib.org/lp/data/emps.c"
EMPS_BINARY   = DATA_DIR / "emps"

# ─── constants ───────────────────────────────────────────────────────────────

NETLIB_BASE         = "https://netlib.org/lp/data"
ENCODING_VERSION    = 1
AGREEMENT_TOL_REL   = 1e-8
ORACLE_TIMEOUT_SEC  = 60

# Dense matrix entry limit.  Problems where n_can × m_can exceeds this
# threshold have their A stored sparsely (A_sparse) rather than densely (A).
# At 2M entries × ~15 chars/entry, that's ~30MB per problem's A matrix in
# JSON — tolerable for 62 problems, total inputs.json ~500MB.
DENSE_LIMIT = 100_000

# ─── problem catalogue ───────────────────────────────────────────────────────
#
# 109 problems across three groups:
#   - 90 direct problems in netlib/lp/data/ (emps-encoded, no extension)
#   - 3 dot-name problems: pilot.ja, pilot.we, vtp.base
#   - 16 Kennington problems in netlib/lp/data/kennington/ (gzipped emps)
#
# Each entry: (problem_id, url_path, is_kennington_gz)
#   problem_id  — canonical id in inputs.json / expected.json
#   url_path    — path appended to NETLIB_BASE for download
#   is_gz       — True for Kennington gzip-compressed files

_DIRECT: list[tuple[str, str, bool]] = [
    ("25fv47",   "25fv47",   False),
    ("80bau3b",  "80bau3b",  False),
    ("adlittle", "adlittle", False),
    ("afiro",    "afiro",    False),
    ("agg",      "agg",      False),
    ("agg2",     "agg2",     False),
    ("agg3",     "agg3",     False),
    ("bandm",    "bandm",    False),
    ("beaconfd", "beaconfd", False),
    ("blend",    "blend",    False),
    ("bnl1",     "bnl1",     False),
    ("bnl2",     "bnl2",     False),
    ("boeing1",  "boeing1",  False),
    ("boeing2",  "boeing2",  False),
    ("bore3d",   "bore3d",   False),
    ("brandy",   "brandy",   False),
    ("capri",    "capri",    False),
    ("cycle",    "cycle",    False),
    ("czprob",   "czprob",   False),
    ("d2q06c",   "d2q06c",   False),
    ("d6cube",   "d6cube",   False),
    ("degen2",   "degen2",   False),
    ("degen3",   "degen3",   False),
    ("dfl001",   "dfl001",   False),
    ("e226",     "e226",     False),
    ("etamacro", "etamacro", False),
    ("fffff800", "fffff800", False),
    ("finnis",   "finnis",   False),
    ("fit1d",    "fit1d",    False),
    ("fit1p",    "fit1p",    False),
    ("fit2d",    "fit2d",    False),
    ("fit2p",    "fit2p",    False),
    ("forplan",  "forplan",  False),
    ("ganges",   "ganges",   False),
    ("gfrd-pnc", "gfrd-pnc", False),
    ("greenbea", "greenbea", False),
    ("greenbeb", "greenbeb", False),
    ("grow15",   "grow15",   False),
    ("grow22",   "grow22",   False),
    ("grow7",    "grow7",    False),
    ("israel",   "israel",   False),
    ("kb2",      "kb2",      False),
    ("lotfi",    "lotfi",    False),
    ("maros",    "maros",    False),
    ("maros-r7", "maros-r7", False),
    ("modszk1",  "modszk1",  False),
    ("nesm",     "nesm",     False),
    ("perold",   "perold",   False),
    ("pilot",    "pilot",    False),
    ("pilot4",   "pilot4",   False),
    ("pilot87",  "pilot87",  False),
    ("pilotnov", "pilotnov", False),
    ("recipe",   "recipe",   False),
    ("sc105",    "sc105",    False),
    ("sc205",    "sc205",    False),
    ("sc50a",    "sc50a",    False),
    ("sc50b",    "sc50b",    False),
    ("scagr25",  "scagr25",  False),
    ("scagr7",   "scagr7",   False),
    ("scfxm1",   "scfxm1",   False),
    ("scfxm2",   "scfxm2",   False),
    ("scfxm3",   "scfxm3",   False),
    ("scorpion", "scorpion", False),
    ("scrs8",    "scrs8",    False),
    ("scsd1",    "scsd1",    False),
    ("scsd6",    "scsd6",    False),
    ("scsd8",    "scsd8",    False),
    ("sctap1",   "sctap1",   False),
    ("sctap2",   "sctap2",   False),
    ("sctap3",   "sctap3",   False),
    ("seba",     "seba",     False),
    ("share1b",  "share1b",  False),
    ("share2b",  "share2b",  False),
    ("shell",    "shell",    False),
    ("ship04l",  "ship04l",  False),
    ("ship04s",  "ship04s",  False),
    ("ship08l",  "ship08l",  False),
    ("ship08s",  "ship08s",  False),
    ("ship12l",  "ship12l",  False),
    ("ship12s",  "ship12s",  False),
    ("sierra",   "sierra",   False),
    ("stair",    "stair",    False),
    ("standata", "standata", False),
    ("standgub", "standgub", False),
    ("standmps", "standmps", False),
    ("stocfor1", "stocfor1", False),
    ("stocfor2", "stocfor2", False),
    ("tuff",     "tuff",     False),
    ("wood1p",   "wood1p",   False),
    ("woodw",    "woodw",    False),
    # Dot-name problems (emps-encoded, dot → dash in id)
    ("pilot-ja", "pilot.ja", False),
    ("pilot-we", "pilot.we", False),
    ("vtp-base", "vtp.base", False),
]

_KENNINGTON: list[tuple[str, str, bool]] = [
    ("cre-a",  "kennington/cre-a.gz",  True),
    ("cre-b",  "kennington/cre-b.gz",  True),
    ("cre-c",  "kennington/cre-c.gz",  True),
    ("cre-d",  "kennington/cre-d.gz",  True),
    ("ken-07", "kennington/ken-07.gz", True),
    ("ken-11", "kennington/ken-11.gz", True),
    ("ken-13", "kennington/ken-13.gz", True),
    ("ken-18", "kennington/ken-18.gz", True),
    ("osa-07", "kennington/osa-07.gz", True),
    ("osa-14", "kennington/osa-14.gz", True),
    ("osa-30", "kennington/osa-30.gz", True),
    ("osa-60", "kennington/osa-60.gz", True),
    ("pds-02", "kennington/pds-02.gz", True),
    ("pds-06", "kennington/pds-06.gz", True),
    ("pds-10", "kennington/pds-10.gz", True),
    ("pds-20", "kennington/pds-20.gz", True),
]

ALL_PROBLEMS: list[tuple[str, str, bool]] = _DIRECT + _KENNINGTON


# ─── phase 1: build / verify the emps binary ─────────────────────────────────


def build_emps() -> Path:
    """Ensure the emps C decoder is compiled and return its path.

    emps (David M. Gay, AT&T Bell Laboratories) decodes NETLIB's proprietary
    compressed-MPS format back to standard MPS.  Compiled once from source;
    the binary lives in data/lp-netlib/.
    """
    if EMPS_BINARY.exists():
        print("  [emps] binary exists, skipping build", file=sys.stderr)
        return EMPS_BINARY

    print("  [emps] downloading and compiling emps.c ...", file=sys.stderr)
    c_path = DATA_DIR / "emps.c"
    urllib.request.urlretrieve(EMPS_C_URL, c_path)
    result = subprocess.run(
        ["gcc", "-O2", "-o", str(EMPS_BINARY), str(c_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to compile emps.c:\n{result.stderr}")
    print(f"  [emps] compiled OK → {EMPS_BINARY}", file=sys.stderr)
    return EMPS_BINARY


# ─── phase 2: download and decode MPS files ──────────────────────────────────


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def download_problem(
    problem_id: str, url_path: str, is_gz: bool, emps_bin: Path
) -> tuple[Path, str]:
    """Download (if not cached) and decode one NETLIB problem to MPS.

    Returns (mps_path, sha256_of_raw_bytes).

    The raw (emps-encoded or gzip-emps-encoded) bytes are the tamper-evidence
    anchor for the MANIFEST.  The decoded MPS is what Gurobi reads.  Downloads
    are cached under raw/{problem_id}.raw and raw/{problem_id}.mps.  A sha256
    ledger (raw/sha256.sum) records the raw-bytes hash; on re-run we verify
    before skipping.
    """
    url        = f"{NETLIB_BASE}/{url_path}"
    raw_path   = RAW_DIR / f"{problem_id}.raw"
    mps_path   = RAW_DIR / f"{problem_id}.mps"
    sha_ledger = RAW_DIR / "sha256.sum"

    ledger: dict[str, str] = {}
    if sha_ledger.exists():
        for line in sha_ledger.read_text().splitlines():
            if "  " in line:
                h, name = line.strip().split("  ", 1)
                ledger[name] = h

    if raw_path.exists() and mps_path.exists() and problem_id in ledger:
        actual = sha256_file(raw_path)
        if actual == ledger[problem_id]:
            return mps_path, ledger[problem_id]
        print(f"  [download] sha256 mismatch for {problem_id}, re-downloading",
              file=sys.stderr)

    print(f"  [download] {problem_id} ← {url}", file=sys.stderr)
    raw_bytes = urllib.request.urlopen(url, timeout=60).read()
    if is_gz:
        raw_bytes = gzip.decompress(raw_bytes)

    raw_path.write_bytes(raw_bytes)
    raw_sha = sha256_bytes(raw_bytes)

    result = subprocess.run(
        [str(emps_bin), str(raw_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"emps failed for {problem_id}:\n{result.stderr}")
    mps_text = result.stdout
    if not mps_text.strip():
        raise RuntimeError(f"emps produced empty output for {problem_id}")
    mps_path.write_text(mps_text, encoding="utf-8")

    ledger[problem_id] = raw_sha
    lines = sorted(f"{h}  {n}" for n, h in ledger.items())
    sha_ledger.write_text("\n".join(lines) + "\n")

    return mps_path, raw_sha


# ─── phase 3: parse MPS via Gurobi ───────────────────────────────────────────


def _gurobipy_env():
    """Return a silent Gurobi environment.

    Imported and started lazily so that importing this module (e.g. by the
    registry or a test harness) does not trigger Gurobi licence activation at
    import time — CLAUDE.md Rule: tool entry points must stay side-effect-free
    at import time.
    """
    import gurobipy as gp
    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 0)
    env.setParam("LogToConsole", 0)
    env.start()
    return env


def parse_general_form(mps_path: Path, env) -> dict:
    """Read an MPS file via Gurobi and return its general-form representation.

    General form:
        minimise    c^T x
        subject to  A_i x {≤, =, ≥} b_i   for each constraint i
                    lo_j ≤ x_j ≤ hi_j      for each variable j

    We use Gurobi's model API rather than parsing MPS text directly because:
    - Gurobi handles all BOUNDS types (LO, UP, FX, FR, MI, BV) correctly.
    - Gurobi expands RANGES sections into their effective sense/rhs.
    - Gurobi preserves original variable and constraint names for meta.var_map.

    Returns a dict with keys:
        c_orig       list[float]          objective coefficients
        var_names    list[str]            original NETLIB variable names
        constr_names list[str]            original NETLIB constraint names
        constr_sense list[str]            '<', '=', or '>'
        constr_rhs   list[float]          right-hand-side values
        A_rows       list[dict[int,float]] sparse rows (col_index → coeff)
        var_lb       list[float]          lower bounds (−∞ = math.inf with neg sign)
        var_ub       list[float]          upper bounds (+∞ = math.inf)
    """
    import gurobipy as gp
    model = gp.read(str(mps_path), env=env)

    vars_list = model.getVars()
    constrs   = model.getConstrs()

    var_names    = [v.VarName for v in vars_list]
    c_orig       = [v.Obj     for v in vars_list]
    var_lb       = [v.LB      for v in vars_list]
    var_ub       = [v.UB      for v in vars_list]
    constr_names = [c.ConstrName for c in constrs]
    constr_sense = [c.Sense      for c in constrs]
    constr_rhs   = [c.RHS        for c in constrs]

    A_rows: list[dict[int, float]] = []
    for c in constrs:
        row  = model.getRow(c)
        d: dict[int, float] = {}
        for k in range(row.size()):
            d[row.getVar(k).index] = row.getCoeff(k)
        A_rows.append(d)

    del model
    return {
        "c_orig": c_orig, "var_names": var_names,
        "constr_names": constr_names, "constr_sense": constr_sense,
        "constr_rhs": constr_rhs, "A_rows": A_rows,
        "var_lb": var_lb, "var_ub": var_ub,
    }


# ─── phase 4: reduce to canonical SCS form ───────────────────────────────────
#
# Canonical form (ADR-0030 §C):
#     minimise  c^T x_can
#     subject to  A_can x_can = b_can
#                 x_can ≥ 0    (NonNegCone over all indices)
#
# Canonical variable layout (see code below for details):
#   [orig_shifted_0, …, orig_shifted_{n-1},
#    free_minus_k, …,               ← one slot per free original var
#    bound_slack_j, …,              ← one slot per upper-bounded var
#    row_slack_i, …]                ← one slot per inequality constraint
#
# The var_map, slack_intro, free_split, bound_slack meta fields record the
# reduction so a downstream consumer can invert x_can back to NETLIB names.


def _is_free(lb: float, ub: float) -> bool:
    return math.isinf(lb) and lb < 0 and math.isinf(ub) and ub > 0


def reduce_to_canonical(gf: dict) -> dict:
    """Apply the Vanderbei §2.5–2.7 reduction to canonical SCS form.

    Returns a dict with keys:
        c_can       list[float]          canonical objective
        A_can_rows  list[dict[int,float]] sparse rows of canonical A
        b_can       list[float]          canonical rhs
        n_can       int                  canonical variable count
        m_can       int                  canonical constraint count
        meta        dict                 var_map, slack_intro, free_split, bound_slack
    """
    c_orig       = gf["c_orig"]
    var_names    = gf["var_names"]
    constr_sense = gf["constr_sense"]
    constr_rhs   = gf["constr_rhs"]
    A_rows       = gf["A_rows"]
    var_lb       = gf["var_lb"]
    var_ub       = gf["var_ub"]

    n_orig = len(c_orig)
    m_orig = len(constr_rhs)

    # ── Classify each original variable ──────────────────────────────────────
    var_class: list[str] = []
    for j in range(n_orig):
        if _is_free(var_lb[j], var_ub[j]):
            var_class.append("free")
        elif not math.isinf(var_ub[j]):
            var_class.append("bounded_above")
        else:
            var_class.append("normal")

    # ── Assign canonical indices ──────────────────────────────────────────────
    # Phase A: primary slot for each original var (indices 0..n_orig-1)
    can_idx: list[int] = list(range(n_orig))
    next_idx = n_orig

    # Phase B: free-split minus-counterparts
    free_minus_idx: dict[int, int] = {}
    for j in range(n_orig):
        if var_class[j] == "free":
            free_minus_idx[j] = next_idx
            next_idx += 1

    # Phase C: bound-constraint slack slots (one per upper-bounded var)
    bound_slack_idx: dict[int, int] = {}
    for j in range(n_orig):
        if var_class[j] == "bounded_above":
            bound_slack_idx[j] = next_idx
            next_idx += 1

    # Phase D: row slack slots (one per inequality constraint)
    row_slack_idx: dict[int, int] = {}
    for i in range(m_orig):
        if constr_sense[i] in ("<", ">"):
            row_slack_idx[i] = next_idx
            next_idx += 1

    n_can = next_idx
    m_can = m_orig + len(bound_slack_idx)

    # ── Build canonical c ─────────────────────────────────────────────────────
    c_can = [0.0] * n_can
    for j in range(n_orig):
        c_can[can_idx[j]] = c_orig[j]
        if var_class[j] == "free":
            c_can[free_minus_idx[j]] = -c_orig[j]
        # Bound slacks and row slacks have zero cost.

    # ── Build canonical A and b ───────────────────────────────────────────────
    A_can: list[dict[int, float]] = [{} for _ in range(m_can)]
    b_can: list[float] = [0.0] * m_can

    for i in range(m_orig):
        row_dict = A_rows[i]
        rhs      = constr_rhs[i]
        sense    = constr_sense[i]

        # Shift rhs to account for lo-bound shifts: x_j → x'_j + lo_j.
        adjusted_rhs = rhs
        for j, coef in row_dict.items():
            lb_j = var_lb[j]
            if not math.isinf(lb_j):
                adjusted_rhs -= coef * lb_j
        b_can[i] = adjusted_rhs

        # Fill A_can[i]
        for j, coef in row_dict.items():
            if var_class[j] == "free":
                A_can[i][can_idx[j]]        =  coef
                A_can[i][free_minus_idx[j]] = -coef
            else:
                A_can[i][can_idx[j]] = coef

        # Row slack: converts inequality to equality
        if sense == "<":
            A_can[i][row_slack_idx[i]] = 1.0     # a^T x' + s = rhs', s≥0
        elif sense == ">":
            A_can[i][row_slack_idx[i]] = -1.0    # a^T x' - s = rhs', s≥0

    # Bound-slack equality rows: x'_j + t_j = hi_j - lo_j
    for k, (j, slack_col) in enumerate(sorted(bound_slack_idx.items())):
        row_num = m_orig + k
        A_can[row_num][can_idx[j]] = 1.0
        A_can[row_num][slack_col]  = 1.0
        b_can[row_num]             = var_ub[j] - var_lb[j]

    # ── Build meta fields ─────────────────────────────────────────────────────
    var_map: dict[str, int] = {}
    for j in range(n_orig):
        name = var_names[j]
        if var_class[j] == "free":
            var_map[name + "+"] = can_idx[j]
            var_map[name + "-"] = free_minus_idx[j]
        else:
            var_map[name] = can_idx[j]

    slack_intro = [
        {
            "row":         gf["constr_names"][i],
            "sense":       "<=" if constr_sense[i] == "<" else ">=",
            "slack_index": row_slack_idx[i],
        }
        for i in sorted(row_slack_idx)
    ]

    free_split = [
        {
            "orig_var":    var_names[j],
            "plus_index":  can_idx[j],
            "minus_index": free_minus_idx[j],
        }
        for j in sorted(free_minus_idx)
    ]

    bound_slack_meta = [
        {
            "orig_var":    var_names[j],
            "slack_index": bound_slack_idx[j],
            "upper_shift": var_ub[j] - var_lb[j],
        }
        for j in sorted(bound_slack_idx)
    ]

    return {
        "c_can":      c_can,
        "A_can_rows": A_can,
        "b_can":      b_can,
        "n_can":      n_can,
        "m_can":      m_can,
        "meta": {
            "var_map":     var_map,
            "slack_intro": slack_intro,
            "free_split":  free_split,
            "bound_slack": bound_slack_meta,
        },
    }


def sparse_rows_to_dense(A_sparse: list[dict[int, float]], n: int) -> list[list[float]]:
    """Convert sparse row dicts to dense list-of-lists for JSON output.

    Only called for problems within the DENSE_LIMIT.  For large problems we
    use the sparse encoding directly.
    """
    return [
        [row.get(j, 0.0) for j in range(n)]
        for row in A_sparse
    ]


def build_input_record(
    problem_id: str,
    gf: dict,
    reduction: dict,
    raw_sha: str,
    is_sparse: bool,
) -> dict:
    """Build the canonical input record for one NETLIB problem.

    For small/medium problems (is_sparse=False), A is stored as a dense
    list-of-lists in Ax_eq_b.A.

    For large problems (is_sparse=True), A is stored as A_sparse (list of
    {col_index: coeff} dicts) to avoid multi-GB JSON.  Ax_eq_b.A is set to []
    and Ax_eq_b.A_sparse carries the sparse encoding.  The verifier's KKT
    checks skip gracefully when A=[].  Oracle calls are bypassed for these
    problems (oracle_bypass=True in meta).
    """
    n_can = reduction["n_can"]
    c_can = reduction["c_can"]
    b_can = reduction["b_can"]
    A_rows = reduction["A_can_rows"]

    if is_sparse:
        # Sparse encoding: list of {str(col): coeff} per row.
        # JSON keys must be strings.
        A_field: list[list[float]] = []
        A_sparse_field = [
            {str(col): coef for col, coef in row.items()}
            for row in A_rows
        ]
        Ax_eq_b: dict[str, Any] = {
            "A":        A_field,       # [] — wire compat marker
            "A_sparse": A_sparse_field,  # the actual data
            "b":        b_can,
        }
    else:
        A_dense = sparse_rows_to_dense(A_rows, n_can)
        Ax_eq_b = {"A": A_dense, "b": b_can}

    return {
        "id": problem_id,
        "input": {
            "minimize": {"c": c_can},
            "subjectTo": {
                "Ax_eq_b": Ax_eq_b,
                "cones":   [{"head": "NonNegCone", "indices": list(range(n_can))}],
            },
            "precision": 1e-8,
        },
        "meta": {
            "source":           f"https://{NETLIB_BASE.replace('https://', '')}/{problem_id.replace('-', '.')}",
            "sha256":           raw_sha,
            "n_vars_original":  len(gf["var_names"]),
            "n_vars_canonical": n_can,
            "n_constraints":    reduction["m_can"],
            "sparse_matrix":    is_sparse,
            **reduction["meta"],
        },
    }


# ─── phase 5: oracle invocation and dual-witness consensus ───────────────────


def run_oracle(
    oracle_path: Path,
    problem: dict,
    timeout: int = ORACLE_TIMEOUT_SEC,
) -> dict | None:
    """Pipe the canonical problem JSON through an oracle subprocess.

    Returns None on timeout or parse error.  The oracle handles all non-optimal
    paths (infeasible, unbounded, numerical-breakdown) via its own status
    taxonomy.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(oracle_path)],
            input=json.dumps(problem).encode(),
            capture_output=True,
            timeout=timeout,
        )
        return json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        return None
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  [oracle] parse error from {oracle_path.name}: {e}", file=sys.stderr)
        return None


def build_consensus(g: dict, m: dict) -> dict:
    """Compare two oracle records and return the pinned consensus block.

    Mirrors lp-small/golden/generate.py build_consensus() exactly.

    Agreement criterion: |obj_g − obj_m| ≤ AGREEMENT_TOL_REL · max(1, |obj_g|)
    — matches the verifier's oracle_agreement check (verifier_protocol.md §9).
    """
    if g["status"] != m["status"]:
        return {
            "agreement":     False,
            "agreement_tol": AGREEMENT_TOL_REL,
            "reason":        f"status mismatch: gurobi={g['status']} mosek={m['status']}",
        }

    if g["status"] == "optimal":
        og    = g["objective"]
        om    = m["objective"]
        diff  = abs(og - om)
        scale = max(1.0, abs(og), abs(om))
        agree = diff <= AGREEMENT_TOL_REL * scale
        return {
            "objective_gurobi": og,
            "objective_mosek":  om,
            "agreement":        agree,
            "agreement_tol":    AGREEMENT_TOL_REL,
            "rel_diff":         diff / scale,
        }

    # Non-optimal: status-only consensus (no objective values to compare)
    return {
        "agreement":     True,
        "agreement_tol": AGREEMENT_TOL_REL,
        "rationale":     f"status-only consensus ({g['status']})",
    }


def build_expected(
    g: dict | None,
    m: dict | None,
    oracle_timeout: bool,
    oracle_bypass: bool = False,
) -> dict:
    """Build the expected.json record for one case.

    Oracle-timeout or oracle-bypass cases get agreement=False so the verifier's
    oracle_agreement check passes trivially (see verifier_protocol.md §9:
    "when expected.consensus.agreement is false, the check passes by N/A").
    """
    if oracle_bypass:
        return {
            "status": "unknown",
            "consensus": {
                "agreement":     False,
                "agreement_tol": AGREEMENT_TOL_REL,
                "reason":        "oracle not invoked — matrix too large for dense wire format",
            },
        }

    if oracle_timeout:
        return {
            "status": "unknown",
            "consensus": {
                "agreement":     False,
                "agreement_tol": AGREEMENT_TOL_REL,
                "reason":        "oracle timeout",
            },
        }

    assert g is not None and m is not None
    consensus = build_consensus(g, m)
    status    = g["status"]
    expected: dict[str, Any] = {"status": status, "consensus": consensus}
    if status == "optimal" and consensus.get("agreement"):
        expected["objective"] = (g["objective"] + m["objective"]) / 2.0
    return expected


# ─── phase 6: MANIFEST.toml writer ───────────────────────────────────────────


def write_manifest(problem_ids: list[str], raw_shas: dict[str, str]) -> None:
    """Write data/lp-netlib/MANIFEST.toml with per-file provenance."""
    sha_ledger_path = RAW_DIR / "sha256.sum"
    ledger_sha = sha256_file(sha_ledger_path) if sha_ledger_path.exists() else "PENDING"
    n_ken = sum(1 for p in problem_ids if p in {
        r[0] for r in _KENNINGTON
    })
    lines = [
        "# MANIFEST.toml — provenance for lp-netlib raw data",
        "#",
        "# NETLIB LP collection: netlib.org/lp/data/",
        "# Compressed MPS format decoded by David M. Gay's emps program (AT&T Bell Labs).",
        "# Each problem: downloaded once, sha256-verified, decoded to standard MPS.",
        "# Per-file sha256 values in raw/sha256.sum; this MANIFEST records the",
        "# ledger sha256 as the tamper-evidence anchor.",
        "",
        "[meta]",
        'collection    = "NETLIB LP"',
        'description   = "The NETLIB LP collection (netlib.org/lp/data/). Public domain since 1985; the canonical LP benchmark battery for four decades. Decoded from NETLIB emps-compressed format via the emps C program (Gay, AT&T Bell Labs)."',
        f'retrieved_at  = "2026-05-11"',
        'retrieved_by  = "benchmarks/lp-netlib/golden/generate.py"',
        "",
        "[fetch]",
        f'emps_decoder_url     = "{EMPS_C_URL}"',
        f'sha256_ledger        = "raw/sha256.sum"',
        f'sha256_ledger_sha256 = "{ledger_sha}"',
        f'total_problems       = {len(problem_ids)}',
        "",
        "[[sources]]",
        f'url_base    = "{NETLIB_BASE}/"',
        'url_pattern = "<problem-name>"',
        'local_path  = "raw/"',
        'source_kind = "download"',
        f'file_count  = {len(problem_ids) - n_ken}',
        'note        = "Direct NETLIB problems plus dot-name variants (pilot.ja, pilot.we, vtp.base). emps-compressed binary format."',
        "",
        "[[sources]]",
        f'url_base    = "{NETLIB_BASE}/kennington/"',
        'url_pattern = "<problem-name>.gz"',
        'local_path  = "raw/kennington/"',
        'source_kind = "download"',
        f'file_count  = {n_ken}',
        'note        = "Kennington subproblems (Kennington 1990): network LP and multi-commodity flow. Gzip-compressed emps format."',
    ]
    (DATA_DIR / "MANIFEST.toml").write_text("\n".join(lines) + "\n")


# ─── main ────────────────────────────────────────────────────────────────────


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    print("=" * 72, file=sys.stderr)
    print("lp-netlib golden generator", file=sys.stderr)
    print("=" * 72, file=sys.stderr)

    # ── 0. Ensure directory layout ──────────────────────────────────────────
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "kennington").mkdir(parents=True, exist_ok=True)

    # ── 1. Build emps binary ────────────────────────────────────────────────
    print("\n[Phase 1] Building emps decoder ...", file=sys.stderr)
    emps_bin = build_emps()

    # ── 2. Start Gurobi parse environment ───────────────────────────────────
    print("\n[Phase 2] Initialising Gurobi parse environment ...", file=sys.stderr)
    gurobi_env = _gurobipy_env()
    print("  Gurobi parse environment ready.", file=sys.stderr)

    # ── 3. Process each problem ─────────────────────────────────────────────
    print(f"\n[Phase 3] Processing {len(ALL_PROBLEMS)} problems ...", file=sys.stderr)

    input_cases:    list[dict] = []
    expected_cases: list[dict] = []
    raw_shas:       dict[str, str] = {}
    skipped:        list[str] = []

    n_dense_ok   = 0  # problems with oracle calls
    n_sparse     = 0  # problems with large sparse A (oracle bypass)
    n_timeout    = 0  # oracle timeouts
    n_disagree   = 0  # oracle disagreements

    for problem_id, url_path, is_gz in ALL_PROBLEMS:
        print(f"\n  ── {problem_id} ──", file=sys.stderr)
        t0 = time.monotonic()

        # Download / decode
        try:
            mps_path, raw_sha = download_problem(problem_id, url_path, is_gz, emps_bin)
        except Exception as exc:
            print(f"  [SKIP] download failed: {exc}", file=sys.stderr)
            skipped.append(f"{problem_id}: download — {exc}")
            continue

        raw_shas[problem_id] = raw_sha

        # Parse via Gurobi
        try:
            gf = parse_general_form(mps_path, gurobi_env)
        except Exception as exc:
            print(f"  [SKIP] Gurobi parse failed: {exc}", file=sys.stderr)
            skipped.append(f"{problem_id}: Gurobi parse — {exc}")
            continue

        n_orig = len(gf["var_names"])
        m_orig = len(gf["constr_rhs"])
        print(f"  Parsed: {n_orig} vars, {m_orig} constraints", file=sys.stderr)

        # Reduce to canonical form
        try:
            reduction = reduce_to_canonical(gf)
        except Exception as exc:
            print(f"  [SKIP] reduction failed: {exc}", file=sys.stderr)
            skipped.append(f"{problem_id}: reduction — {exc}")
            continue

        n_can = reduction["n_can"]
        m_can = reduction["m_can"]
        n_free   = len(reduction["meta"]["free_split"])
        n_slacks = len(reduction["meta"]["slack_intro"])
        n_bounds = len(reduction["meta"]["bound_slack"])
        print(
            f"  Canonical: {n_can} vars "
            f"({n_free} free-splits, {n_bounds} bound-slacks, {n_slacks} row-slacks), "
            f"{m_can} equalities",
            file=sys.stderr,
        )

        # Dense-limit gate: cases above DENSE_LIMIT are skipped entirely
        # from inputs.json (no sparse-bypass — the canonical wire is dense,
        # full stop, per ADR-0030 §C).  The .mps file remains downloaded
        # under data/lp-netlib/raw/ so the case is re-includable when
        # v0.2 sparse wire format lands.
        dense_entries = n_can * m_can
        if dense_entries > DENSE_LIMIT:
            sparse_mb = dense_entries * 15 / 1e6
            print(
                f"  [SKIP] n_can×m_can = {n_can}×{m_can} = {dense_entries:,} entries "
                f"(~{sparse_mb:.0f}MB dense) > limit {DENSE_LIMIT:,} — deferred to v0.2",
                file=sys.stderr,
            )
            skipped.append(f"{problem_id}: exceeds DENSE_LIMIT ({dense_entries:,} > {DENSE_LIMIT:,})")
            n_sparse += 1
            continue

        # Build input record
        try:
            input_rec = build_input_record(problem_id, gf, reduction, raw_sha, False)
        except Exception as exc:
            print(f"  [SKIP] input record build failed: {exc}", file=sys.stderr)
            skipped.append(f"{problem_id}: input build — {exc}")
            continue

        # Oracle calls (always — every committed case is dense and graded)
        oracle_bypass  = False
        oracle_timeout = False
        g_result = m_result = None

        if not oracle_bypass:
            problem_json = input_rec["input"]
            print(f"  Running Gurobi oracle ...", file=sys.stderr)
            g_result = run_oracle(ORACLE_GUROBI, problem_json)
            g_timeout = g_result is None
            print(f"  Running Mosek oracle ...", file=sys.stderr)
            m_result = run_oracle(ORACLE_MOSEK, problem_json)
            m_timeout = m_result is None
            oracle_timeout = g_timeout or m_timeout

            if oracle_timeout:
                print(
                    f"  [WARNING] oracle timeout: gurobi={g_timeout}, mosek={m_timeout}",
                    file=sys.stderr,
                )
                input_rec["meta"]["oracle_timeout"] = True
                n_timeout += 1

        expected = build_expected(g_result, m_result, oracle_timeout, oracle_bypass)
        agree    = expected["consensus"].get("agreement", False)

        if oracle_bypass:
            n_sparse += 1
            print(f"  Oracle bypassed (sparse matrix gate)", file=sys.stderr)
        elif not oracle_timeout:
            g_status = g_result["status"]   # type: ignore[index]
            m_status = m_result["status"]   # type: ignore[index]
            obj_str  = (
                f"obj={expected.get('objective', 'N/A'):.8g}"
                if "objective" in expected
                else f"gurobi={g_status}, mosek={m_status}"
            )
            print(
                f"  Oracle result: gurobi={g_status}, mosek={m_status}, "
                f"agree={agree}  {obj_str}",
                file=sys.stderr,
            )
            if agree:
                n_dense_ok += 1
            else:
                n_disagree += 1

        elapsed = time.monotonic() - t0
        print(f"  Done in {elapsed:.1f}s", file=sys.stderr)

        input_cases.append({
            "id":    input_rec["id"],
            "input": input_rec["input"],
            "meta":  input_rec["meta"],
        })
        expected_cases.append({"id": problem_id, "expected": expected})

    # ── 4. Write outputs ─────────────────────────────────────────────────────
    print("\n[Phase 4] Writing outputs ...", file=sys.stderr)

    inputs_doc: dict[str, Any] = {
        "encoding_version": ENCODING_VERSION,
        "seed":             None,
        "problem":          "lp-netlib — the NETLIB LP collection in canonical SCS form",
        "cases":            input_cases,
    }
    expected_doc: dict[str, Any] = {"cases": expected_cases}

    inputs_path   = HERE / "inputs.json"
    expected_path = HERE / "expected.json"

    print("  Writing inputs.json ...", file=sys.stderr)
    inputs_path.write_text(json.dumps(inputs_doc, indent=2, allow_nan=False) + "\n")
    print("  Writing expected.json ...", file=sys.stderr)
    expected_path.write_text(json.dumps(expected_doc, indent=2, allow_nan=False) + "\n")

    # ── 5. Write MANIFEST and sha256.sum ────────────────────────────────────
    write_manifest(list(raw_shas.keys()), raw_shas)

    # ── 6. Summary ──────────────────────────────────────────────────────────
    n_ok = len(input_cases)
    print("\n" + "=" * 72, file=sys.stderr)
    print("Summary", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    print(f"  Problems attempted      : {len(ALL_PROBLEMS)}", file=sys.stderr)
    print(f"  Successfully processed  : {n_ok}", file=sys.stderr)
    print(f"  Skipped (fatal error)   : {len(skipped)}", file=sys.stderr)
    print(f"  Dense (oracle ran)      : {n_dense_ok + n_disagree + n_timeout}", file=sys.stderr)
    print(f"    Oracle agreed         : {n_dense_ok}", file=sys.stderr)
    print(f"    Oracle disagreed      : {n_disagree}", file=sys.stderr)
    print(f"    Oracle timeout        : {n_timeout}", file=sys.stderr)
    print(f"  Sparse (oracle bypassed): {n_sparse}", file=sys.stderr)
    print(f"  DENSE_LIMIT             : {DENSE_LIMIT:,} entries", file=sys.stderr)

    if skipped:
        print("\n  Skipped problems:", file=sys.stderr)
        for s in skipped:
            print(f"    {s}", file=sys.stderr)

    # Oracle disagreements listing
    disagrees = [
        ec for ec in expected_cases
        if not ec["expected"]["consensus"].get("agreement")
        and "oracle not invoked" not in ec["expected"]["consensus"].get("reason", "")
        and "oracle timeout" not in ec["expected"]["consensus"].get("reason", "")
    ]
    if disagrees:
        print("\n  Oracle disagreements:", file=sys.stderr)
        for ec in disagrees:
            reason = ec["expected"]["consensus"].get("reason", ec["expected"]["consensus"].get("rationale", ""))
            print(f"    {ec['id']}: {reason}", file=sys.stderr)

    print("\n  Wrote:", file=sys.stderr)
    print(f"    {inputs_path}   sha256={sha256_of(inputs_path)}", file=sys.stderr)
    print(f"    {expected_path} sha256={sha256_of(expected_path)}", file=sys.stderr)
    print(f"    {DATA_DIR}/MANIFEST.toml", file=sys.stderr)
    print(f"    {RAW_DIR}/sha256.sum ({n_ok} entries)", file=sys.stderr)
    print(f"  Cases: {n_ok}", file=sys.stderr)


if __name__ == "__main__":
    main()
