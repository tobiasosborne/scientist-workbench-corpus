#!/usr/bin/env bun
// =============================================================================
// scripts/fix_wolfram_v2_arity.ts — surgical arity correction.
// =============================================================================
//
// Targeted fix for bead scientist-workbench-corpus-3pu: the wolfram-v1
// ingestor (`ingest_wolfram_v1.ts`) hard-coded `arity = 1` for every PDF it
// processed. BesselJ[n,z] / BesselY[n,z] / BesselI[n,z] / BesselK[n,z] are
// arity 2, not 1, and the wrong value will propagate into B16's mappings to
// `tools/special-eval --head=BesselJ`.
//
// This script:
//   1. For each NAME in TARGETS, reads the existing TOML in place.
//   2. Replaces the `arity  = <N>` line in `[signature]` with the correct
//      value (NEW_ARITY).
//   3. Appends a NEW `[[provenance]]` row recording the correction with
//      today's date and the rationale string. The original auto-provenance
//      row is preserved unchanged — historical record stands per CLAUDE.md
//      Rule 6 (provenance is append-only).
//
// Scope discipline: this script is intentionally narrow. The broader survey
// (~405 TOMLs have wrong arity) is tracked in a follow-up bead — see the
// 3pu bead notes for the rationale and the survey scope.
//
// The ingestor itself was also patched to derive arity from
// `signature.input` going forward, so this fix won't be needed for the
// Bessel family on the next re-ingest.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..");
const CAPS = join(ROOT, "capabilities/wolfram-v2");

// (name, true_arity). Bessel family — all four are Bessel<X>[n, z], arity 2.
const TARGETS: Array<[string, number]> = [
  ["BesselJ", 2],
  ["BesselY", 2],
  ["BesselI", 2],
  ["BesselK", 2],
];

const TODAY = new Date().toISOString().slice(0, 10);
const RATIONALE =
  "Arity corrected from 1 to true value derived from the PDF signature. " +
  "Ingestor scripts/ingest_wolfram_v1.ts pre-2026-05-23 hard-coded arity=1 " +
  "for every entry; see bead scientist-workbench-corpus-3pu.";

function fixOne(name: string, newArity: number): boolean {
  const path = join(CAPS, `${name}.toml`);
  if (!existsSync(path)) {
    process.stderr.write(`  SKIP ${name}: no TOML at ${path}\n`);
    return false;
  }
  const original = readFileSync(path, "utf8");

  // Replace the `arity  = N` line inside [signature]. The ingestor's
  // emitter aligns this with two spaces ("arity  = ").
  const arityRe = /^arity\s*=\s*\d+\s*$/m;
  if (!arityRe.test(original)) {
    process.stderr.write(`  SKIP ${name}: no 'arity = N' line to replace\n`);
    return false;
  }
  let patched = original.replace(arityRe, `arity  = ${newArity}`);

  // Idempotency: if a prior run wrote a [[provenance]] row pointing at this
  // bead, strip it before re-emitting. The block starts at "[[provenance]]"
  // and ends at the next blank line or the next "[" header. This keeps the
  // script safe to re-run if the row's shape needs another tweak.
  const beadRowRe = /\n\[\[provenance\]\][^[]*?source_url\s*=\s*"bead:scientist-workbench-corpus-3pu"[^[]*?(?=\n\[|\n*$)/g;
  patched = patched.replace(beadRowRe, "");

  // Append a new [[provenance]] row recording the correction. Place it at
  // end of file (after any existing provenance + verification + mapping
  // blocks) to avoid disturbing line offsets the auto-provenance row sits at.
  //
  // Schema constraints (capability.schema.json):
  //   - source_kind ∈ {manual, blog, scrape, paper, user, oracle, code}.
  //     "manual" is the right slot for a hand-applied correction.
  //   - no `notes` field exists; the correction rationale is folded into
  //     `value` (which is a free-form string per schema) using a
  //     "<new-value>; rationale: …" convention so machines can still parse
  //     the leading integer if needed.
  const valueWithRationale = `${newArity}; rationale: ${RATIONALE}`;
  const provRow = [
    "",
    "[[provenance]]",
    `field_path   = "signature.arity"`,
    `value        = ${JSON.stringify(valueWithRationale)}`,
    `source_url   = "bead:scientist-workbench-corpus-3pu"`,
    `source_kind  = "manual"`,
    `retrieved_at = "${TODAY}"`,
    "",
  ].join("\n");

  // Trim trailing whitespace, then append; keeps the file tidy.
  patched = patched.replace(/\s*$/, "") + "\n" + provRow;
  writeFileSync(path, patched);
  process.stdout.write(`  fixed ${name}: arity 1 → ${newArity} (+1 provenance row)\n`);
  return true;
}

let fixed = 0;
for (const [name, arity] of TARGETS) {
  if (fixOne(name, arity)) fixed++;
}
process.stdout.write(`\ndone. ${fixed}/${TARGETS.length} TOMLs patched.\n`);
