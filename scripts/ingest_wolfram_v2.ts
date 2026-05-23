#!/usr/bin/env bun
// =============================================================================
// scripts/ingest_wolfram_v2.ts — extract one capability TOML per per-function
// PDF in data/wolfram-v2/raw/contents/.
// =============================================================================
//
// Modes:
//   single — one capability:
//     bun scripts/ingest_wolfram_v2.ts \
//         (--name <Function> | --pdf <listN.pdf>) \
//         [--out <path>] \
//         [--category-primary <p> [--category-secondary <s1,s2,...>]] \
//         [--map-tool <workbench-tool>] [--map-status <s>] [--map-notes <text>]
//   bulk — every entry in B.8.html (the master function index):
//     bun scripts/ingest_wolfram_v2.ts --bulk [--max-n <N>]
//
// Pipeline per PDF:
//   raw bytes        --pdftotext -layout--> plain text
//   plain text       --parse-->             { name, description, signature, see_also, see_page }
//   structured       --emit (round-trip merge if file exists)--> TOML
//
// Round-trip discipline:
//   - meta, signature, description, the primary [[provenance]] row are AUTO.
//     They are regenerated from the PDF every run.
//   - category, mapping, verification, and any extra [[provenance]] rows are HAND.
//     If the output TOML already exists, hand fields are read back and preserved.
//   - The "auto" provenance row is identified by source_url matching
//     `legacy/v1/contents/list*.pdf`; everything else in the array is hand.
//
// Known artifacts of pdftotext on these typeset PDFs (we leave the rest alone):
//   - "[" before the argument list is dropped: "Eigenvalues m]" not
//     "Eigenvalues[m]". Recovered deterministically via a name-anchored regex.
//   - "×" between matrix dimensions becomes whitespace: "n      n matrix".
//     Recovered for the common single-letter pattern (m, n, p, k).
//   - Sentence-internal newlines and indentation are collapsed by -layout.

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { parse as parseToml } from "smol-toml";

const ROOT     = join(import.meta.dir, "..");
const RAW      = join(ROOT, "data/wolfram-v2/raw/contents");
const LEDGER   = join(ROOT, "data/wolfram-v2/raw/sha256.sum");
const B8_HTML  = join(RAW, "B.8.html");
const CAPS_DIR = join(ROOT, "capabilities/wolfram-v2");

// ─── category map ────────────────────────────────────────────────────────────
//
// 20 thematic groups. Resolution per function name: exact match in `match`
// wins; otherwise first regex hit in `pattern` wins; otherwise "uncategorized".
// First matching category wins (ordered by specificity). The map is a draft —
// hand-tuned via repeated `bun corpus list` / spot-checks; expand it by
// editing this array, not by re-running the ingestor.

interface Category { primary: string; secondary?: string[]; match?: string[]; pattern?: RegExp[] }

