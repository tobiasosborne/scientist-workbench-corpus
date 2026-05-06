-- =============================================================================
-- failing-invariants.sql — every failing (case, check) pair from the most
-- recent grade run per (target × suite).  The "what's broken right now" view.
-- =============================================================================
WITH latest_runs AS (
  SELECT id, candidate_target, suite_id, run_at
  FROM grade_runs
  QUALIFY ROW_NUMBER() OVER (PARTITION BY candidate_target, suite_id ORDER BY run_at DESC) = 1
)
SELECT
  r.candidate_target  AS target,
  r.suite_id          AS suite,
  gr.case_id          AS case_id,
  gr.check_name       AS check_name,
  gr.detail           AS detail
FROM latest_runs r
JOIN grade_results gr ON gr.run_id = r.id
WHERE gr.pass = FALSE
ORDER BY r.candidate_target, r.suite_id, gr.case_id, gr.check_name;
