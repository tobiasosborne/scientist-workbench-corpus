"""Reference implementation: Bareiss one-step fraction-free Gaussian
elimination over Q.

This is the algorithm the bench tests against. The candidate
implementation in TypeScript should produce the same outputs (modulo
representation: parametric forms can differ structurally as long as
the affine span is identical).

Reference: Bareiss 1968, Math. Comp. 22(103), §II.A.2 Eq. (8) on
p. 569. Local PDF: ../docs/ground-truth/linear/bareiss-1968-mathcomp.pdf

The implementation uses `fractions.Fraction` for exact rationals.
Pure Python — slow, but correct, and fast enough for the bench's
50-case 100×100-max workload (~10s for the full bench at most).
"""
from __future__ import annotations

import json
import re
import sys
from fractions import Fraction
from typing import Any, Dict, List, Tuple


def parse_rat(s: str) -> Fraction:
    """Strict rational-string parse: '3/4', '-7', '0', '1/2'."""
    s = s.strip()
    if not re.match(r"^-?(\d+|\d+/\d+)$", s):
        raise ValueError(f"not a canonical rational string: {s!r}")
    return Fraction(s)


def fmt_rat(r: Fraction) -> str:
    """Canonical-form output: '3/4', '-7', '0' (no trailing /1)."""
    if r.denominator == 1:
        return str(r.numerator)
    return f"{r.numerator}/{r.denominator}"


def bareiss_solve(
    A: List[List[Fraction]], b: List[Fraction]
) -> Dict[str, Any]:
    """Solve A·x = b exactly over Q via Bareiss one-step elimination.

    Input: m×n rational matrix A, length-m rational vector b.
    Output dict: see PROMPT.md "Output (one JSON object on stdout)".
    """
    m = len(A)
    n = len(A[0]) if m > 0 else 0

    # ---- Boundary cases ----
    if m == 0:
        # 0×n matrix: every x ∈ Q^n is a solution.
        if n == 0:
            return {
                "kind": "unique",
                "x": [],
                "free_vars": [],
                "rank": 0,
                "method": "bareiss-one-step",
                "warnings": [],
            }
        return {
            "kind": "under-determined",
            "x": [f"t_{j}" for j in range(n)],
            "free_vars": [f"t_{j}" for j in range(n)],
            "rank": 0,
            "method": "bareiss-one-step",
            "warnings": [],
        }
    if n == 0:
        # m×0 matrix: solvable iff b == 0 (every row).
        if all(bi == 0 for bi in b):
            return {
                "kind": "unique",
                "x": [],
                "free_vars": [],
                "rank": 0,
                "method": "bareiss-one-step",
                "warnings": [],
            }
        rank_aug = sum(1 for bi in b if bi != 0)
        return {
            "kind": "inconsistent",
            "rank": 0,
            "augmented_rank": min(rank_aug, m),
            "method": "bareiss-one-step",
            "warnings": [],
        }

    # ---- Augmented matrix M = [A | b], pivot tracking ----
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    pivot_col_for_row: List[int] = []  # which column each pivot row's pivot is in
    free_cols: List[int] = []          # columns with no pivot ⇒ free variables

    prev_pivot = Fraction(1)  # a_00^(-1) = 1 sentinel

    pivot_row = 0  # next row to install a pivot in
    max_bit_length = 0

    for col in range(n):
        # Find a non-zero pivot at or below pivot_row in this column.
        nz = None
        for i in range(pivot_row, m):
            if M[i][col] != 0:
                nz = i
                break
        if nz is None:
            free_cols.append(col)
            continue
        # Swap into pivot_row.
        if nz != pivot_row:
            M[pivot_row], M[nz] = M[nz], M[pivot_row]

        # Bareiss reduction for rows below pivot_row.
        Mkk = M[pivot_row][col]
        for i in range(m):
            if i == pivot_row:
                continue
            Mik = M[i][col]
            if Mik == 0:
                continue
            for j in range(n + 1):
                # Exact integer-preserving update; division by prev_pivot is
                # exact (Sylvester's identity).
                new = (Mkk * M[i][j] - Mik * M[pivot_row][j]) / prev_pivot
                M[i][j] = new
                # Track largest bit length for the Tier-H budget check.
                bl = max(_bit_length(new.numerator), _bit_length(new.denominator))
                if bl > max_bit_length:
                    max_bit_length = bl
            M[i][col] = Fraction(0)
        prev_pivot = Mkk
        pivot_col_for_row.append(col)
        pivot_row += 1
        if pivot_row == m:
            break

    rank = pivot_row
    # Any remaining columns (from pivot_row's last value onward) are free.
    free_cols.extend(c for c in range(n) if c not in set(pivot_col_for_row))
    # De-dup + sort
    free_cols = sorted(set(free_cols))

    # ---- Inconsistency check: any row [0, …, 0 | nonzero]? ----
    for i in range(rank, m):
        # rows beyond the pivot rows must have all-zero coefficient parts.
        # row i column n is the augmented entry.
        if any(M[i][j] != 0 for j in range(n)):
            # Safety: shouldn't happen with the elimination above; an
            # all-zero row in A-part is what marks rank-deficiency.
            continue
        if M[i][n] != 0:
            return {
                "kind": "inconsistent",
                "rank": rank,
                "augmented_rank": rank + 1,
                "method": "bareiss-one-step",
                "warnings": [
                    f"max intermediate bit length: {max_bit_length}"
                ],
            }

    # ---- Build solution ----
    if not free_cols:
        # Unique solution. Back-substitute.
        x = [Fraction(0)] * n
        for r in range(rank - 1, -1, -1):
            col = pivot_col_for_row[r]
            s = M[r][n]
            for j in range(col + 1, n):
                s -= M[r][j] * x[j]
            x[col] = s / M[r][col]
        return {
            "kind": "unique",
            "x": [fmt_rat(xi) for xi in x],
            "free_vars": [],
            "rank": rank,
            "method": "bareiss-one-step",
            "warnings": [
                f"max intermediate bit length: {max_bit_length}"
            ],
        }

    # ---- Under-determined: parametric form ----
    # Each free column c gets a symbol "t_<index>"; index runs over the
    # POSITIONS in `free_cols` (not the column index itself).
    free_syms = [f"t_{i}" for i in range(len(free_cols))]
    free_col_to_sym = dict(zip(free_cols, free_syms))

    # x[j] is either a free symbol (if j ∈ free_cols) or a linear
    # combination of free symbols + constant (if j is a pivot column).
    # We represent each x[j] as a dict: {"<sym>": <coeff>, "_const": <const>}
    x: List[Dict[str, Fraction]] = [{} for _ in range(n)]
    for c, sym in free_col_to_sym.items():
        x[c] = {sym: Fraction(1), "_const": Fraction(0)}

    for r in range(rank - 1, -1, -1):
        col = pivot_col_for_row[r]
        # Build x[col] = (M[r][n] - Σ_{j > col} M[r][j] * x[j]) / M[r][col]
        accum: Dict[str, Fraction] = {"_const": M[r][n]}
        for j in range(col + 1, n):
            coeff = M[r][j]
            if coeff == 0:
                continue
            for k, v in x[j].items():
                accum[k] = accum.get(k, Fraction(0)) - coeff * v
        # Divide by pivot.
        pivot = M[r][col]
        x[col] = {k: v / pivot for k, v in accum.items() if v != 0 or k == "_const"}

    return {
        "kind": "under-determined",
        "x": [_format_affine(x[j]) for j in range(n)],
        "free_vars": free_syms,
        "rank": rank,
        "method": "bareiss-one-step",
        "warnings": [
            f"max intermediate bit length: {max_bit_length}"
        ],
    }