const CATEGORIES: Category[] = [
  // Trig / hyperbolic — match before "special" so Sin doesn't get caught by
  // a generic SpecialFunction substring match.
  { primary: "trigonometric", secondary: ["elementary"],
    match: ["Sin", "Cos", "Tan", "Cot", "Sec", "Csc",
            "ArcSin", "ArcCos", "ArcTan", "ArcCot", "ArcSec", "ArcCsc",
            "Sinh", "Cosh", "Tanh", "Coth", "Sech", "Csch",
            "ArcSinh", "ArcCosh", "ArcTanh", "ArcCoth", "ArcSech", "ArcCsch"] },
  { primary: "linalg", secondary: ["matrix", "decomposition"],
    match: ["Det", "Inverse", "Eigenvalues", "Eigenvectors", "Transpose",
            "MatrixPower", "MatrixForm", "MatrixExp", "RowReduce", "NullSpace",
            "LinearSolve", "Minors", "IdentityMatrix", "DiagonalMatrix",
            "Dot", "Outer", "Inner", "Cross", "PseudoInverse", "Tr",
            "MatrixQ", "VectorQ"] },
  { primary: "calculus", secondary: ["differentiation", "integration", "limits"],
    match: ["D", "Dt", "Integrate", "NIntegrate", "Series", "SeriesData",
            "Normal", "Limit", "Sum", "Product", "NSum", "NProduct",
            "Solve", "DSolve", "NDSolve", "InverseSeries", "ComposeSeries"] },
  { primary: "algebra", secondary: ["polynomial", "symbolic"],
    match: ["Expand", "Factor", "Simplify", "Together", "Apart", "Cancel",
            "Collect", "PolynomialQuotient", "PolynomialRemainder", "PolynomialGCD",
            "PolynomialLCM", "Resultant", "Variables", "Coefficient", "CoefficientList",
            "Exponent", "Numerator", "Denominator", "ExpandAll", "PowerExpand",
            "ExpandDenominator", "ExpandNumerator", "FactorTerms", "FactorSquareFree",
            "Decompose", "Roots", "Eliminate", "Reduce", "AlgebraicRules",
            "TrigExpand", "TrigFactor", "TrigReduce", "TrigToExp", "ExpToTrig",
            "ComplexExpand", "RealExpand"] },
  { primary: "special-functions", secondary: ["mathematical"],
    match: ["Gamma", "Beta", "Erf", "Erfc", "EllipticE", "EllipticF", "EllipticK",
            "EllipticPi", "EllipticTheta", "Hypergeometric0F1", "Hypergeometric1F1",
            "Hypergeometric2F1", "HypergeometricU", "LegendreP", "LegendreQ",
            "ChebyshevT", "ChebyshevU", "GegenbauerC", "JacobiP", "LaguerreL",
            "HermiteH", "BesselI", "BesselJ", "BesselK", "BesselY",
            "AiryAi", "AiryBi", "Zeta", "PolyLog", "PolyGamma", "LogGamma",
            "LogIntegral", "ExpIntegralE", "ExpIntegralEi", "SinIntegral",
            "CosIntegral", "SinhIntegral", "CoshIntegral", "FresnelC", "FresnelS",
            "ArithmeticGeometricMean", "JacobiSN", "JacobiCN", "JacobiDN", "JacobiAmplitude"] },
  { primary: "complex",
    match: ["Re", "Im", "Conjugate", "Arg", "Abs", "Sign"] },
  { primary: "numerical", secondary: ["precision"],
    match: ["N", "Precision", "Accuracy", "MachinePrecision", "AccuracyGoal",
            "PrecisionGoal", "WorkingPrecision", "MaxIterations", "Chop",
            "FindRoot", "FindMinimum", "Fit", "InterpolatingFunction",
            "Interpolation", "ListInterpolation", "FunctionInterpolation",
            "Compile", "CompiledFunction"] },
  { primary: "discrete", secondary: ["number-theory", "combinatorics"],
    match: ["Mod", "Quotient", "GCD", "LCM", "PowerMod", "PrimeQ", "Prime",
            "PrimePi", "Divisors", "DivisorSigma", "EulerPhi", "JacobiSymbol",
            "MoebiusMu", "Factorial", "Factorial2", "Binomial", "Multinomial",
            "Fibonacci", "BernoulliB", "EulerE", "StirlingS1", "StirlingS2",
            "Permutations", "Subsets", "FactorInteger", "DigitCount",
            "IntegerDigits", "IntegerExponent", "IntegerQ", "EvenQ", "OddQ",
            "Floor", "Ceiling", "Round", "IntegerPart", "FractionalPart",
            "Rationalize", "ContinuedFraction"] },
  { primary: "graphics", secondary: ["plotting", "visualization"],
    match: ["Plot", "ListPlot", "Plot3D", "ListPlot3D", "ParametricPlot",
            "ParametricPlot3D", "ContourPlot", "ContourPlot3D", "DensityPlot",
            "Show", "Graphics", "Graphics3D", "DensityGraphics", "ContourGraphics",
            "SurfaceGraphics", "Rectangle", "Disk", "Circle", "Polygon",
            "Line", "Point", "Text", "RGBColor", "Hue", "GrayLevel", "CMYKColor",
            "Thickness", "PointSize", "Dashing", "AbsoluteThickness",
            "AbsolutePointSize", "AbsoluteDashing", "AspectRatio", "PlotRange",
            "PlotPoints", "PlotStyle", "PlotLabel", "PlotRegion",
            "Axes", "AxesLabel", "AxesOrigin", "AxesStyle", "Frame", "FrameLabel",
            "FrameStyle", "FrameTicks", "Ticks", "GridLines", "Mesh",
            "Lighting", "LightSources", "AmbientLight", "Shading", "HiddenSurface",
            "ColorOutput", "Background", "DefaultColor", "DefaultFont",
            "ViewPoint", "ViewVertical", "ViewCenter", "Boxed", "BoxRatios",
            "BoxStyle", "ContourLines", "ContourSmoothing", "ContourStyle",
            "Contours", "PlotDivision", "PlotJoined", "Compiled", "MeshRange",
            "MeshStyle", "ClipFill"] },
  { primary: "logic", secondary: ["control-flow"],
    match: ["And", "Or", "Not", "Xor", "Nor", "Nand", "Implies",
            "True", "False", "If", "Which", "Switch", "Do", "While", "For",
            "Return", "Continue", "Break", "Goto", "Label", "Throw", "Catch",
            "Check", "TrueQ", "SameQ", "UnsameQ", "Equal", "Unequal",
            "Less", "LessEqual", "Greater", "GreaterEqual", "Inequality"] },
  { primary: "patterns",
    match: ["Blank", "BlankSequence", "BlankNullSequence", "Pattern",
            "Optional", "Default", "Repeated", "RepeatedNull", "Condition",
            "PatternTest", "Verbatim", "HoldPattern", "Alternatives",
            "MatchQ", "FreeQ", "Cases", "Position", "Count", "Select",
            "DeleteCases", "Replace", "ReplaceAll", "ReplaceRepeated",
            "ReplacePart"] },
  { primary: "rules",
    match: ["Rule", "RuleDelayed", "AlgebraicRules", "Dispatch", "Set",
            "SetDelayed", "Unset", "TagSet", "TagSetDelayed", "TagUnset",
            "UpSet", "UpSetDelayed", "Update", "Clear", "ClearAll",
            "Remove", "Protected", "Unprotect", "Protect"] },
  { primary: "assignment", secondary: ["mutation"],
    match: ["AddTo", "SubtractFrom", "TimesBy", "DivideBy", "Increment",
            "Decrement", "PreIncrement", "PreDecrement", "AppendTo", "PrependTo"] },
  { primary: "strings",
    match: ["StringJoin", "StringLength", "StringPosition", "StringReplace",
            "StringDrop", "StringInsert", "StringMatchQ", "StringTake",
            "StringReverse", "StringForm", "ToString", "ToExpression",
            "Characters", "FromCharacterCode", "ToCharacterCode",
            "Character", "DigitQ", "LetterQ", "UpperCaseQ", "LowerCaseQ",
            "ToUpperCase", "ToLowerCase"],
    pattern: [/^Character/] },
  { primary: "io", secondary: ["files"],
    match: ["Print", "Read", "Write", "Get", "Put", "PutAppend",
            "OpenRead", "OpenWrite", "OpenAppend", "Close", "Skip", "Find",
            "ReadList", "Save", "DumpSave", "BinaryFormat", "FileNames",
            "FileType", "ResetMedium", "SetStreamPosition", "StreamPosition",
            "Streams", "DirectoryStack", "Directory", "ResetDirectory",
            "SetDirectory", "ParentDirectory", "HomeDirectory", "FileDate",
            "FileByteCount", "DeleteFile", "RenameFile", "CopyFile",
            "CreateDirectory", "DeleteDirectory", "RenameDirectory",
            "CallProcess", "Run", "Display", "Splice"] },
  { primary: "system", secondary: ["globals"],
    pattern: [/^\$/] },
  { primary: "constants",
    match: ["E", "Pi", "Degree", "GoldenRatio", "Catalan", "EulerGamma",
            "Infinity", "ComplexInfinity", "DirectedInfinity", "Indeterminate",
            "Null", "Automatic", "All", "None", "I"] },
  { primary: "structure", secondary: ["meta", "traversal"],
    match: ["Head", "AtomQ", "Length", "Depth", "Position", "Level",
            "Apply", "Map", "MapAll", "MapAt", "MapIndexed", "MapThread",
            "Thread", "Nest", "NestList", "FixedPoint", "FixedPointList",
            "Fold", "FoldList", "Through", "Operate", "Function",
            "Compose", "Identity", "Composition", "Slot", "SlotSequence",
            "Hold", "HoldFirst", "HoldRest", "HoldAll", "HoldForm",
            "Evaluate", "Unevaluated", "ReleaseHold",
            "Attributes", "SetAttributes", "ClearAttributes",
            "DownValues", "UpValues", "SubValues", "OwnValues", "Definition"] },
  { primary: "lists",
    match: ["List", "First", "Last", "Rest", "Most", "Take", "Drop",
            "Append", "Prepend", "Insert", "Delete", "Join", "Union",
            "Intersection", "Complement", "Reverse", "Sort", "RotateLeft",
            "RotateRight", "Partition", "Flatten", "FlattenAt", "Range",
            "Table", "Array", "ConstantArray", "Tuples", "Distribute",
            "Transpose", "Riffle", "Split", "SplitBy",
            "Accumulate", "Total", "Innermost"],
    pattern: [/^List/] },
  { primary: "arithmetic",
    match: ["Plus", "Minus", "Times", "Divide", "Power", "Surd",
            "Rational", "Complex", "Real", "Integer"] },
];

