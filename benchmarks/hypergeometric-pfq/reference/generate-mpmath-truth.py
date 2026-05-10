#!/usr/bin/env python3
"""Generate `golden/inputs.json` and `golden/expected.json` for the
`hypergeometric-pfq` bench by computing each case's pFq value at high
precision via mpmath, with optional Wolfram cross-validation.

This script is the single source of truth for the corpus. Re-running
it must produce byte-identical inputs.json and expected.json (no RNG;
every input is hand-curated). Per ADR-0019 §6 generators are
seeded/deterministic.

Pipeline per case:

    a, b, z, target_dps        ⟶  parameters (rationals where possible)
    mpmath.hyper(a, b, z)       ⟶  primary 60-dps truth value
    wolframscript Hypergeometric  ⟶  cross-witness (when available)
    consensus(mp, wolf, tol)    ⟶  pinned truth + consensus-tag
    case row                    ⟶  inputs.json (id + tier + a/b/z/precision)
                                 ⟶  expected.json (id + truth + tolerance)

Number-bearing fields are STRINGS (PRD §0.1). The bench wire format is
raw JSON-with-string-decimals; the run-candidate adapter parses each
into a `BigComplex` at the requested working precision.

Tier structure (per the bead spec hv0.4):

  Tier 0  closed-form anchors        — every case reduces to an
                                       elementary function (exp / log /
                                       sin / cos / 1/(1−z)^a)
  Tier A  generic happy path         — mid-range parameters, |z| ≤ 0.5
  Tier B  large parameters           — a, b ∈ [10, 100]; cancellation
                                       in the direct power series
  Tier C  near-unit-circle           — |z| ∈ [0.85, 0.95], slow conv
  Tier D  parameter coalescence      — integer-spaced parameters; the
                                       tool's bumped-precision retry
                                       must fire and succeed
  Tier E  refusal cases              — known-refused inputs; verifier
                                       asserts the structured envelope

Run as:

    python3 bench/hypergeometric-pfq/reference/generate-mpmath-truth.py \
        --output bench/hypergeometric-pfq/golden \
        [--no-wolfram]

The `--no-wolfram` flag skips Wolfram cross-validation (mpmath only).
The default attempts Wolfram first; missing/timed-out Wolfram is
recorded with consensus="mpmath-only" and the run continues.

References
----------
* mpmath documentation, `mpmath.hyper(a, b, z)`:
  https://mpmath.org/doc/current/functions/hypergeometric.html
* Pearson, Olver, Porter (2017): "Numerical methods for the computation
  of the confluent and Gauss hypergeometric functions". Numerical
  Algorithms 74, 821-866. The taxonomy underlying the tier structure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Optional

# mpmath is the primary high-precision oracle. We pin a working
# precision of 80 decimal digits so that 60-dps truth values are
# accurate to relative `1e-60`-or-better, leaving margin against
# cancellation in mpmath's own evaluator.
import mpmath
mpmath.mp.dps = 80

WOLFRAM_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    """One bench case.

    Parameters `a` and `b` are tuples of (re, im) string pairs that mpmath
    parses verbatim. `z` is a single (re, im) pair. `truth_method` is
    a label that documents how the reference value was derived; for
    Tier 0 cases we cross-check against the elementary closed form
    too, recorded in `truth_xref_method`.
    """

    id: str
    tier: str
    category: str
    a: tuple[tuple[str, str], ...]
    b: tuple[tuple[str, str], ...]
    z: tuple[str, str]
    precision: int                              # dps the *tool* runs at
    tolerance_rel: str                          # decimal-string tolerance
    rule: str = ""                              # human-readable identity
    truth_xref_method: str = ""                 # secondary witness label
    expected_kind: str = "value"                # "value" or "tagged"
    expected_tag: str = ""                      # for refusal cases
    expected_payload: dict = field(default_factory=dict)
    skip_wolfram: bool = False                  # skip Wolfram cross-check
    notes: str = ""


def _r(re: str, im: str = "0") -> tuple[str, str]:
    return (re, im)


# ----- Tier 0: closed-form anchors -----------------------------------

TIER0: list[Case] = [
    # 0F0(;;z) = exp(z)
    Case(
        id="t0-0F0-exp-z1",
        tier="0",
        category="closed-form",
        a=(),
        b=(),
        z=_r("1"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F0(;;z) = exp(z)  →  exp(1)",
        truth_xref_method="bigfloat::exp(1) at 60 dps",
    ),
    Case(
        id="t0-0F0-exp-z5",
        tier="0",
        category="closed-form",
        a=(),
        b=(),
        z=_r("5"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F0(;;z) = exp(z)  →  exp(5)",
        truth_xref_method="bigfloat::exp(5) at 60 dps",
    ),
    Case(
        id="t0-0F0-exp-zneg5",
        tier="0",
        category="closed-form",
        a=(),
        b=(),
        z=_r("-5"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F0(;;z) = exp(z)  →  exp(-5)",
        truth_xref_method="bigfloat::exp(-5) at 60 dps",
    ),
    # 1F0(a;;z) = (1-z)^{-a}
    Case(
        id="t0-1F0-half-z-third",
        tier="0",
        category="closed-form",
        a=(_r("1/2"),),
        b=(),
        z=_r("1/3"),
        precision=50,
        tolerance_rel="1e-48",
        rule="1F0(a;;z) = (1-z)^{-a}  →  (2/3)^{-1/2} = √(3/2)",
        truth_xref_method="bigfloat::sqrt(3/2)",
    ),
    Case(
        id="t0-1F0-2-z-half",
        tier="0",
        category="closed-form",
        a=(_r("2"),),
        b=(),
        z=_r("1/2"),
        precision=50,
        tolerance_rel="1e-48",
        rule="1F0(2;;1/2) = (1/2)^{-2} = 4",
        truth_xref_method="exact arithmetic",
    ),
    # 2F1(1, 1; 2; z) = -log(1-z)/z
    Case(
        id="t0-2F1-log-z-third",
        tier="0",
        category="closed-form",
        a=(_r("1"), _r("1")),
        b=(_r("2"),),
        z=_r("1/3"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1(1,1;2;z) = -log(1-z)/z  →  -3 log(2/3)",
        truth_xref_method="bigfloat::log(2/3)",
    ),
    Case(
        id="t0-2F1-log-zneg-half",
        tier="0",
        category="closed-form",
        a=(_r("1"), _r("1")),
        b=(_r("2"),),
        z=_r("-1/2"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1(1,1;2;-1/2) = -log(3/2)/(-1/2) = 2 log(3/2)",
        truth_xref_method="bigfloat::2*log(3/2)",
    ),
    # 1F1(1; 2; z) = (e^z - 1)/z
    Case(
        id="t0-1F1-expminus1-z1",
        tier="0",
        category="closed-form",
        a=(_r("1"),),
        b=(_r("2"),),
        z=_r("1"),
        precision=50,
        tolerance_rel="1e-48",
        rule="1F1(1; 2; z) = (e^z − 1)/z  →  e − 1",
        truth_xref_method="bigfloat::exp(1) − 1",
    ),
    Case(
        id="t0-1F1-expminus1-z5",
        tier="0",
        category="closed-form",
        a=(_r("1"),),
        b=(_r("2"),),
        z=_r("5"),
        precision=50,
        tolerance_rel="1e-48",
        rule="1F1(1; 2; z) = (e^z − 1)/z  →  (e^5 − 1)/5",
        truth_xref_method="bigfloat::(exp(5) − 1)/5",
    ),
    # 0F1(; 1/2; -z²/4) = cos(z) — pick z=1
    Case(
        id="t0-0F1-cos-z1",
        tier="0",
        category="closed-form",
        a=(),
        b=(_r("1/2"),),
        z=_r("-1/4"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F1(; 1/2; −z²/4) = cos(z)  →  cos(1)",
        truth_xref_method="bigfloat::cos(1)",
    ),
    # 0F1(; 3/2; -z²/4) = sin(z)/z — pick z=1 ⟹ argument −1/4 again, but b=3/2
    Case(
        id="t0-0F1-sinc-z1",
        tier="0",
        category="closed-form",
        a=(),
        b=(_r("3/2"),),
        z=_r("-1/4"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F1(; 3/2; −z²/4) = sin(z)/z  →  sin(1)",
        truth_xref_method="bigfloat::sin(1)",
    ),
]


# ----- Tier A: generic happy path ------------------------------------

TIERA: list[Case] = [
    Case(
        id="tA-2F1-mid-real",
        tier="A",
        category="happy-path",
        a=(_r("1/2"), _r("1/3")),
        b=(_r("1"),),
        z=_r("3/10"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1(1/2, 1/3; 1; 0.3) — Pearson-Olver-Porter happy-path probe",
    ),
    Case(
        id="tA-2F1-mid-real-neg",
        tier="A",
        category="happy-path",
        a=(_r("1/2"), _r("3/2")),
        b=(_r("2"),),
        z=_r("-2/5"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1(1/2, 3/2; 2; -0.4) — moderate-magnitude negative real z",
    ),
    Case(
        id="tA-2F1-mid-complex",
        tier="A",
        category="happy-path",
        a=(_r("1"), _r("2")),
        b=(_r("3"),),
        z=_r("3/10", "2/5"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1(1, 2; 3; 0.3 + 0.4i) — complex z away from singularity",
    ),
    Case(
        id="tA-1F1-mid-real",
        tier="A",
        category="happy-path",
        a=(_r("3/2"),),
        b=(_r("5/2"),),
        z=_r("1"),
        precision=50,
        tolerance_rel="1e-48",
        rule="1F1(3/2; 5/2; 1) — classic Kummer at z=1, well inside the convergent region",
    ),
    Case(
        id="tA-1F1-mid-complex",
        tier="A",
        category="happy-path",
        a=(_r("1/2"),),
        b=(_r("3/2"),),
        z=_r("1/2", "1/2"),
        precision=50,
        tolerance_rel="1e-48",
        rule="1F1(1/2; 3/2; 0.5 + 0.5i) — complex argument, related to erf",
    ),
    Case(
        id="tA-0F1-mid-real",
        tier="A",
        category="happy-path",
        a=(),
        b=(_r("3"),),
        z=_r("2"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F1(;3;2) — Bessel-related, infinite radius of convergence",
    ),
    Case(
        id="tA-0F1-mid-real-neg",
        tier="A",
        category="happy-path",
        a=(),
        b=(_r("2"),),
        z=_r("-3"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F1(;2;-3) — Bessel-J shape, oscillatory negative argument",
    ),
    Case(
        id="tA-2F1-half-real",
        tier="A",
        category="happy-path",
        a=(_r("1/2"), _r("1/2")),
        b=(_r("1"),),
        z=_r("1/2"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1(1/2, 1/2; 1; 1/2) = 4 K(1/√2)/π (complete elliptic K)",
    ),
    Case(
        id="tA-3F2-mid",
        tier="A",
        category="happy-path",
        a=(_r("1/2"), _r("1"), _r("3/2")),
        b=(_r("2"), _r("5/2")),
        z=_r("1/4"),
        precision=50,
        tolerance_rel="1e-48",
        rule="3F2(1/2, 1, 3/2; 2, 5/2; 1/4) — cancellation-light, well inside |z|=1",
    ),
    Case(
        id="tA-3F2-mid-real-neg",
        tier="A",
        category="happy-path",
        a=(_r("1"), _r("2"), _r("3")),
        b=(_r("4"), _r("5")),
        z=_r("-1/3"),
        precision=50,
        tolerance_rel="1e-48",
        rule="3F2 with negative real z — standard probe",
    ),
    Case(
        id="tA-1F1-fresnel-like",
        tier="A",
        category="happy-path",
        a=(_r("1/4"),),
        b=(_r("5/4"),),
        z=_r("0", "1"),
        precision=50,
        tolerance_rel="1e-48",
        rule="1F1(1/4; 5/4; i) — Fresnel-shape, pure-imaginary z",
    ),
    Case(
        id="tA-2F1-low-z",
        tier="A",
        category="happy-path",
        a=(_r("2"), _r("3")),
        b=(_r("4"),),
        z=_r("1/10"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1 with |z|=0.1 — dead-centre happy path",
    ),
    Case(
        id="tA-0F1-imag",
        tier="A",
        category="happy-path",
        a=(),
        b=(_r("1"),),
        z=_r("0", "1"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F1(;1;i) — Bessel I_0(2√i) shape; oscillatory in complex plane",
    ),
    Case(
        id="tA-2F1-real-3-4",
        tier="A",
        category="happy-path",
        a=(_r("3"), _r("4")),
        b=(_r("5"),),
        z=_r("1/4"),
        precision=50,
        tolerance_rel="1e-48",
        rule="2F1(3, 4; 5; 1/4) — integer parameters, mid-range |z|",
    ),
    Case(
        id="tA-pfq-precision100",
        tier="A",
        category="happy-path",
        a=(_r("1/2"),),
        b=(_r("3/2"),),
        z=_r("1/3"),
        precision=100,
        tolerance_rel="1e-98",
        rule="1F1(1/2; 3/2; 1/3) at 100 dps — precision-flag exercise",
    ),
]


# ----- Tier B: large parameters --------------------------------------

TIERB: list[Case] = [
    Case(
        id="tB-2F1-large-int-a",
        tier="B",
        category="large-params",
        a=(_r("20"), _r("30")),
        b=(_r("40"),),
        z=_r("1/4"),
        precision=50,
        tolerance_rel="1e-46",
        rule="2F1(20, 30; 40; 0.25) — tens of cancellation bits",
    ),
    Case(
        id="tB-2F1-large-half-int",
        tier="B",
        category="large-params",
        a=(_r("21/2"), _r("33/2")),
        b=(_r("17"),),
        z=_r("3/10"),
        precision=50,
        tolerance_rel="1e-46",
        rule="2F1(10.5, 16.5; 17; 0.3) — half-integer large params",
    ),
    Case(
        id="tB-1F1-large-a",
        tier="B",
        category="large-params",
        a=(_r("50"),),
        b=(_r("75"),),
        z=_r("2"),
        precision=50,
        tolerance_rel="1e-46",
        rule="1F1(50; 75; 2) — confluent with mid-large parameters",
    ),
    Case(
        id="tB-1F1-large-50-100",
        tier="B",
        category="large-params",
        a=(_r("50"),),
        b=(_r("100"),),
        z=_r("5"),
        precision=50,
        tolerance_rel="1e-46",
        rule="1F1(50; 100; 5) — Pochhammer ratios growing then shrinking",
    ),
    Case(
        id="tB-2F1-100-50",
        tier="B",
        category="large-params",
        a=(_r("10"), _r("100")),
        b=(_r("50"),),
        z=_r("1/3"),
        precision=50,
        tolerance_rel="1e-44",
        rule="2F1(10, 100; 50; 1/3) — large b/large-a difference probe",
    ),
    Case(
        id="tB-3F2-large",
        tier="B",
        category="large-params",
        a=(_r("10"), _r("20"), _r("30")),
        b=(_r("40"), _r("50")),
        z=_r("1/5"),
        precision=50,
        tolerance_rel="1e-46",
        rule="3F2 with all params in [10, 50]",
    ),
    Case(
        id="tB-2F1-largeneg-z",
        tier="B",
        category="large-params",
        a=(_r("20"), _r("30")),
        b=(_r("25"),),
        z=_r("-1/2"),
        precision=50,
        tolerance_rel="1e-44",
        rule="2F1(20, 30; 25; -0.5) — alternating-sign cancellation hot spot",
    ),
    Case(
        id="tB-0F1-large-b",
        tier="B",
        category="large-params",
        a=(),
        b=(_r("50"),),
        z=_r("10"),
        precision=50,
        tolerance_rel="1e-48",
        rule="0F1(;50;10) — large-b Bessel regime",
    ),
    Case(
        id="tB-1F1-large-z",
        tier="B",
        category="large-params",
        a=(_r("3"),),
        b=(_r("4"),),
        z=_r("20"),
        precision=50,
        tolerance_rel="1e-44",
        rule="1F1(3; 4; 20) — moderate params, large |z| forces many series terms",
    ),
    Case(
        id="tB-2F1-large-near-int-coalesce",
        tier="B",
        category="large-params",
        a=(_r("21/2"), _r("23/2")),
        b=(_r("12"),),
        z=_r("1/4"),
        precision=50,
        tolerance_rel="1e-46",
        rule="2F1 large half-integer params probe",
    ),
]


# ----- Tier C: near-unit-circle --------------------------------------

TIERC: list[Case] = [
    Case(
        id="tC-2F1-z-085",
        tier="C",
        category="near-unit",
        a=(_r("1/2"), _r("1/2")),
        b=(_r("1"),),
        z=_r("17/20"),
        precision=50,
        tolerance_rel="1e-46",
        rule="2F1(1/2, 1/2; 1; 0.85) — slow-convergence regime",
    ),
    Case(
        id="tC-2F1-z-090",
        tier="C",
        category="near-unit",
        a=(_r("1"), _r("1")),
        b=(_r("3"),),
        z=_r("9/10"),
        precision=50,
        tolerance_rel="1e-44",
        rule="2F1(1, 1; 3; 0.9) — slow direct-series convergence",
    ),
    Case(
        id="tC-2F1-z-095",
        tier="C",
        category="near-unit",
        a=(_r("1/2"), _r("3/2")),
        b=(_r("2"),),
        z=_r("19/20"),
        precision=50,
        tolerance_rel="1e-44",
        rule="2F1 at |z|=0.95 — boundary just inside admitted region",
    ),
    Case(
        id="tC-2F1-z-097",
        tier="C",
        category="near-unit",
        a=(_r("3/2"), _r("5/2")),
        b=(_r("3"),),
        z=_r("97/100"),
        precision=50,
        tolerance_rel="1e-42",
        rule="2F1 at |z|=0.97 — high-cost case (many terms)",
    ),
    Case(
        id="tC-2F1-z-098",
        tier="C",
        category="near-unit",
        a=(_r("3/4"), _r("5/4")),
        b=(_r("2"),),
        z=_r("49/50"),
        precision=50,
        tolerance_rel="1e-40",
        rule="2F1 at |z|=0.98 — tightest admitted slow-converger",
    ),
    Case(
        id="tC-2F1-complex-085",
        tier="C",
        category="near-unit",
        a=(_r("1"), _r("3/2")),
        b=(_r("5/2"),),
        z=_r("1/2", "17/25"),
        precision=50,
        tolerance_rel="1e-44",
        rule="2F1 with |z|=0.85 in the complex plane (z = 0.5 + 0.68i)",
    ),
    Case(
        id="tC-3F2-z-090",
        tier="C",
        category="near-unit",
        a=(_r("1/2"), _r("1"), _r("3/2")),
        b=(_r("2"), _r("3"),),
        z=_r("9/10"),
        precision=50,
        tolerance_rel="1e-44",
        rule="3F2 at |z|=0.9 — bigger-p slow converger",
    ),
    Case(
        id="tC-2F1-neg-095",
        tier="C",
        category="near-unit",
        a=(_r("1/2"), _r("1/2")),
        b=(_r("1"),),
        z=_r("-19/20"),
        precision=50,
        tolerance_rel="1e-44",
        rule="2F1 at z=-0.95 — alternating-sign cancellation hot spot",
    ),
]


# ----- Tier D: parameter coalescence ---------------------------------

TIERD: list[Case] = [
    Case(
        id="tD-2F1-coalesce-a-eq-c",
        tier="D",
        category="coalescence",
        a=(_r("1"), _r("3/2")),
        b=(_r("3/2"),),
        z=_r("3/10"),
        precision=50,
        tolerance_rel="1e-46",
        rule="2F1(1, 3/2; 3/2; 0.3) — a₂ = b₁ (Pochhammer cancellation in the limit)",
    ),
    Case(
        id="tD-2F1-near-coalesce",
        tier="D",
        category="coalescence",
        a=(_r("2"), _r("3")),
        b=(_r("301/100"),),
        z=_r("2/5"),
        precision=50,
        tolerance_rel="1e-44",
        rule="2F1(2, 3; 3.01; 0.4) — b near a₂; near-coalescence cancellation",
    ),
    Case(
        id="tD-1F1-coalesce-int",
        tier="D",
        category="coalescence",
        a=(_r("3"),),
        b=(_r("3"),),
        z=_r("1"),
        precision=50,
        tolerance_rel="1e-46",
        rule="1F1(3; 3; 1) = e¹ (Kummer's a=b case)",
    ),
    Case(
        id="tD-2F1-int-spaced",
        tier="D",
        category="coalescence",
        a=(_r("2"), _r("4")),
        b=(_r("3"),),
        z=_r("1/3"),
        precision=50,
        tolerance_rel="1e-46",
        rule="2F1(2, 4; 3; 1/3) — integer-spaced numerator/denominator parameters",
    ),
    Case(
        id="tD-3F2-coalesce",
        tier="D",
        category="coalescence",
        a=(_r("1"), _r("2"), _r("5/2")),
        b=(_r("5/2"), _r("3"),),
        z=_r("1/4"),
        precision=50,
        tolerance_rel="1e-46",
        rule="3F2 with a₃ = b₁ — limit-of-Pochhammer cancellation",
    ),
]


# ----- Tier E: refusal cases -----------------------------------------

TIERE: list[Case] = [
    Case(
        id="tE-3F1-asymptotic",
        tier="E",
        category="refusal",
        a=(_r("1"), _r("2"), _r("3")),
        b=(_r("4"),),
        z=_r("1"),
        precision=50,
        tolerance_rel="0",
        rule="3F1 with p > q+1 (asymptotic only)",
        expected_kind="tagged",
        expected_tag="hypergeometric-pfq/non-convergent",
        expected_payload={"reason_substr": "asymptotic"},
        skip_wolfram=True,
    ),
    Case(
        id="tE-2F1-z-099",
        tier="E",
        category="refusal",
        a=(_r("1"), _r("1")),
        b=(_r("2"),),
        z=_r("99/100"),
        precision=50,
        tolerance_rel="0",
        rule="2F1 with |z|=0.99 — too close to radius of convergence",
        expected_kind="tagged",
        expected_tag="hypergeometric-pfq/non-convergent",
        expected_payload={"reason_substr": "too close"},
        skip_wolfram=True,
    ),
    Case(
        id="tE-1F0-z1-singular",
        tier="E",
        category="refusal",
        a=(_r("1"),),
        b=(),
        z=_r("1"),
        precision=50,
        tolerance_rel="0",
        rule="1F0(1;;1) — closed-form (1-z)^{-a} singular at z=1",
        expected_kind="tagged",
        expected_tag="hypergeometric-pfq/non-convergent",
        expected_payload={"reason_substr": "singular"},
        skip_wolfram=True,
    ),
    Case(
        id="tE-2F1-z-neg1-boundary",
        tier="E",
        category="refusal",
        a=(_r("1"), _r("1")),
        b=(_r("2"),),
        z=_r("-1"),
        precision=50,
        tolerance_rel="0",
        rule=(
            "2F1(1,1;2;-1) = log(2) is a famous Abel-summation identity "
            "at the boundary of the unit disc; the tool conservatively "
            "refuses with non-convergent rather than emit a value whose "
            "cancellation budget the direct-series cannot meet"
        ),
        expected_kind="tagged",
        expected_tag="hypergeometric-pfq/non-convergent",
        expected_payload={"reason_substr": "too close"},
        skip_wolfram=True,
        notes=("v0.2 follow-up: an analytic-continuation path or Abel-sum "
               "fast-path could reclaim this case. Tracked under hv0.10 "
               "in the campaign log."),
    ),
]


ALL_TIERS: list[Case] = TIER0 + TIERA + TIERB + TIERC + TIERD + TIERE


# ---------------------------------------------------------------------
# Truth computation
# ---------------------------------------------------------------------

def _parse_rational_or_decimal(s: str) -> Fraction:
    """Parse '1/3', '2', '0.5', '-3/4' etc. into a Fraction."""
    s = s.strip()
    if "/" in s:
        return Fraction(s)
    return Fraction(s)  # Fraction handles '0.5' decimal strings via _convert


def _to_mp(re: str, im: str) -> mpmath.mpc:
    """Convert (re-string, im-string) to a high-precision mpc."""
    rr = _parse_rational_or_decimal(re)
    ii = _parse_rational_or_decimal(im)
    return mpmath.mpc(mpmath.mpf(rr.numerator) / mpmath.mpf(rr.denominator),
                       mpmath.mpf(ii.numerator) / mpmath.mpf(ii.denominator))


def mpmath_truth(case: Case, dps: int = 80) -> mpmath.mpc:
    """Compute pFq via mpmath at `dps` working precision."""
    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps
        a_list = [_to_mp(re, im) for re, im in case.a]
        b_list = [_to_mp(re, im) for re, im in case.b]
        z = _to_mp(case.z[0], case.z[1])
        return mpmath.hyper(a_list, b_list, z)
    finally:
        mpmath.mp.dps = saved


# ---------------------------------------------------------------------
# Wolfram cross-validation (optional)
# ---------------------------------------------------------------------

def _wolfram_complex(re: str, im: str) -> str:
    """Format a complex number as a Wolfram input expression."""
    if im in ("0", "0/1"):
        return f"({re})"
    return f"({re}) + ({im}) I"


def wolfram_truth(case: Case, dps: int = 60) -> Optional[str]:
    """Compute Hypergeometric pFq via wolframscript at `dps` precision.

    Returns a decimal-string complex like "1.234..." or "1.234... + 5.678... I",
    or None if Wolfram is unavailable / timed out / returned an error.
    """
    if case.skip_wolfram:
        return None

    a_args = ",".join(_wolfram_complex(re, im) for re, im in case.a)
    b_args = ",".join(_wolfram_complex(re, im) for re, im in case.b)
    z_expr = _wolfram_complex(case.z[0], case.z[1])

    # `ToString[..., InputForm]` is the only reliable way to get a parseable
    # decimal-string from `wolframscript -code`. Naked `InputForm[...]` and
    # `Print[InputForm[...]]` both leave a literal `InputForm[...]` wrapper
    # in the output (verified empirically on TIB-Hannover-VPN host).
    #
    # `Block[{$MaxExtraPrecision = 5000}, ...]` is required: with rational
    # inputs Wolfram first tries exact-symbolic evaluation; for moderate-or-
    # large parameters that closed form is enormous and `N[]` requires a
    # large internal precision budget to evaluate it numerically. The
    # default $MaxExtraPrecision = 50 is too tight; without the Block,
    # mid-tier-B cases like 1F1(50; 75; 2) emit the symbolic E-tower
    # rather than a decimal.
    code = (
        f"Block[{{$MaxExtraPrecision = 5000}}, "
        f"v = HypergeometricPFQ[{{{a_args}}}, {{{b_args}}}, {z_expr}]; "
        f"ToString[N[v, {dps}], InputForm]]"
    )

    try:
        result = subprocess.run(
            ["wolframscript", "-code", code],
            capture_output=True,
            text=True,
            timeout=WOLFRAM_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _strip_wolfram_backticks(s: str) -> str:
    """Remove `<digits>.<digits>` precision suffixes from a Wolfram number.

    Wolfram's InputForm decorates every machine/arbitrary-precision number
    with a backtick suffix encoding its precision: `2.71828...`60.`,
    `2.71828...`60.12587`. The suffix runs from `\`` to the next
    non-digit-non-dot character.

    The backticks are not parseable by mpmath; we strip them and let mpmath
    re-infer the precision from `mp.dps`.
    """
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


def _wolfram_to_mpc(s: str, dps: int = 80) -> Optional[mpmath.mpc]:
    """Parse Wolfram InputForm `1.23 + 4.56*I` to an mpc.

    Two Wolfram-isms must be handled: backtick precision suffixes
    (e.g. `2.71828`60.`) and the `*I` imaginary marker. mpmath's
    `mpmathify` accepts `1.0+2.0j` (Python complex literal syntax).
    """
    if s is None:
        return None

    cleaned = _strip_wolfram_backticks(s).replace("*^", "e").strip()

    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps
        # If imaginary part present (`...*I`), split on top-level `+`/`-`
        # respecting sign; otherwise it's a real number.
        if "*I" not in cleaned and "I" not in cleaned:
            try:
                return mpmath.mpc(mpmath.mpf(cleaned), mpmath.mpf(0))
            except Exception:
                return None

        # Find the split point: scan for ` + ` or ` - ` between the real
        # and imaginary terms, ignoring leading sign.
        body = cleaned
        sign = 1
        if body.startswith("-"):
            sign = -1
            body = body[1:]

        # Walk and find the operator separating real from imaginary.
        # Wolfram emits `<re> + <im>*I` or `<re> - <im>*I`.
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
            # No imaginary component after the leading number.
            return mpmath.mpc(mpmath.mpf(cleaned), mpmath.mpf(0))

        re_str = body[:split_at].strip()
        im_op = body[split_at]
        im_str = body[split_at+1:].strip()
        # Drop trailing `*I` / `I`
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
# Consensus pinning
# ---------------------------------------------------------------------

def _format_mpc(z: mpmath.mpc, dps: int) -> tuple[str, str]:
    """Format an mpc as (re-decimal-string, im-decimal-string) at `dps`."""
    saved = mpmath.mp.dps
    try:
        mpmath.mp.dps = dps + 5  # margin for round-trip
        return (mpmath.nstr(z.real, dps, strip_zeros=False),
                mpmath.nstr(z.imag, dps, strip_zeros=False))
    finally:
        mpmath.mp.dps = saved


def consensus(case: Case, mp_truth: mpmath.mpc, wolf_truth: Optional[mpmath.mpc],
              cmp_dps: int = 50) -> dict:
    """Compare mpmath and wolfram values; return a consensus record.

    For VALUE cases, compares at `cmp_dps` relative precision. For TAGGED
    cases the truth is the structured envelope, not a numerical value.
    """
    if case.expected_kind == "tagged":
        return {
            "consensus": "structural-refusal",
            "mpmath_skipped": True,
            "wolfram_skipped": True,
        }

    if wolf_truth is None:
        return {
            "consensus": "mpmath-only",
            "mpmath_value": _format_mpc(mp_truth, cmp_dps + 8),
            "wolfram_value": None,
            "rel_disagreement": None,
        }

    diff = abs(mp_truth - wolf_truth)
    scale = max(abs(mp_truth), mpmath.mpf("1e-300"))
    rel = float(diff / scale)
    threshold = 10.0 ** (-cmp_dps + 2)
    return {
        "consensus": "mpmath+wolfram-agree" if rel < threshold else "mpmath+wolfram-DISAGREE",
        "mpmath_value": _format_mpc(mp_truth, cmp_dps + 8),
        "wolfram_value": _format_mpc(wolf_truth, cmp_dps + 8),
        "rel_disagreement": rel,
    }


# ---------------------------------------------------------------------
# Build inputs.json + expected.json
# ---------------------------------------------------------------------

def build_input_record(case: Case) -> dict:
    """One row of inputs.json — the parameters the bench feeds to the tool."""
    return {
        "id": case.id,
        "tier": case.tier,
        "category": case.category,
        "input": {
            "a": [{"re": re, "im": im} for re, im in case.a],
            "b": [{"re": re, "im": im} for re, im in case.b],
            "z": {"re": case.z[0], "im": case.z[1]},
            "precision": case.precision,
        },
        "rule": case.rule,
    }


def build_expected_record(case: Case, mp_truth: mpmath.mpc,
                           consensus_record: dict) -> dict:
    """One row of expected.json — the truth value + tolerance contract."""
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
    else:
        # Save truth at precision + 8 so verifier has margin.
        re_s, im_s = _format_mpc(mp_truth, case.precision + 8)
        base["expected"] = {
            "kind": "value",
            "truth": {"re": re_s, "im": im_s},
            "truth_method": "mpmath.hyper@80dps",
            "truth_xref_method": case.truth_xref_method,
        }
    return base


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="output directory (typically bench/.../golden)")
    parser.add_argument("--no-wolfram", action="store_true",
                        help="skip Wolfram cross-validation")
    parser.add_argument("--limit", type=int, default=None,
                        help="(testing) only build the first N cases")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    inputs = {"version": 1, "problem": "hypergeometric-pfq", "cases": []}
    expected = {"version": 1, "problem": "hypergeometric-pfq", "cases": []}

    cases = ALL_TIERS if args.limit is None else ALL_TIERS[:args.limit]
    n = len(cases)

    print(f"# building corpus — {n} cases", file=sys.stderr)
    for i, case in enumerate(cases):
        print(f"  [{i+1:2d}/{n}] {case.id}  tier={case.tier}", file=sys.stderr)

        if case.expected_kind == "tagged":
            cons = consensus(case, mpmath.mpc(0), None)
            inputs["cases"].append(build_input_record(case))
            expected["cases"].append(build_expected_record(case, mpmath.mpc(0), cons))
            continue

        # mpmath dps must match the Wolfram cross-check budget so the
        # high-precision case (precision=100) gets compared at full
        # rigour. We pick max(80, case.precision + 30).
        mp_dps = max(80, case.precision + 30)
        mp_truth = mpmath_truth(case, dps=mp_dps)

        if args.no_wolfram:
            wolf_truth_mpc = None
        else:
            # Cross-check Wolfram precision must comfortably exceed the
            # case's tool-precision so the comparison threshold (set below at
            # case.precision dps) can be cleared by the Wolfram value's own
            # internal precision. mpmath runs at 80 dps; Wolfram default
            # wolf_dps is max(80, precision + 30) to leave a 30-dps margin
            # over the bench tolerance.
            wolf_dps = max(80, case.precision + 30)
            wolf_str = wolfram_truth(case, dps=wolf_dps)
            wolf_truth_mpc = _wolfram_to_mpc(wolf_str, dps=max(120, wolf_dps + 20)) if wolf_str else None

        # Consensus is checked at min(case.precision, wolf_dps - 10) — we
        # cannot detect disagreement below the Wolfram cross-check's actual
        # working precision, only confirm or fail. The −10 margin absorbs
        # the residual roundoff at the *requested* Wolfram precision.
        cmp_dps = min(case.precision, max(50, wolf_dps - 10) if not args.no_wolfram else case.precision)
        cons = consensus(case, mp_truth, wolf_truth_mpc, cmp_dps=cmp_dps)

        if cons["consensus"] == "mpmath+wolfram-DISAGREE":
            print(
                f"    !! disagreement: rel = {cons['rel_disagreement']:.3e}",
                file=sys.stderr,
            )

        inputs["cases"].append(build_input_record(case))
        expected["cases"].append(build_expected_record(case, mp_truth, cons))

    inputs_path = out_dir / "inputs.json"
    expected_path = out_dir / "expected.json"
    inputs_path.write_text(json.dumps(inputs, indent=2) + "\n")
    expected_path.write_text(json.dumps(expected, indent=2) + "\n")

    print(f"# wrote {inputs_path}", file=sys.stderr)
    print(f"# wrote {expected_path}", file=sys.stderr)

    # Disagreement summary
    n_agree = sum(1 for c in expected["cases"]
                   if c["consensus"]["consensus"] == "mpmath+wolfram-agree")
    n_only = sum(1 for c in expected["cases"]
                  if c["consensus"]["consensus"] == "mpmath-only")
    n_disagree = sum(1 for c in expected["cases"]
                      if c["consensus"]["consensus"] == "mpmath+wolfram-DISAGREE")
    n_refusal = sum(1 for c in expected["cases"]
                     if c["consensus"]["consensus"] == "structural-refusal")
    print(
        f"# consensus: {n_agree} agree, {n_only} mpmath-only, "
        f"{n_disagree} disagree, {n_refusal} structural-refusal",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
