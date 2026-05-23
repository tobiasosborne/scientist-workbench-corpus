# `data/` — ground-truth source archives

One sub-directory per source system, each with the public archival
form of that system's reference documentation. The Integralis
convention applies (`data/<source>/`), and per-source provenance
follows the corpus's `[[provenance]]` row shape so a future ingestor
can map manifest rows directly into capability TOMLs.

| sub-directory     | system         | archive                                       | size  | items |
|-------------------|----------------|-----------------------------------------------|-------|-------|
| `wolfram-v2/`     | mathematica 2  | reference.wolfram.com/legacy/v1/ (HTML + PDF — 2nd ed. 1991 text) | ~50MB | 48 HTML + 915 PDF |
| `matlab-v1/`      | matlab 1       | Cleve's Corner posts (Wayback Machine)        | <1MB  | 2 HTML |
| `macsyma-v9/`     | macsyma 9      | bitsavers MIT Mathlab scan                    | 14MB  | 1 PDF  |

Each sub-directory contains:

- `MANIFEST.toml` — provenance metadata (URLs, SHA-256 of the ledger,
  per-source notes). The schema mirrors the corpus's
  `[[provenance]]` rows so an ingestor reads the manifest and emits
  provenance directly.
- `raw/` — the bytes-on-disk. Never modify; ingestors are
  read-only consumers.
- `raw/sha256.sum` — sorted `sha256sum`-format ledger of every file
  in `raw/`. The MANIFEST records the ledger's own SHA-256 as a
  tamper-evidence anchor.

## Refresh / verify

Each source has a fetch script (under `scripts/fetch_<source>.sh`)
or a single `curl` documented in the MANIFEST. Re-running the fetch
is idempotent (file-exists short-circuit); verifying integrity is
`(cd raw && sha256sum -c sha256.sum)`.

## What goes in vs. doesn't

In: anything redistributable that an ingestor or human reader needs
as ground truth. The bitsavers PDF is in. The 1988 Mathematica book
PDF (archive.org borrowable) is **not** — the Wolfram legacy site
serves the 2nd edition (1991) text as per-function PDFs that *are*
redistributable, and that's the form actually fed to ingestors. The
function set tracks Mathematica software v1.x either way; the edition
choice is whichever Wolfram chose to preserve at legacy/v1/.

Not in: copyrighted PDFs that aren't on archive.org / bitsavers /
publisher pages. If a source can't be fetched from a public URL,
record a `[[provenance]]` row with `source_kind = "user"` and a
`local_path` pointing inside this tree, but populate the file out-
of-band — and document that out-of-band step in the MANIFEST.