def _format_affine(terms: Dict[str, Fraction]) -> str:
    """Format `{'_const': c, 't_0': a, 't_1': b}` as 'c + a*t_0 + b*t_1'."""
    parts: List[str] = []
    const = terms.get("_const", Fraction(0))
    if const != 0:
        parts.append(fmt_rat(const))
    for sym, coeff in terms.items():
        if sym == "_const":
            continue
        if coeff == 0:
            continue
        if coeff == 1:
            parts.append(sym if not parts else f"+ {sym}")
        elif coeff == -1:
            parts.append(f"-{sym}" if not parts else f"- {sym}")
        else:
            sign = "" if not parts else (" + " if coeff > 0 else " - ")
            mag = fmt_rat(abs(coeff))
            parts.append(f"{sign}{mag}*{sym}" if parts else f"{fmt_rat(coeff)}*{sym}")
    if not parts:
        return "0"
    return "".join(parts) if parts[0].startswith("-") or len(parts) == 1 else \
        " ".join(parts).replace("  ", " ").replace(" + -", " - ")


def _bit_length(n: int) -> int:
    return n.bit_length()


# ---------------------------------------------------------------------
# stdin/stdout adapter for the bench harness
# ---------------------------------------------------------------------


def main() -> int:
    """Read input JSON on stdin, write output JSON on stdout."""
    raw = json.loads(sys.stdin.read())
    A = [[parse_rat(s) for s in row] for row in raw["A"]]
    b = [parse_rat(s) for s in raw["b"]]
    out = bareiss_solve(A, b)
    sys.stdout.write(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
