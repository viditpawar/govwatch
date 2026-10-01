import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from govwatch import db, metrics
from govwatch.audit import AuditTarget, audit_and_repair, audit_due, audit_window, run_audit
from govwatch.ingest import Source, upsert_bills
from govwatch.sources.congress import parse_bill

FIXTURES = Path(__file__).parent / "fixtures"
# both fixture bills have updateDate 2026-09-28
NOW = datetime(2026, 9, 30, 15, 30, tzinfo=UTC)
WINDOW = (datetime(2026, 9, 27, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC))


class FakeUpstream:
    def __init__(self, count=0, error=None):
        self.count = count
        self.error = error
        self.calls = []

    def __call__(self, since, until):
        self.calls.append((since, until))
        if self.error:
            raise self.error
        return self.count


@pytest.fixture
def conn(pg):
    db.migrate(pg)
    raw = json.loads((FIXTURES / "congress_bills_page1.json").read_text())["bills"]
    upsert_bills(pg, [parse_bill(b) for b in raw])
    return pg


def covered(conn, start, end):
    conn.execute(
        "INSERT INTO ingest_runs (source, status, window_start, window_end, finished_at) "
        "VALUES ('congress', 'success', %s, %s, %s)",
        (start, end, end),
    )


def test_window_is_last_full_utc_days():
    assert audit_window(NOW, 3) == WINDOW


def test_complete_data_reconciles(conn):
    covered(conn, WINDOW[0] - timedelta(days=4), NOW)
    upstream = FakeUpstream(count=2)

    result = run_audit(conn, AuditTarget("congress", "bills", upstream), days=3, now=NOW)

    assert result.status == "ok"
    assert upstream.calls == [WINDOW]
    assert (result.upstream_count, result.local_count, result.missing) == (2, 2, 0)
    assert result.ratio == 1.0


def test_missing_records_are_reported_and_stored(conn):
    covered(conn, WINDOW[0], NOW)
    result = run_audit(
        conn, AuditTarget("congress", "bills", FakeUpstream(count=8)), days=3, now=NOW
    )

    assert result.missing == 6
    assert result.ratio == pytest.approx(0.25)
    row = conn.execute(
        "SELECT source, upstream_count, local_count FROM completeness_audits"
    ).fetchone()
    assert row == ("congress", 8, 2)

    metrics.refresh_from_db(conn)
    labels = {"source": "congress"}
    assert REGISTRY.get_sample_value("govwatch_completeness_missing_records", labels) == 6
    assert REGISTRY.get_sample_value("govwatch_completeness_ratio", labels) == 0.25


def test_extra_local_records_are_not_missing(conn):
    # a record re-updated upstream leaves their window before it leaves ours
    covered(conn, WINDOW[0], NOW)
    result = run_audit(
        conn, AuditTarget("congress", "bills", FakeUpstream(count=1)), days=3, now=NOW
    )
    assert result.missing == 0
    assert result.ratio == 1.0


@pytest.mark.parametrize(
    "start, end",
    [
        (None, None),  # never ingested
        (WINDOW[0] + timedelta(days=1), NOW),  # backfill didn't reach far enough back
        (WINDOW[0], WINDOW[1] - timedelta(hours=1)),  # hasn't caught up to the window end
    ],
)
def test_skips_windows_that_are_not_fully_ingested(conn, start, end):
    if start:
        covered(conn, start, end)
    upstream = FakeUpstream(count=2)
    result = run_audit(conn, AuditTarget("congress", "bills", upstream), days=3, now=NOW)

    assert result.status == "skipped"
    assert upstream.calls == []
    assert conn.execute("SELECT count(*) FROM completeness_audits").fetchone()[0] == 0


def test_upstream_errors_mark_the_audit_failed(conn):
    covered(conn, WINDOW[0], NOW)
    upstream = FakeUpstream(error=RuntimeError("503"))
    result = run_audit(conn, AuditTarget("congress", "bills", upstream), days=3, now=NOW)

    assert result.status == "failed"
    assert "503" in result.reason
    assert conn.execute("SELECT count(*) FROM completeness_audits").fetchone()[0] == 0


def test_audit_due(conn):
    interval = timedelta(hours=6)
    assert audit_due(conn, "congress", interval, NOW)

    conn.execute(
        "INSERT INTO completeness_audits "
        "(source, window_start, window_end, upstream_count, local_count, audited_at) "
        "VALUES ('congress', %s, %s, 1, 1, %s)",
        (*WINDOW, NOW - timedelta(hours=2)),
    )
    assert not audit_due(conn, "congress", interval, NOW)
    assert audit_due(conn, "congress", interval, NOW + timedelta(hours=5))
    assert audit_due(conn, "regulations", interval, NOW)


class FakeApi:
    requests_made = 0


def repairs(source="congress"):
    return REGISTRY.get_sample_value("govwatch_completeness_repairs_total", {"source": source}) or 0


def test_repair_restores_missing_records_and_reaudits(pg):
    db.migrate(pg)
    raw = json.loads((FIXTURES / "congress_bills_page1.json").read_text())["bills"]
    bills = [parse_bill(b) for b in raw]
    upsert_bills(pg, bills[:1])  # one of the two bills got lost somewhere
    covered(pg, WINDOW[0], NOW)

    fetched = []

    def fetch(since, until):
        fetched.append((since, until))
        return bills

    source = Source("congress", timedelta(days=1), fetch, upsert_bills, FakeApi())
    before = repairs()
    result = audit_and_repair(
        pg, AuditTarget("congress", "bills", FakeUpstream(count=2)), source, days=3, now=NOW
    )

    assert fetched == [WINDOW]
    assert (result.local_count, result.missing) == (2, 0)
    assert repairs() == before + 1
    # both the failing audit and the follow-up are on record
    rows = pg.execute("SELECT local_count FROM completeness_audits ORDER BY id").fetchall()
    assert rows == [(1,), (2,)]
    # repairs never move the cursor
    assert pg.execute("SELECT count(*) FROM sync_cursors").fetchone()[0] == 0


def test_no_repair_when_nothing_is_missing(conn):
    covered(conn, WINDOW[0], NOW)

    def fetch(since, until):
        raise AssertionError("should not re-ingest")

    source = Source("congress", timedelta(days=1), fetch, upsert_bills, FakeApi())
    result = audit_and_repair(
        conn, AuditTarget("congress", "bills", FakeUpstream(count=2)), source, days=3, now=NOW
    )
    assert result.missing == 0


def test_no_repair_without_a_source(conn):
    covered(conn, WINDOW[0], NOW)
    before = repairs()
    result = audit_and_repair(
        conn, AuditTarget("congress", "bills", FakeUpstream(count=5)), None, days=3, now=NOW
    )
    assert result.missing == 3
    assert repairs() == before
