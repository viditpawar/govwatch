"""All prometheus metrics live here so the full set is easy to review in one place."""

from typing import TYPE_CHECKING

import psycopg
from prometheus_client import Counter, Gauge, Histogram, Info

from govwatch import __version__

if TYPE_CHECKING:
    from govwatch.ingest import RunResult

SOURCES = ("congress", "regulations")
SOURCE_TABLES = {"congress": "bills", "regulations": "regulatory_documents"}

BUILD = Info("govwatch_build", "Build information")
BUILD.info({"version": __version__})

# --- ingest runs ---------------------------------------------------------------

RUNS = Counter("govwatch_ingest_runs_total", "Ingest runs by outcome", ["source", "status"])
RUN_DURATION = Histogram(
    "govwatch_ingest_run_duration_seconds",
    "Wall time of one ingest run",
    ["source"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
)
RECORDS_SEEN = Counter(
    "govwatch_ingest_records_seen_total", "Records returned by the source API", ["source"]
)
RECORDS_CHANGED = Counter(
    "govwatch_ingest_records_changed_total",
    "Records that were new or whose content actually changed",
    ["source"],
)
MALFORMED = Counter(
    "govwatch_ingest_malformed_records_total",
    "Records skipped because they couldn't be parsed",
    ["source"],
)

# --- lag / freshness (read back from postgres so they survive restarts) -------

LAST_SUCCESS = Gauge(
    "govwatch_ingest_last_success_timestamp_seconds",
    "When the last successful run for a source finished",
    ["source"],
)
LAST_RUN = Gauge(
    "govwatch_ingest_last_run_timestamp_seconds",
    "When the last run for a source finished, successful or not",
    ["source"],
)
NEWEST_RECORD = Gauge(
    "govwatch_source_newest_record_timestamp_seconds",
    "Source-side update time of the newest stored record",
    ["source"],
)
STORED_RECORDS = Gauge("govwatch_stored_records", "Rows currently stored", ["source"])

# --- upstream APIs -------------------------------------------------------------

API_REQUESTS = Counter(
    "govwatch_api_requests_total",
    "HTTP requests to source APIs by status code ('error' = no response)",
    ["source", "code"],
)
API_LATENCY = Histogram(
    "govwatch_api_request_duration_seconds",
    "Latency of a single source API request",
    ["source"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
API_RETRIES = Counter(
    "govwatch_api_retries_total", "Retried API requests by reason", ["source", "reason"]
)
RATELIMIT_REMAINING = Gauge(
    "govwatch_api_ratelimit_remaining",
    "Requests left in the current window, as reported by api.data.gov",
    ["source"],
)
PAGING_REPASSES = Counter(
    "govwatch_api_paging_repasses_total",
    "Extra passes over a window because paging returned fewer distinct records than the "
    "api's own count",
    ["source"],
)
RATELIMIT_LIMIT = Gauge(
    "govwatch_api_ratelimit_limit",
    "Size of the rate limit window, as reported by api.data.gov",
    ["source"],
)

# --- completeness audits --------------------------------------------------------

AUDITS = Counter(
    "govwatch_completeness_audits_total", "Completeness audits by outcome", ["source", "status"]
)
COMPLETENESS_RATIO = Gauge(
    "govwatch_completeness_ratio",
    "Share of the records the source reports for the audit window that are stored",
    ["source"],
)
MISSING_RECORDS = Gauge(
    "govwatch_completeness_missing_records",
    "Records the source reports for the audit window that aren't stored",
    ["source"],
)
REPAIRS = Counter(
    "govwatch_completeness_repairs_total",
    "Times an audit found missing records and the window was re-ingested",
    ["source"],
)
REPAIRED_RECORDS = Counter(
    "govwatch_completeness_repaired_records_total",
    "Records restored by re-ingesting an audit window",
    ["source"],
)
LAST_AUDIT = Gauge(
    "govwatch_completeness_last_audit_timestamp_seconds",
    "When the last completed audit for a source ran",
    ["source"],
)

# --- agent ----------------------------------------------------------------------

AGENT_STEP = Histogram(
    "govwatch_agent_step_duration_seconds",
    "Time spent in each step of summarizing one bill",
    ["step"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60),
)
AGENT_BILLS = Counter(
    "govwatch_agent_bills_total",
    "Bills the agent finished, by outcome (pending_review, needs_attention, stub, failed)",
    ["outcome"],
)
AGENT_VALIDATION_FAILURES = Counter(
    "govwatch_agent_validation_failures_total",
    "Model outputs rejected by the validation gate, by check",
    ["check"],
)
AGENT_RETRIES = Counter(
    "govwatch_agent_retries_total", "Second attempts after a rejected output", ["reason"]
)
AGENT_POLICY_AREA = Counter(
    "govwatch_agent_policy_area_checks_total",
    "Shadow check of the model's policy area against the official CRS one",
    ["result"],
)
LLM_REQUESTS = Counter(
    "govwatch_llm_requests_total", "Calls to the local model by outcome", ["model", "outcome"]
)
LLM_LATENCY = Histogram(
    "govwatch_llm_request_duration_seconds",
    "Latency of one model call",
    ["model"],
    buckets=(0.5, 1, 2, 3, 5, 8, 13, 20, 30, 60, 120),
)
LLM_TOKENS = Counter("govwatch_llm_tokens_total", "Tokens processed", ["model", "kind"])

# --- worker --------------------------------------------------------------------

HEARTBEAT = Gauge(
    "govwatch_worker_heartbeat_timestamp_seconds", "Last time the worker loop made progress"
)


def init_labels() -> None:
    """Create every known label combo at 0, so rate() and absent() behave from the start."""
    for source in SOURCES:
        for status in ("success", "failed"):
            RUNS.labels(source, status)
        RUN_DURATION.labels(source)
        RECORDS_SEEN.labels(source)
        RECORDS_CHANGED.labels(source)
        MALFORMED.labels(source)
        API_LATENCY.labels(source)
        PAGING_REPASSES.labels(source)
        for status in ("ok", "skipped", "failed"):
            AUDITS.labels(source, status)
        REPAIRS.labels(source)
        REPAIRED_RECORDS.labels(source)


def record_run(result: "RunResult") -> None:
    RUNS.labels(result.source, result.status).inc()
    RUN_DURATION.labels(result.source).observe(result.duration_seconds)
    RECORDS_SEEN.labels(result.source).inc(result.records_seen)
    RECORDS_CHANGED.labels(result.source).inc(result.records_changed)


def refresh_from_db(conn: psycopg.Connection) -> None:
    for source, table in SOURCE_TABLES.items():
        newest, count = conn.execute(
            f"SELECT max(source_updated_at), count(*) FROM {table}"
        ).fetchone()  # type: ignore[misc]
        STORED_RECORDS.labels(source).set(count)
        if newest:
            NEWEST_RECORD.labels(source).set(newest.timestamp())

    rows = conn.execute(
        """
        SELECT source,
               max(finished_at) FILTER (WHERE status = 'success'),
               max(finished_at)
          FROM ingest_runs
         GROUP BY source
        """
    ).fetchall()
    for source, last_success, last_run in rows:
        if last_success:
            LAST_SUCCESS.labels(source).set(last_success.timestamp())
        if last_run:
            LAST_RUN.labels(source).set(last_run.timestamp())

    audits = conn.execute(
        """
        SELECT DISTINCT ON (source) source, upstream_count, local_count, audited_at
          FROM completeness_audits
         ORDER BY source, audited_at DESC
        """
    ).fetchall()
    for source, upstream, local, audited_at in audits:
        missing = max(upstream - local, 0)
        COMPLETENESS_RATIO.labels(source).set(1.0 if upstream == 0 else 1 - missing / upstream)
        MISSING_RECORDS.labels(source).set(missing)
        LAST_AUDIT.labels(source).set(audited_at.timestamp())
