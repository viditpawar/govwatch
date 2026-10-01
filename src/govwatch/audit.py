"""Completeness auditing: does Postgres hold everything the source says it has?

Lag and freshness metrics show the pipeline is running, but a paging bug or a missed
edge case can drop records while every dashboard stays green. The auditor asks each
API how many records were updated in a recent, already-ingested window and compares
that to what's stored for the same window.

Counts can drift slightly in the other direction too: if a record in the window gets
updated again upstream, it leaves the upstream window right away but only leaves ours
on the next ingest. That makes local > upstream briefly, which is harmless, so only
the shortfall counts as missing.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

from govwatch import metrics
from govwatch.ingest import Source, reingest_window

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditTarget:
    name: str
    table: str
    count_upstream: Callable[[datetime, datetime], int]


@dataclass
class AuditResult:
    source: str
    window_start: datetime
    window_end: datetime
    status: str  # ok | skipped | failed
    upstream_count: int = 0
    local_count: int = 0
    reason: str | None = None

    @property
    def missing(self) -> int:
        return max(self.upstream_count - self.local_count, 0)

    @property
    def ratio(self) -> float:
        if self.upstream_count == 0:
            return 1.0
        return min(self.local_count, self.upstream_count) / self.upstream_count


def audit_window(now: datetime, days: int) -> tuple[datetime, datetime]:
    """The last `days` full UTC days, ending at midnight today."""
    end = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=days), end


def run_audit(
    conn: psycopg.Connection,
    target: AuditTarget,
    days: int,
    now: datetime | None = None,
) -> AuditResult:
    now = now or datetime.now(UTC)
    start, end = audit_window(now, days)
    result = AuditResult(target.name, start, end, status="ok")

    covered_from, covered_to = conn.execute(
        "SELECT min(window_start), max(window_end) FROM ingest_runs "
        "WHERE source = %s AND status = 'success'",
        (target.name,),
    ).fetchone()  # type: ignore[misc]
    # successful runs chain off the cursor with no gaps, so first start -> last end
    # is exactly what we've ingested
    if covered_from is None or covered_from > start or covered_to < end:
        result.status = "skipped"
        result.reason = "audit window not fully ingested yet"
    else:
        try:
            result.upstream_count = target.count_upstream(start, end)
            result.local_count = _count_local(conn, target.table, start, end)
        except Exception as exc:
            log.exception("%s: completeness audit failed", target.name)
            result.status = "failed"
            result.reason = f"{type(exc).__name__}: {exc}"

    metrics.AUDITS.labels(target.name, result.status).inc()
    if result.status == "ok":
        conn.execute(
            "INSERT INTO completeness_audits "
            "(source, window_start, window_end, upstream_count, local_count) "
            "VALUES (%s, %s, %s, %s, %s)",
            (target.name, start, end, result.upstream_count, result.local_count),
        )
        log.info(
            "%s: audit %s -> %s: upstream %d, stored %d, missing %d (%.2f%%)",
            target.name,
            start.date(),
            end.date(),
            result.upstream_count,
            result.local_count,
            result.missing,
            result.ratio * 100,
        )
    else:
        log.info("%s: audit %s: %s", target.name, result.status, result.reason)
    return result


def audit_and_repair(
    conn: psycopg.Connection,
    target: AuditTarget,
    source: Source | None,
    days: int,
    now: datetime | None = None,
) -> AuditResult:
    """Audit, and if records are missing and a source is given, re-pull the window and
    audit again. Repairs are counted so a recurring gap still shows up even though
    the follow-up audit comes back clean."""
    result = run_audit(conn, target, days, now=now)
    if result.status != "ok" or not result.missing or source is None:
        return result

    log.warning(
        "%s: %d records missing, re-ingesting %s -> %s",
        target.name,
        result.missing,
        result.window_start.isoformat(),
        result.window_end.isoformat(),
    )
    try:
        restored = reingest_window(conn, source, result.window_start, result.window_end)
    except Exception:
        log.exception("%s: repair failed", target.name)
        return result

    metrics.REPAIRS.labels(target.name).inc()
    metrics.REPAIRED_RECORDS.labels(target.name).inc(restored)
    return run_audit(conn, target, days, now=now)


def audit_due(conn: psycopg.Connection, source: str, interval: timedelta, now: datetime) -> bool:
    row = conn.execute(
        "SELECT max(audited_at) FROM completeness_audits WHERE source = %s", (source,)
    ).fetchone()
    last = row[0] if row else None
    return last is None or now - last >= interval


def _count_local(conn: psycopg.Connection, table: str, start: datetime, end: datetime) -> int:
    row = conn.execute(
        f"SELECT count(*) FROM {table} WHERE source_updated_at >= %s AND source_updated_at < %s",
        (start, end),
    ).fetchone()
    return row[0] if row else 0
