-- Bills from congress.gov. bill_id is "<congress>-<type>-<number>", e.g. "119-hr-1234".
CREATE TABLE bills (
    bill_id             text PRIMARY KEY,
    congress            integer NOT NULL,
    bill_type           text NOT NULL,
    bill_number         integer NOT NULL,
    title               text NOT NULL,
    origin_chamber      text,
    latest_action_date  date,
    latest_action_text  text,
    source_updated_at   timestamptz NOT NULL,
    source_url          text,
    -- hash of the normalized fields, so downstream consumers can tell
    -- "actually changed" apart from "re-fetched"
    content_hash        text NOT NULL,
    raw                 jsonb NOT NULL,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_changed_at     timestamptz NOT NULL DEFAULT now(),
    last_ingested_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX bills_source_updated_at_idx ON bills (source_updated_at DESC);
CREATE INDEX bills_last_changed_at_idx ON bills (last_changed_at DESC);

-- Documents from regulations.gov (rules, proposed rules, notices, ...).
CREATE TABLE regulatory_documents (
    document_id         text PRIMARY KEY,
    docket_id           text,
    agency_id           text,
    document_type       text,
    subtype             text,
    title               text,
    fr_doc_num          text,
    posted_at           timestamptz,
    comment_start_at    timestamptz,
    comment_end_at      timestamptz,
    open_for_comment    boolean,
    withdrawn           boolean,
    source_updated_at   timestamptz NOT NULL,
    content_hash        text NOT NULL,
    raw                 jsonb NOT NULL,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_changed_at     timestamptz NOT NULL DEFAULT now(),
    last_ingested_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX regdocs_source_updated_at_idx ON regulatory_documents (source_updated_at DESC);
CREATE INDEX regdocs_last_changed_at_idx ON regulatory_documents (last_changed_at DESC);
CREATE INDEX regdocs_agency_idx ON regulatory_documents (agency_id);

-- Per-source high watermark for incremental pulls.
CREATE TABLE sync_cursors (
    source          text PRIMARY KEY,
    high_watermark  timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per ingestion run. Prometheus covers the live view; this is the audit trail.
CREATE TABLE ingest_runs (
    id                bigserial PRIMARY KEY,
    source            text NOT NULL,
    status            text NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'success', 'failed')),
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    window_start      timestamptz,
    window_end        timestamptz,
    records_seen      integer NOT NULL DEFAULT 0,
    records_changed   integer NOT NULL DEFAULT 0,
    api_requests      integer NOT NULL DEFAULT 0,
    error             text
);

CREATE INDEX ingest_runs_source_started_idx ON ingest_runs (source, started_at DESC);
