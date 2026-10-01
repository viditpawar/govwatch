import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from govwatch import db
from govwatch.ingest import upsert_bills, upsert_documents
from govwatch.sources.congress import parse_bill
from govwatch.sources.regulations import parse_document

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def conn(pg):
    db.migrate(pg)
    return pg


def test_bill_urls_point_at_congress_gov(conn):
    bills = [parse_bill(b) for b in load("congress_bills_page1.json")["bills"]]
    # 121st needs the 'st' suffix, 112th is the 11-13 exception
    bills += [
        replace(bills[0], bill_id="121-hjres-7", congress=121, bill_type="hjres", bill_number=7),
        replace(bills[0], bill_id="112-sres-3", congress=112, bill_type="sres", bill_number=3),
    ]
    upsert_bills(conn, bills)

    urls = dict(conn.execute("SELECT bill_id, url FROM v_bill_activity").fetchall())
    assert urls == {
        "119-hr-6417": "https://www.congress.gov/bill/119th-congress/house-bill/6417",
        "119-s-1972": "https://www.congress.gov/bill/119th-congress/senate-bill/1972",
        "121-hjres-7": "https://www.congress.gov/bill/121st-congress/house-joint-resolution/7",
        "112-sres-3": "https://www.congress.gov/bill/112th-congress/senate-resolution/3",
    }


def test_open_comment_periods_excludes_closed_and_withdrawn(conn):
    base = parse_document(load("regulations_docs_page1.json")["data"][0])
    now = datetime.now(UTC)
    upsert_documents(
        conn,
        [
            replace(base, document_id="OPEN", comment_end_at=now + timedelta(days=3)),
            replace(base, document_id="CLOSED", comment_end_at=now - timedelta(days=1)),
            replace(
                base,
                document_id="WITHDRAWN",
                comment_end_at=now + timedelta(days=3),
                withdrawn=True,
            ),
            replace(base, document_id="NO-PERIOD", comment_end_at=None),
        ],
    )
    rows = conn.execute("SELECT document_id, url FROM v_open_comment_periods").fetchall()
    assert rows == [("OPEN", "https://www.regulations.gov/document/OPEN")]


def test_readonly_role_sees_views_but_not_tables(conn):
    upsert_bills(conn, [parse_bill(b) for b in load("congress_bills_page1.json")["bills"]])
    conn.execute("SET ROLE govwatch_readonly")
    try:
        assert conn.execute("SELECT count(*) FROM v_bill_activity").fetchone()[0] == 2
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM bills")
    finally:
        conn.execute("RESET ROLE")
