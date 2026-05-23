// =============================================================================
// adapters/wolfram/oracles/besselj-anchor-replay.ts — wolfram replay shim
// =============================================================================
//
// Pre-computed-oracle pattern (B18 erf-anchor blueprint): the workbench ran
// `wolframscript` once against the 1766-case besselj-anchor corpus (bead
// scientist-workbench-z9fq, 2026-05-17) and the resulting per-record
// outputs were normalised + id-enriched into
// `adapters/wolfram/data/besselj-anchor-results.json` by the B19 build
// script.  This shim reads the committed snapshot and emits the recorded
// value for the (head, nu, z) tuple the runner asks about.
//
// Wire contract (mirrors the candidate adapter):
//   stdin:  { "head": "BesselJ"|"BesselY"|"BesselI"|"BesselK"|
//                     "BesselIScaled"|"BesselKScaled",
//             "nu":   "<decimal>" | "<a/b>",
//             "z":    "<60-char>" | {"re":"<>","im":"<>"} }
//
//   stdout (success):
//             { "value":             "<decimal>" | {"re":"<>","im":"<>"},
//               "method":            "wolfram-N-at-60-decimal",
//               "achieved_precision": 60,
//               "warnings":          [] }
//   stdout (oracle limit/refused):
//             { "kind":"tagged", "tag":"oracle/wolfram-refused", "payload":{...} }
//
// 45 cases hit Wolfram's "limit" status (unevaluated symbolic returns like
// `BesselI[0, Infinity]`) — the build-script normaliser collapses those
// into output=null + method=wolfram-limit; the shim surfaces them as
// `oracle/wolfram-refused` tagged envelopes.

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
      message: `no committed wolfram record for (head=${raw.head}, nu=${raw.nu}, z=${JSON.stringify(raw.z)})`,
    }) + "\n");
    return;
  }

  if (rec.output === null) {
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
