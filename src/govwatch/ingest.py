import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from govwatch import metrics
from govwatch.sources.base import ApiClient
from govwatch.sources.congress import Bill
from govwatch.sources.regulations import RegulatoryDocument

log = logging.getLogger(__name__)

BATCH_SIZE = 250


@dataclass(frozen=True)
class Source:
    name: str
    # how far before the cursor to start each pull, to cover late-arriving or
    # coarse-grained timestamps. re-fetched records are cheap thanks to the hash check
    overlap: timedelta
    fetch: Callable[[datetime, datetime], Iterable[Any]]
    upsert: Callable[[psycopg.Connection, Sequence[Any]], int]
    api: ApiClient


@dataclass
class RunResult:
    source: str
    window_start: datetime
    window_end: datetime
    status: str = "running"
    records_seen: int = 0
    records_changed: int = 0
    api_requests: int = 0
    newest_record_at: datetime | None = None
    error: str | None = None
    duration_seconds: float = 0.0


def run_ingest(
    conn: psycopg.Connection,
    source: Source,
    backfill: timedelta,
    now: datetime | None = None,
) -> RunResult | None:
    """Pull one source from its cursor up to now. Returns None if another worker has it.

    conn must be in autocommit mode - each batch commits on its own, so a failure
    halfway through keeps what was already written. The cursor only moves on success.
    """
    now = now or datetime.now(UTC)
    lock_key = f"govwatch.ingest.{source.name}"
    locked = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (lock_key,)).fetchone()
    if not locked or not locked[0]:
        log.info("%s: another worker holds the lock, skipping", source.name)
        return None

    try:
        _mark_abandoned_runs(conn, source.name)

        cursor = get_cursor(conn, source.name)
        start = cursor - source.overlap if cursor else now - backfill
        result = RunResult(source.name, window_start=start, window_end=now)
        run_id = _start_run(conn, result)
        log.info("%s: ingesting %s -> %s", source.name, start.isoformat(), now.isoformat())

        requests_before = source.api.requests_made
        started = time.monotonic()
        try:
            batch: list[Any] = []
            for record in source.fetch(start, now):
                batch.append(record)
                result.records_seen += 1
                if result.newest_record_at is None or (
                    record.source_updated_at > result.newest_record_at
                ):
                    result.newest_record_at = record.source_updated_at
                if len(batch) >= BATCH_SIZE:
                    result.records_changed += source.upsert(conn, batch)
                    batch = []
            if batch:
                result.records_changed += source.upsert(conn, batch)

            set_cursor(conn, source.name, now)
            result.status = "success"
        except Exception as exc:
            log.exception("%s: ingest run failed", source.name)
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            result.api_requests = source.api.requests_made - requests_before
            result.duration_seconds = time.monotonic() - started
            _finish_run(conn, run_id, result)
            metrics.record_run(result)

        log.info(
            "%s: %s, %d seen, %d changed, %d requests",
            source.name,
            result.status,
            result.records_seen,
            result.records_changed,
            result.api_requests,
        )
        return result
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,))


def get_cursor(conn: psycopg.Connection, source: str) -> datetime | None:
    row = conn.execute(
        "SELECT high_watermark FROM sync_cursors WHERE source = %s", (source,)
    ).fetchone()
    return row[0] if row else None


def set_cursor(conn: psycopg.Connection, source: str, ts: datetime) -> None:
    conn.execute(
        """
        INSERT INTO sync_cursors (source, high_watermark) VALUES (%s, %s)
        ON CONFLICT (source) DO UPDATE
            SET high_watermark = EXCLUDED.high_watermark, updated_at = now()
        """,
        (source, ts),
    )


def _start_run(conn: psycopg.Connection, r: RunResult) -> int:
    row = conn.execute(
        "INSERT INTO ingest_runs (source, window_start, window_end) VALUES (%s, %s, %s) "
        "RETURNING id",
        (r.source, r.window_start, r.window_end),
    ).fetchone()
    assert row is not None
    return row[0]


def _finish_run(conn: psycopg.Connection, run_id: int, r: RunResult) -> None:
    conn.execute(
        """
        UPDATE ingest_runs
           SET status = %s, finished_at = now(), records_seen = %s,
               records_changed = %s, api_requests = %s, error = %s
         WHERE id = %s
        """,
        (r.status, r.records_seen, r.records_changed, r.api_requests, r.error, run_id),
    )


def _mark_abandoned_runs(conn: psycopg.Connection, source: str) -> None:
    # we hold this source's lock, so anything still 'running' is from a worker that died
    cur = conn.execute(
        """
        UPDATE ingest_runs
           SET status = 'failed', finished_at = now(), error = 'abandoned: worker exited mid-run'
         WHERE source = %s AND status = 'running'
        """,
        (source,),
    )
    if cur.rowcount:
        log.warning("%s: marked %d abandoned run(s) as failed", source, cur.rowcount)


# --- upserts -----------------------------------------------------------------

BILL_COLUMNS = [
    "bill_id",
    "congress",
    "bill_type",
    "bill_number",
    "title",
    "origin_chamber",
    "introduced_date",
    "latest_action_date",
    "latest_action_text",
    "source_updated_at",
    "source_url",
    "content_hash",
    "raw",
]

DOCUMENT_COLUMNS = [
    "document_id",
    "docket_id",
    "agency_id",
    "document_type",
    "subtype",
    "title",
    "fr_doc_num",
    "posted_at",
    "comment_start_at",
    "comment_end_at",
    "open_for_comment",
    "withdrawn",
    "source_updated_at",
    "content_hash",
    "raw",
]


def _upsert_sql(table: str, key: str, columns: list[str]) -> str:
    updates = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != key)
    return f"""
INSERT INTO {table} ({", ".join(columns)})
VALUES ({", ".join(f"%({c})s" for c in columns)})
ON CONFLICT ({key}) DO UPDATE SET
    {updates},
    last_changed_at = CASE
        WHEN {table}.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN now()
        ELSE {table}.last_changed_at
    END,
    last_ingested_at = now()
RETURNING last_changed_at = now()
"""


BILLS_UPSERT = _upsert_sql("bills", "bill_id", BILL_COLUMNS)
DOCUMENTS_UPSERT = _upsert_sql("regulatory_documents", "document_id", DOCUMENT_COLUMNS)


def _upsert(
    conn: psycopg.Connection, sql: str, key: str, columns: list[str], records: Sequence[Any]
) -> int:
    """Upsert a batch in one transaction. Returns how many rows were new or changed.

    now() is fixed for the whole transaction, so last_changed_at = now() in the
    RETURNING clause is true exactly for rows this batch inserted or changed.
    """
    # the overlap window can hand us the same record twice; keep the last one
    unique = {getattr(r, key): r for r in records}
    rows = [
        {c: Jsonb(r.raw) if c == "raw" else getattr(r, c) for c in columns} for r in unique.values()
    ]

    changed = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(sql, rows, returning=True)
        while True:
            row = cur.fetchone()
            changed += bool(row and row[0])
            if not cur.nextset():
                break
    return changed


def upsert_bills(conn: psycopg.Connection, bills: Sequence[Bill]) -> int:
    return _upsert(conn, BILLS_UPSERT, "bill_id", BILL_COLUMNS, bills)


def upsert_documents(conn: psycopg.Connection, docs: Sequence[RegulatoryDocument]) -> int:
    return _upsert(conn, DOCUMENTS_UPSERT, "document_id", DOCUMENT_COLUMNS, docs)
