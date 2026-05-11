// =============================================================================
// lp-small/golden/verify.ts — re-export of the lp-netlib verifier.
// =============================================================================
//
// The two LP suites share an identical verifier surface; lp-netlib's
// `verify.ts` is the canonical source.  We import its `runVerifier`
// (ADR-0010-style: gated `if (import.meta.main)` in the canonical
// file lets the same module act as both a script and a library) and
// pass this suite's `expected.json` path explicitly.

import { resolve } from "node:path";
import { runVerifier } from "../../lp-netlib/golden/verify.ts";

await runVerifier(resolve(import.meta.dir, "expected.json"));
