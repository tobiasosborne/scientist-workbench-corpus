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
// v2 schema (bead q4r) extended verification.method with 'arbprec_oracle' —
// arbitrary-precision multi-oracle anchor, between oracle_run and
// cross_system_consensus in stringency.
export type VerificationMethod =
  | "raw" | "manual_quote" | "numerical" | "oracle_run"
  | "cross_system_consensus" | "arbprec_oracle" | "formal";
export type MappingStatus =
  | "unmapped" | "planned" | "implemented" | "partial" | "out-of-scope";
export type VerifierKind =
  | "bun" | "python3" | "wolframscript" | "octave"
  | "Rscript" | "julia" | "sage" | "gap" | "custom";

// v2 schema (bead q4r) — structured scalar-domain markers for signature.
export type SignatureDomain   = "real" | "complex" | "both"  | "unspecified";
export type SignatureCodomain = "real" | "complex" | "mixed" | "unspecified";

// v2 schema (bead q4r) — which precision path a mapping exercises.
export type PrecisionTier = "float64" | "arbprec" | "both";

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
    // v2 schema additions (bead q4r): structured real/complex markers.
    // Free-form input/output strings remain source-faithful; these add a
    // queryable shape for tools/special-eval and tools/linalg-*-complex.
    domain?:   SignatureDomain;
    codomain?: SignatureCodomain;
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
    // v2 schema additions (bead q4r). `flags` lifts what was previously
    // free-form prose in `notes` into a queryable key/value shape, driving
    // head-dispatched tools (e.g. {"head": "BesselJ"}). `precision_tier`
    // records whether the mapping exercises float64, arbprec, or both.
    flags?:          Record<string, string>;
    precision_tier?: PrecisionTier;
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
      // v2 schema additions (bead wr2). `machine_checkable` mirrors the
      // workbench's InvariantEntry flag (defaults true when omitted);
      // `applies_when` is a free-form predicate string (e.g. "status ==
      // 'infeasible'") under which the check is applicable.
      machine_checkable?: boolean;
      applies_when?:      string;
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
    // v2 schema additions (bead wr2) for multi-head, multi-tier mega-anchor
    // corpora (ADR-0040/0041). Single-head/single-tier suites omit them.
    n_heads?: number;
    n_tiers?: number;
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
    // v2 schema addition (bead 3u3): structured head/precision dispatch
    // flags (e.g. {"head": "BesselJ", "precision": "53"}) — queryable from
    // DuckDB without parsing args[].
    tool_flags?: Record<string, string>;
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
