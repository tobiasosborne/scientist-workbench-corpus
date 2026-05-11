# lp-small — agent prompt

You are grading a candidate LP solver against the small-LP pathology
battery. Forty-ish problems across eight families covering the
resource-limited regime that the NETLIB collection under-weights.

See `lp-netlib/PROMPT.md` for the wire format and "what wonderful
looks like" framing. This suite adds:

- **Family C (Beale) and Family B (Klee-Minty)**: if a candidate
  passes random-dense but fails these, the failure is *algorithmic*
  (pivot-rule choice or simplex-only dispatch) — surface the
  diagnosis in the worklog, not as a tolerance tweak.
- **Family F (near-infeasible) and Family G (unbounded)**: a
  candidate that returns `status: "optimal"` here is *lying*. The
  status-consistency check fails hard.
- **Family H (boundary tags)**: a candidate that throws or hangs
  here is broken. The expected output is a `tagged` envelope; the
  candidate must produce one to pass.

When debugging a failure: look at `meta.family` and `meta.generator`
first. A consistent-failure family (e.g. "all of Family B fails") is
diagnostically very different from a scattered failure (e.g. "two
random-dense cases fail").
