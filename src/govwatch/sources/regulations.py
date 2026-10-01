import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from govwatch.sources.base import ApiClient, ApiError, content_hash, parse_timestamp

log = logging.getLogger(__name__)

BASE_URL = "https://api.regulations.gov/v4/"
PAGE_SIZE = 250
# Docs say page[number] maxes out at 20. In practice it's a bit higher, but 20 x 250
# per query is safe either way - past that we slide the window forward instead.
MAX_PAGES = 20

# Date filters are interpreted as US Eastern time, even though the API
# returns UTC timestamps. Everything internal stays UTC.
EASTERN = ZoneInfo("America/New_York")


class MalformedRecord(ValueError):
    pass


@dataclass(frozen=True)
class RegulatoryDocument:
    document_id: str
    docket_id: str | None
    agency_id: str | None
    document_type: str | None
    subtype: str | None
    title: str | None
    fr_doc_num: str | None
    posted_at: datetime | None
    comment_start_at: datetime | None
    comment_end_at: datetime | None
    open_for_comment: bool | None
    withdrawn: bool | None
    source_updated_at: datetime
    raw: dict[str, Any] = field(repr=False)

    @property
    def content_hash(self) -> str:
        return content_hash(
            {
                "docket_id": self.docket_id,
                "agency_id": self.agency_id,
                "document_type": self.document_type,
                "subtype": self.subtype,
                "title": self.title,
                "fr_doc_num": self.fr_doc_num,
                "posted_at": self.posted_at,
                "comment_start_at": self.comment_start_at,
                "comment_end_at": self.comment_end_at,
                "open_for_comment": self.open_for_comment,
                "withdrawn": self.withdrawn,
            }
        )


def parse_document(raw: dict[str, Any]) -> RegulatoryDocument:
    attrs = raw.get("attributes") or {}
    try:
        doc_id = raw["id"]
        updated = parse_timestamp(attrs["lastModifiedDate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedRecord(f"bad document record: {exc!r}") from exc

    return RegulatoryDocument(
        document_id=doc_id,
        docket_id=attrs.get("docketId"),
        agency_id=attrs.get("agencyId"),
        document_type=attrs.get("documentType"),
        subtype=attrs.get("subtype"),
        title=attrs.get("title"),
        fr_doc_num=attrs.get("frDocNum"),
        posted_at=_maybe_ts(attrs.get("postedDate")),
        comment_start_at=_maybe_ts(attrs.get("commentStartDate")),
        comment_end_at=_maybe_ts(attrs.get("commentEndDate")),
        open_for_comment=attrs.get("openForComment"),
        withdrawn=attrs.get("withdrawn"),
        source_updated_at=updated,
        raw=raw,
    )


class RegulationsClient:
    def __init__(self, api_key: str, *, max_pages: int = MAX_PAGES, **client_kwargs: Any):
        self.api = ApiClient("regulations", BASE_URL, api_key, **client_kwargs)
        self.max_pages = max_pages
        self.malformed = 0

    def iter_updated_documents(
        self, since: datetime, until: datetime
    ) -> Iterator[RegulatoryDocument]:
        """Every document modified in [since, until], oldest first.

        regulations.gov caps how deep you can page, so when a window runs past
        max_pages we restart the query from the last timestamp we saw. Records
        sitting exactly on that boundary come back twice; we drop the repeats.
        """
        window_start = since
        already_seen: set[str] = set()

        while True:
            last_ts: datetime | None = None
            # ids sharing the newest timestamp so far - these are what the next
            # window will hand back again, since the filter is second-granular
            last_ts_ids: set[str] = set()

            for page in range(1, self.max_pages + 1):
                data = self.api.get_json("documents", self._params(window_start, until, page))
                for raw in data.get("data") or []:
                    try:
                        doc = parse_document(raw)
                    except MalformedRecord as exc:
                        self.malformed += 1
                        log.warning("regulations: skipping record: %s", exc)
                        continue

                    if doc.source_updated_at != last_ts:
                        last_ts, last_ts_ids = doc.source_updated_at, set()
                    last_ts_ids.add(doc.document_id)

                    if doc.document_id not in already_seen:
                        yield doc

                if not (data.get("meta") or {}).get("hasNextPage"):
                    return

            # ran out of pages but there's more - slide the window forward
            if last_ts is None or last_ts <= window_start:
                raise ApiError(
                    f"regulations: over {self.max_pages * PAGE_SIZE} documents share "
                    f"timestamp {window_start.isoformat()}, can't page past them"
                )
            log.info("regulations: hit page cap, sliding window to %s", last_ts.isoformat())
            window_start = last_ts
            already_seen = last_ts_ids

    def close(self) -> None:
        self.api.close()

    def __enter__(self) -> "RegulationsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _params(since: datetime, until: datetime, page: int) -> dict[str, Any]:
        return {
            "filter[lastModifiedDate][ge]": _eastern(since),
            "filter[lastModifiedDate][le]": _eastern(until),
            # documentId as a tiebreaker keeps paging stable for equal timestamps
            "sort": "lastModifiedDate,documentId",
            "page[size]": PAGE_SIZE,
            "page[number]": page,
        }


def _eastern(ts: datetime) -> str:
    return ts.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S")


def _maybe_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_timestamp(value)
    except ValueError:
        return None
