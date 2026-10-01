-- Agent output. One row per summarization attempt that finished, so history is kept;
-- the current summary for a bill is its newest row.
CREATE TABLE bill_summaries (
    id                   bigserial PRIMARY KEY,
    bill_id              text NOT NULL REFERENCES bills (bill_id) ON DELETE CASCADE,
    -- bills.content_hash at the time, so a real change to the bill triggers a new summary
    source_hash          text NOT NULL,
    status               text NOT NULL CHECK (status IN (
                             'pending_review',   -- passed the gate, waiting for a human
                             'needs_attention',  -- failed the gate twice, kept for review
                             'stub',             -- no CRS summary yet, nothing generated
                             'approved',
                             'rejected'
                         )),
    stage                text NOT NULL,          -- code-derived, never from the model
    summary              text,
    policy_area          text,                   -- official CRS policy area
    model_policy_area    text,                   -- the model's pick, a shadow check only
    crs_summary_version  text,
    source_truncated     boolean NOT NULL DEFAULT false,
    validation_issues    jsonb NOT NULL DEFAULT '[]',
    attempts             integer NOT NULL DEFAULT 0,
    model                text,
    prompt_version       text NOT NULL,
    llm_seconds          real,
    prompt_tokens        integer,
    completion_tokens    integer,
    created_at           timestamptz NOT NULL DEFAULT now(),
    reviewed_at          timestamptz,
    reviewer             text,
    review_note          text
);

CREATE INDEX bill_summaries_bill_idx ON bill_summaries (bill_id, created_at DESC);
CREATE INDEX bill_summaries_status_idx ON bill_summaries (status, created_at DESC);
