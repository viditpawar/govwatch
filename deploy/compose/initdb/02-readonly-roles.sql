-- local-dev login for grafana. it only gets the govwatch_readonly group role, which
-- can read the reporting views (migration 003) and nothing else
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'govwatch_readonly') THEN
        CREATE ROLE govwatch_readonly NOLOGIN;
    END IF;
END
$$;

CREATE ROLE grafana_reader LOGIN PASSWORD 'grafana_reader' IN ROLE govwatch_readonly;
