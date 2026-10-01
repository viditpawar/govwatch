-- Results of reconciling upstream record counts against what's stored.
-- Only completed audits land here; skipped ones are just logged and counted.
CREATE TABLE completeness_audits (
    id              bigserial PRIMARY KEY,
    source          text NOT NULL,
    window_start    timestamptz NOT NULL,
    window_end      timestamptz NOT NULL,
    upstream_count  integer NOT NULL,
    local_count     integer NOT NULL,
    audited_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX completeness_audits_source_idx ON completeness_audits (source, audited_at DESC);

CREATE VIEW v_completeness_audits AS
SELECT
    source,
    audited_at,
    window_start,
    window_end,
    upstream_count,
    local_count,
    greatest(upstream_count - local_count, 0) AS missing,
    CASE
        WHEN upstream_count = 0 THEN 1.0
        ELSE least(local_count, upstream_count)::numeric / upstream_count
    END AS completeness
FROM completeness_audits;

GRANT SELECT ON v_completeness_audits TO govwatch_readonly;