function categorize(name: string): { primary: string; secondary?: string[] } | null {
  for (const c of CATEGORIES) {
    if (c.match?.includes(name)) return { primary: c.primary, secondary: c.secondary };
    if (c.pattern?.some((re) => re.test(name))) return { primary: c.primary, secondary: c.secondary };
  }
  return null;
}

// ─── B.8 index ───────────────────────────────────────────────────────────────
//
// The Appendix B.8 page is the master alphabetic index of every built-in
// Mathematica object. It links to one PDF per object. Build a bijective
// name ↔ filename map once per process.

interface IndexEntry { name: string; pdf: string }

function loadIndex(): { byName: Map<string, IndexEntry>; byPdf: Map<string, IndexEntry>; all: IndexEntry[] } {
  const html = readFileSync(B8_HTML, "utf8");
  const re = /href='(list\d+\.pdf)'><b>([^<]+)<\/b>/g;
  const byName = new Map<string, IndexEntry>();
  const byPdf  = new Map<string, IndexEntry>();
  const all: IndexEntry[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) != null) {
    const e: IndexEntry = { name: m[2]!.trim(), pdf: m[1]! };
    byName.set(e.name, e); byPdf.set(e.pdf, e);
    all.push(e);
  }
  return { byName, byPdf, all };
}

