// =============================================================================
// adapters/mpmath/oracles/gamma-anchor-replay.ts -- mpmath replay shim
// =============================================================================
//
// Reads adapters/mpmath/data/gamma-anchor-results.json, emits the
// recorded value for the case id the runner asks about.  20
// refusals split: 12 InverseIncompleteGamma{P,Q} unsupported (L13);
// 8 L17 exact-pole refusals.

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
  mpmath_returned_token?: string | null;
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
      message: "mpmath replay shim requires `input.id` (case id)",
    }) + "\n");
    return;
  }

  const idx = loadIndex();
  const rec = idx.get(id);

  if (!rec) {
    process.stdout.write(JSON.stringify({
      kind: "tool_error",
      name: "OracleReplayMiss",
      message: `no committed mpmath record for case id='${id}'`,
    }) + "\n");
    return;
  }

  if (rec.output === null) {
    const tag = rec.status === "unsupported"
      ? "oracle/mpmath-unsupported"
      : "oracle/mpmath-refused";
    process.stdout.write(JSON.stringify({
      kind: "tagged",
      tag,
      payload: {
        reason: rec.failure_reason ?? rec.mpmath_returned_token ?? "mpmath declined to evaluate",
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
