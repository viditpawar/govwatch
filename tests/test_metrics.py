import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from prometheus_client import REGISTRY

from govwatch import db, metrics
from govwatch.ingest import RunResult, upsert_bills
from govwatch.server import start_server
from govwatch.sources.base import ApiClient
from govwatch.sources.congress import parse_bill

FIXTURES = Path(__file__).parent / "fixtures"


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


@respx.mock
def test_api_client_records_requests_retries_and_ratelimit():
    # unique source label so other tests can't interfere with the counts
    api = ApiClient("metrics-test", "https://example.test/", "k", sleep=lambda _: None)
    respx.get("https://example.test/thing").mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("nope"),
            httpx.Response(
                200, json={}, headers={"X-Ratelimit-Remaining": "42", "X-Ratelimit-Limit": "1000"}
            ),
        ]
    )
    api.get_json("thing", {})

    labels = {"source": "metrics-test"}
    assert sample("govwatch_api_requests_total", code="503", **labels) == 1
    assert sample("govwatch_api_requests_total", code="error", **labels) == 1
    assert sample("govwatch_api_requests_total", code="200", **labels) == 1
    assert sample("govwatch_api_retries_total", reason="503", **labels) == 1
    assert sample("govwatch_api_retries_total", reason="ConnectError", **labels) == 1
    assert sample("govwatch_api_request_duration_seconds_count", **labels) == 3
    assert sample("govwatch_api_ratelimit_remaining", **labels) == 42
    assert sample("govwatch_api_ratelimit_limit", **labels) == 1000


def test_record_run():
    before = sample("govwatch_ingest_runs_total", source="metrics-run", status="failed")
    now = datetime.now(UTC)
    result = RunResult(
        "metrics-run",
        now,
        now,
        status="failed",
        records_seen=10,
        records_changed=3,
        duration_seconds=2.5,
    )
    metrics.record_run(result)

    assert sample("govwatch_ingest_runs_total", source="metrics-run", status="failed") == (
        before + 1
    )
    assert sample("govwatch_ingest_records_changed_total", source="metrics-run") >= 3
    assert sample("govwatch_ingest_run_duration_seconds_sum", source="metrics-run") >= 2.5


def test_refresh_from_db(pg):
    db.migrate(pg)
    raw = json.loads((FIXTURES / "congress_bills_page1.json").read_text())["bills"]
    upsert_bills(pg, [parse_bill(b) for b in raw])
    finished = datetime.now(UTC) - timedelta(minutes=5)
    pg.execute(
        "INSERT INTO ingest_runs (source, status, finished_at) VALUES "
        "('congress', 'success', %s), ('congress', 'failed', %s)",
        (finished, finished + timedelta(minutes=1)),
    )

    metrics.refresh_from_db(pg)

    assert sample("govwatch_stored_records", source="congress") == 2
    assert sample("govwatch_source_newest_record_timestamp_seconds", source="congress") == (
        datetime(2026, 9, 28, tzinfo=UTC).timestamp()
    )
    assert sample(
        "govwatch_ingest_last_success_timestamp_seconds", source="congress"
    ) == pytest.approx(finished.timestamp())
    # last *run* includes the later failure
    assert sample("govwatch_ingest_last_run_timestamp_seconds", source="congress") == (
        pytest.approx((finished + timedelta(minutes=1)).timestamp())
    )


@pytest.mark.parametrize("healthy, code", [(True, 200), (False, 503)])
def test_server_endpoints(healthy, code):
    metrics.init_labels()
    server = start_server("127.0.0.1", 0, lambda: healthy)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        scrape = httpx.get(f"{base}/metrics")
        assert scrape.status_code == 200
        assert 'govwatch_ingest_runs_total{source="congress",status="success"}' in scrape.text
        assert "govwatch_build_info" in scrape.text

        assert httpx.get(f"{base}/healthz").status_code == code
        assert httpx.get(f"{base}/nope").status_code == 404
    finally:
        server.shutdown()
        server.server_close()