// ─── PDF → text ──────────────────────────────────────────────────────────────

function pdftotext(pdfPath: string): string {
  const out = Bun.spawnSync(["pdftotext", "-layout", pdfPath, "-"]);
  if (!out.success) {
    throw new Error(`pdftotext failed for ${pdfPath}: ${new TextDecoder().decode(out.stderr)}`);
  }
  return new TextDecoder().decode(out.stdout);
}

// ─── parse ───────────────────────────────────────────────────────────────────

interface Parsed {
  name: string;
  description: string;
  signature_input: string;
  signature_output: string;
  signature_arity: number | null;
  see_also: string[];
  see_page: number | null;
}

// Compute arity from a parsed `signature_input` string.
//
// Rules:
//   - "(none)"   → 0   (constants, options, attributes — no arguments).
//   - "any"      → null (signature extraction failed; arity unknown).
//   - otherwise  → count of top-level comma-separated args, where "top-level"
//     means commas inside {…}, […], (…) don't split.
//
// Var-arg signatures (`expr1, expr2, …`) collapse to the literal token count
// (3 here). Callers/auditors who need true variadic semantics should treat
// any signature containing "…" as variadic and use the count as a lower
// bound. This is conservative: the corpus contract for `signature.arity` is
// "fixed integer arity when known; omit when unknown".
//
// Bug history: pre-2026-05-23 the ingestor hard-coded `arity = 1` regardless
// of signature shape. See bead scientist-workbench-corpus-3pu and the
// follow-up survey bead for the broader fix.
function computeArity(input: string): number | null {
  const s = input.trim();
  if (s === "(none)") return 0;
  if (s === "any") return null;
  let depth = 0;
  let count = 1;
  for (const ch of s) {
    if (ch === "{" || ch === "[" || ch === "(") depth++;
    else if (ch === "}" || ch === "]" || ch === ")") depth--;
    else if (ch === "," && depth === 0) count++;
  }
  return count;
}

