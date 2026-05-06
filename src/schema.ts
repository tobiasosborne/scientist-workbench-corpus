// =============================================================================
// src/schema.ts — TypeScript types mirroring the JSON Schema files.
// =============================================================================
//
// JSON Schema is the canonical contract (consumable by Python, Julia, etc.).
// These TS types are derived for the corpus's own TS code.  Keep them aligned
// when the schemas change; the validator runs JSON Schema, so drift between
// these types and the schema files manifests at type-check or runtime.

export type System =
  | "mathematica" | "matlab" | "macsyma" | "maple" | "reduce"
  | "sympy" | "maxima" | "octave" | "scientist-workbench"
  | string; // open enum — additional systems allowed without schema bump

export type EvidenceKind = "manual_quote" | "oracle_run" | "both";
export type SourceKind   = "manual" | "blog" | "scrape" | "paper" | "user" | "oracle" | "code";
export type VerificationMethod =
  | "raw" | "manual_quote" | "numerical" | "oracle_run"
  | "cross_system_consensus" | "formal";
export type MappingStatus =
  | "unmapped" | "planned" | "implemented" | "partial" | "out-of-scope";
export type VerifierKind =
  | "bun" | "python3" | "wolframscript" | "octave"
  | "Rscript" | "julia" | "sage" | "gap" | "custom";

export interface Capability {
  meta: {
    system: System;
    version: string;
    name: string;
    section?: string;
    ingested_at?: string;
    ingested_by?: string;
  };
  signature: {
    input: string;
    output: string;
    arity?: number;
  };
  description: { md: string };
  category: { primary: string; secondary?: string[] };
  examples?: Array<{
    label: string;
    input_repr: string;
    expected_output_repr?: string;
    evidence_kind: EvidenceKind;
    manual_section?: string;
    oracle?: string;
    oracle_version?: string;
    oracle_invocation?: string;
  }>;
  benchmark?: {
    suite: string;
    n_cases?: number;
    n_invariants_per_case?: number;
  };
  provenance?: Array<{
    field_path: string;
    value: string;
    source_url: string;
    source_kind: SourceKind;
    retrieved_at?: string;
    local_path?: string;
    page?: number;
    sha256?: string;
    conflict?: boolean;
  }>;
  verification?: {
    level: 0 | 1 | 2 | 3 | 4;
    method: VerificationMethod;
    oracle?: string;
    oracle_version?: string;
    last_verified_at?: string;
  };
  mapping?: Array<{
    target: string;
    tool: string;
    status: MappingStatus;
    notes?: string;
  }>;
}

export interface BenchmarkSuite {
  meta: {
    name: string;
    domain: string;
    description?: string;
    ported_from?: string;
  };
  verifier: {
    kind?: VerifierKind;
    cmd: string;
    args: string[];
    env?: Record<string, string>;
    checks: Array<{
      name: string;
      description: string;
      tolerance_source?: string;
    }>;
  };
  golden: {
    inputs: string;
    inputs_sha256?: string;
    expected: string;
    expected_sha256?: string;
    n_cases: number;
    regenerated_at?: string;
    generate_cmd?: string;
  };
}

export interface AdapterDoc {
  adapter: {
    target: string;
    capability_id: string;
    cmd: string;
    args: string[];
    cwd?: string;
    env?: Record<string, string>;
    version?: string;
    platform_pinned?: boolean;
  };
}

// One row of inputs.json's `cases` field — tournament-protocol case shape.
export interface BenchCase {
  id: string;
  input: unknown;
  tags?: string[];
}

// Verifier output shape (per-case).
export interface VerifierVerdict {
  pass: boolean;
  reason: string;
  checks: Record<string, { pass: boolean; detail: string }>;
}
