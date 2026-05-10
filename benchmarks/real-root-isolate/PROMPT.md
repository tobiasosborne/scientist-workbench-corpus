# Bench — `real-root-isolate` (rational isolating intervals for real roots of a squarefree ℚ[x] polynomial)

## ⚠ How you will be graded

You will be graded on **exact rational-interval correctness** under the
Sturm-sequence ground truth. Every interval must isolate exactly one
real root of `f` (verified via `Poly.count_roots`); intervals must be
disjoint and ascending; the count must equal the total number of real
roots of `f`. No floating-point appears at any step. A small tolerance
on rationals is **not** allowed.

This bench is the floor for `tools/real-root-isolate` (bead
`scientist-workbench-rra` — VAS with LMQ bound). Passing it is
necessary but not sufficient — the seven-artefact contract still
applies.

## Problem statement

Implement real-root isolation for univariate polynomials over ℚ:

  given `f ∈ ℚ[x]`, **squarefree**, return a list of disjoint rational
  intervals — open `(a, b)` for irrational roots and singletons
  `(r, r)` for rational roots — covering every real root of `f`
  exactly once, in ascending order. The intervals' rational endpoints
  are exact (no floats); the "isolating" property is verified against
  Sturm-sequence ground truth.

### Algorithm path

The canonical algorithm is the **Vincent-Akritas-Strzebonski (VAS)
continued-fraction method** with the **Local-Max Quadratic (LMQ)**
positive-root bound (Akritas-Strzebonski-Vigklas 2008). The reference
implementation in this bench is SymPy's `Poly.intervals()` —
`sympy/polys/rootisolation.py::dup_isolate_real_roots` is a faithful
BSD port of VAS-LMQ that scientist-workbench's `packages/real-roots`
will mirror in TypeScript.

The high-level recipe:

1. Reduce to positive roots: substitute `x → −x` and `x → 1/x` to map
   negative / large-positive / small-positive cases onto the positive
   real line in three regions.
2. **VAS positive-root isolation:** apply Vincent's theorem (1836) — a
   sufficient condition for "exactly one positive root" via Möbius
   transformations (`x → x + a` and `x → 1/(x + 1)`) and Descartes'
   rule of signs. The LMQ bound (Akritas-Strzebonski-Vigklas 2008)
   provides the tight upper bound that controls the recursion depth
   and the constant factor; it is the algorithm's headline efficiency
   improvement over naive bisection.
3. **Rational roots:** detected by Lagrange/Cauchy bound + the rational
   root theorem; each rational root is emitted as a singleton
   `(r, r)`.

### Why the squarefree precondition

VAS isolates real roots by tracking sign-changes in successively
refined intervals. Repeated factors break the sign-change ↔ root
bijection (a double root has no sign change; a triple root has
*one* sign change but represents three roots counted with
multiplicity). Composing with `packages/poly-factor::squareFree`
(Yun 1976, worklog 052) recovers the squarefree precondition cleanly.
The non-squarefree boundary is therefore tagged refusal, not
"silently correct" — see Tier F.

## I/O contract

### Input

```jsonc
{
  "f":   "x^3 - 3*x + 1",   // polynomial in `var` over ℚ
  "var": "x"
}
```

### Output

```jsonc
{
  "kind":      "ok",
  "intervals": [
    {"lo": "-2", "hi": "-1"},  // open (lo, hi) — bracketing irrational root
    {"lo": "0",  "hi": "1"},   // open (lo, hi) — bracketing irrational root
    {"lo": "1",  "hi": "2"}    // open (lo, hi) — bracketing irrational root
  ],
  "method":   "vas-lmq",       // informational
  "warnings": []
}

// or with a rational root via singleton:
{
  "kind":      "ok",
  "intervals": [
    {"lo": "1", "hi": "1"},    // singleton — f(1) = 0 (rational root)
    {"lo": "2", "hi": "2"}
  ],
  "method":   "vas-lmq",
  "warnings": []
}

// or refusal:
{
  "kind": "tagged",
  "tag":  "real-root-isolate/<class>",
  "payload": {"detail": "..."}
}
```

Refusal classes:

- `real-root-isolate/not-squarefree` — input has repeated factors.
- `real-root-isolate/non-polynomial`  — `f` is not a polynomial in `var` over ℚ.
- `real-root-isolate/multivariate`    — `f` mentions a non-`var` symbol.

The two interval shapes are mutually exclusive per entry: `lo == hi`
iff the entry is a singleton naming a rational root; otherwise `lo <
hi` and the entry is the open interval `(lo, hi)` bracketing one
irrational root. The candidate may emit either shape per root —
SymPy's reference always emits singletons for rational roots and
open intervals for irrational roots.

## Invariants the verifier checks

Per ADR-0019 §1, four checks per happy-path case + two for refusals.
All checks are *exact* (rational and Sturm-sequence — no float).

### Happy-path (`kind = "ok"`)

1. **`shape`** — kind="ok", intervals is a list of `{lo, hi}` records,
   each lo/hi is a rational string parseable via `sympy.Rational`.
2. **`each_interval_contains_one_root`** — for each entry: if
   `lo == hi`, then `f(lo) == 0` (singleton); else
   `count_roots(lo, hi) − [f(lo) = 0] − [f(hi) = 0] == 1` (open count).