function parse(text: string, expectedName: string): Parsed {
  // Slice off the boilerplate footer ("Web sample page from ..." onwards).
  const footerIdx = text.indexOf("Web sample page from");
  const head = footerIdx >= 0 ? text.slice(0, footerIdx) : text;

  // Strip the running page header. PDFs at the top of a printed page have a
  // header line "Listing of Built-in Mathematica Objects ... <Name> — <Name>
  // ... <pageno>"; PDFs mid-page don't. Drop it deterministically.
  const lines = head.split("\n")
    .map((l) => l.replace(/\s+$/, ""))
    .filter((l) => l.trim().length > 0)
    .filter((l) => !l.includes("Listing of Built-in Mathematica Objects"));

  if (lines.length < 2) throw new Error(`PDF too short: ${lines.length} non-empty line(s)`);

  // The B.8 index name is authoritative. The PDF's own first line is usually
  // the same after header-strip, but if not, prefer B.8 — a typesetter quirk
  // is not a name change.
  const name = expectedName;
  const firstLine = lines[0]!.trim();
  if (firstLine !== expectedName) {
    process.stderr.write(`  warn: post-header first line "${firstLine}" != B.8 index name "${expectedName}" (using B.8)\n`);
  }

  // Body = lines after the function-name line (whether it matched or not).
  const bodyStart = firstLine === expectedName ? 1 : 0;
  const bodyRaw = lines.slice(bodyStart).join(" ").replace(/\s{2,}/g, " ").trim();

  // Recover the dropped "[" — `<Name><whitespace><nonbracket>...]` → `<Name>[...]`.
  const escName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  let fixed = bodyRaw.replace(new RegExp(`${escName}\\s+([^\\]\\s][^\\]]*?)\\]`, "g"), `${name}[$1]`);

  // Font-cmap artifact patches — deterministic, low false-positive rate.
  // Anything more contextual (e.g., `R` for ∫, `P` for Σ, `j…j` for |…|)
  // varies by surrounding math display and we leave alone (description.md
  // is informational, not contract).
  fixed = fixed
    // Scrub control characters (U+0000..U+001F except tab; U+007F).
    // pdftotext emits these for font glyphs with no Unicode cmap entry —
    // Quit.toml had a U+0002 inside "Quitn]" before this scrub.
    // smol-toml's writer rejects them; we drop rather than escape because
    // the byte was already unintelligible upstream.
    // eslint-disable-next-line no-control-regex
    .replace(/[\x00-\x08\x0B-\x1F\x7F]/g, "")
    // ":::" is the cmap rendering of "..." (ellipsis) in display equations.
    .replace(/\s*:::\s*/g, " … ")
    // "->" stays as-is — Mathematica's literal rule syntax.
    // "n × n" matrix pattern: single-letter dims joined by whitespace.
    .replace(/\b([mnpk])\s+([mnpk])\s+(matrix|vector|tensor|array)\b/g, "$1 × $2 $3")
    // collapse double-spaces introduced by the substitutions
    .replace(/\s{2,}/g, " ");

  // Strip "See also: A, B, C." (sentence-final) and "See page N." (just before).
  let trimmed = fixed;
  let see_also: string[] = [];
  const seeAlsoMatch = trimmed.match(/\s*See also:\s*([^.]+?)\.?\s*$/);
  if (seeAlsoMatch) {
    trimmed = trimmed.slice(0, seeAlsoMatch.index!);
    see_also = seeAlsoMatch[1]!.split(/,\s*/).map((s) => s.trim()).filter(Boolean);
  }
  let see_page: number | null = null;
  const seePageMatch = trimmed.match(/\s*See page\s+(\d+)\.?\s*$/);
  if (seePageMatch) {
    trimmed = trimmed.slice(0, seePageMatch.index!);
    see_page = Number(seePageMatch[1]);
  }

  const description = trimmed.trim().replace(/\s+/g, " ");

  // Synthesise a signature from the first sentence whose subject is
  // `Name[args]` or `Name` (no args, for constants/attributes/options).
  // The verb list covers the common openings v1 uses: gives/returns/finds/
  // yields are the "computes a value" forms; is/represents/specifies are
  // the "is a thing" forms (options, constants, attributes).
  let signature_input  = "any";
  let signature_output = "any";
  const VERBS = "gives|returns|finds|yields|is|represents|specifies|sets|tests|applies|prints|reads|writes|opens|closes";
  const sigArgRe = new RegExp(`${escName}\\[([^\\]]+)\\]\\s+(?:${VERBS})\\s+([^.]+?)\\.`);
  const sigArgMatch = description.match(sigArgRe);
  if (sigArgMatch) {
    signature_input  = sigArgMatch[1]!.trim();
    signature_output = sigArgMatch[2]!.trim();
  } else {
    const sigBareRe = new RegExp(`(?:^|[^A-Za-z])${escName}\\s+(?:${VERBS})\\s+([^.]+?)\\.`);
    const sigBareMatch = description.match(sigBareRe);
    if (sigBareMatch) {
      signature_input  = "(none)";
      signature_output = sigBareMatch[1]!.trim();
    }
  }

  const signature_arity = computeArity(signature_input);

  return { name, description, signature_input, signature_output, signature_arity, see_also, see_page };
}

