# References — `poly-factor-q` bench

## Primary algorithms

### Square-free decomposition

**Yun 1976** — "On square-free decomposition algorithms",
*SYMSAC '76*, 26-35. ACM 10.1145/800205.806320.

- Not staged locally. The algorithm is a six-line gcd-recurrence
  reproduced verbatim in Cox-Little-O'Shea §4.5 (local PDF
  `docs/ground-truth/factor/cox-little-oshea-ideals-varieties-algorithms-4th.pdf`)
  and Geddes-Czapor-Labahn §8.2 (TIB-Hannover Springer access).
- Pseudocode: `Yun(f)`:
  - `b = gcd(f, f')`, `c = f / b`, `d = c'`
  - `i = 1`; loop:
      `a_i = gcd(c, d - c'); c = c / a_i; d = (d - c') / a_i; i += 1`
    until `c = 1`
  - Output: `[(a_i, i)]`

### Factorisation over `𝔽_p`

**Berlekamp 1967** — "Factoring polynomials over finite fields",
*Bell Syst. Tech. J.* 46(8), 1853-1859.

- Local: `docs/ground-truth/factor/berlekamp-1967.pdf`
- Reduces factorization in `𝔽_p[x]` to the null space of the
  *Berlekamp matrix* `Q − I`, where `Q` is the matrix of `x^{p·i}
  mod f` for `i = 0, …, n-1`. The kernel dimension equals the
  number of distinct irreducible factors. Each non-trivial kernel
  vector `g(x)` yields a non-trivial factor via
  `gcd(f, g(x) − s)` for `s ∈ 𝔽_p`.
- For "small" primes (we use `p ≤ 100` typically) Berlekamp's
  matrix-kernel approach is the cleanest; for larger primes
  Cantor-Zassenhaus 1981 (distinct-degree + equal-degree
  decompositions, randomized) is faster but more bookkeeping.
- v1 ships Berlekamp; CZ as a future bead if profile demands.

### Hensel lifting

**Zassenhaus 1969** — "On Hensel factorization, I",
*J. Number Theory* 1(3), 291-311. DOI 10.1016/0022-314X(69)90013-5.

- **Not staged locally** — Elsevier paywall, bot-walled even via
  Wayback (see `docs/ground-truth/factor/MISSING.md`). The
  *quadratic Hensel* variant we implement is reproduced in
  Knuth TAOCP Vol 2 §4.6.2 Algorithm D, Bach-Shallit §6.4, and
  Cox-Little-O'Shea §4.6 (local PDF, the cleanest pedagogical
  version).
- Given `f ≡ g₀ · h₀ (mod p)` with `gcd(g₀, h₀) ≡ 1 (mod p)`,
  computes `(g_k, h_k)` with `f ≡ g_k · h_k (mod p^{2^k})` in `k`
  iterations — each iteration *doubles* the precision (vs linear
  Hensel which adds one). Standard quadratic-convergence form.

### Recombination via lattice reduction

**van Hoeij 2002** — "Factoring polynomials and the knapsack
problem", *J. Number Theory* 95(2), 167-189.

- Local: `docs/ground-truth/factor/vanhoeij-2002-knapsack.pdf`
- The pivotal idea: rather than try all `2^r` subsets of mod-`p`
  factors and trial-divide each candidate against `f`, build an
  LLL-reducible "knapsack lattice" whose **short vectors are the
  combination indicators** of true integer factors. Polynomial in
  `r` rather than exponential.

**Hart-van Hoeij-Novocin 2011** — "Practical polynomial-time
factoring algorithms for the field rationals", *ISSAC 2011*.

- Local: `docs/ground-truth/factor/hart-vanhoeij-novocin-2011.pdf`
- Refines van Hoeij's algorithm with a "logarithmic-precision LLL"
  variant that achieves true polynomial wall-clock on Swinnerton-
  Dyer-class inputs. The reference for "polynomial-time recombination
  must be polynomial in *practice*, not just in asymptotic theory."

### Coefficient bound

**Mignotte 1974** — "An inequality about factors of polynomials",
*Math. Comp.* 28(128), 1153-1157. DOI 10.1090/S0025-5718-1974-0354006-0.

- **Not staged locally** — AMS Cloudflare-walled even via Wayback
  (a known acquisition gap; tried via direct AMS, Wayback CDX,
  Project Euclid; all failed for headless fetchers).
