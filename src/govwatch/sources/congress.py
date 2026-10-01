import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from govwatch import metrics
from govwatch.sources.base import ApiClient, content_hash, parse_timestamp

log = logging.getLogger(__name__)

BASE_URL = "https://api.congress.gov/v3/"
PAGE_SIZE = 250  # API max


class MalformedRecord(ValueError):
    pass


@dataclass(frozen=True)
class Bill:
    bill_id: str
    congress: int
    bill_type: str
    bill_number: int
    title: str
    origin_chamber: str | None
    introduced_date: date | None
    latest_action_date: date | None
    latest_action_text: str | None
    source_updated_at: datetime
    source_url: str | None
    raw: dict[str, Any] = field(repr=False)

    @property
    def content_hash(self) -> str:
        # source_updated_at is left out on purpose: congress.gov bumps updateDate
        # for things we don't store, and those shouldn't count as a change
        return content_hash(
            {
                "title": self.title,
                "origin_chamber": self.origin_chamber,
                "introduced_date": self.introduced_date,
                "latest_action_date": self.latest_action_date,
                "latest_action_text": self.latest_action_text,
            }
        )


def parse_bill(raw: dict[str, Any]) -> Bill:
    try:
        congress = int(raw["congress"])
        bill_type = raw["type"].lower()
        number = int(raw["number"])
        updated = parse_timestamp(raw["updateDate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedRecord(f"bad bill record: {exc!r}") from exc

    action = raw.get("latestAction") or {}
    return Bill(
        bill_id=f"{congress}-{bill_type}-{number}",
        congress=congress,
        bill_type=bill_type,
        bill_number=number,
        title=raw.get("title") or "",
        origin_chamber=raw.get("originChamber"),
        introduced_date=_maybe_date(raw.get("introducedDate")),
        latest_action_date=_maybe_date(action.get("actionDate")),
        latest_action_text=action.get("text"),
        source_updated_at=updated,
        source_url=raw.get("url"),
        raw=raw,
    )


class CongressClient:
    def __init__(self, api_key: str, **client_kwargs: Any):
        self.api = ApiClient("congress", BASE_URL, api_key, **client_kwargs)
        self.malformed = 0

    def iter_updated_bills(self, since: datetime, until: datetime) -> Iterator[Bill]:
        """Every bill whose updateDate falls in [since, until], oldest first.

        Sorting ascending matters for offset paging: bills that get updated while
        we're paging move to the end of the list instead of shifting earlier pages.
        """
        offset = 0
        while True:
            data = self.api.get_json(
                "bill",
                {
                    "fromDateTime": _fmt(since),
                    "toDateTime": _fmt(until),
                    "sort": "updateDate asc",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "format": "json",
                },
            )
            records = data.get("bills") or []
            for raw in records:
                try:
                    yield parse_bill(raw)
                except MalformedRecord as exc:
                    self.malformed += 1
                    metrics.MALFORMED.labels("congress").inc()
                    log.warning("congress: skipping record: %s", exc)

            if not records or not (data.get("pagination") or {}).get("next"):
                return
            offset += len(records)

    def count_updated_bills(self, since: datetime, until: datetime) -> int:
        """How many bills congress.gov reports as updated in [since, until).

        congress.gov is fronted by a CDN that caches by URL for 30 minutes, shared across
        every API user (the key is a header, so it isn't part of the cache key). Audit
        windows are midnight-aligned, so the same URL repeats all day and a cached count
        can disagree with a fresh listing. The extra param gives each call its own URL;
        the API ignores it. max_age catches it if that ever stops working.
        """
        data = self.api.get_json(
            "bill",
            {
                "fromDateTime": _fmt(since),
                "toDateTime": _fmt(until - timedelta(seconds=1)),
                "limit": 1,
                "format": "json",
                "_nocache": uuid.uuid4().hex,
            },
            max_age=60,
        )
        return int((data.get("pagination") or {}).get("count") or 0)

    def close(self) -> None:
        self.api.close()

    def __enter__(self) -> "CongressClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _fmt(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _maybe_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