// ─── ledger lookup ───────────────────────────────────────────────────────────

let ledgerCache: Map<string, string> | null = null;
function ledgerSha256(filename: string): string {
  if (!ledgerCache) {
    ledgerCache = new Map();
    for (const line of readFileSync(LEDGER, "utf8").split("\n")) {
      const m = line.match(/^([a-f0-9]{64})\s+contents\/(.+)$/);
      if (m) ledgerCache.set(m[2]!, m[1]!);
    }
  }
  const sha = ledgerCache.get(filename);
  if (!sha) throw new Error(`no SHA-256 entry for ${filename} in ${LEDGER}`);
  return sha;
}

// ─── round-trip merge ────────────────────────────────────────────────────────

interface HandFields {
  category?:    { primary: string; secondary?: string[] };
  mapping?:     Array<{ target: string; tool: string; status: string; notes?: string }>;
  verification?: { level: number; method: string; oracle?: string; oracle_version?: string; last_verified_at?: string };
  extraProvenance: Array<Record<string, unknown>>;
}

const AUTO_PROV_URL_PREFIX = "https://reference.wolfram.com/legacy/v1/contents/list";

function readHandFields(outPath: string): HandFields {
  if (!existsSync(outPath)) return { extraProvenance: [] };
  const doc = parseToml(readFileSync(outPath, "utf8")) as Record<string, unknown>;
  const hf: HandFields = { extraProvenance: [] };
  if (doc["category"])     hf.category     = doc["category"]     as HandFields["category"];
  if (doc["mapping"])      hf.mapping      = doc["mapping"]      as HandFields["mapping"];
  if (doc["verification"]) hf.verification = doc["verification"] as HandFields["verification"];
  const prov = doc["provenance"] as Array<Record<string, unknown>> | undefined;
  if (prov) {
    for (const row of prov) {
      const url = row["source_url"] as string | undefined;
      if (!url || !url.startsWith(AUTO_PROV_URL_PREFIX)) hf.extraProvenance.push(row);
    }
  }
  return hf;
}

// ─── emit TOML ───────────────────────────────────────────────────────────────

function tomlString(s: string): string { return JSON.stringify(s); }

function tomlList(items: string[]): string {
  return `[${items.map((x) => JSON.stringify(x)).join(", ")}]`;
}

interface EmitOpts {
  parsed: Parsed;
  pdfFilename: string;
  hand: HandFields;
  cliCategory?: { primary: string; secondary?: string[] };
  cliMapping?:  { target: string; tool: string; status: string; notes?: string };
}

