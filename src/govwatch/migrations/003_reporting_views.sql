-- Curated read-only views for dashboards and anything else downstream that shouldn't
-- touch the raw tables. Views run with the owner's privileges, so the readonly role
-- only ever needs SELECT on these.

CREATE VIEW v_bill_activity AS
SELECT
    b.bill_id,
    b.congress,
    b.bill_type,
    b.bill_number,
    b.title,
    b.origin_chamber,
    b.introduced_date,
    b.latest_action_date,
    b.latest_action_text,
    b.last_changed_at,
    format(
        'https://www.congress.gov/bill/%s%s-congress/%s/%s',
        b.congress,
        CASE
            WHEN b.congress % 100 BETWEEN 11 AND 13 THEN 'th'
            WHEN b.congress % 10 = 1 THEN 'st'
            WHEN b.congress % 10 = 2 THEN 'nd'
            WHEN b.congress % 10 = 3 THEN 'rd'
            ELSE 'th'
        END,
        CASE b.bill_type
            WHEN 'hr' THEN 'house-bill'
            WHEN 's' THEN 'senate-bill'
            WHEN 'hres' THEN 'house-resolution'
            WHEN 'sres' THEN 'senate-resolution'
            WHEN 'hjres' THEN 'house-joint-resolution'
            WHEN 'sjres' THEN 'senate-joint-resolution'
            WHEN 'hconres' THEN 'house-concurrent-resolution'
            WHEN 'sconres' THEN 'senate-concurrent-resolution'
            ELSE b.bill_type
        END,
        b.bill_number
    ) AS url
FROM bills b;

CREATE VIEW v_open_comment_periods AS
SELECT
    d.document_id,
    d.docket_id,
    d.agency_id,
    d.document_type,
    d.title,
    d.comment_start_at,
    d.comment_end_at,
    'https://www.regulations.gov/document/' || d.document_id AS url
FROM regulatory_documents d
WHERE d.comment_end_at > now()
  AND NOT coalesce(d.withdrawn, false);

-- one row per day per kind: documents by posted date, bills by latest action date
CREATE VIEW v_daily_activity AS
SELECT posted_at::date AS day, 'regulatory documents posted' AS kind, count(*) AS n
  FROM regulatory_documents
 WHERE posted_at IS NOT NULL
 GROUP BY 1
UNION ALL
SELECT latest_action_date AS day, 'bill actions' AS kind, count(*) AS n
  FROM bills
 WHERE latest_action_date IS NOT NULL
 GROUP BY 1;

CREATE VIEW v_agency_activity AS
SELECT
    agency_id,
    count(*) FILTER (WHERE posted_at > now() - interval '7 days') AS docs_last_7d,
    count(*) FILTER (WHERE comment_end_at > now() AND NOT coalesce(withdrawn, false))
        AS open_for_comment
FROM regulatory_documents
WHERE agency_id IS NOT NULL
GROUP BY agency_id;

-- NOLOGIN group role. Login users (e.g. grafana's) are created per environment and
-- granted this role; see deploy/compose/initdb for the local one.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'govwatch_readonly') THEN
        CREATE ROLE govwatch_readonly NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO govwatch_readonly;
GRANT SELECT ON v_bill_activity, v_open_comment_periods, v_daily_activity, v_agency_activity
    TO govwatch_readonly;
