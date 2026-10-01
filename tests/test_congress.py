import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx
from prometheus_client import REGISTRY

from govwatch.sources.base import ApiError
from govwatch.sources.congress import (
    BASE_URL,
    MAX_PASSES,
    CongressClient,
    MalformedRecord,
    parse_bill,
)

FIXTURES = Path(__file__).parent / "fixtures"
SINCE = datetime(2026, 9, 28, tzinfo=UTC)
UNTIL = datetime(2026, 9, 29, tzinfo=UTC)


def load(name):
    return json.loads((FIXTURES / name).read_text())


def listing(name, count, bills=None):
    """A recorded listing page with pagination.count set to the size of the test's world.
    The fixtures were recorded with limit=2, so their real count (284) doesn't apply."""
    page = load(name)
    page["pagination"]["count"] = count
    if bills is not None:
        page["bills"] = bills
    return httpx.Response(200, json=page)


@pytest.fixture
def client():
    with CongressClient("test-key", sleep=lambda _: None) as c:
        yield c


def test_parse_bill_normalizes_fields():
    bill = parse_bill(load("congress_bills_page1.json")["bills"][0])
    assert bill.bill_id == "119-hr-6417"
    assert bill.bill_type == "hr"
    assert bill.bill_number == 6417
    assert bill.introduced_date == date(2025, 12, 3)
    assert bill.latest_action_date == date(2025, 12, 3)
    assert bill.source_updated_at == datetime(2026, 9, 28, tzinfo=UTC)
    assert len(bill.content_hash) == 64


def test_parse_bill_rejects_missing_keys():
    with pytest.raises(MalformedRecord):
        parse_bill({"title": "no congress or number"})


def test_content_hash_ignores_update_date():
    raw = load("congress_bills_page1.json")["bills"][0]
    bumped = {**raw, "updateDate": "2026-09-30"}
    assert parse_bill(raw).content_hash == parse_bill(bumped).content_hash

    changed = {**raw, "latestAction": {"actionDate": "2026-09-30", "text": "Passed House."}}
    assert parse_bill(raw).content_hash != parse_bill(changed).content_hash


@respx.mock
def test_pages_until_no_next_link(client):
    route = respx.get(f"{BASE_URL}bill").mock(
        side_effect=[
            listing("congress_bills_page1.json", 3),
            listing("congress_bills_page2.json", 3),
        ]
    )
    bills = list(client.iter_updated_bills(SINCE, UNTIL))

    assert [b.bill_id for b in bills] == ["119-hr-6417", "119-s-1972", "119-hr-10233"]
    assert route.call_count == 2
    first, second = (call.request.url.params for call in route.calls)
    assert first["offset"] == "0"
    assert second["offset"] == "2"
    assert first["fromDateTime"] == "2026-09-28T00:00:00Z"
    assert first["sort"] == "updateDate asc"


@respx.mock
def test_api_key_sent_as_header_not_query(client):
    route = respx.get(f"{BASE_URL}bill").mock(return_value=listing("congress_bills_page2.json", 1))
    list(client.iter_updated_bills(SINCE, UNTIL))

    request = route.calls.last.request
    assert request.headers["X-Api-Key"] == "test-key"
    assert "api_key" not in request.url.params
    assert "test-key" not in str(request.url)


@respx.mock
def test_retries_on_429_and_tracks_ratelimit(client):
    route = respx.get(f"{BASE_URL}bill").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
            httpx.Response(
                200,
                json=listing("congress_bills_page2.json", 1).json(),
                headers={"X-Ratelimit-Remaining": "4321"},
            ),
        ]
    )
    bills = list(client.iter_updated_bills(SINCE, UNTIL))

    assert len(bills) == 1
    assert route.call_count == 3
    assert client.api.requests_made == 3
    assert client.api.ratelimit_remaining == 4321


@respx.mock
def test_gives_up_after_max_retries(client):
    respx.get(f"{BASE_URL}bill").mock(return_value=httpx.Response(502))
    with pytest.raises(ApiError, match="HTTP 502"):
        list(client.iter_updated_bills(SINCE, UNTIL))
    assert client.api.requests_made == client.api.max_retries + 1


@respx.mock
def test_does_not_retry_client_errors(client):
    route = respx.get(f"{BASE_URL}bill").mock(return_value=httpx.Response(403))
    with pytest.raises(ApiError, match="HTTP 403"):
        list(client.iter_updated_bills(SINCE, UNTIL))
    assert route.call_count == 1


@respx.mock
def test_retries_transport_errors(client):
    route = respx.get(f"{BASE_URL}bill").mock(
        side_effect=[
            httpx.ConnectTimeout("boom"),
            listing("congress_bills_page2.json", 1),
        ]
    )
    assert len(list(client.iter_updated_bills(SINCE, UNTIL))) == 1
    assert route.call_count == 2


@respx.mock
def test_skips_malformed_records(client):
    page = load("congress_bills_page2.json")
    page["pagination"]["count"] = 2
    page["bills"].append({"title": "garbage"})
    respx.get(f"{BASE_URL}bill").mock(return_value=httpx.Response(200, json=page))

    bills = list(client.iter_updated_bills(SINCE, UNTIL))
    assert len(bills) == 1
    assert client.malformed == 1