function emitToml(opts: EmitOpts): string {
  const { parsed: p, pdfFilename, hand, cliCategory, cliMapping } = opts;
  const today = new Date().toISOString().slice(0, 10);
  const sha = ledgerSha256(pdfFilename);
  const provValue = p.description.length > 200 ? p.description.slice(0, 200) + "..." : p.description;

  // Resolution order for hand-fields: explicit CLI flag > existing TOML > heuristic > default.
  // CLI is an active override signal; existing is the round-trip preserve;
  // heuristic (categorize()) is the bulk-ingest auto-classification.
  // Verification has no CLI override (always preserve if present, else default).
  const heuristicCategory = categorize(p.name);
  const category = cliCategory ?? hand.category ?? heuristicCategory ?? { primary: "uncategorized" };
  const mapping  = cliMapping ? [cliMapping] : hand.mapping ?? [];
  const verification = hand.verification ?? { level: 1, method: "manual_quote", last_verified_at: today };

  // Header comments (regenerated each run; include round-trip provenance).
  const seeAlsoComment = p.see_also.length > 0
    ? `# See also (cross-refs from PDF — candidates for cross-system aliases):\n#   ${p.see_also.join(", ")}\n`
    : "";
  const seePageComment = p.see_page != null
    ? `# Back-reference: page ${p.see_page} of the 2nd edition.\n`
    : "";

  // [category]
  const catSecondary = category.secondary?.length ? `\nsecondary = ${tomlList(category.secondary)}` : "";
  const catBlock = `[category]\nprimary   = ${tomlString(category.primary)}${catSecondary}\n`;

  // [[provenance]] — auto row first, then any preserved hand rows.
  const autoProv = `[[provenance]]
field_path   = "description.md"
value        = ${tomlString(provValue)}
source_url   = "https://reference.wolfram.com/legacy/v1/contents/${pdfFilename}"
source_kind  = "scrape"
retrieved_at = "${today}"
local_path   = "data/wolfram-v2/raw/contents/${pdfFilename}"
sha256       = "${sha}"
`;
  const handProv = hand.extraProvenance.map((row) => {
    const fields = Object.entries(row)
      .filter(([, v]) => v !== undefined && v !== null)
      .map(([k, v]) => `${k.padEnd(12)} = ${typeof v === "string" ? tomlString(v) : typeof v === "boolean" || typeof v === "number" ? String(v) : tomlString(JSON.stringify(v))}`)
      .join("\n");
    return `\n[[provenance]]\n${fields}\n`;
  }).join("");

  // [verification]
  const verLines = ["[verification]",
    `level             = ${verification.level}`,
    `method            = ${tomlString(verification.method)}`,
  ];
  if (verification.oracle)           verLines.push(`oracle            = ${tomlString(verification.oracle)}`);
  if (verification.oracle_version)   verLines.push(`oracle_version    = ${tomlString(verification.oracle_version)}`);
  if (verification.last_verified_at) verLines.push(`last_verified_at  = ${tomlString(verification.last_verified_at)}`);
  const verBlock = verLines.join("\n") + "\n";

  // [[mapping]]
  const mapBlocks = mapping.map((m) => {
    const lines = ["[[mapping]]",
      `target = ${tomlString(m.target)}`,
      `tool   = ${tomlString(m.tool)}`,
      `status = ${tomlString(m.status)}`,
    ];
    if (m.notes) lines.push(`notes  = ${tomlString(m.notes)}`);
    return lines.join("\n");
  }).join("\n\n");

  return `# Auto-generated by scripts/ingest_wolfram_v2.ts on ${today}.
# Source: data/wolfram-v2/raw/contents/${pdfFilename}.
#
# Round-trip discipline: re-running the ingestor regenerates [meta], [signature],
# [description], and the primary [[provenance]] row from the PDF. Hand-edits to
# [category], [verification], [[mapping]], and any additional [[provenance]]
# rows are preserved across re-ingestion.
${seeAlsoComment}${seePageComment}
[meta]
system      = "mathematica"
version     = "1"
name        = ${tomlString(p.name)}
section     = ${tomlString(`ref-guide:${pdfFilename.replace(".pdf", "")}`)}
ingested_at = "${today}"
ingested_by = "scripts/ingest_wolfram_v2.ts"

[signature]
input  = ${tomlString(p.signature_input)}
output = ${tomlString(p.signature_output)}${p.signature_arity !== null ? `\narity  = ${p.signature_arity}` : ""}

[description]
md = '''
${p.description.replace(/'''/g, "''​'")}
'''

${catBlock}
${autoProv}${handProv}
${verBlock}${mapping.length > 0 ? `\n${mapBlocks}\n` : ""}`;
}

// ─── per-PDF driver ──────────────────────────────────────────────────────────

interface SingleOpts {
  pdfFilename: string;
  expectedName: string;
  outPath: string;
  cliCategory?: { primary: string; secondary?: string[] };
  cliMapping?:  { target: string; tool: string; status: string; notes?: string };
  quiet?: boolean;
}

