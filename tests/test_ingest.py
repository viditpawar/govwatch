import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from govwatch import db
from govwatch.ingest import Source, get_cursor, run_ingest, upsert_bills, upsert_documents
from govwatch.sources.congress import parse_bill
from govwatch.sources.regulations import parse_document

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
BACKFILL = timedelta(days=7)


def load(name):
    return json.loads((FIXTURES / name).read_text())


BILLS = [parse_bill(b) for b in load("congress_bills_page1.json")["bills"]]
DOCS = [parse_document(d) for d in load("regulations_docs_page1.json")["data"]]


class FakeApi:
    def __init__(self):
        self.requests_made = 0


class FakeFetch:
    """Stands in for a client: records the window it was asked for, returns canned records."""

    def __init__(self, api, records, fail_after=None):
        self.api = api
        self.records = records
        self.fail_after = fail_after
        self.windows = []

    def __call__(self, since, until):
        self.windows.append((since, until))
        self.api.requests_made += 1
        for i, record in enumerate(self.records):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("upstream blew up")
            yield record


def make_source(records, name="congress", upsert=upsert_bills, **kw):
    api = FakeApi()
    fetch = FakeFetch(api, records, **kw)
    return Source(name, timedelta(days=1), fetch, upsert, api), fetch


@pytest.fixture
def conn(pg):
    db.migrate(pg)
    return pg


def runs(conn):
    return conn.execute(
        "SELECT source, status, records_seen, records_changed, api_requests, error "
        "FROM ingest_runs ORDER BY id"
    ).fetchall()


def test_first_run_backfills_and_sets_cursor(conn):
    source, fetch = make_source(BILLS)
    result = run_ingest(conn, source, BACKFILL, now=NOW)

    assert result.status == "success"
    assert fetch.windows == [(NOW - BACKFILL, NOW)]
    assert result.records_seen == result.records_changed == 2
    assert result.newest_record_at == datetime(2026, 9, 28, tzinfo=UTC)
    assert get_cursor(conn, "congress") == NOW
    assert runs(conn) == [("congress", "success", 2, 2, 1, None)]
    assert conn.execute("SELECT count(*) FROM bills").fetchone()[0] == 2


def test_next_run_starts_from_cursor_minus_overlap(conn):
    source, fetch = make_source(BILLS)
    run_ingest(conn, source, BACKFILL, now=NOW)
    later = NOW + timedelta(minutes=15)
    run_ingest(conn, source, BACKFILL, now=later)

    assert fetch.windows[1] == (NOW - timedelta(days=1), later)


def test_unchanged_records_do_not_count_as_changed(conn):
    source, _ = make_source(BILLS)
    run_ingest(conn, source, BACKFILL, now=NOW)
    before = conn.execute("SELECT bill_id, last_changed_at FROM bills ORDER BY 1").fetchall()

    result = run_ingest(conn, source, BACKFILL, now=NOW + timedelta(minutes=15))
    after = conn.execute("SELECT bill_id, last_changed_at FROM bills ORDER BY 1").fetchall()

    assert result.records_seen == 2
    assert result.records_changed == 0
    assert before == after


def test_real_change_bumps_last_changed_at_only_for_that_row(conn):
    source, _ = make_source(BILLS)
    run_ingest(conn, source, BACKFILL, now=NOW)

    moved = replace(BILLS[0], latest_action_text="Passed House.")
    source2, _ = make_source([moved, BILLS[1]])
    result = run_ingest(conn, source2, BACKFILL, now=NOW + timedelta(hours=1))

    assert result.records_changed == 1
    rows = dict(
        conn.execute("SELECT bill_id, last_changed_at > first_seen_at FROM bills").fetchall()
    )
    assert rows == {"119-hr-6417": True, "119-s-1972": False}
    text = conn.execute(
        "SELECT latest_action_text FROM bills WHERE bill_id = '119-hr-6417'"
    ).fetchone()[0]
    assert text == "Passed House."


def test_duplicates_within_a_batch_are_collapsed(conn):
    assert upsert_bills(conn, [BILLS[0], BILLS[0], BILLS[1]]) == 2


def test_failed_run_keeps_partial_data_but_not_cursor(conn, monkeypatch):
    monkeypatch.setattr("govwatch.ingest.BATCH_SIZE", 1)
    source, _ = make_source(BILLS, fail_after=1)
    result = run_ingest(conn, source, BACKFILL, now=NOW)

    assert result.status == "failed"
    assert "upstream blew up" in result.error
    assert get_cursor(conn, "congress") is None
    # first record was committed in its own batch before the failure
    assert conn.execute("SELECT count(*) FROM bills").fetchone()[0] == 1
    assert runs(conn)[0][:2] == ("congress", "failed")


def test_skips_when_another_worker_holds_the_lock(conn):
    other = psycopg.connect(conn.info.dsn, password="govwatch", autocommit=True)
    try:
        other.execute("SELECT pg_advisory_lock(hashtext('govwatch.ingest.congress'))")
        source, fetch = make_source(BILLS)
        assert run_ingest(conn, source, BACKFILL, now=NOW) is None
        assert fetch.windows == []
    finally:
        other.close()


def test_marks_abandoned_runs_failed(conn):
    conn.execute("INSERT INTO ingest_runs (source) VALUES ('congress'), ('regulations')")
    source, _ = make_source(BILLS)
    run_ingest(conn, source, BACKFILL, now=NOW)

    statuses = conn.execute("SELECT source, status FROM ingest_runs ORDER BY id").fetchall()
    # only the source we hold the lock for gets cleaned up
    assert statuses == [
        ("congress", "failed"),
        ("regulations", "running"),
        ("congress", "success"),
    ]


def test_documents_upsert(conn):
    source, _ = make_source(DOCS, name="regulations", upsert=upsert_documents)
    result = run_ingest(conn, source, BACKFILL, now=NOW)

    assert result.status == "success"
    assert result.records_changed == 5
    row = conn.execute(
        "SELECT agency_id, docket_id, raw->'attributes'->>'title' FROM regulatory_documents "
        "WHERE document_id = 'EPA-R06-OAR-2026-1323-0003'"
    ).fetchone()
    assert row == ("EPA", "EPA-R06-OAR-2026-1323", "03. BMOP Permit Application Submittals")
