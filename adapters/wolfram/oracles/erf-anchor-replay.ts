// =============================================================================
// adapters/wolfram/oracles/erf-anchor-replay.ts — wolfram replay shim
// =============================================================================
//
// Pre-computed-oracle pattern: the workbench ran `wolframscript` once
// against the 271-case erf-anchor corpus (bead scientist-workbench-ufgd,
// 2026-05-16) and the resulting `results.json` is committed under
// `adapters/wolfram/data/erf-anchor-results.json`.  Re-running Wolfram
// at grade time would require WolframScript installed on every device;
// per recon spec (`scientist-workbench-corpus-fpn`) the corpus is a
// self-contained static artefact, so this replay shim reads the
// committed results.json and emits the recorded value for the case ID
// the runner asks about.
//
// Wire contract (mirrors the candidate adapter):
//   stdin:  { "head": "Erf"|..., "z": "<60-char>" | {"re":"<>","im":"<>"} }
//           + corpus runner passes `id` separately via env var
//           ORACLE_REPLAY_CASE_ID (set by the per-suite grade plumbing).
//
// Lookup-key resolution: the runner currently passes input on stdin but
// not the case ID.  Until grade.ts adds an `id` env var (small follow-up),
// this shim derives the lookup key from the input shape: match on
// `(head, z)` against the indexed results table.  Since the workbench
// generator emits z as IEEE-754-exact 60-char decimal strings AND we
// preserve those strings byte-for-byte in inputs.json, the (head, z)
// pair is a unique key into the results table.
//
//   stdout: { "value":             "<decimal>" | {"re":"<>","im":"<>"},
//             "method":            "wolfram-N-at-60-decimal",
//             "achieved_precision": 60,
//             "warnings":          [] }
//           OR (oracle refused):
//             { "kind":"tagged", "tag":"oracle/<class>", "payload":{...} }

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

interface OracleRecord {
  input_id?: string;
  id?: string;
  head:    string;
  z:       string | { re: string; im: string };
  output:  string | { re: string; im: string } | null;
  failure_reason?: string;
  method:  string;
  achieved_precision: number;
  note?: string;
}

interface OracleFile {
  oracle_id: string;
  oracle_version: string;
  results: OracleRecord[];
}

const DATA_PATH = resolve(import.meta.dir, "..", "data", "erf-anchor-results.json");

let _idx: Map<string, OracleRecord> | null = null;

function keyOf(head: string, z: string | { re: string; im: string }): string {
  if (typeof z === "string") return `${head}|R|${z}`;
  return `${head}|C|${z.re}|${z.im}`;
}

function loadIndex(): Map<string, OracleRecord> {
  if (_idx !== null) return _idx;
  const file = JSON.parse(readFileSync(DATA_PATH, "utf8")) as OracleFile;
  _idx = new Map();
  for (const r of file.results) {
    _idx.set(keyOf(r.head, r.z), r);
  }
  return _idx;
}

async function main(): Promise<void> {
  const raw = JSON.parse(readFileSync(0, "utf8")) as {
    head: string;
    z: string | { re: string; im: string };
  };

  const idx = loadIndex();
  const rec = idx.get(keyOf(raw.head, raw.z));

  if (!rec) {
    process.stdout.write(JSON.stringify({
      kind: "tool_error",
      name: "OracleReplayMiss",
      message: `no committed wolfram record for (head=${raw.head}, z=${JSON.stringify(raw.z)})`,
    }) + "\n");
    return;
  }

  if (rec.output === null) {
    // Committed refusal — surface as a tagged envelope.
    process.stdout.write(JSON.stringify({
      kind: "tagged",
      tag: "oracle/wolfram-refused",
      payload: {
        reason: rec.failure_reason ?? rec.note ?? "wolfram declined to evaluate",
        method: rec.method,
      },
    }) + "\n");
    return;
  }

  process.stdout.write(JSON.stringify({
    value: rec.output,
    method: rec.method,
    achieved_precision: rec.achieved_precision,
    warnings: [],
  }) + "\n");
}

await main();
