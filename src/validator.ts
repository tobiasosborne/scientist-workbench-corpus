// =============================================================================
// src/validator.ts — JSON-Schema validate every TOML; fail loud.
// =============================================================================
//
// Run before `build` and `grade`.  No silent drops — every TOML must conform
// or the whole pipeline halts with the offending path + the validator's
// error trail.  Anti-pattern guarded against: fundingscape's silent ETL
// drops where 401K abstracts disappeared undetected for months.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import Ajv from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { loadAdapters, loadCapabilities, loadSuites, ROOT } from "./loader.ts";

interface Issue { path: string; errors: string[] }

function loadSchema(name: string): object {
  return JSON.parse(readFileSync(join(ROOT, "schema", name), "utf8"));
}

export function validateAll(): { ok: boolean; issues: Issue[]; counts: { capabilities: number; suites: number; adapters: number } } {
  const ajv = new Ajv({ allErrors: true, strict: false });
  addFormats(ajv);

  const validateCap     = ajv.compile(loadSchema("capability.schema.json"));
  const validateSuite   = ajv.compile(loadSchema("benchmark-suite.schema.json"));
  const validateAdapter = ajv.compile(loadSchema("adapter.schema.json"));

  const issues: Issue[] = [];
  const fmt = (errs: typeof validateCap.errors): string[] =>
    (errs ?? []).map((e) => `${e.instancePath || "/"} ${e.message ?? ""} ${JSON.stringify(e.params)}`);

  const caps     = loadCapabilities();
  const suites   = loadSuites();
  const adapters = loadAdapters();

  for (const c of caps)     if (!validateCap(c.doc))     issues.push({ path: c.rel,     errors: fmt(validateCap.errors)     });
  for (const s of suites)   if (!validateSuite(s.doc))   issues.push({ path: s.rel,     errors: fmt(validateSuite.errors)   });
  for (const a of adapters) if (!validateAdapter(a.doc)) issues.push({ path: a.rel,     errors: fmt(validateAdapter.errors) });

  // Cross-references: every capability.benchmark.suite path must resolve.
  const knownSuites = new Set(suites.map((s) => s.rel));
  for (const c of caps) {
    if (c.doc.benchmark?.suite && !knownSuites.has(c.doc.benchmark.suite)) {
      issues.push({ path: c.rel, errors: [`benchmark.suite "${c.doc.benchmark.suite}" does not match any benchmarks/<name>/manifest.toml`] });
    }
  }
  // Every adapter must target a capability_id that matches a known suite.
  const suiteNames = new Set(suites.map((s) => s.doc.meta.name));
  for (const a of adapters) {
    if (!suiteNames.has(a.doc.adapter.capability_id)) {
      issues.push({ path: a.rel, errors: [`adapter.capability_id "${a.doc.adapter.capability_id}" matches no benchmark suite name`] });
    }
  }
  return { ok: issues.length === 0, issues, counts: { capabilities: caps.length, suites: suites.length, adapters: adapters.length } };
}

// CLI entry
if (import.meta.main) {
  const r = validateAll();
  if (r.ok) {
    process.stdout.write(`OK — ${r.counts.capabilities} capabilities, ${r.counts.suites} suites, ${r.counts.adapters} adapters validated.\n`);
    process.exit(0);
  }
  process.stderr.write(`FAIL — ${r.issues.length} validation issue(s):\n`);
  for (const i of r.issues) {
    process.stderr.write(`  ${i.path}\n`);
    for (const e of i.errors) process.stderr.write(`    ${e}\n`);
  }
  process.exit(1);
}