- Statement (textbook form, reproduced verbatim in
  Cox-Little-O'Shea §4.5 and Geddes-Czapor-Labahn §6.4):

      For f ∈ ℤ[x] of degree n with leading coefficient lc(f),
      every factor g ∈ ℤ[x] of f satisfies
        ‖g‖_∞ ≤ 2^n · ‖f‖_2

  where `‖f‖_2 = √(Σ c_i²)` is the polynomial 2-norm. The simpler
  variant we use is

      ‖g‖_∞ ≤ binom(n, ⌊n/2⌋) · ‖f‖_∞ + |lc(f)|

  which is loose but cheap. v1 uses the looser bound; tightening
  is a profile-driven optimisation, deferred.

## Modern textbook treatment

**Cox-Little-O'Shea (2015)** — *Ideals, Varieties, and Algorithms*
4th ed., Springer.

- Local: `docs/ground-truth/factor/cox-little-oshea-ideals-varieties-algorithms-4th.pdf`
- Chapter 4 covers exact factorization over ℚ end-to-end:
  - §4.5 — square-free decomposition + Mignotte bound
  - §4.6 — Hensel lifting (linear and quadratic)
  - §4.7 — Berlekamp + Cantor-Zassenhaus
  - §4.8 — recombination (naive subset; van Hoeij sketched)
- The cleanest "implementor's reference" for the full pipeline.
  When the original papers are dense (Berlekamp's BSTJ exposition,
  van Hoeij's lattice setup), CLO §4 is what we read.

**Geddes-Czapor-Labahn (1992)** — *Algorithms for Computer Algebra*,
Kluwer.

- TIB-Hannover access:
  <https://link.springer.com/book/10.1007/b102438>
- Chapter 8 covers polynomial factorization with implementer-
  friendly pseudocode; complementary to CLO.

## Cross-validation oracles

Per ADR-0019 (`docs/adr/0019-solve-bench-discipline.md`), goldens
are admitted iff ≥ 2 oracles agree.

- **Wolfram `FactorList[f, x]`** — primary oracle. Returns the list
  `{{c, e}, {p_1, e_1}, …}` with `c` the constant prefactor and the
  `p_i` irreducible over ℚ. Activated Wolfram kernel under TIB
  Hannover VPN.
  Documentation:
  <https://reference.wolfram.com/language/ref/FactorList.html>
- **SymPy `Poly(f, x).factor_list()`** — secondary oracle. Returns
  `(c, [(p_i, e_i)])` with `c` rational and `p_i` ∈ `Poly`,
  irreducible over the working domain (we use `domain="QQ"`).
  Documentation:
  <https://docs.sympy.org/latest/modules/polys/reference.html#sympy.polys.polytools.factor_list>
- **SymPy `Poly.is_irreducible`** — used by the verifier as the
  ground-truth oracle for irreducibility (delegated; we don't ship
  our own irreducibility check at the bench level).
- **SageMath `R(f).factor()`** *(when SageMath available)* —
  tertiary oracle. Sage's factorization is built on FLINT, which
  is independent of SymPy's (Python-native) and Wolfram's (closed-
  source) implementations — the cleanest third witness.

The agreement layer at `bench/_corpus/oracle/agreement.py` already
implements `kind="factor-list"` (canonical-form sorted comparison
with sign / content normalisation). See `_agree_factor_list` and
its tests.

## Theoretical context

- **Gauss's lemma** — content × primitive part decomposition is
  unique; `f ∈ ℚ[x]` factors into primitives in `ℤ[x]` iff its
  content/primitive split factors. This is what justifies "lift
  the input to ℤ[x] and factor there" as a complete reduction.
- **Sylvester's resultant** — irreducibility test fallback; if
  `Res(f, f') = 0` then `f` has a square factor (caught earlier
  by Yun anyway). Not used in the v1 algorithm, but classical
  context.
- **LLL (Lenstra-Lenstra-Lovász 1982)** — lattice basis reduction.
  The substrate van Hoeij relies on. We do not ship our own LLL
  for v1; the substrate `packages/poly-factor` will need a small
  pure-TS LLL (typically 200-300 LOC; standard textbook).

## Reference implementations consulted (none included in repo)

- **FLINT** `nmod_poly_factor`, `fmpz_poly_factor` —
  production C implementation. License: LGPL.
  Source: <https://github.com/flintlib/flint/blob/main/src/fmpz_poly_factor/factor.c>
- **SymPy** `sympy.polys.factortools` — Python-native; the
  algorithm reference for the v1 implementation. License: BSD.
  See `sympy/polys/factortools.py:dup_zz_factor`.
- **PARI/GP** `factor()` — well-engineered C; uses van Hoeij +
  Bertrand for univariate ℤ[x]. License: GPL.
- **Maple** `factor()` — proprietary; benchmark target only.

For the v1 implementation we read CLO §4 + Berlekamp 1967 +
van Hoeij 2002 directly and re-implement; cross-validation against
SymPy/Wolfram happens at the bench-oracle layer, not the source-code
layer.

## Closed-form / structural verification

For Tier-A through Tier-G cases, the verifier computes
`prod_i factor_i^{e_i}` exactly and compares coefficient-vectors.
For Tier-D Swinnerton-Dyer cases, the bench *additionally* checks
that the input polynomial is the minimal polynomial of the named
algebraic sum — a structural witness independent of the candidate's
factorization output. (If the candidate factored a Swinnerton-Dyer
polynomial as anything other than itself with multiplicity 1, the
candidate is wrong; this check is implicit in
`each_factor_irreducible` but stated for the test-case-design
record.)

## Acquisition note (for future re-staging)

Three Elsevier papers (Zassenhaus 1969, Strzebonski 1997,
Strzebonski 2012) are bot-walled at ScienceDirect headlessly; the
Mignotte 1974 paper is Cloudflare-walled at AMS even via Wayback.
The Strzebonski 2012 paper is now staged (manual fetch from
TIB-VPN browser, see `docs/ground-truth/solve-disp/strzebonski-
2012-cylindrical-algebraic-formulas.pdf`); Zassenhaus, Strzebonski
1997, and Mignotte remain as open re-acquisition tasks.

The textbook treatment in CLO §4 + Geddes Ch. 8 is sufficient for
v1 implementation. Re-acquiring the originals is a "nice to have"
and tracked in the per-phase `MISSING.md` files.
