#!/usr/bin/env bun
// =============================================================================
// scripts/fix_wolfram_v2_arity_0gu.ts — surgical arity correction (G1 sibling).
// =============================================================================
//
// Targeted fix for bead scientist-workbench-corpus-0gu. Mirrors B7's
// fix_wolfram_v2_arity.ts (bead 3pu) pattern but scoped to two TOMLs flagged
// by drift analyses D1+D2:
//   - Beta.toml         arity 1 → 2   (Beta[x, y] is 2-argument)
//   - Pochhammer.toml   arity 1 → 2   (Pochhammer[a, n] is 2-argument)
//
// Surfaced opportunistically ahead of bead G2 (scientist-workbench-corpus-eak),
// which wires mappings to these caps; the corpus must not ship the bad arity
// alongside the new mapping. Broader 401-TOML sweep is tracked in child bead
// scientist-workbench-corpus-698.
//
// Discipline (CLAUDE.md Rule 6): provenance is append-only. The original
// auto-ingest [[provenance]] row is preserved; a NEW row with
// source_kind = "manual" records the correction with a bead: source_url.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..");
const CAPS = join(ROOT, "capabilities/wolfram-v2");

const TARGETS: Array<[string, number]> = [
  ["Beta", 2],
  ["Pochhammer", 2],
];

const TODAY = new Date().toISOString().slice(0, 10);
const BEAD = "scientist-workbench-corpus-0gu";
const RATIONALE_TEMPLATE = (name: string) =>
  `${name}[...] is arity-2 per Wolfram 2nd-ed; ingestor's computeArity bug ` +
  `fixed by sibling bead 698 sweep`;

function fixOne(name: string, newArity: number): boolean {
  const path = join(CAPS, `${name}.toml`);
  if (!existsSync(path)) {
    process.stderr.write(`  SKIP ${name}: no TOML at ${path}\n`);
    return false;
  }
  const original = readFileSync(path, "utf8");

  const arityRe = /^arity\s*=\s*\d+\s*$/m;
  if (!arityRe.test(original)) {
    process.stderr.write(`  SKIP ${name}: no 'arity = N' line to replace\n`);
    return false;
  }
  let patched = original.replace(arityRe, `arity  = ${newArity}`);

  // Idempotency: strip any prior 0gu-tagged provenance row before re-emitting.
  const beadRowRe = new RegExp(
    `\\n\\[\\[provenance\\]\\][^\\[]*?source_url\\s*=\\s*"bead:${BEAD}"[^\\[]*?(?=\\n\\[|\\n*$)`,
    "g",
  );
  patched = patched.replace(beadRowRe, "");

  const valueWithRationale = `${newArity}; rationale: ${RATIONALE_TEMPLATE(name)}`;
  const provRow = [
    "",
    "[[provenance]]",
    `field_path   = "signature.arity"`,
    `value        = ${JSON.stringify(valueWithRationale)}`,
    `source_url   = "bead:${BEAD}"`,
    `source_kind  = "manual"`,
    `retrieved_at = "${TODAY}"`,
    "",
  ].join("\n");

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
