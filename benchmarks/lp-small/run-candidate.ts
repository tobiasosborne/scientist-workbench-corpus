// =============================================================================
// benchmarks/lp-small/run-candidate.ts — re-export of the lp-netlib bridge.
// =============================================================================
//
// Both LP suites share the same canonical SCS wire format (ADR-0030 §C),
// so the bridge implementation lives in lp-netlib/run-candidate.ts as the
// canonical source.  This file exists so the adapter TOML's
// `${SUITE_ROOT}/run-candidate.ts` reference resolves to a real script
// in this suite's directory.
//
// The lp-netlib bridge is a top-level-await script with no exports; re-using
// it via dynamic import re-executes the whole script (which is what we want
// — same stdin/stdout contract end-to-end).

await import("../lp-netlib/run-candidate.ts");
