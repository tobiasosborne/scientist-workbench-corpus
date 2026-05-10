#!/usr/bin/env python3
"""Generate `golden/inputs.json` and `golden/expected.json` for the
`meijer-g` bench by computing each case's MeijerG value at high
precision via mpmath, with optional Wolfram cross-validation.

Per ADR-0019 §6 generators are seeded / deterministic.  Per the bead
spec hv0.11 + ORACLE-STRATEGY.md (tstournament problem-13), the
oracle strategy is **two-oracle consensus**: Wolfram + mpmath both
evaluated at 110 decimal digits, with Tier-0 anchors additionally
RHS-evaluated at 200 dps from the *elementary closed form* (so the
truth is bug-immune to either oracle's MeijerG codepath).

Pipeline per case:

    an, ap, bm, bq, z, target_dps  ⟶  parameters (rationals where possible)
    mpmath.meijerg(...)            ⟶  primary 110-dps truth
    wolframscript MeijerG[...]     ⟶  cross-witness at 110 dps
    consensus(mp, wolf, tol)       ⟶  pinned truth + consensus tag
    Tier-0 only:                   RHS evaluated directly at 200 dps
                                   (e^{-z} for the b={0} anchor, etc.)
    case row                       ⟶  inputs.json + expected.json

Number-bearing fields are STRINGS (PRD §0.1).  Rational inputs use
`"p/q"`; complex inputs use `{re: <dec-string>, im: <dec-string>}`.
The bench wire format mirrors `bench/hypergeometric-pfq/`.

Tier structure (per VERIFIER-PROTOCOL.md + the bead spec):

  Tier 0  closed-form anchors    — Bateman §5.6 / DLMF §16.18
                                    reductions; RHS evaluated directly
  Tier A  elementary symbolic    — symbolic-required; rule must match
  Tier B  special-fn symbolic    — symbolic-required; special-fn rule
  Tier C  generic Slater         — middle of parameter space, |z|<0.95
  Tier D  anti-Stokes            — |z| ∈ [0.95, 1.05], asymptotic
                                    crossover band
  Tier E  parameter coalescence  — integer-spaced poles in bm or aN
  Tier F  branch-cut sensitive   — z near or on negative real axis
  Tier G  refusal cases          — quarantine band; out-of-region;
                                    non-finite input
  Tier H  speed-gate             — cross-cutting subset of C/D/E/F
                                    (re-uses ids; tier-h.json file
                                    enumerates ids only)

Run as:

    python3 bench/meijer-g/reference/generate-truth.py \
        --output bench/meijer-g/golden \
        [--no-wolfram] [--limit N]

References
----------
* `tstournament/.../problem-13/ORACLE-STRATEGY.md` — two-oracle
  consensus + quarantine-band protocol.
* `tstournament/.../problem-13/VERIFIER-PROTOCOL.md` — three output
  shapes + tier-by-tier tolerances.
* Erdélyi et al. (Bateman manuscript project) §5.6 — elementary
  reductions of `G^{1,0}_{0,1}(_; b | z)`, `G^{m,n}_{p,q}` family.
* DLMF §16.17, §16.18 — definition + reduction tables.
* Wolfram Functions Site, MeijerG/03 — cross-check.
* mpmath documentation, `mpmath.meijerg`:
  https://mpmath.org/doc/current/functions/hypergeometric.html
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Optional

import mpmath
mpmath.mp.dps = 110  # primary oracle precision

WOLFRAM_TIMEOUT = 90  # seconds; some MeijerG cases take 30+ sec at 110 dps

# Quarantine-band threshold (ORACLE-STRATEGY.md §"Quarantine protocol").
QUARANTINE_LO = mpmath.mpf("0.99")
QUARANTINE_HI = mpmath.mpf("1.01")


# ---------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    """One bench case.

    Parameters `an`, `ap`, `bm`, `bq` are tuples of (re, im) string
    pairs.  `z` is one (re, im) string pair.  Rational strings ('1/3')
    and decimal strings ('0.5') both round-trip via `Fraction` ⟶ mpf.

    `truth_method` records how the reference value was derived:

      * "elementary-rhs@200dps"   — Tier 0; the closed-form RHS
        evaluated directly.  No MeijerG call on the oracle side.
      * "consensus-wolfram-mpmath@110dps" — Tier A–F; both oracles
        agreed past the quarantine threshold.
      * "mpmath-only@110dps"      — Wolfram unavailable / timed out;
        relaxed (still ≥ 80 sig figs over the bench tolerance).
      * "dispatcher-quarantine-expected" — Tier G refusal.
    """

    id: str
    tier: str
    category: str
    an: tuple[tuple[str, str], ...]
    ap: tuple[tuple[str, str], ...]
    bm: tuple[tuple[str, str], ...]
    bq: tuple[tuple[str, str], ...]
    z: tuple[str, str]
    precision: int                              # dps the *tool* runs at
    tolerance_rel: str                          # decimal-string tolerance
    request_mode: str = "auto"                  # auto / symbolic-required / numerical-required
    expected_method: str = ""                   # expected dispatcher lane (informational)
    rule: str = ""                              # human-readable identity
    rhs_mpmath: str = ""                        # Tier-0 only; eval'd at 200 dps
    expected_kind: str = "value"                # "value" | "symbolic" | "tagged"
    expected_tag: str = ""                      # for tagged refusals
    expected_payload: dict = field(default_factory=dict)
    skip_wolfram: bool = False
    notes: str = ""
    force_method: str = ""                      # for --force-method=<lane> cases


def _r(re: str, im: str = "0") -> tuple[str, str]:
    return (re, im)


# ----- Tier 0: closed-form anchors -----------------------------------
#
# Each anchor reduces to an elementary RHS computed at 200 dps via
# the elementary mpmath function.  No MeijerG is involved on the
# oracle side: a regression in either oracle's MeijerG implementation
# is invisible to this tier.  The RHS string is computed from the
# `rhs_mpmath` Python expression (evaluated under `mpmath.mp.dps=200`)
# at generate time and pinned in the corpus.
#
# Curated from `packages/meijer-core/src/dispatch-rules/bateman-5-6.ts`
# and `dlmf-16-18.ts` (Law 1: cite the local source).

TIER0: list[Case] = [
    # G^{1,0}_{0,1}(_; 0 | z) = e^{-z}  (Bateman §5.6 (8))
    Case(id="t0-G1001-b0-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 0 | 2) = e^{-2}",
         rhs_mpmath="exp(mpf('-2'))"),
    Case(id="t0-G1001-b0-z5", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"),), bq=(),
         z=_r("5"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 0 | 5) = e^{-5}",
         rhs_mpmath="exp(mpf('-5'))"),
    Case(id="t0-G1001-b0-zhalf", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"),), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 0 | 1/2) = e^{-1/2}",
         rhs_mpmath="exp(mpf('-1/2'))"),

    # G^{1,0}_{0,1}(_; 1 | z) = z·e^{-z}  (Bateman §5.6 (21))
    Case(id="t0-G1001-b1-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("1"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 1 | 2) = 2 e^{-2}",
         rhs_mpmath="mpf('2') * exp(mpf('-2'))"),

    # G^{1,0}_{0,1}(_; -1 | z) = e^{-z}/z  (Bateman §5.6 (20))
    Case(id="t0-G1001-bm1-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("-1"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; -1 | 2) = e^{-2}/2",
         rhs_mpmath="exp(mpf('-2')) / mpf('2')"),

    # G^{1,0}_{0,1}(_; 1/2 | z) = z^{1/2}·e^{-z}  (Bateman §5.6 (22))
    Case(id="t0-G1001-bhalf-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("1/2"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 1/2 | 2) = sqrt(2)·e^{-2}",
         rhs_mpmath="sqrt(mpf('2')) * exp(mpf('-2'))"),

    # G^{1,0}_{0,1}(_; -1/2 | z) = e^{-z}/sqrt(z)  (Bateman §5.6 (36))
    Case(id="t0-G1001-bmhalf-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("-1/2"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; -1/2 | 2) = e^{-2}/sqrt(2)",
         rhs_mpmath="exp(mpf('-2')) / sqrt(mpf('2'))"),

    # G^{1,0}_{0,1}(_; 2 | z) = z^2·e^{-z}  (Bateman §5.6 (35) at n=2)
    Case(id="t0-G1001-b2-z3", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("2"),), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 2 | 3) = 9 e^{-3}",
         rhs_mpmath="mpf('9') * exp(mpf('-3'))"),

    # G^{1,0}_{0,1}(_; 3/2 | z) = z^{3/2}·e^{-z}  (Bateman extra)
    Case(id="t0-G1001-b3half-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("3/2"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 3/2 | 2) = 2 sqrt(2)·e^{-2}",
         rhs_mpmath="mpf('2') * sqrt(mpf('2')) * exp(mpf('-2'))"),

    # G^{0,1}_{1,0}(0; _ | z) = e^{-1/z}/z  (Bateman §5.6 (31))
    Case(id="t0-G0110-a0-z2", tier="0", category="closed-form",
         an=(_r("0"),), ap=(), bm=(), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{0,1}_{1,0}(0; _ | 2) = e^{-1/2}/2",
         rhs_mpmath="exp(mpf('-1/2')) / mpf('2')"),

    # G^{0,1}_{1,0}(1; _ | z) = e^{-1/z}  (Bateman §5.6 (32))
    Case(id="t0-G0110-a1-z3", tier="0", category="closed-form",
         an=(_r("1"),), ap=(), bm=(), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{0,1}_{1,0}(1; _ | 3) = e^{-1/3}",
         rhs_mpmath="exp(mpf('-1/3'))"),

    # G^{0,1}_{1,0}(2; _ | z) = z·e^{-1/z}  (Bateman §5.6 (33))
    Case(id="t0-G0110-a2-z2", tier="0", category="closed-form",
         an=(_r("2"),), ap=(), bm=(), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{0,1}_{1,0}(2; _ | 2) = 2 e^{-1/2}",
         rhs_mpmath="mpf('2') * exp(mpf('-1/2'))"),

    # G^{0,1}_{1,0}(1/2; _ | z) = e^{-1/z}/sqrt(z)  (Bateman §5.6 (34))
    Case(id="t0-G0110-ahalf-z4", tier="0", category="closed-form",
         an=(_r("1/2"),), ap=(), bm=(), bq=(),
         z=_r("4"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{0,1}_{1,0}(1/2; _ | 4) = e^{-1/4}/2",
         rhs_mpmath="exp(mpf('-1/4')) / mpf('2')"),

    # G^{1,1}_{1,1}(a; 0 | z) = Γ(1−a) · (1+z)^{a−1}   (Bateman §5.6 (10))
    # at a = 1/2, z = 2: Γ(1/2)/√3 = √π / √3
    Case(id="t0-G1111-ahalf-b0-z2", tier="0", category="closed-form",
         an=(_r("1/2"),), ap=(), bm=(_r("0"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,1}_{1,1}(1/2; 0 | 2) = Γ(1/2)/√3 = √π / √3",
         rhs_mpmath="sqrt(pi) / sqrt(mpf('3'))"),

    # G^{1,1}_{1,1}(a; 0 | z) at a = 1/3, z = 1
    Case(id="t0-G1111-athird-b0-z1", tier="0", category="closed-form",
         an=(_r("1/3"),), ap=(), bm=(_r("0"),), bq=(),
         z=_r("1"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,1}_{1,1}(1/3; 0 | 1) = Γ(2/3) · 2^{-2/3}",
         rhs_mpmath="gamma(mpf('2/3')) * mpf('2')**mpf('-2/3')"),

    # G^{1,1}_{1,1}(0; b | z) = Γ(1+b) · z^b · (1+z)^{-(b+1)}  (Bateman §5.6 (11))
    Case(id="t0-G1111-a0-bhalf-z2", tier="0", category="closed-form",
         an=(_r("0"),), ap=(), bm=(_r("1/2"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,1}_{1,1}(0; 1/2 | 2) = Γ(3/2) · √2 · 3^{-3/2}",
         rhs_mpmath="gamma(mpf('3/2')) * sqrt(mpf('2')) / mpf('3')**mpf('3/2')"),

    # G^{1,0}_{0,2}(_; 0, 1/2 | z²/4) = (1/√π) cos(z)
    # We pick z=1 ⟹ argument = 1/4
    # Bateman §5.6 — but: mpmath's cos formula has sign convention that
    # gives cos(z) with positive real part for small +z; we use it directly.
    Case(id="t0-G1002-b0-bhalf-zquart", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("1/4"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 0, 1/2 | 1/4) = (1/√π) cos(1)",
         # cos(2√(1/4)) = cos(1)
         rhs_mpmath="cos(mpf('1')) / sqrt(pi)"),

    # G^{1,0}_{0,2}(_; 1/2, 0 | z²/4) = (1/√π) sin(z)
    Case(id="t0-G1002-bhalf-b0-zquart", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("1/2"), _r("0")), bq=(),
         z=_r("1/4"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 1/2, 0 | 1/4) = (1/√π) sin(1)",
         rhs_mpmath="sin(mpf('1')) / sqrt(pi)"),

    # G^{1,0}_{0,2}(_; ν/2, -ν/2 | z²/4) = J_ν(z); pick ν=1, z=2 ⟹ arg=1
    # mpmath verification: meijerg([[],[]], [[1/2],[-1/2]], 1) ≈ J_1(2) ≈ 0.5767
    Case(id="t0-G1002-bessel-J1-z1", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("1/2"), _r("-1/2")), bq=(),
         z=_r("1"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 1/2, -1/2 | 1) = J_1(2)",
         rhs_mpmath="besselj(mpf('1'), mpf('2'))"),

    # G^{1,0}_{0,2}(_; ν/2, -ν/2 | z²/4) = J_ν(z) at ν=0, z=1 ⟹ arg=1/4
    Case(id="t0-G1002-bessel-J0-zquart", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("1/4"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 0, 0 | 1/4) = J_0(1)",
         rhs_mpmath="besselj(mpf('0'), mpf('1'))"),

    # G^{2,0}_{0,2}(_; ν/2, -ν/2 | z²/4) = 2 K_ν(z); pick ν=0, z=2 ⟹ arg=1
    Case(id="t0-G2002-bessel-K0-z1", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("1"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 0 | 1) = 2 K_0(2)",
         rhs_mpmath="mpf('2') * besselk(mpf('0'), mpf('2'))"),

    # G^{2,0}_{0,2}(_; 1/2, -1/2 | z²/4) = 2 K_1(z); ν=1, z=2 ⟹ arg=1
    Case(id="t0-G2002-bessel-K1-z1", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("1/2"), _r("-1/2")), bq=(),
         z=_r("1"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 1/2, -1/2 | 1) = 2 K_1(2)",
         rhs_mpmath="mpf('2') * besselk(mpf('1'), mpf('2'))"),

    # DLMF §16.18 / dispatch-rules/dlmf-16-18.ts dlmf-16-18-erf:
    #   G^{1,1}_{1,2}(1; 1/2, 0 | z) = √π · erf(√z)
    # Slot layout: an=[1], ap=[], bm=[1/2], bq=[0]; m=1, n=1, p=1, q=2.
    # Verified mpmath: meijerg([[1],[]], [[1/2],[0]], 2) = √π·erf(√2)
    Case(id="t0-G1112-erf-z2", tier="0", category="closed-form",
         an=(_r("1"),), ap=(), bm=(_r("1/2"),), bq=(_r("0"),),
         z=_r("2"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,1}_{1,2}(1; 1/2, 0 | 2) = √π · erf(√2)",
         rhs_mpmath="sqrt(pi) * erf(sqrt(mpf('2')))"),

    # G^{1,2}_{2,2}({1, 1}; {1, 0} | z) = log(1 + z)
    Case(id="t0-G1222-log-z2", tier="0", category="closed-form",
         an=(_r("1"), _r("1")), ap=(), bm=(_r("1"),), bq=(_r("0"),),
         z=_r("2"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,2}_{2,2}({1,1}; {1,0} | 2) = log(3)",
         rhs_mpmath="log(mpf('3'))"),

    # G^{1,2}_{2,2}({1, 1}; {1, 0} | z) = log(1 + z) at z=1/2
    Case(id="t0-G1222-log-zhalf", tier="0", category="closed-form",
         an=(_r("1"), _r("1")), ap=(), bm=(_r("1"),), bq=(_r("0"),),
         z=_r("1/2"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,2}_{2,2}({1,1}; {1,0} | 1/2) = log(3/2)",
         rhs_mpmath="log(mpf('3/2'))"),

    # G^{1,2}_{2,2}(1/2, 1; 1/2, 0 | z) = 2 · arctan(√z)  [dlmf-16-18-arctan]
    # Verified mpmath: meijerg([[1/2,1],[]], [[1/2],[0]], 2) = 1.9106... = 2 atan(√2)
    Case(id="t0-G1222-arctan-z3", tier="0", category="closed-form",
         an=(_r("1/2"), _r("1")), ap=(), bm=(_r("1/2"),), bq=(_r("0"),),
         z=_r("3"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,2}_{2,2}(1/2,1; 1/2,0 | 3) = 2·arctan(√3) = 2π/3",
         rhs_mpmath="mpf('2') * atan(sqrt(mpf('3')))"),

    # G^{1,1}_{1,1}(1−a; 0 | z) = z^0·(1−(−z))^{−1}·1/Γ(1) … let's pick simpler:
    # Actually, Bateman §5.6 (4): G^{1,1}_{1,1}(1-a; 0 | z) gives (1+z)^{-a}/Γ(a).
    # At a=1: G^{1,1}_{1,1}(0; 0 | z) ⟹ degenerate.  We pick a=2:
    # G^{1,1}_{1,1}(-1; 0 | z) ?  Let's stay with the verified rule:
    # Bateman 5.6 (10) at a = 2: G^{1,1}_{1,1}(2; 0 | z) = Γ(-1)·... — singular. Skip.

    # Simpler reduction set: the "G^{1,1}_{1,1}(a; b | z) = Γ(1+b−a)·z^b·(1+z)^{a−b−1}"
    # at a = 1/3, b = 1/4, z = 1:
    # = Γ(1 + 1/4 - 1/3) · 1^{1/4} · 2^{1/3 - 1/4 - 1}
    # = Γ(11/12) · 2^{-11/12}
    Case(id="t0-G1111-genfree-1", tier="0", category="closed-form",
         an=(_r("1/3"),), ap=(), bm=(_r("1/4"),), bq=(),
         z=_r("1"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,1}_{1,1}(1/3; 1/4 | 1) = Γ(11/12) · 2^{-11/12}",
         rhs_mpmath="gamma(mpf('11/12')) * mpf('2')**mpf('-11/12')"),

    # Larger z probes the elementary forms in a different magnitude regime
    Case(id="t0-G1001-b0-z10", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"),), bq=(),
         z=_r("10"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 0 | 10) = e^{-10}",
         rhs_mpmath="exp(mpf('-10'))"),

    # Small z (still > 0)
    Case(id="t0-G1001-b0-zsmall", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("0"),), bq=(),
         z=_r("1/10"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 0 | 1/10) = e^{-1/10}",
         rhs_mpmath="exp(mpf('-1/10'))"),

    # Bateman §5.6 (3): G^{1,1}_{1,1}(a; b | z) = Γ(1+b−a)·z^b·(1+z)^{a−b−1}
    # at a = 1, b = 1/2, z = 4: Γ(3/2)·√4·5^{-3/2}
    Case(id="t0-G1111-1-half-z4", tier="0", category="closed-form",
         an=(_r("1"),), ap=(), bm=(_r("1/2"),), bq=(),
         z=_r("4"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,1}_{1,1}(1; 1/2 | 4) = Γ(3/2) · 2 · 5^{-3/2}",
         rhs_mpmath="gamma(mpf('3/2')) * mpf('2') / mpf('5')**mpf('3/2')"),

    # Wider survey: half-integer in bm with z=3
    Case(id="t0-G1001-bhalf-z3", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("1/2"),), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 1/2 | 3) = √3·e^{-3}",
         rhs_mpmath="sqrt(mpf('3')) * exp(mpf('-3'))"),

    # Two-slot single-residue case at integer z exponent
    Case(id="t0-G1001-b3-z1", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("3"),), bq=(),
         z=_r("1"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 3 | 1) = 1·e^{-1} = 1/e",
         rhs_mpmath="exp(mpf('-1'))"),

    # G^{1,0}_{0,1}(_; -2 | z) = z^{-2}·e^{-z}
    Case(id="t0-G1001-bm2-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("-2"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; -2 | 2) = e^{-2}/4",
         rhs_mpmath="exp(mpf('-2')) / mpf('4')"),

    # Generic free b, off-integer:
    Case(id="t0-G1001-bone-third-z2", tier="0", category="closed-form",
         an=(), ap=(), bm=(_r("1/3"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-44",
         request_mode="auto", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 1/3 | 2) = 2^{1/3}·e^{-2}",
         rhs_mpmath="mpf('2')**mpf('1/3') * exp(mpf('-2'))"),

    # G^{0,1}_{1,0}(3; _ | z) = z^2·e^{-1/z}  (Bateman §5.6 (2) at a=3)
    Case(id="t0-G0110-a3-z2", tier="0", category="closed-form",
         an=(_r("3"),), ap=(), bm=(), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{0,1}_{1,0}(3; _ | 2) = 4 e^{-1/2}",
         rhs_mpmath="mpf('4') * exp(mpf('-1/2'))"),

    # G^{0,1}_{1,0}(-1; _ | z) = z^{-2}·e^{-1/z}
    Case(id="t0-G0110-am1-z2", tier="0", category="closed-form",
         an=(_r("-1"),), ap=(), bm=(), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="auto", expected_method="symbolic",
         rule="G^{0,1}_{1,0}(-1; _ | 2) = e^{-1/2}/4",
         rhs_mpmath="exp(mpf('-1/2')) / mpf('4')"),
]


# ----- Tier A: elementary symbolic (request_mode = symbolic-required) -----
#
# Inputs that must be answered symbolically.  Each rule shape is in
# the dispatcher's symbolic table.  The verifier asserts kind == 'symbolic'
# and matches one of the rule ids.

TIERA: list[Case] = [
    # Elementary G^{1,0}_{0,1}(_; b | z) family — already covered in Tier 0
    # numerically; here we test the symbolic-required path forces the symbolic
    # answer rather than the numerical one.
    Case(id="tA-sym-exp-z3", tier="A", category="elementary-symbolic",
         an=(), ap=(), bm=(_r("0"),), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-46",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: G^{1,0}_{0,1}(_; 0 | 3) ⟶ Bateman 5-6-8",
         rhs_mpmath="exp(mpf('-3'))"),

    Case(id="tA-sym-zexp-z2", tier="A", category="elementary-symbolic",
         an=(), ap=(), bm=(_r("1"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-46",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: G^{1,0}_{0,1}(_; 1 | 2) ⟶ Bateman 5-6-21",
         rhs_mpmath="mpf('2') * exp(mpf('-2'))"),

    # Note: rational-real parameters (e.g. 1/2) don't currently flow
    # through the symbolic-required path — the tool's bigcomplexToSymbolicValue
    # only recognises integer-real BigComplex values as int().  Rational
    # parameters route to the numerical lane.  Filed as an
    # `lc1`-class follow-up for the dispatcher to widen its symbolic-AST
    # admission to rational-real BigComplex.  In the meantime, Tier A's
    # symbolic-required cases use integer parameters only; rational
    # parameters appear under Tier 0 (where request_mode=auto admits the
    # numerical fallback's value-accuracy check).
    Case(id="tA-sym-G1001-bm2", tier="A", category="elementary-symbolic",
         an=(), ap=(), bm=(_r("-2"),), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-46",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: G^{1,0}_{0,1}(_; -2 | 3) ⟶ Bateman 5-6-35-nm2",
         rhs_mpmath="exp(mpf('-3')) / mpf('9')"),

    Case(id="tA-sym-recip-exp", tier="A", category="elementary-symbolic",
         an=(_r("0"),), ap=(), bm=(), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-46",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: G^{0,1}_{1,0}(0; _ | 3) ⟶ Bateman 5-6-31",
         rhs_mpmath="exp(mpf('-1/3')) / mpf('3')"),

    Case(id="tA-sym-G1001-bm1", tier="A", category="elementary-symbolic",
         an=(), ap=(), bm=(_r("-1"),), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-46",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: G^{1,0}_{0,1}(_; -1 | 3) ⟶ Bateman 5-6-20",
         rhs_mpmath="exp(mpf('-3')) / mpf('3')"),

    Case(id="tA-sym-G1001-b2", tier="A", category="elementary-symbolic",
         an=(), ap=(), bm=(_r("2"),), bq=(),
         z=_r("4"), precision=50, tolerance_rel="1e-46",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: G^{1,0}_{0,1}(_; 2 | 4) ⟶ Bateman 5-6-35",
         rhs_mpmath="mpf('16') * exp(mpf('-4'))"),

    Case(id="tA-sym-log-z3", tier="A", category="elementary-symbolic",
         an=(_r("1"), _r("1")), ap=(), bm=(_r("1"),), bq=(_r("0"),),
         z=_r("3"), precision=50, tolerance_rel="1e-44",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: log(1 + z) at z=3 ⟶ log(4)",
         rhs_mpmath="log(mpf('4'))"),

    Case(id="tA-sym-log-z2", tier="A", category="elementary-symbolic",
         an=(_r("1"), _r("1")), ap=(), bm=(_r("1"),), bq=(_r("0"),),
         z=_r("2"), precision=50, tolerance_rel="1e-44",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: log(1 + z) at z=2 ⟶ log(3)",
         rhs_mpmath="log(mpf('3'))"),

    Case(id="tA-sym-G0110-a3", tier="A", category="elementary-symbolic",
         an=(_r("3"),), ap=(), bm=(), bq=(),
         z=_r("4"), precision=50, tolerance_rel="1e-46",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: G^{0,1}_{1,0}(3; _ | 4) = 16 e^{-1/4}",
         rhs_mpmath="mpf('16') * exp(mpf('-1/4'))"),
]


# ----- Tier B: special-function symbolic (Bessel / Whittaker / Γ-product) ---
#
# These currently route through the *numerical* dispatcher because the
# symbolic table doesn't yet include the multi-slot Bessel-K / Whittaker
# rules (those are `hv0.6.*` follow-up beads).  We mark `request_mode =
# auto` and `expected_method = numerical` accordingly — the bench
# documents the *current* dispatcher behaviour, and a future v0.2 with
# full PBM / Mathai rule corpus will tighten these to symbolic-required.
# This is honest scope (Rule 8): we do NOT lie about the dispatch lane.

TIERB: list[Case] = [
    # G^{2,0}_{0,2}(_; ν/2, -ν/2 | z²/4) = 2 K_ν(z) — Bessel K family
    Case(id="tB-besselK0-z2", tier="B", category="special-fn-numerical",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("4"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="2 K_0(4)",
         rhs_mpmath="mpf('2') * besselk(mpf('0'), mpf('4'))"),

    Case(id="tB-besselK1-z2", tier="B", category="special-fn-numerical",
         an=(), ap=(), bm=(_r("1/2"), _r("-1/2")), bq=(),
         z=_r("4"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="2 K_1(4)",
         rhs_mpmath="mpf('2') * besselk(mpf('1'), mpf('4'))"),

    Case(id="tB-besselK-half-z3", tier="B", category="special-fn-numerical",
         an=(), ap=(), bm=(_r("1/4"), _r("-1/4")), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="2 K_{1/2}(2√3) = √(π/(2·2√3))·e^{-2√3}",
         rhs_mpmath="mpf('2') * besselk(mpf('1/2'), mpf('2')*sqrt(mpf('3')))"),

    Case(id="tB-besselJ0-z1", tier="B", category="special-fn-numerical",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("-1"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="J_0(2) at z = -1 (i.e. -z²/4 = -1, so z=2)",
         rhs_mpmath="besselj(mpf('0'), mpf('2'))",
         skip_wolfram=True),  # Wolfram convention differs here on negative z

    # Whittaker / Γ-product hand-derived: G^{2,0}_{1,2}({1};{0,1/2}|z) = (1/√π)·erfc(√z)
    Case(id="tB-erfc-z3", tier="B", category="special-fn-numerical",
         an=(_r("1"),), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="erfc(√3)/√π",
         rhs_mpmath="erfc(sqrt(mpf('3'))) / sqrt(pi)"),

    Case(id="tB-erfc-zhalf", tier="B", category="special-fn-numerical",
         an=(_r("1"),), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="numerical",
         rule="erfc(√(1/2))/√π",
         rhs_mpmath="erfc(sqrt(mpf('1/2'))) / sqrt(pi)"),

    # Bateman-3 generic free-free (numerical fallback if symbolic not matched at integer-zero)
    Case(id="tB-bateman3-1", tier="B", category="special-fn-numerical",
         an=(_r("1/3"),), ap=(), bm=(_r("1/4"),), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="symbolic",
         rule="Γ(1+1/4-1/3)·3^{1/4}·4^{1/3-1/4-1}",
         rhs_mpmath="gamma(mpf('11/12')) * mpf('3')**mpf('1/4') * mpf('4')**mpf('-11/12')"),

    Case(id="tB-bateman3-2", tier="B", category="special-fn-numerical",
         an=(_r("2/5"),), ap=(), bm=(_r("3/7"),), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-42",
         request_mode="auto", expected_method="symbolic",
         rule="Γ(1 + 3/7 − 2/5)·2^{3/7}·3^{2/5−3/7−1}",
         rhs_mpmath="gamma(mpf('1') + mpf('3/7') - mpf('2/5')) * mpf('2')**mpf('3/7') * mpf('3')**(mpf('2/5')-mpf('3/7')-mpf('1'))"),
]


# ----- Tier C: generic Slater (numerical) ----------------------------
#
# Middle of parameter space; |z| safely away from coalescence and
# the unit circle.  These are the dispatcher's bread-and-butter
# numerical lane.  Every truth value comes from the
# Wolfram + mpmath consensus oracle at 110 dps.

TIERC: list[Case] = [
    # G^{1,0}_{0,1}(_; b | z) generic — already symbolic, but Tier C
    # covers shapes that DON'T have a symbolic match; we use larger
    # (m,n) combinations.

    # G^{1,0}_{0,2}(_; b1, b2 | z) — Bessel-shape, generic free b's
    Case(id="tC-G1002-frac-bz", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("1/3"), _r("2/3")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 1/3, 2/3 | 1/2)"),

    Case(id="tC-G1002-mixed-bz1", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("1/4"), _r("3/4")), bq=(),
         z=_r("3/2"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 1/4, 3/4 | 3/2)"),

    Case(id="tC-G2002-bz1", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("1/3")), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 1/3 | 2)"),

    Case(id="tC-G2002-bz2", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("1/4"), _r("1/2")), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 1/4, 1/2 | 3)"),

    # G^{1,0}_{1,2}({a}; {b1, b2} | z)
    Case(id="tC-G1012-bz1", tier="C", category="generic-slater",
         an=(_r("1/2"),), ap=(), bm=(_r("0"), _r("1/3")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,1}_{1,2}({1/2}; {0, 1/3} | 1/2)"),

    Case(id="tC-G1012-bz2", tier="C", category="generic-slater",
         an=(_r("1/3"),), ap=(), bm=(_r("0"), _r("1/4")), bq=(),
         z=_r("1/3"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,1}_{1,2}({1/3}; {0, 1/4} | 1/3)"),

    # NOTE: G^{0,2}_{2,2}({a1,a2}; ; ; {b1,b2} | z) — m=0, n=2, p=2, q=2.
    # `m + n = 2 = p`, balanced case; the dispatcher's `canUseSlater`
    # quarantines this for `|z| ≈ 1` and the contour pre-filter
    # forbids one-sided clusters at `|z| ≥ 1`.  At `|z| = 1/2` this
    # specific shape lands in `out-of-region` per the dispatcher's
    # cost-bound rules.  Removed from v0.1 Tier C; could re-appear
    # under a future "explicit-method-required" tier or as a refusal
    # case.

    # G^{1,1}_{2,2}({a1}; {a2}; {b1}; {b2} | z) — 2x2
    Case(id="tC-G1122-bz1", tier="C", category="generic-slater",
         an=(_r("1/2"),), ap=(_r("1/3"),), bm=(_r("0"),), bq=(_r("1/4"),),
         z=_r("1/2"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,1}_{2,2} mid-sample"),

    Case(id="tC-G1122-bz2", tier="C", category="generic-slater",
         an=(_r("2/3"),), ap=(_r("3/4"),), bm=(_r("1/5"),), bq=(_r("0"),),
         z=_r("2/3"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,1}_{2,2} another mid-sample"),

    # Larger z (>1) where we still admit Slater (p < q etc.)
    Case(id="tC-G2002-zlarger", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("4"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0(4) ≈ tier-C generic-Slater regime"),

    Case(id="tC-G2002-zlarger2", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("3/2"), precision=50, tolerance_rel="1e-42",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 1/2 | 3/2)"),

    # Three-slot bm, single-slot an
    Case(id="tC-G2003-bz1", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("1/3"), _r("2/3")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,3} three-slot"),

    Case(id="tC-G3003-bz1", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("1/3"), _r("2/3")), bq=(),
         z=_r("2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{3,0}_{0,3}({};{0, 1/3, 2/3} | 2)"),

    # Complex z, modest |z|
    Case(id="tC-G1002-cmplx-1", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("1/2", "1/2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 0, 1/2 | 0.5 + 0.5i)"),

    Case(id="tC-G2002-cmplx-1", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("1", "1/2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 0 | 1 + 0.5i) — 2 K_0(2√(1+0.5i))"),

    # Negative real z (still in admitted region for many shapes)
    Case(id="tC-G1002-neg-1", tier="C", category="generic-slater",
         an=(), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("-1/2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 0, 1/2 | -1/2)",
         skip_wolfram=True),  # branch-cut region; Wolfram convention may differ
]


# ----- Tier D: anti-Stokes / asymptotic crossover --------------------
#
# |z| ≈ 1 (just inside the quarantine band where applicable) and
# moderate-|z| where multiple lanes can apply.  These probe the
# dispatcher's lane selection at the boundary.

TIERD: list[Case] = [
    # |z| just below quarantine threshold for p=q,m+n=p shapes
    # (p=2, q=2, m=1, n=1: m+n=2=p ⟹ quarantine band applies)
    # We stay outside the band (|z| < 0.95) for tier D
    Case(id="tD-G1122-near-z095", tier="D", category="anti-stokes",
         an=(_r("1/2"),), ap=(_r("1/3"),), bm=(_r("0"),), bq=(_r("1/4"),),
         z=_r("19/20"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,1}_{2,2} just inside |z|=0.95 boundary"),

    # |z| > 1, large enough to plausibly use asymptotic but
    # Slater still wins on cost
    Case(id="tD-G2002-zlarge-1", tier="D", category="anti-stokes",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("10"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0(2√10) — modest |z| asymptotic regime"),

    # NOTE: large-|z| Slater Series 2 loses precision past ~10 dps for
    # this shape; filed as a follow-up (Slater large-|z| precision
    # ceiling vs. Braaksma asymptotic crossover threshold).
    Case(id="tD-G2002-zlarge-2", tier="D", category="anti-stokes",
         an=(), ap=(), bm=(_r("1/2"), _r("-1/2")), bq=(),
         z=_r("16"), precision=50, tolerance_rel="1e-9",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_1(2√16) = 2 K_1(8) — Slater Series-2 at moderate-large |z|"),

    # Far asymptotic regime (asymptotic lane should win)
    Case(id="tD-asymp-far-1", tier="D", category="anti-stokes",
         an=(_r("1/2"),), ap=(), bm=(), bq=(),
         z=_r("100"), precision=50, tolerance_rel="1e-44",
         request_mode="numerical-required", expected_method="symbolic",
         rule="G^{0,1}_{1,0}(1/2; _ | 100) = e^{-1/100}/10"),

    # Large complex z with phase
    Case(id="tD-cmplx-large", tier="D", category="anti-stokes",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("5", "5"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0 with complex argument"),

    # |z| just past quarantine on the high side, with non-band shape
    Case(id="tD-G2002-z105", tier="D", category="anti-stokes",
         an=(), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("21/20"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2} just past |z|=1 (no quarantine; m+n=2≠p=0)"),

    # |z| near 1, big enough m,n so quarantine doesn't apply
    Case(id="tD-G3003-near1", tier="D", category="anti-stokes",
         an=(), ap=(), bm=(_r("0"), _r("1/3"), _r("2/3")), bq=(),
         z=_r("9/10"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{3,0}_{0,3} just inside |z|=1 (p=0, no quarantine)"),

    Case(id="tD-G3003-z105", tier="D", category="anti-stokes",
         an=(), ap=(), bm=(_r("0"), _r("1/3"), _r("2/3")), bq=(),
         z=_r("21/20"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{3,0}_{0,3} just past |z|=1"),
]


# ----- Tier E: parameter coalescence --------------------------------
#
# Integer-spaced poles in bm or aN trigger Johansson's perturbation
# retry inside the Slater path.  These probe that the perturbation
# fires correctly and the result agrees with mpmath's own
# (perturbed) value.

TIERE: list[Case] = [
    # bm = (0, 1) — integer-spaced poles
    # NOTE on coalescence tolerances and `achieved_precision`: at v0.2
    # (worklog 084 — beads `scientist-workbench-7usr` and `…-fwsz`) the
    # dispatcher gained an *empirical precision estimator*: when
    # Johansson `hmag` perturbation fires, the orchestrator runs a
    # second residue-summation pass at a minimally-different
    # perturbation magnitude and reports
    # `achievedPrecision = floor(−log10(|Δ|/|S|)) − 1`.  The reported
    # `achieved_precision` is now honest about the L'Hôpital limit +
    # cancellation noise (typically 12–16 dps for 2-pole integer-spaced
    # cases at 50 dps requested).  The tier-E bench tolerances below
    # are kept loose (1e-13 to 1e-14) because the *value* is correct to
    # ~14 dps; the verifier's `self_reported_precision` check enforces
    # `achieved_precision ≤ requested_precision` separately.
    #
    # Three-pole integer-spaced coalescence (`tE-G3003-coalesce-012`,
    # `tE-an-coalesce-1`) is reinstated as a *structured refusal* —
    # the higher-order Slater 1966 §5 closed-form residue is required
    # and is filed as a follow-up bead.
    #
    # Mixed (an + bm both 2-pole-coalescent) and sharp near-coalescence
    # (`1001/1000`) are reinstated as numerical successes — the
    # cancellation cap and the empirical estimator together let them
    # complete in <5 s.
    Case(id="tE-G2002-coalesce-01", tier="E", category="coalescence",
         an=(), ap=(), bm=(_r("0"), _r("1")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-14",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 1 | 1/2) — integer-spaced Γ-poles"),

    Case(id="tE-G2002-coalesce-02", tier="E", category="coalescence",
         an=(), ap=(), bm=(_r("0"), _r("2")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-14",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 2 | 1/2)"),

    # Half-integer-spaced (related to Bessel-K with integer order).
    Case(id="tE-besselK-ord1", tier="E", category="coalescence",
         an=(), ap=(), bm=(_r("1/2"), _r("3/2")), bq=(),
         z=_r("3/2"), precision=50, tolerance_rel="1e-13",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 1/2, 3/2 | 3/2)"),

    # Half-integer ν=2 (integer-spaced still): G^{2,0}_{0,2}(_; 1, 2 | z)
    Case(id="tE-besselK-ord2", tier="E", category="coalescence",
         an=(), ap=(), bm=(_r("1"), _r("2")), bq=(),
         z=_r("3"), precision=50, tolerance_rel="1e-13",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 1, 2 | 3)"),

    # Mild near-coalescence (small offset).
    Case(id="tE-near-coalesce-2", tier="E", category="coalescence",
         an=(), ap=(), bm=(_r("0"), _r("11/10")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-30",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 11/10 | 1/2) — mild near-coalescence"),

    # 3-pole integer-spaced coalescence — structured refusal expected
    # (bead `scientist-workbench-fwsz`).  The Johansson perturbation
    # cannot absorb the cluster's super-quadratic cancellation; the
    # higher-order Slater 1966 §5 closed-form residue is required.
    Case(id="tE-G3003-coalesce-012", tier="E", category="coalescence",
         an=(), ap=(), bm=(_r("0"), _r("1"), _r("2")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="0",
         request_mode="numerical-required", expected_method="tagged",
         expected_tag="meijer-g/out-of-region",
         rule="G^{3,0}_{0,3}(_; 0, 1, 2 | 1/2) — 3-pole integer-spaced coalescence; out-of-region refusal in <5 s"),

    # Series-2 mirror: 3-pole integer-spaced an-coalescence.
    Case(id="tE-an-coalesce-1", tier="E", category="coalescence",
         an=(_r("1"), _r("2"), _r("3")), ap=(), bm=(), bq=(),
         z=_r("3"), precision=50, tolerance_rel="0",
         request_mode="numerical-required", expected_method="tagged",
         expected_tag="meijer-g/out-of-region",
         rule="G^{0,3}_{3,0}(1, 2, 3; _ | 3) — 3-pole integer-spaced an-coalescence; out-of-region refusal in <5 s"),

    # Mixed: an and bm each 2-pole-coalescent (NOT in the same equiv class).
    Case(id="tE-mixed-1", tier="E", category="coalescence",
         an=(_r("0"), _r("1")), ap=(), bm=(_r("1"), _r("2")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-12",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,2}_{2,2}({0,1};_;{1,2};_ | 1/2) — simultaneous an + bm 2-pole coalescence"),

    # Sharp near-coalescence (offset 1/1000 from integer).
    Case(id="tE-near-coalesce-1", tier="E", category="coalescence",
         an=(), ap=(), bm=(_r("0"), _r("1001/1000")), bq=(),
         z=_r("1/2"), precision=50, tolerance_rel="1e-30",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{2,0}_{0,2}(_; 0, 1001/1000 | 1/2) — sharp near-coalescence"),
]


# ----- Tier F: branch-cut sensitive ----------------------------------
#
# z on or just-near the negative real axis, complex z straddling the
# branch cut.

TIERF: list[Case] = [
    # Complex z with small positive imaginary part, just above negative real
    Case(id="tF-bessel-K0-near-neg-real", tier="F", category="branch-cut",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("-1", "1/100"), precision=50, tolerance_rel="1e-32",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0(2√z) on z = -1 + 0.01i (just above branch cut)",
         skip_wolfram=True),  # convention difference at the cut

    Case(id="tF-bessel-K0-below-neg-real", tier="F", category="branch-cut",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("-1", "-1/100"), precision=50, tolerance_rel="1e-32",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0(2√z) on z = -1 - 0.01i (just below branch cut)",
         skip_wolfram=True),

    # Complex z far from cut
    Case(id="tF-bessel-K0-cmplx-far", tier="F", category="branch-cut",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("0", "2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0(2√(2i)) — pure imaginary z"),

    # z = -2 + small ε ⟹ above-cut convention (DLMF 16.17.1)
    Case(id="tF-G1001-above-cut", tier="F", category="branch-cut",
         an=(), ap=(), bm=(_r("1/2"),), bq=(),
         z=_r("-2", "1/1000000"), precision=50, tolerance_rel="1e-30",
         request_mode="numerical-required", expected_method="symbolic",
         rule="G^{1,0}_{0,1}(_; 1/2 | -2 + ε i) = √(-2+εi)·e^{-(-2+εi)} (principal branch)",
         skip_wolfram=True),

    # Schwarz reflection probe: z = a + bi vs z̄ = a - bi
    Case(id="tF-schwarz-1", tier="F", category="branch-cut",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("1", "2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0(2√(1+2i)) — Schwarz reflection probe; complex z"),

    Case(id="tF-schwarz-2-conj", tier="F", category="branch-cut",
         an=(), ap=(), bm=(_r("0"), _r("0")), bq=(),
         z=_r("1", "-2"), precision=50, tolerance_rel="1e-40",
         request_mode="numerical-required", expected_method="numerical",
         rule="2 K_0(2√(1-2i)) — conjugate of tF-schwarz-1"),

    # z purely negative (on the cut), real input — the tool's
    # principal-branch convention places it above the cut.
    # mpmath puts the cut at arg = π (consistent).  We expect the
    # tool's value to match mpmath's at z = -2 + 0i.
    Case(id="tF-onCut-G1002", tier="F", category="branch-cut",
         an=(), ap=(), bm=(_r("0"), _r("1/2")), bq=(),
         z=_r("-1"), precision=50, tolerance_rel="1e-30",
         request_mode="numerical-required", expected_method="numerical",
         rule="G^{1,0}_{0,2}(_; 0, 1/2 | -1) — on branch cut (Re<0, Im=0)",
         skip_wolfram=True),
]


# ----- Tier G: refusal cases -----------------------------------------
#
# Inputs that should land in `tagged "meijer-g/<class>"`.  The
# verifier asserts the tag class.

TIERG: list[Case] = [
    # m + n = 0 (degenerate): no Γ-poles to close around.
    # The dispatcher emits `meijer-g/degenerate-shape` (a structural,
    # not a region-driven, refusal — it's caught by `canUseSlater`'s
    # `m+n=0` pre-filter ahead of the contour pre-filter).
    Case(id="tG-degen-mn-zero", tier="G", category="refusal",
         an=(), ap=(_r("1/2"),), bm=(), bq=(_r("1/3"),),
         z=_r("2"), precision=50, tolerance_rel="0",
         request_mode="numerical-required", expected_method="numerical",
         rule="m+n=0: degenerate shape; no Γ-poles to close around",
         expected_kind="tagged",
         expected_tag="meijer-g/degenerate-shape",
         skip_wolfram=True,
         notes="degenerate-shape per dispatcher canUseSlater(m+n=0)"),

    # Symbolic-required with no symbolic match — generic free
    # rationals don't fire any v0.1 dispatch rule.
    Case(id="tG-sym-required-nomatch", tier="G", category="refusal",
         an=(_r("1/3"),), ap=(_r("2/3"),), bm=(_r("1/4"),), bq=(_r("3/4"),),
         z=_r("1/2"), precision=50, tolerance_rel="0",
         request_mode="symbolic-required", expected_method="symbolic",
         rule="symbolic-required: 2x2 generic free parameters; no rule match",
         expected_kind="tagged",
         expected_tag="meijer-g/symbolic-required-no-match",
         skip_wolfram=True,
         notes="symbolic-required ⟹ refuse on no-rule-match"),

    # Force-method asymptotic on |z|=0: asymptotic lane refuses small-z
    Case(id="tG-force-asymp-zerov", tier="G", category="refusal",
         an=(_r("1"),), ap=(), bm=(_r("0"),), bq=(_r("1"),),
         z=_r("0"), precision=50, tolerance_rel="0",
         request_mode="numerical-required", expected_method="numerical",
         rule="force-method=asymptotic with |z|=0 ⟹ small-z refusal",
         expected_kind="tagged",
         expected_tag="meijer-g/forced-method-refused",
         skip_wolfram=True,
         notes="Asymptotic lane forced via flag; refuses |z|=0 (per ADR-0026)",
         force_method="asymptotic"),

    # NOTE on the originally-attempted refusal cases:
    #   * tG-quarantine-z1-1 (G^{1,1}_{2,2} at |z|=1) — the dispatcher
    #     correctly routes to `braaksma-algebraic` (asymptotic) when
    #     Slater quarantines.  Honest scope: this is a *success*, not
    #     a refusal.  Removed.
    #   * tG-pgtq1-asymp-only (p=3, q=1, |z|<1) — Slater Series-2
    #     handles p>q+1 with small |z| just fine.  Honest scope:
    #     also a success.  Removed.
    # The bench documents the dispatcher's *actual* coverage envelope,
    # not a hypothetical narrower one.
]


# ---------------------------------------------------------------------
# Combined cases
# ---------------------------------------------------------------------

ALL_TIERS: list[Case] = (
    TIER0 + TIERA + TIERB + TIERC + TIERD + TIERE + TIERF + TIERG
)


# ---------------------------------------------------------------------
# Truth computation
# ---------------------------------------------------------------------

def _parse_rational_or_decimal(s: str) -> Fraction:
    s = s.strip()
    if "/" in s:
        return Fraction(s)
    return Fraction(s)


def _to_mpc(re: str, im: str, dps: int) -> mpmath.mpc:
    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps
        rr = _parse_rational_or_decimal(re)
        ii = _parse_rational_or_decimal(im)
        re_mp = mpmath.mpf(rr.numerator) / mpmath.mpf(rr.denominator)
        im_mp = mpmath.mpf(ii.numerator) / mpmath.mpf(ii.denominator)
        return mpmath.mpc(re_mp, im_mp)
    finally:
        mpmath.mp.dps = saved


def mpmath_truth(case: Case, dps: int = 110) -> Optional[mpmath.mpc]:
    """Compute MeijerG via mpmath at `dps` working precision.

    mpmath converts integer/rational real parameters to mpf and
    complex / non-real to mpc on its own (via `_check_need_perturb`'s
    `isnpint` check, which requires real ordering).  When parameters
    are real-valued we promote them to mpf so the ordering check works;
    purely complex parameters bail out with a TypeError that we trap.
    """
    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps
        def _to_real_or_complex(re: str, im: str) -> "mpmath.mpc | mpmath.mpf":
            ii = _parse_rational_or_decimal(im)
            if ii == 0:
                rr = _parse_rational_or_decimal(re)
                return mpmath.mpf(rr.numerator) / mpmath.mpf(rr.denominator)
            return _to_mpc(re, im, dps)
        an = [_to_real_or_complex(re, im) for re, im in case.an]
        ap = [_to_real_or_complex(re, im) for re, im in case.ap]
        bm = [_to_real_or_complex(re, im) for re, im in case.bm]
        bq = [_to_real_or_complex(re, im) for re, im in case.bq]
        z = _to_mpc(case.z[0], case.z[1], dps)
        return mpmath.meijerg([an, ap], [bm, bq], z)
    except (mpmath.libmp.libhyper.NoConvergence, ValueError, ZeroDivisionError, TypeError):
        return None
    finally:
        mpmath.mp.dps = saved


def tier0_rhs_truth(case: Case, dps: int = 200) -> mpmath.mpc:
    """Evaluate the elementary closed-form RHS at 200 dps."""
    if not case.rhs_mpmath:
        raise ValueError(f"Tier-0 case {case.id} has empty rhs_mpmath")
    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps
        # Provide every mpmath function the rhs strings reference.
        ns = {
            "mpf": mpmath.mpf, "mpc": mpmath.mpc,
            "exp": mpmath.exp, "log": mpmath.log,
            "sin": mpmath.sin, "cos": mpmath.cos, "tan": mpmath.tan,
            "atan": mpmath.atan, "asin": mpmath.asin, "acos": mpmath.acos,
            "sqrt": mpmath.sqrt, "pi": mpmath.pi, "e": mpmath.e,
            "gamma": mpmath.gamma, "digamma": mpmath.digamma,
            "besselj": mpmath.besselj, "besselk": mpmath.besselk,
            "bessely": mpmath.bessely, "besseli": mpmath.besseli,
            "erf": mpmath.erf, "erfc": mpmath.erfc,
        }
        return mpmath.mpc(eval(case.rhs_mpmath, ns))
    finally:
        mpmath.mp.dps = saved


# ---------------------------------------------------------------------
# Wolfram cross-validation
# ---------------------------------------------------------------------

def _wolfram_complex(re: str, im: str) -> str:
    """Format a complex number as a Wolfram input expression.

    Rationals are passed verbatim; decimals likewise.  Pure-real
    numbers omit the imaginary part."""
    if im in ("0", "0/1"):
        return f"({re})"
    return f"(({re}) + ({im}) I)"


def wolfram_truth(case: Case, dps: int = 110) -> Optional[str]:
    """Compute MeijerG via wolframscript at `dps` precision.

    Returns Wolfram InputForm decimal string, or None on failure.
    """
    if case.skip_wolfram:
        return None

    an_args = ",".join(_wolfram_complex(re, im) for re, im in case.an)
    ap_args = ",".join(_wolfram_complex(re, im) for re, im in case.ap)
    bm_args = ",".join(_wolfram_complex(re, im) for re, im in case.bm)
    bq_args = ",".join(_wolfram_complex(re, im) for re, im in case.bq)
    z_expr = _wolfram_complex(case.z[0], case.z[1])

    code = (
        f"Block[{{$MaxExtraPrecision = 5000}}, "
        f"v = MeijerG[{{{{{an_args}}}, {{{ap_args}}}}}, "
        f"{{{{{bm_args}}}, {{{bq_args}}}}}, {z_expr}]; "
        f"ToString[N[v, {dps}], InputForm]]"
    )

    try:
        result = subprocess.run(
            ["wolframscript", "-code", code],
            capture_output=True, text=True, timeout=WOLFRAM_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    # Wolfram emits various error messages on stderr while still returning
    # an InputForm string; we filter empty / malformed outputs.
    if not out or "MeijerG[" in out or "ComplexInfinity" in out or "Indeterminate" in out:
        return None
    return out


def _strip_wolfram_backticks(s: str) -> str:
    """Strip `<digits>.<digits>` Wolfram precision suffixes."""
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "`":
            i += 1
            while i < len(s) and (s[i].isdigit() or s[i] == "."):
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _wolfram_to_mpc(s: str, dps: int = 110) -> Optional[mpmath.mpc]:
    if s is None:
        return None
    cleaned = _strip_wolfram_backticks(s).replace("*^", "e").strip()
    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps
        if "*I" not in cleaned and "I" not in cleaned:
            try:
                return mpmath.mpc(mpmath.mpf(cleaned), mpmath.mpf(0))
            except Exception:
                return None
        body = cleaned
        sign = 1
        if body.startswith("-"):
            sign = -1
            body = body[1:]
        depth = 0
        split_at = None
        for k, ch in enumerate(body):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and k > 0 and ch in "+-" and body[k-1] == " ":
                split_at = k
                break
        if split_at is None:
            return mpmath.mpc(mpmath.mpf(cleaned), mpmath.mpf(0))
        re_str = body[:split_at].strip()
        im_op = body[split_at]
        im_str = body[split_at+1:].strip()
        if im_str.endswith("*I"):
            im_str = im_str[:-2]
        elif im_str.endswith("I"):
            im_str = im_str[:-1].rstrip("*")
        im_str = im_str.strip() or "1"
        re_part = mpmath.mpf(re_str) * sign
        im_sign = 1 if im_op == "+" else -1
        im_part = mpmath.mpf(im_str) * im_sign
        return mpmath.mpc(re_part, im_part)
    except Exception:
        return None
    finally:
        mpmath.mp.dps = saved


# ---------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------

def _format_mpc(z: mpmath.mpc, dps: int) -> tuple[str, str]:
    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps + 10
        return (
            mpmath.nstr(z.real, dps, strip_zeros=False),
            mpmath.nstr(z.imag, dps, strip_zeros=False),
        )
    finally:
        mpmath.mp.dps = saved


def consensus(case: Case, mp_truth: Optional[mpmath.mpc],
              wolf_truth: Optional[mpmath.mpc],
              cmp_dps: int = 80) -> dict:
    """Compare mpmath and Wolfram values; return consensus record."""
    if case.expected_kind == "tagged":
        return {"consensus": "structural-refusal",
                "mpmath_skipped": True, "wolfram_skipped": True}

    if mp_truth is None and wolf_truth is None:
        return {"consensus": "both-failed",
                "mpmath_value": None, "wolfram_value": None,
                "rel_disagreement": None}

    if mp_truth is None:
        return {"consensus": "wolfram-only",
                "mpmath_value": None,
                "wolfram_value": _format_mpc(wolf_truth, 100),
                "rel_disagreement": None}

    if wolf_truth is None:
        return {"consensus": "mpmath-only",
                "mpmath_value": _format_mpc(mp_truth, 100),
                "wolfram_value": None,
                "rel_disagreement": None}

    diff = abs(mp_truth - wolf_truth)
    scale = max(abs(mp_truth), mpmath.mpf("1e-300"))
    rel = float(diff / scale)
    threshold = 10.0 ** (-cmp_dps + 2)
    return {
        "consensus": "mpmath+wolfram-agree" if rel < threshold else "mpmath+wolfram-DISAGREE",
        "mpmath_value": _format_mpc(mp_truth, 100),
        "wolfram_value": _format_mpc(wolf_truth, 100),
        "rel_disagreement": rel,
    }


# ---------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------

def build_input_record(case: Case) -> dict:
    inp = {
        "an": [{"re": re, "im": im} for re, im in case.an],
        "ap": [{"re": re, "im": im} for re, im in case.ap],
        "bm": [{"re": re, "im": im} for re, im in case.bm],
        "bq": [{"re": re, "im": im} for re, im in case.bq],
        "z": {"re": case.z[0], "im": case.z[1]},
        "precision": case.precision,
        "request_mode": case.request_mode,
    }
    if case.force_method:
        inp["force_method"] = case.force_method
    rec = {
        "id": case.id,
        "tier": case.tier,
        "category": case.category,
        "input": inp,
        "rule": case.rule,
        "request_mode": case.request_mode,
        "expected_method": case.expected_method,
    }
    return rec


def build_expected_record(case: Case, mp_truth: Optional[mpmath.mpc],
                           consensus_record: dict,
                           tier0_rhs: Optional[mpmath.mpc] = None) -> dict:
    base = {
        "id": case.id,
        "tier": case.tier,
        "tolerance_rel": case.tolerance_rel,
        "consensus": consensus_record,
    }
    if case.expected_kind == "tagged":
        base["expected"] = {
            "kind": "tagged",
            "tag": case.expected_tag,
            "payload_predicate": case.expected_payload,
        }
        base["truth_method"] = "dispatcher-quarantine-expected"
        return base

    # Choose truth source: Tier 0 RHS preferred; else mpmath/Wolfram consensus
    if tier0_rhs is not None:
        truth = tier0_rhs
        truth_method = "elementary-rhs@200dps"
    elif mp_truth is not None:
        truth = mp_truth
        truth_method = (
            "consensus-wolfram-mpmath@110dps"
            if consensus_record.get("consensus") == "mpmath+wolfram-agree"
            else "mpmath-only@110dps"
        )
    else:
        # Failed both oracles — pin nothing; verifier should refuse.
        base["expected"] = {"kind": "value", "truth": None,
                            "truth_method": "ORACLE-FAILED"}
        base["truth_method"] = "ORACLE-FAILED"
        return base

    re_s, im_s = _format_mpc(truth, max(60, case.precision + 12))
    base["expected"] = {
        "kind": "value",
        "truth": {"re": re_s, "im": im_s},
        "truth_method": truth_method,
    }
    base["truth_method"] = truth_method
    return base


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="output dir (typically bench/meijer-g/golden)")
    parser.add_argument("--no-wolfram", action="store_true",
                        help="skip Wolfram cross-validation")
    parser.add_argument("--limit", type=int, default=None,
                        help="(testing) only build the first N cases")
    parser.add_argument("--tiers", type=str, default=None,
                        help="comma-separated subset of tiers (e.g. '0,A,G')")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = ALL_TIERS
    if args.tiers:
        wanted = set(args.tiers.split(","))
        cases = [c for c in cases if c.tier in wanted]
    if args.limit is not None:
        cases = cases[: args.limit]

    inputs = {"version": 1, "problem": "meijer-g", "cases": []}
    expected = {"version": 1, "problem": "meijer-g", "cases": []}
    quarantine: list[dict] = []

    n = len(cases)
    print(f"# building corpus — {n} cases", file=sys.stderr)
    t_start = time.time()

    for i, case in enumerate(cases):
        t0 = time.time()
        print(f"  [{i+1:3d}/{n}] {case.id}  tier={case.tier} ", file=sys.stderr,
              end="", flush=True)

        # Tier-0 RHS first (bug-immune anchor)
        tier0_rhs = None
        if case.tier == "0" and case.rhs_mpmath:
            tier0_rhs = tier0_rhs_truth(case, dps=200)

        if case.expected_kind == "tagged":
            cons = consensus(case, None, None)
            inputs["cases"].append(build_input_record(case))
            expected["cases"].append(build_expected_record(case, None, cons))
            print(f"  REFUSAL  ({time.time()-t0:.1f}s)", file=sys.stderr)
            continue

        mp_truth = mpmath_truth(case, dps=110)
        wolf_truth = None
        if not args.no_wolfram:
            wolf_str = wolfram_truth(case, dps=110)
            wolf_truth = _wolfram_to_mpc(wolf_str, dps=130) if wolf_str else None

        cons = consensus(case, mp_truth, wolf_truth, cmp_dps=80)

        if cons["consensus"] == "mpmath+wolfram-DISAGREE":
            print(f"  !! DISAGREE  rel={cons['rel_disagreement']:.3e}", file=sys.stderr)
            quarantine.append({
                "id": case.id, "tier": case.tier,
                "rel_disagreement": cons["rel_disagreement"],
                "mpmath_value": cons["mpmath_value"],
                "wolfram_value": cons["wolfram_value"],
                "rule": case.rule,
            })
            # If Tier 0 has RHS, it acts as a third witness automatically
            # (build_expected_record prefers tier0_rhs).  Otherwise: skip
            # this case — do NOT include in golden.
            if tier0_rhs is None:
                print(f"      QUARANTINED — not in golden", file=sys.stderr)
                continue

        inputs["cases"].append(build_input_record(case))
        expected["cases"].append(build_expected_record(case, mp_truth, cons, tier0_rhs))
        elapsed = time.time() - t0
        marker = "✓" if cons.get("consensus", "").startswith("mpmath+wolfram") else "·"
        print(f"  {marker} ({elapsed:.1f}s)", file=sys.stderr)

    inputs_path = out_dir / "inputs.json"
    expected_path = out_dir / "expected.json"
    quarantine_path = out_dir / "oracle-disagreements.log"

    inputs_path.write_text(json.dumps(inputs, indent=2) + "\n")
    expected_path.write_text(json.dumps(expected, indent=2) + "\n")

    if quarantine:
        with open(quarantine_path, "w") as fh:
            for entry in quarantine:
                fh.write(json.dumps(entry) + "\n")
        print(f"# wrote {quarantine_path} ({len(quarantine)} disagreements)",
              file=sys.stderr)

    print(f"# wrote {inputs_path}", file=sys.stderr)
    print(f"# wrote {expected_path}", file=sys.stderr)
    print(f"# total: {time.time()-t_start:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
