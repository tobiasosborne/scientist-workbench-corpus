// =============================================================================
// adapters/arb/oracles/besselj-anchor-replay.ts — arb replay shim
// =============================================================================
//
// Pre-computed-oracle pattern (see wolfram/besselj-anchor-replay.ts for the
// shared notes).  arb (python-flint / FLINT 3.x) variant — the third gold
// oracle alongside wolfram + mpmath per ADR-0041 §"Decision 8".  1718 /
// 1766 success (48 refused on non-finite z, all in T6 edge tier).
//
// This is the FIRST adapter under adapters/arb/ in the corpus — B19
// introduces a new candidate target string (`arb`) into the corpus
// DuckDB.  The directory layout matches the other 4 oracles' replay
// shims structurally; the adapter.toml at adapters/arb/besselj-anchor.toml
// declares the target name.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

interface OracleRecord {
  input_id: string;
  head:    string;
  nu:      string;
  z:       string | { re: string; im: string };
  output:  string | { re: string; im: string } | null;
  method:  string;
  achieved_precision: number;
  status:  string;
  failure_reason?: string;
  note?: string;
}

interface OracleFile {
  oracle_id: string;
  oracle_version: string;
  results: OracleRecord[];
}

const DATA_PATH = resolve(import.meta.dir, "..", "data", "besselj-anchor-results.json");

let _idx: Map<string, OracleRecord> | null = null;

function keyOf(head: string, nu: string, z: string | { re: string; im: string }): string {
  if (typeof z === "string") return `${head}|${nu}|R|${z}`;
  return `${head}|${nu}|C|${z.re}|${z.im}`;
}

function loadIndex(): Map<string, OracleRecord> {
  if (_idx !== null) return _idx;
  const file = JSON.parse(readFileSync(DATA_PATH, "utf8")) as OracleFile;
  _idx = new Map();
  for (const r of file.results) {
    _idx.set(keyOf(r.head, r.nu, r.z), r);
  }
  return _idx;
}

async function main(): Promise<void> {
  const raw = JSON.parse(readFileSync(0, "utf8")) as {
    head: string;
    nu: string;
    z: string | { re: string; im: string };
  };

  const idx = loadIndex();
  const rec = idx.get(keyOf(raw.head, raw.nu, raw.z));

  if (!rec) {
    process.stdout.write(JSON.stringify({
      kind: "tool_error",
      name: "OracleReplayMiss",
      message: `no committed arb record for (head=${raw.head}, nu=${raw.nu}, z=${JSON.stringify(raw.z)})`,
    }) + "\n");
    return;
  }

  if (rec.output === null) {
    process.stdout.write(JSON.stringify({
      kind: "tagged",
      tag: "oracle/arb-refused",
      payload: {
        reason: rec.failure_reason ?? rec.note ?? "arb declined to evaluate (non-finite or singular)",
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
