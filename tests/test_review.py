import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from govwatch import db
from govwatch.ingest import upsert_bills
from govwatch.review import store
from govwatch.review.app import create_app
from govwatch.sources.congress import parse_bill

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = "This bill expands Air Traffic Control (ATC) workforce training programs."


@pytest.fixture
def conn(pg):
    db.migrate(pg)
    raw = json.loads((FIXTURES / "congress_bills_page1.json").read_text())["bills"]
    upsert_bills(pg, [parse_bill(b) for b in raw])
    return pg


@pytest.fixture
def client(conn):
    url = conn.info.dsn + " password=govwatch"
    with TestClient(create_app(url), follow_redirects=False) as c:
        yield c


def add_summary(conn, bill_id, status="pending_review", issues=(), created="now()"):
    return conn.execute(
        f"""
        INSERT INTO bill_summaries (bill_id, source_hash, status, stage, summary, policy_area,
            model_policy_area, source_text, validation_issues, attempts, model, prompt_version,
            created_at)
        VALUES (%s, 'h', %s, 'in_committee', 'It expands ATC training for new controllers.',
            'Transportation and Public Works', 'Commerce', %s, %s, 1, 'fake', 'v1', {created})
        RETURNING id
        """,
        (bill_id, status, SOURCE, json.dumps([{"check": c, "detail": d} for c, d in issues])),
    ).fetchone()[0]


def status_of(conn, summary_id):
    return conn.execute(
        "SELECT status, reviewer, review_note FROM bill_summaries WHERE id = %s", (summary_id,)
    ).fetchone()


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def test_queue_lists_only_reviewable_current_summaries_oldest_first(conn, client):
    add_summary(conn, "119-s-1972", created="now() - interval '1 hour'")
    add_summary(conn, "119-hr-6417", status="needs_attention")
    # an old row for the same bill doesn't count once a newer one exists
    add_summary(conn, "119-s-1972", status="stub", created="now() - interval '2 hours'")

    page = client.get("/").text
    assert page.index("119-s-1972") < page.index("119-hr-6417")
    assert "Passed validation (1)" in page and "Failed validation (1)" in page

    only_failed = client.get("/?status=needs_attention").text
    assert "119-hr-6417" in only_failed and "119-s-1972" not in only_failed


def test_bill_page_shows_summary_next_to_its_source_and_gate_issues(conn, client):
    add_summary(conn, "119-hr-6417", "needs_attention", [("ungrounded_number", "numbers: 40")])
    page = client.get("/bills/119-hr-6417").text

    assert "It expands ATC training" in page
    assert SOURCE in page
    assert "ungrounded_number" in page
    assert "model disagrees" in page or "Commerce" in page
    assert "https://www.congress.gov/bill/119th-congress/house-bill/6417" in page


def test_approve_records_the_decision_and_moves_to_the_next_item(conn, client):
    first = add_summary(conn, "119-hr-6417", created="now() - interval '1 hour'")
    add_summary(conn, "119-s-1972")
    before = sample(
        "govwatch_review_decisions_total", decision="approved", from_status="pending_review"
    )

    resp = client.post(
        f"/summaries/{first}/decision",
        data={"bill_id": "119-hr-6417", "decision": "approved", "reviewer": "vidit"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/bills/119-s-1972"
    assert resp.cookies.get("reviewer") == "vidit"
    assert status_of(conn, first) == ("approved", "vidit", None)
    assert (
        sample("govwatch_review_decisions_total", decision="approved", from_status="pending_review")
        == before + 1
    )


def test_reject_needs_a_reason(conn, client):
    sid = add_summary(conn, "119-hr-6417")
    form = {"bill_id": "119-hr-6417", "decision": "rejected", "reviewer": "vidit"}

    resp = client.post(f"/summaries/{sid}/decision", data=form)
    assert resp.status_code == 303 and "error=" in resp.headers["location"]
    assert status_of(conn, sid)[0] == "pending_review"

    client.post(f"/summaries/{sid}/decision", data={**form, "note": "misses the funding"})
    assert status_of(conn, sid) == ("rejected", "vidit", "misses the funding")


def test_reviewer_name_is_required(conn, client):
    sid = add_summary(conn, "119-hr-6417")
    resp = client.post(
        f"/summaries/{sid}/decision",
        data={"bill_id": "119-hr-6417", "decision": "approved", "reviewer": "  "},
    )
    assert "error=" in resp.headers["location"]
    assert status_of(conn, sid)[0] == "pending_review"


def test_a_decision_cant_be_made_twice(conn, client):
    sid = add_summary(conn, "119-hr-6417")
    form = {"bill_id": "119-hr-6417", "decision": "approved", "reviewer": "a"}
    client.post(f"/summaries/{sid}/decision", data=form)

    resp = client.post(f"/summaries/{sid}/decision", data={**form, "reviewer": "b"})
    assert resp.status_code == 409
    assert status_of(conn, sid)[1] == "a"


def test_a_superseded_summary_cant_be_approved(conn):
    """If the agent re-summarized a bill while someone was reading the old version, approving
    the old one must fail rather than quietly approve stale text."""
    old = add_summary(conn, "119-hr-6417", created="now() - interval '1 hour'")
    add_summary(conn, "119-hr-6417")
    with pytest.raises(store.AlreadyDecided):
        store.decide(conn, old, "approved", "vidit", None)


def test_unknown_decision_and_bill(conn, client):
    sid = add_summary(conn, "119-hr-6417")
    resp = client.post(
        f"/summaries/{sid}/decision",
        data={"bill_id": "119-hr-6417", "decision": "maybe", "reviewer": "a"},
    )
    assert resp.status_code == 400
    assert client.get("/bills/119-hr-1").status_code == 404


def test_html_in_model_output_is_escaped(conn, client):
    sid = add_summary(conn, "119-hr-6417")
    conn.execute(
        "UPDATE bill_summaries SET summary = '<script>alert(1)</script>' WHERE id = %s", (sid,)
    )
    page = client.get("/bills/119-hr-6417").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_queue_metrics_come_from_the_database(conn):
    from govwatch import metrics

    add_summary(conn, "119-hr-6417", created="now() - interval '3 hours'")
    add_summary(conn, "119-s-1972", status="approved")
    metrics.refresh_from_db(conn)

    assert sample("govwatch_review_queue_bills", status="pending_review") == 1
    assert sample("govwatch_review_queue_bills", status="approved") == 1
    assert sample("govwatch_review_queue_oldest_timestamp_seconds", status="pending_review") > 0

    conn.execute("UPDATE bill_summaries SET status = 'approved'")
    metrics.refresh_from_db(conn)
    # nothing waiting any more, so the age series goes away and an age alert can clear
    assert (
        REGISTRY.get_sample_value(
            "govwatch_review_queue_oldest_timestamp_seconds", {"status": "pending_review"}
        )
        is None
    )


def test_health_and_metrics_endpoints(client):
    assert client.get("/healthz").text == "ok\n"
    assert "govwatch_review_decisions_total" in client.get("/metrics").text
