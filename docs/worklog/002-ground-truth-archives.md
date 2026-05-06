# 002 — ground-truth source archives (2026-05-06)

## Context

The capability TOMLs cite three public archives — Cleve Moler's Cleve's
Corner posts on PC-MATLAB v1.0, the Wolfram legacy v1 reference site,
and bitsavers' MIT MACSYMA RefMan V9 (Dec 1977) — but the bytes lived
only behind URLs. The CLAUDE.md mantra "URLs rot; archives don't"
made the next move obvious before any ingestion: materialise the bytes
locally, anchored to SHA-256 ledgers.

This shard covers the fetch and provenance discipline; ingestion is
shard 003.

## What changed

Three sub-trees under a new top-level `data/` directory, one per source
system, modelled on Integralis's `data/<source>/` convention (the user
flagged that pattern explicitly mid-session as superior to the
year/month structure I'd guessed at first):

```
data/
├── README.md                          top-level overview, refresh + verify recipe
├── wolfram-v1/                        Mathematica 1 (1988 software, 2nd-edition 1991 docs)
│   ├── MANIFEST.toml                  URL patterns, retrieval metadata, ledger anchor
│   └── raw/
│       ├── sha256.sum                 963 entries, OK on `sha256sum -c`
│       └── contents/                  48 section HTML + 915 per-function PDFs (~50 MB)
├── matlab-v1/                         PC-MATLAB v1.0 (1984)
│   ├── MANIFEST.toml
│   └── raw/                           2 HTMLs incl. canonical HELP listing inline (~600 KB)
└── macsyma-v9/                        MACSYMA RefMan V9 (Dec 1977)
    ├── MANIFEST.toml
    └── raw/
        └── MACSYMA_RefMan_V9_Dec77.pdf  (~14 MB, PDF v1.4)

scripts/
└── fetch_wolfram_v1.sh                idempotent re-fetcher; per-file progress lines
```

Each `MANIFEST.toml` carries provenance rows whose schema mirrors the
corpus's `[[provenance]]` shape (field_path / value / source_url /
source_kind / sha256), so a future ingestor reads the manifest and
emits provenance rows directly. The per-source `sha256.sum` is the
tamper-evidence ledger; the MANIFEST records the ledger's *own*
SHA-256 as a tier-2 anchor (flip a byte ⇒ ledger hash changes ⇒
manifest stamp changes).

Total: 64 MB on disk, under the 100 MB threshold worklog 001 pinned
for git-vs-LFS. Not yet switching to LFS.

## Why these choices

- **Integralis layout (`data/<source>/raw/`)** rather than my first
  guess of year/month subdirectories. Integralis is the closer
  analogue (per-source ingestor, per-source raw archive, DuckDB as
  compiled view); inheriting its pattern keeps the cross-repo idiom
  consistent and makes future Integralis ↔ corpus tooling moves
  straightforward.

- **Wolfram legacy `/legacy/v1/` URL, not the archive.org book PDF.**
  The user's correction. Archive.org has the 1988 first-edition book
  but it's borrowable-only (not downloadable). The Wolfram legacy
  site serves the *same canonical content* as per-function PDFs that
  *are* directly redistributable, and that's the form an ingestor can
  parse — one PDF per function, same layout, same footer.

  Found mid-session: the URL says "v1" but the PDFs carry a
  `1991 Wolfram Research` copyright. The legacy site preserves the
  Mathematica software v1 reference using the **2nd Edition (1991)**
  text. The 1st-edition (1988) text isn't online. MANIFEST records
  this honestly: `system = "mathematica"`, `version = "1"` (software),
  with the description naming the 2nd-edition as the documentation
  source.

- **Wayback Machine for Cleve's Corner posts.** Both `curl` and
  `WebFetch` get a 403 from `blogs.mathworks.com` — bot-detected.
  Wayback snapshots succeed and arguably constitute better provenance
  ("URLs rot; archives don't" applied to its first object).

- **Per-PDF rows in the MANIFEST would bloat to 900+ lines.** Two-tier
  approach instead: the MANIFEST records URL *patterns* (`list<N>.pdf`
  for per-function refs, `<section>.html` for prose) and a single
  `sha256_ledger_sha256` anchoring the ledger. The ledger has the
  900+ rows; tampering with one byte changes the ledger hash, which
  changes the MANIFEST stamp, which is git-tracked.

- **Fetch script is idempotent and lives in `scripts/`** (also
  Integralis pattern). Safe re-run via file-existence short-circuit;
  ~3 min full fetch at 0.2 s sleep between requests.

## Frictions surfaced

- **Hidden background bash extending past the foreground exit.** The
  first scrape was launched as `cmd & echo pid` — Bash's foreground
  statement returns 0 immediately, so the harness reported the task
  "completed" while the actual work was still mid-flight. Discovered
  by repeated `ls | wc -l` showing the count rising. Cleaned up via
  `TaskStop` once spotted.

- **First scrape was batched-progress (every 50 files), not verbose.**
  The user pulled the run mid-flight asking "how are you tracking
  progress?" — the honest answer was *not really*. Rewrote with
  per-file lines + `stdbuf -oL`, then armed `Monitor` to stream the
  log. Saved as feedback memory: live per-item, no batching, no
  `| tail` (which buffers).

- **Bot detection on Cleve's Corner.** Blanket 403 on scripted clients
  even with a real UA. Wayback Machine sidesteps this and records the
  archival snapshot timestamp into the URL — *better* provenance for
  this corpus's purposes than the live blog.

- **`sha256sum` ledger path discipline.** First wolfram-v1 ledger
  generated from inside `contents/` had bare filenames; verifying
  from `raw/` failed because `sha256sum -c` resolves paths relative
  to its cwd. Fixed by `awk '{printf "%s  contents/%s\n", $1, $2}'`
  so the ledger paths are relative to `raw/` and verify works from
  the consistent `raw/` cwd.

- **Wolfram legacy fetch surface size grew during exploration.** First
  pass scraped 583 PDFs (the B.8 master function index), but section
  HTMLs (1.x – 4.x, A.x, B.x) link to *additional* prose-chunk PDFs
  (e.g. `1.4.2.pdf`, `B.8.1.pdf`). Final tally: 915 PDFs + 48 HTMLs.
  Better to over-fetch than under-fetch — no PDF goes stale, and the
  ingestor can ignore non-`list<N>.pdf` entries.

## Acceptance

```sh
$ (cd data/wolfram-v1/raw && sha256sum -c sha256.sum > /dev/null) && echo OK
OK
$ (cd data/macsyma-v9/raw  && sha256sum -c sha256.sum > /dev/null) && echo OK
OK
$ (cd data/matlab-v1/raw   && sha256sum -c sha256.sum > /dev/null) && echo OK
OK
$ du -sh data/
64M     data/
$ bun src/cli.ts validate
OK — 1 caps, 1 suites, 1 adapters.        # data/ doesn't perturb the TOML graph
```

## Pointers

- `data/README.md` — top-level overview, refresh recipe.
- `data/wolfram-v1/MANIFEST.toml` — Wolfram URL patterns, edition note.
- `data/matlab-v1/MANIFEST.toml` — Cleve's Corner Wayback URLs.
- `data/macsyma-v9/MANIFEST.toml` — bitsavers single-PDF entry.
- `scripts/fetch_wolfram_v1.sh` — idempotent re-fetcher.
- Memory: `feedback_long_running_progress.md` — per-item live progress
  for any future multi-minute task.
- Memory: `data_layout.md` — the sha256-ledger tamper-evidence pattern.
