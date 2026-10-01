import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from govwatch.sources.base import ApiError
from govwatch.sources.regulations import (
    BASE_URL,
    RegulationsClient,
    parse_document,
)

FIXTURES = Path(__file__).parent / "fixtures"
# 2026-09-29 00:00 Eastern (EDT, UTC-4)
SINCE = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)
UNTIL = datetime(2026, 9, 30, 3, 59, 59, tzinfo=UTC)
DOCS = f"{BASE_URL}documents"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def page(docs, has_next):
    return httpx.Response(200, json={"data": docs, "meta": {"hasNextPage": has_next}})


def doc(doc_id, modified):
    return {"id": doc_id, "type": "documents", "attributes": {"lastModifiedDate": modified}}


@pytest.fixture
def client():
    with RegulationsClient("test-key", sleep=lambda _: None) as c:
        yield c


def test_parse_document():
    d = parse_document(load("regulations_docs_page1.json")["data"][0])
    assert d.document_id == "EPA-R06-OAR-2026-1323-0003"
    assert d.docket_id == "EPA-R06-OAR-2026-1323"
    assert d.agency_id == "EPA"
    assert d.source_updated_at == datetime(2026, 9, 29, 4, 1, 14, tzinfo=UTC)
    assert d.posted_at == datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    assert d.comment_end_at is None
    assert d.withdrawn is False


@respx.mock
def test_date_filters_are_sent_in_eastern_time(client):
    route = respx.get(DOCS).mock(return_value=page([], False))
    list(client.iter_updated_documents(SINCE, UNTIL))

    params = route.calls.last.request.url.params
    assert params["filter[lastModifiedDate][ge]"] == "2026-09-29 00:00:00"
    assert params["filter[lastModifiedDate][le]"] == "2026-09-29 23:59:59"
    assert params["sort"] == "lastModifiedDate,documentId"


@respx.mock
def test_eastern_conversion_handles_standard_time(client):
    # January is EST (UTC-5), not EDT
    route = respx.get(DOCS).mock(return_value=page([], False))
    list(client.iter_updated_documents(datetime(2026, 1, 15, 5, 0, tzinfo=UTC), UNTIL))
    assert route.calls.last.request.url.params["filter[lastModifiedDate][ge]"] == (
        "2026-01-15 00:00:00"
    )


@respx.mock
def test_pages_until_has_next_page_is_false(client):
    route = respx.get(DOCS).mock(
        side_effect=[
            httpx.Response(200, json=load("regulations_docs_page1.json")),
            page([doc("X-1", "2026-09-29T05:00:00Z")], False),
        ]
    )
    docs = list(client.iter_updated_documents(SINCE, UNTIL))

    assert len(docs) == 6
    assert [c.request.url.params["page[number]"] for c in route.calls] == ["1", "2"]


@respx.mock
def test_slides_window_at_page_cap_and_drops_boundary_repeats():
    """Recorded case: page 1 ends on -0007, and re-querying from its timestamp
    hands -0007 straight back."""
    client = RegulationsClient("k", max_pages=1, sleep=lambda _: None)
    route = respx.get(DOCS).mock(
        side_effect=[
            httpx.Response(200, json=load("regulations_docs_page1.json")),
            httpx.Response(200, json=load("regulations_docs_last.json")),
        ]
    )
    ids = [d.document_id for d in client.iter_updated_documents(SINCE, UNTIL)]

    assert ids == [f"EPA-R06-OAR-2026-1323-000{n}" for n in range(3, 8)]
    second = route.calls[1].request.url.params
    # 04:01:32Z -> 00:01:32 Eastern, and paging restarts at 1
    assert second["filter[lastModifiedDate][ge]"] == "2026-09-29 00:01:32"
    assert second["page[number]"] == "1"


@respx.mock
def test_boundary_dedupe_covers_every_doc_in_the_last_second():
    client = RegulationsClient("k", max_pages=1, sleep=lambda _: None)
    respx.get(DOCS).mock(
        side_effect=[
            page(
                [
                    doc("A", "2026-09-29T05:00:00Z"),
                    doc("B", "2026-09-29T05:00:07Z"),
                    doc("C", "2026-09-29T05:00:07Z"),
                ],
                True,
            ),
            page(
                [
                    doc("B", "2026-09-29T05:00:07Z"),
                    doc("C", "2026-09-29T05:00:07Z"),
                    doc("D", "2026-09-29T05:00:09Z"),
                ],
                False,
            ),
        ]
    )
    ids = [d.document_id for d in client.iter_updated_documents(SINCE, UNTIL)]
    assert ids == ["A", "B", "C", "D"]


@respx.mock
def test_errors_if_window_cannot_advance():
    client = RegulationsClient("k", max_pages=1, sleep=lambda _: None)
    stuck = [doc("A", "2026-09-29T04:00:00Z"), doc("B", "2026-09-29T04:00:00Z")]
    respx.get(DOCS).mock(return_value=page(stuck, True))

    with pytest.raises(ApiError, match="can't page past"):
        list(client.iter_updated_documents(SINCE, UNTIL))


@respx.mock
def test_skips_malformed(client):
    respx.get(DOCS).mock(
        return_value=page([doc("A", "2026-09-29T05:00:00Z"), {"id": "no-attrs"}], False)
    )
    assert [d.document_id for d in client.iter_updated_documents(SINCE, UNTIL)] == ["A"]
    assert client.malformed == 1
