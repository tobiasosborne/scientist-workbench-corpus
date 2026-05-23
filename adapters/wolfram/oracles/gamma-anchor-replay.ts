// =============================================================================
// adapters/wolfram/oracles/gamma-anchor-replay.ts -- wolfram replay shim
// =============================================================================
//
// Pre-computed-oracle pattern (G6 / B19 / B18 blueprint).  Reads the
// id-enriched results.json snapshot under
// adapters/wolfram/data/gamma-anchor-results.json and emits the
// recorded value for the case id the runner asks about.
//
// Wire contract (mirrors the candidate adapter):
//   stdin:  { "head": "...", "z?": ..., "a?": ..., ..., "id": "<case id>" }
//   stdout (success):
//     { "value": ..., "method": "wolfram-N-at-60-decimal",
//       "achieved_precision": 60, "warnings": [] }
//   stdout (refusal):
//     { "kind":"tagged", "tag":"oracle/wolfram-refused", "payload":{...} }
//
// 8 refusals (L17 exact poles) -- normalised to output=null in the
// build script; this shim emits them as oracle/wolfram-refused.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

interface OracleRecord {
  input_id: string;
  id?: string;
  head: string;
  output: string | { re: string; im: string } | null;
  method: string;
  achieved_precision: number;
  status: string;
  failure_reason?: string;
  wolfram_returned_token?: string | null;
}

interface OracleFile {
  oracle_id: string;
  oracle_version: string;
  results: OracleRecord[];
}

const DATA_PATH = resolve(import.meta.dir, "..", "data", "gamma-anchor-results.json");

let _idx: Map<string, OracleRecord> | null = null;

function loadIndex(): Map<string, OracleRecord> {
  if (_idx !== null) return _idx;
  const file = JSON.parse(readFileSync(DATA_PATH, "utf8")) as OracleFile;
  _idx = new Map();
  for (const r of file.results) {
    _idx.set(r.input_id, r);
  }
  return _idx;
}

async function main(): Promise<void> {
  const raw = JSON.parse(readFileSync(0, "utf8")) as { id?: string; head: string };
  const id = raw.id;
  if (!id) {
    process.stdout.write(JSON.stringify({
      kind: "tool_error",
      name: "OracleReplayNoId",
      message: "wolfram replay shim requires `input.id` (case id) -- check corpus generator",
    }) + "\n");
    return;
  }

  const idx = loadIndex();
  const rec = idx.get(id);

  if (!rec) {
    process.stdout.write(JSON.stringify({
      kind: "tool_error",
      name: "OracleReplayMiss",
      message: `no committed wolfram record for case id='${id}'`,
    }) + "\n");
    return;
  }

  if (rec.output === null) {
    process.stdout.write(JSON.stringify({
      kind: "tagged",
      tag: "oracle/wolfram-refused",
      payload: {
        reason: rec.failure_reason ?? rec.wolfram_returned_token ?? "wolfram declined to evaluate",
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