@respx.mock
def test_count_updated_bills_uses_pagination_count(client):
    route = respx.get(f"{BASE_URL}bill").mock(
        return_value=httpx.Response(200, json=load("congress_bills_page1.json"))
    )
    assert client.count_updated_bills(SINCE, UNTIL) == 284

    params = route.calls.last.request.url.params
    assert params["limit"] == "1"
    # window end is exclusive, the API's toDateTime is inclusive
    assert params["toDateTime"] == "2026-09-28T23:59:59Z"


@respx.mock
def test_count_bypasses_the_cdn_cache(client):
    route = respx.get(f"{BASE_URL}bill").mock(
        return_value=httpx.Response(200, json=load("congress_bills_page1.json"))
    )
    client.count_updated_bills(SINCE, UNTIL)
    client.count_updated_bills(SINCE, UNTIL)

    first, second = (str(c.request.url) for c in route.calls)
    assert "_nocache=" in first
    # same window, different url -> separate cache entries
    assert first != second


@respx.mock
def test_count_rejects_a_stale_cached_response(client):
    respx.get(f"{BASE_URL}bill").mock(
        return_value=httpx.Response(
            200, json=load("congress_bills_page1.json"), headers={"Age": "965"}
        )
    )
    with pytest.raises(ApiError, match="965s old"):
        client.count_updated_bills(SINCE, UNTIL)


@respx.mock
def test_listing_ignores_age(client):
    # paging uses unique toDateTime values, so cache age doesn't matter there
    respx.get(f"{BASE_URL}bill").mock(
        return_value=httpx.Response(
            200, json=listing("congress_bills_page2.json", 1).json(), headers={"Age": "965"}
        )
    )
    assert len(list(client.iter_updated_bills(SINCE, UNTIL))) == 1


def _raw_bills():
    return load("congress_bills_page1.json")["bills"] + load("congress_bills_page2.json")["bills"]


@respx.mock
def test_unstable_tie_order_is_deduped_and_repaged(client):
    """What production hit: a bill tied across a page boundary came back twice and its
    neighbour was skipped. A pass with a different page size picks the neighbour up."""
    a, b, c = _raw_bills()
    route = respx.get(f"{BASE_URL}bill").mock(
        side_effect=[
            # pass 1: b shows up on both pages, c never does
            listing("congress_bills_page1.json", 3, bills=[a, b]),
            listing("congress_bills_page2.json", 3, bills=[b]),
            # pass 2: a different order
            listing("congress_bills_page1.json", 3, bills=[c, a]),
            listing("congress_bills_page2.json", 3, bills=[b]),
        ]
    )
    before = (
        REGISTRY.get_sample_value("govwatch_api_paging_repasses_total", {"source": "congress"}) or 0
    )

    ids = [bill.bill_id for bill in client.iter_updated_bills(SINCE, UNTIL)]

    assert ids == ["119-hr-6417", "119-s-1972", "119-hr-10233"]  # each exactly once
    assert route.call_count == 4
    # the second pass uses a different page size so the boundaries move
    limits = [c.request.url.params["limit"] for c in route.calls]
    assert limits == ["250", "250", "230", "230"]
    assert (
        REGISTRY.get_sample_value("govwatch_api_paging_repasses_total", {"source": "congress"})
        == before + 1
    )


@respx.mock
def test_gives_up_re_paging_after_max_passes(client):
    a, _, _ = _raw_bills()
    route = respx.get(f"{BASE_URL}bill").mock(
        return_value=listing("congress_bills_page2.json", 5, bills=[a])
    )
    assert len(list(client.iter_updated_bills(SINCE, UNTIL))) == 1
    assert route.call_count == MAX_PASSES


@respx.mock
def test_bill_context_with_a_crs_summary(client):
    respx.get(f"{BASE_URL}bill/119/hr/3270").mock(
        return_value=httpx.Response(200, json=load("congress_bill_with_summary_detail.json"))
    )
    respx.get(f"{BASE_URL}bill/119/hr/3270/summaries").mock(
        return_value=httpx.Response(200, json=load("congress_bill_with_summary_summaries.json"))
    )
    ctx = client.bill_context(119, "hr", 3270)

    assert ctx.policy_area == "Transportation and Public Works"
    assert ctx.crs_summary.startswith("Air Traffic Control Workforce Development Act")
    assert "<" not in ctx.crs_summary
    assert ctx.crs_summary_version


@respx.mock
def test_bill_context_without_a_crs_summary(client):
    respx.get(f"{BASE_URL}bill/119/hr/9786").mock(
        return_value=httpx.Response(200, json=load("congress_bill_no_summary_detail.json"))
    )
    respx.get(f"{BASE_URL}bill/119/hr/9786/summaries").mock(
        return_value=httpx.Response(200, json=load("congress_bill_no_summary_summaries.json"))
    )
    ctx = client.bill_context(119, "hr", 9786)

    assert ctx.policy_area == "Crime and Law Enforcement"
    assert ctx.crs_summary is None