function ingestOne(opts: SingleOpts): Parsed {
  const text   = pdftotext(join(RAW, opts.pdfFilename));
  const parsed = parse(text, opts.expectedName);
  const hand   = readHandFields(opts.outPath);
  const toml   = emitToml({ parsed, pdfFilename: opts.pdfFilename, hand, cliCategory: opts.cliCategory, cliMapping: opts.cliMapping });
  mkdirSync(dirname(opts.outPath), { recursive: true });
  writeFileSync(opts.outPath, toml);

  if (!opts.quiet) {
    const wasUpdate = hand.category != null || hand.mapping != null || hand.extraProvenance.length > 0;
    process.stdout.write(`${wasUpdate ? "updated " : "wrote   "} ${opts.outPath}\n`);
    process.stdout.write(`  signature:   ${parsed.signature_input} → ${parsed.signature_output}\n`);
    if (parsed.see_also.length) process.stdout.write(`  see_also:    [${parsed.see_also.join(", ")}]\n`);
    if (parsed.see_page != null) process.stdout.write(`  see_page:    ${parsed.see_page}\n`);
    if (wasUpdate) process.stdout.write(`  preserved:   ${hand.category ? "category " : ""}${hand.mapping ? "mapping " : ""}${hand.verification ? "verification " : ""}${hand.extraProvenance.length ? `+${hand.extraProvenance.length}-prov ` : ""}\n`);
  }
  return parsed;
}

// ─── CLI ─────────────────────────────────────────────────────────────────────

const args = Bun.argv.slice(2);
function arg(flag: string): string | undefined {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : undefined;
}
function has(flag: string): boolean { return args.indexOf(flag) >= 0; }

const idx = loadIndex();

if (has("--bulk")) {
  const maxN = arg("--max-n") ? Number(arg("--max-n")) : Infinity;
  const entries = idx.all.slice(0, Number.isFinite(maxN) ? maxN : idx.all.length);
  process.stdout.write(`bulk ingest: ${entries.length} of ${idx.all.length} entries\n`);
  let i = 0;
  for (const e of entries) {
    i++;
    const outPath = join(CAPS_DIR, `${e.name}.toml`);
    try {
      ingestOne({ pdfFilename: e.pdf, expectedName: e.name, outPath, quiet: true });
      if (i % 25 === 0) process.stdout.write(`  [${i}/${entries.length}] ${e.name}\n`);
    } catch (err) {
      process.stderr.write(`  [${i}/${entries.length}] FAIL ${e.name} (${e.pdf}): ${(err as Error).message}\n`);
    }
  }
  process.stdout.write(`bulk done. ${entries.length} processed.\n`);
} else {
  // single mode
  const nameArg = arg("--name");
  const pdfArg  = arg("--pdf");
  if (!nameArg && !pdfArg) {
    process.stderr.write("usage: bun scripts/ingest_wolfram_v2.ts (--name <Function> | --pdf <listN.pdf>) [--out <path>]\n");
    process.stderr.write("                                       [--category-primary <p>] [--category-secondary <s1,s2,...>]\n");
    process.stderr.write("                                       [--map-tool <tool>] [--map-status <s>] [--map-notes <text>]\n");
    process.stderr.write("       bun scripts/ingest_wolfram_v2.ts --bulk [--max-n <N>]\n");
    process.exit(2);
  }
  const entry = nameArg ? idx.byName.get(nameArg) : idx.byPdf.get(pdfArg!);
  if (!entry) {
    process.stderr.write(`no B.8 index entry for ${nameArg ?? pdfArg}\n`);
    process.exit(2);
  }
  const outPath = arg("--out") ?? join(CAPS_DIR, `${entry.name}.toml`);

  const cliCategory = arg("--category-primary")
    ? { primary: arg("--category-primary")!, secondary: arg("--category-secondary")?.split(",").map((s) => s.trim()).filter(Boolean) }
    : undefined;
  const cliMapping = arg("--map-tool")
    ? { target: "scientist-workbench", tool: arg("--map-tool")!, status: arg("--map-status") ?? "planned", notes: arg("--map-notes") ?? "" }
    : undefined;

  ingestOne({ pdfFilename: entry.pdf, expectedName: entry.name, outPath, cliCategory, cliMapping });
}
