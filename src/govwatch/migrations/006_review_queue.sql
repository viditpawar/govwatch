-- What the model was shown, so a reviewer checks the summary against exactly that text
-- (CRS revises summaries; re-fetching later could show something different)
ALTER TABLE bill_summaries ADD COLUMN source_text text;

-- a reviewer acts on one row at a time and must be able to find "the current one" fast
CREATE INDEX bill_summaries_review_idx ON bill_summaries (status, created_at)
    WHERE status IN ('pending_review', 'needs_attention');

-- queue health for dashboards: how much is waiting, and how long the oldest has waited.
-- only the newest row per bill counts; older rows are history
CREATE VIEW v_review_queue AS
WITH current AS (
    SELECT DISTINCT ON (bill_id) *
      FROM bill_summaries
     ORDER BY bill_id, created_at DESC, id DESC
)
SELECT status,
       count(*) AS bills,
       min(created_at) AS oldest_created_at
  FROM current
 GROUP BY status;

-- reviewer verdicts over time, for agreement/quality panels
CREATE VIEW v_review_decisions AS
SELECT bill_id,
       status AS decision,
       reviewer,
       review_note,
       reviewed_at,
       reviewed_at - created_at AS time_to_review,
       model_policy_area = policy_area AS model_policy_area_matched,
       jsonb_array_length(validation_issues) > 0 AS had_validation_issues
  FROM bill_summaries
 WHERE status IN ('approved', 'rejected');

GRANT SELECT ON v_review_queue, v_review_decisions TO govwatch_readonly;
