#!/usr/bin/env bash
# Fetches the complete Mathematica v1 (1988) legacy reference from
# reference.wolfram.com/legacy/v1/. Two artefacts:
#
#   data/wolfram-v2/raw/contents/<section>.html  — TOC + section pages
#                                                  (1.0..4.2, A.0..A.6, B.0..B.8)
#   data/wolfram-v2/raw/contents/<file>.pdf      — per-function reference PDFs
#                                                  (list1.pdf .. list581.pdf,
#                                                  plus B.x.<n>.pdf prose chunks)
#
# Idempotent — resumes via curl -C - and skips files that already exist.
# Polite: 200ms sleep between requests, custom UA.
#
# Usage:
#   scripts/fetch_wolfram_v2.sh
#
# Re-run is safe; only missing files are fetched.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RAW="${ROOT}/data/wolfram-v2/raw"
DEST="${RAW}/contents"
BASE="https://reference.wolfram.com/legacy/v1/contents"
UA="Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/126.0"
SLEEP="${SLEEP:-0.2}"

mkdir -p "$DEST"

fetch() {
  local rel="$1" idx="${2:-?}" total="${3:-?}"
  local url="${BASE}/${rel}"
  local out="${DEST}/${rel}"
  if [[ -s "$out" ]]; then printf '  [%s/%s] skip   %s\n' "$idx" "$total" "$rel"; return 0; fi
  if curl -sSL -A "$UA" --fail --retry 2 --max-time 30 -o "$out" "$url"; then
    printf '  [%s/%s] got    %s (%s bytes)\n' "$idx" "$total" "$rel" "$(stat -c%s "$out")"
    sleep "$SLEEP"
  else
    rm -f "$out"
    printf '  [%s/%s] MISS   %s\n' "$idx" "$total" "$rel" >&2
    return 1
  fi
}

SECTIONS=(
  1.0 1.1 1.2 1.3 1.4 1.5 1.6 1.7 1.8 1.9
  2.0 2.1 2.2 2.3 2.4 2.5 2.6 2.7 2.8
  3.0 3.1 3.2 3.3 3.4 3.5 3.6 3.7 3.8 3.9
  4.0 4.1 4.2
  A.0 A.1 A.2 A.3 A.4 A.5 A.6
  B.0 B.1 B.2 B.3 B.4 B.5 B.6 B.7 B.8
)

# --- Section HTML pages -------------------------------------------------------
echo "[1/3] section HTML pages (${#SECTIONS[@]} pages)"
i=0; n=${#SECTIONS[@]}
for section in "${SECTIONS[@]}"; do
  i=$((i+1))
  fetch "${section}.html" "$i" "$n" || true
done

# --- Build PDF queue by parsing every section HTML ----------------------------
echo "[2/3] enumerating PDFs from section HTMLs"
PDF_QUEUE="$(mktemp)"
trap 'rm -f "$PDF_QUEUE"' EXIT
for f in "$DEST"/*.html ; do
  grep -oE "href='[^']+\.pdf'" "$f" | sed -E "s/^href='//; s/'$//"
done | LC_ALL=C sort -u > "$PDF_QUEUE"
total=$(wc -l < "$PDF_QUEUE")
echo "  found ${total} PDFs"

# --- Fetch PDFs ---------------------------------------------------------------
echo "[3/3] fetching PDFs"
i=0
while IFS= read -r pdf; do
  i=$((i+1))
  fetch "$pdf" "$i" "$total" || true
done < "$PDF_QUEUE"

echo "done. $(ls "$DEST"/*.pdf 2>/dev/null | wc -l) PDFs, $(ls "$DEST"/*.html 2>/dev/null | wc -l) HTML pages."