3. **`intervals_disjoint_and_ordered`** — for each adjacent pair,
   `intervals[i].hi <= intervals[i+1].lo`. Equality permitted (shared
   non-root separator under SymPy's open + singleton convention).
4. **`count_matches_total_real_roots`** — `len(intervals) ==
   Poly(f).count_roots()` over ℝ (Sturm ground truth).

### Refusal (`kind = "tagged"`)

1. **`shape`** — tag in `real-root-isolate/*` namespace; payload is a record.
2. **`refusal_class_matches`** — exact tag string match;
   `payload.detail` is non-empty.

These checks are **necessary AND sufficient** for a valid real-root-
isolate output modulo representation choice (open versus singleton for
the same rational root is also accepted via the verifier's two-shape
handling).

## Test set tiers

`golden/inputs.json` contains **37 cases**:

| Tier | Cases | What it probes |
|---|---|---|
| **A. trivial** | 5 | linear with rational root, linear with negative leading coef, quadratic two real, quadratic rational |
| **B. Chebyshev / Legendre** | 8 | T_3..T_7, P_3, P_4, P_5 — known real-root counts (`deg(P_n) = n` distinct reals) |
| **C. clustered** | 5 | Mignotte M_{n,a} (two roots ~1/a apart), three rational cluster, two roots 1e-6 apart |
| **D. large-degree** | 5 | Wilkinson 20 / 50, Chebyshev product (deg 15), Legendre product (deg 12), half-integer deg 30 |
| **E. rational stress** | 5 | large coefs, mixed denominators, tiny leading, dense rational quartic, spread roots |
| **F. refusals** | 4 | non-squarefree, non-squarefree mixed, multivariate, non-polynomial |
| **G. structural edges** | 5 | no real roots (deg-2, deg-4, Φ_12, Φ_13), poly with rational root at 0 |

**37 cases × 4 happy-path checks (or 2 refusal) ≈ 130 invariant assertions.**

## Triple-witness oracle protocol

Per ADR-0019 §3, golden cases are admitted iff ≥ 2 of 3 oracles agree.
Real-root isolation has the special property that *count* is invariant
across implementations (every VAS-class isolator returns the same
number of intervals for the same input) but *endpoints* are not (each
chooses its own bisection cadence). The agreement is therefore
checked on the **count of real roots**:

1. **`wolframscript`** via `Length[RootIntervals[poly][[1]]]`
   (`bench/_corpus/oracle/wolfram.py`).
2. **SymPy** via `len(Poly(p, x, domain="QQ").intervals())`
   (the bench's reference implementation).
3. SageMath when available — preferred third witness (`QQ['x'](p)
   .roots(RR)` count).

For tier-F refusals, the workbench's bounded-scope refusal
(non-squarefree, multivariate, non-polynomial) is admitted even when
Wolfram solves the input (it squarefree's internally) — that's the
honest "we expect squarefree input; caller composes with poly-factor"
boundary. The oracle log records this as
`wolfram-ok-workbench-bounded-scope`.

## Verifying your solution

```sh
# Generate goldens (slow; full triple-witness via wolframscript).
python3 bench/real-root-isolate/golden/generate.py

# Fast iteration with SymPy-only:
WB_LIVE_ORACLE=0 python3 bench/real-root-isolate/golden/generate.py

# Mutation-prove gate:
python3 bench/real-root-isolate/golden/test_mutations.py
```

### Files

- `golden/inputs.json` — 37 test cases.
- `golden/expected.json` — reference outputs (kind + tag).
- `golden/oracle_log.json` — triple-witness consensus per case.
- `golden/verify.py` — 4-check (happy) / 2-check (refusal) verifier.
- `golden/verifier_protocol.md` — exact specifications per check.
- `golden/generate.py` — reproducible golden generation.
- `golden/test_mutations.py` — mutation-prove harness (≥5 RED).
- `reference/real_root_isolate_reference.py` — SymPy-backed reference.

## Hard constraints (sci-wb-specific)

- Pure TypeScript on Bun. No FFI.
- Default determinism tier (symbolic, bit-identical cross-platform
  forever — no `numerical: true`).
- Output shape per the contract above; rational endpoints in canonical
  lowest-terms form (`p` or `p/q` with `gcd(p, q) = 1`, `q > 0`).
- Boundary categories per ADR-0003: tagged refusals listed above;
  `ToolError` for malformed input only (`f` not a string, `var` not a
  symbol, parse failures, zero polynomial).

## What this bench does NOT cover

- **Multiplicity** — bead's input is squarefree by precondition; multiplicity
  is a `tools/poly-factor` concern (worklog 052).
- **Complex root isolation** (Wilf 1978 / Pinkert 1976) — out of v0.1
  scope; the alg-num substrate (`xyt → xkz`) handles complex algebraics
  via the (minpoly, k) Root[] form.
- **Algebraic-number arithmetic** between roots — `packages/alg-num`
  stack (`xyt → xkz → 6cd → rti → 5i2`).
- **High-precision numerical roots** — `tools/linalg-solve`'s companion-
  matrix path covers the float64 case.
