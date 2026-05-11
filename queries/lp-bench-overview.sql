-- =============================================================================
-- lp-bench-overview.sql — per-(target × LP suite) dashboard view.
--
-- The view a grader reads to ask:
--   "How are the candidate LP solvers performing across the canonical
--    NETLIB battery and the small-LP pathology companion?"
--
-- Pivots `grade_results` per check_name to surface where any candidate
-- fails *systematically* (e.g. all `optimality_gap` failures cluster on
-- one solver — likely a sign/tolerance bug in that candidate, not a
-- problem-by-problem accident).  Slowest-case probe surfaces
-- algorithm-cliff problems (a candidate that times out on the largest
-- 5 NETLIB problems but is fine on the rest is a memory-allocation /
-- LU-update issue, not an algorithmic bug).
-- =============================================================================

WITH latest_runs AS (
  SELECT id, candidate_target, candidate_version, suite_id, run_at,
         cases_total, cases_passed, invariants_total, invariants_passed,
         platform_arch || '/' || platform_os || '/' || platform_runtime AS platform
  FROM grade_runs
  WHERE suite_id IN ('lp-netlib', 'lp-small')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY candidate_target, suite_id ORDER BY run_at DESC) = 1
),
per_check_pass AS (
  SELECT
    r.suite_id           AS suite,
    r.candidate_target   AS target,
    gr.check_name        AS check_name,
    COUNT(*) FILTER (WHERE gr.pass)     AS n_pass,
    COUNT(*)                            AS n_total,
    AVG(gr.runtime_sec)                 AS mean_runtime_sec
  FROM grade_results gr
  JOIN latest_runs r ON gr.run_id = r.id
  GROUP BY r.suite_id, r.candidate_target, gr.check_name
)
SELECT
  suite,
  target,
  check_name,
  n_pass        AS pass,
  n_total       AS total,
  ROUND(100.0 * n_pass / n_total, 1)        AS pass_pct,
  ROUND(mean_runtime_sec * 1000, 1)         AS mean_ms
FROM per_check_pass
ORDER BY suite, target, check_name;
