// =============================================================================
// adapters/boost/oracles/gamma-anchor-replay.ts -- boost replay shim
// =============================================================================
//
// Reads adapters/boost/data/gamma-anchor-results.json, emits the
// recorded value for the case id the runner asks about.  82
// refusals: 71 unsupported (no complex Gamma support -- all T4
// complex; BarnesG absence per L16; Pochhammer absence) + 11
// refused at L17 poles / overflow.
//
// L18 documented: Boost digamma at negative half-integer arguments
// returns incorrect values; results.json carries them verbatim.
// The bench grader compares the candidate (not boost) against the
// gold consensus; L18 is a documented provenance flag in
// expected.json landmine_flags, no special verifier logic here.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

interface OracleRecord {
  input_id: string;
  id?: string;
  head: string;
  output: string | null;
  method: string;
  achieved_precision: number;
  status: string;
  failure_reason?: string;
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
      message: "boost replay shim requires `input.id` (case id)",
    }) + "\n");
    return;
  }

  const idx = loadIndex();
  const rec = idx.get(id);

  if (!rec) {
    process.stdout.write(JSON.stringify({
      kind: "tool_error",
      name: "OracleReplayMiss",
      message: `no committed boost record for case id='${id}'`,
    }) + "\n");
    return;
  }

  if (rec.output === null) {
    const tag = rec.status === "unsupported"
      ? "oracle/boost-unsupported"
      : "oracle/boost-refused";
    process.stdout.write(JSON.stringify({
      kind: "tagged",
      tag,
      payload: {
        reason: rec.failure_reason ?? rec.status,
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
