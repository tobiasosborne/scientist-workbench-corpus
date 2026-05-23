// =============================================================================
// adapters/boost/oracles/besselj-anchor-replay.ts — boost replay shim
// =============================================================================
//
// Pre-computed-oracle pattern (see wolfram/besselj-anchor-replay.ts for the
// shared notes).  Boost silver variant (cpp_bin_float<50>): 1578 / 1766
// success.  188 refusals — Boost has no std::complex Bessel specialisation,
// so all 128 T5 complex inputs are honest-refused, plus ~60 large-ν /
// overflow-boundary T7/T10 cases.

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
  value_bronze?: string;
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
      message: `no committed boost record for (head=${raw.head}, nu=${raw.nu}, z=${JSON.stringify(raw.z)})`,
    }) + "\n");
    return;
  }

  if (rec.output === null) {
    process.stdout.write(JSON.stringify({
      kind: "tagged",
      tag: "oracle/boost-refused",
      payload: {
        reason: rec.failure_reason ?? rec.note ?? "boost declined to evaluate (no std::complex Bessel in 1_83 or overflow boundary)",
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
