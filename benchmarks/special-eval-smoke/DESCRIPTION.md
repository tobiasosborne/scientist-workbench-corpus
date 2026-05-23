# special-eval-smoke

Smoke benchmark for the per-head Erf+Bessel umbrella evaluator
(`tools/special-eval` in scientist-workbench).  This is a wire-validation
suite, not a publication-grade accuracy bench.

## What it proves

End-to-end flow of a per-case `head` field from corpus inputs through:

1. corpus runner reads `golden/inputs.json`
2. spawns `run-candidate.ts` per case
3. `run-candidate.ts` encodes `{head, args}` into the canonical Value
   protocol and calls `executeToolDef(specialEvalDef, ...)` in-process
4. tool dispatches via `--head=<Name>` (`special-eval/tool.ts:1254`)
5. tool returns float64-lane bigfloat-wrapped result
6. `run-candidate.ts` decodes to raw JSON
7. corpus runner pipes `{input, candidate, id}` to `golden/verify.ts`
8. verifier runs 6 invariants per case

If the wire works, all 10 cases pass.  If any single byte of the
multi-head dispatch contract drifts, the verifier surfaces it.

## What it does NOT prove

- High-precision agreement with mpmath / Arb / Wolfram (see
  `benchmarks/erf-anchor/` (B18) and `benchmarks/besselj-anchor/`
  (B19) for that)
- Coverage of the complex axis (smoke is real-axis only)
- Coverage of arb-prec lane (smoke is float64-only)
- Coverage of refusal envelopes (smoke is success-path only)
- Coverage of all 14 brief-listed heads: only 10 ship; the 4
  unsupported heads (HankelH1, HankelH2, SphericalBesselJ,
  SphericalBesselY) have child beads tracking their admission

## References

- **ADR-0040** — per-head substrate, Erf prototype
- **ADR-0041** — per-head substrate, Bessel extension; the
  `ADMITTED_HEADS` table this smoke aligns with
- **B14** (closed) — `adapters/scientist-workbench/special-eval-smoke.toml`,
  the adapter targeting this suite
- **B15** (this bead) — `scientist-workbench-corpus-seu`,
  benchmark-suite creation
- `worklog 005` of scientist-workbench-corpus — self-reported precision
  tolerance convention (2x slack at the 1e-13 floor for float64
  summation noise)
- `tools/special-eval/tool.ts` (scientist-workbench) — the dispatcher
  this smoke exercises; `realSuccess` / `dispatchReal` /
  `dispatchRealBessel` are the load-bearing call sites

## Lane choice and the precision flag

The B14 adapter declares `tool_flags.precision = "53"` with comment
"⇒ float64 lane".  This is the IEEE-754 binary64 mantissa width
(53 bits), NOT the `--precision` flag value the tool's dispatcher
parses (which is decimal digits; `<= 15` ⇒ float64, `> 15` ⇒
arb-prec).  `grade.ts` does not forward `tool_flags` to the
candidate today (schema-v3 follow-up), so `run-candidate.ts` is
the authoritative site for the lane decision.  It hardcodes
`precision = 10n`, which unambiguously routes the float64 lane
across all 10 smoke heads.

When grade.ts wires `tool_flags` through, the adapter can pin the
lane directly and the run-candidate can become a thinner bridge
(reading flags from a runner-provided env var or argv).

## The 10 shipped cases

| id                    | head         | args    | expected_value          | source                                |
|-----------------------|--------------|---------|-------------------------|---------------------------------------|
| `erf-zero`            | Erf          | [0]     | 0                       | DLMF §7.2.1 (closed form)             |
| `erfc-zero`           | Erfc         | [0]     | 1                       | DLMF §7.2.2 (closed form)             |
| `erfcx-zero`          | Erfcx        | [0]     | 1                       | DLMF §7.7.2 (closed form)             |
| `erfi-zero`           | Erfi         | [0]     | 0                       | DLMF §7.2.3 (closed form, odd)        |
| `inverseerf-zero`     | InverseErf   | [0]     | 0                       | DLMF §7.17.2 (closed form, odd)       |
| `inverseerfc-one`     | InverseErfc  | [1]     | 0                       | DLMF §7.17.2 (erfc(0)=1 inverse)      |
| `besselj-zero-zero`   | BesselJ      | [0, 0]  | 1                       | DLMF §10.2.2 (Maclaurin a₀=1)         |
| `bessely-one-one`     | BesselY      | [1, 1]  | -0.7812128213002887     | DLMF §10.2.3 tabulated                |
| `besseli-zero-zero`   | BesselI      | [0, 0]  | 1                       | DLMF §10.25.2 (modified Maclaurin a₀=1)|
| `besselk-one-one`     | BesselK      | [1, 1]  |  0.6019072301972346     | DLMF §10.32 tabulated                 |

Singular Bessel cases (BesselY(0,0), BesselK(0,0), negative non-integer
ν at z=0) are deliberately NOT in the smoke — those are refusal-path
exercises that belong in a dedicated boundary-envelope bench.

## Child beads filed by B15

- Hankel H₁ / H₂ admission into `tools/special-eval`'s ADMITTED_HEADS
  + dispatch matrix
- Spherical-Bessel j / y admission into the same

(Filed as plain `bd note`s on B15 closure — see `bd show
scientist-workbench-corpus-seu`.)
