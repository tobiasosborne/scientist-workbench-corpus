-- =============================================================================
-- grade-vs-corpus.sql — most-recent grade per (target × suite) joined onto
-- the capability that points at that suite.  The single row a planner reads
-- to ask "is the workbench's linalg-eigh up to v1 MATLAB's eig?".
-- =============================================================================
WITH latest_runs AS (
  SELECT id, candidate_target, candidate_version, suite_id, run_at,
         cases_total, cases_passed, invariants_total, invariants_passed,
         platform_arch || '/' || platform_os || '/' || platform_runtime AS platform
  FROM grade_runs
  QUALIFY ROW_NUMBER() OVER (PARTITION BY candidate_target, suite_id ORDER BY run_at DESC) = 1
)
SELECT
  c.id                           AS capability,
  c.system || '-' || c.version   AS source,
  s.name                         AS suite,
  r.candidate_target             AS target,
  r.candidate_version            AS target_version,
  r.cases_passed                 AS cases_pass,
  r.cases_total                  AS cases_total,
  r.invariants_passed            AS inv_pass,
  r.invariants_total             AS inv_total,
  r.platform                     AS platform,
  r.run_at                       AS run_at
FROM capabilities c
JOIN benchmark_suites s ON c.benchmark_suite = s.file_path
LEFT JOIN latest_runs r ON r.suite_id = s.name
ORDER BY c.id, r.candidate_target;
