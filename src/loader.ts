// =============================================================================
// src/loader.ts — read TOML files into typed records.
// =============================================================================
//
// The corpus tree is the source of truth; the DuckDB build emits a flat
// queryable view.  This module is the boundary: TOML on disk → typed records
// in memory.  No schema validation happens here (that's `validator.ts`); the
// loader trusts that `validator.ts` ran first.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { parse as parseToml } from "smol-toml";
import type { AdapterDoc, BenchmarkSuite, Capability } from "./schema.ts";

export const ROOT = process.env["CORPUS_ROOT"] ?? join(import.meta.dir, "..");

function walk(dir: string, suffix: string): string[] {
  const out: string[] = [];
  let entries: string[];
  try { entries = readdirSync(dir); }
  catch { return out; }
  for (const name of entries) {
    const path = join(dir, name);
    const st = statSync(path);
    if (st.isDirectory()) out.push(...walk(path, suffix));
    else if (name.endsWith(suffix)) out.push(path);
  }
  return out;
}

function readToml<T>(path: string): T {
  const txt = readFileSync(path, "utf8");
  return parseToml(txt) as T;
}

export interface LoadedCapability {
  path: string;          // absolute path
  rel: string;           // relative to ROOT, posix-form-ish
  id: string;            // <system>-<version>/<name>, derived from meta
  doc: Capability;
}

export interface LoadedSuite {
  path: string;
  rel: string;
  id: string;            // suite name (= meta.name)
  doc: BenchmarkSuite;
}

export interface LoadedAdapter {
  path: string;
  rel: string;
  id: string;            // <target>/<capability_id>
  doc: AdapterDoc;
}

export function loadCapabilities(root: string = ROOT): LoadedCapability[] {
  const paths = walk(join(root, "capabilities"), ".toml");
  return paths.map((p) => {
    const doc = readToml<Capability>(p);
    const id = `${doc.meta.system}-${doc.meta.version}/${doc.meta.name}`;
    return { path: p, rel: relative(root, p).split(sep).join("/"), id, doc };
  });
}

export function loadSuites(root: string = ROOT): LoadedSuite[] {
  const paths = walk(join(root, "benchmarks"), "manifest.toml");
  return paths.map((p) => {
    const doc = readToml<BenchmarkSuite>(p);
    return { path: p, rel: relative(root, p).split(sep).join("/"), id: doc.meta.name, doc };
  });
}

export function loadAdapters(root: string = ROOT): LoadedAdapter[] {
  const paths = walk(join(root, "adapters"), ".toml");
  return paths.map((p) => {
    const doc = readToml<AdapterDoc>(p);
    return {
      path: p,
      rel: relative(root, p).split(sep).join("/"),
      id: `${doc.adapter.target}/${doc.adapter.capability_id}`,
      doc,
    };
  });
}
