import logging
import uuid
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from govwatch import metrics
from govwatch.agent.facts import html_to_text
from govwatch.sources.base import ApiClient, content_hash, parse_timestamp

log = logging.getLogger(__name__)

BASE_URL = "https://api.congress.gov/v3/"
# page size for each pass over a window (250 is the API max). later passes only run when
# an earlier one came up short, and use different sizes so page boundaries move
PASS_PAGE_SIZES = (250, 230, 190)
MAX_PASSES = len(PASS_PAGE_SIZES)


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


@dataclass(frozen=True)
class BillContext:
    policy_area: str | None
    crs_summary: str | None  # plain text, latest version
    crs_summary_version: str | None


class CongressClient:
    def __init__(self, api_key: str, **client_kwargs: Any):
        self.api = ApiClient("congress", BASE_URL, api_key, **client_kwargs)
        self.malformed = 0

    def iter_updated_bills(self, since: datetime, until: datetime) -> Iterator[Bill]:
        """Every bill whose updateDate falls in [since, until], each exactly once.

        congress.gov can only sort by updateDate, which is a bare date, so hundreds of
        bills tie. Rows tied across a page boundary come back on both sides of it and
        their neighbours get skipped - deterministically, so re-paging the same way
        skips the same bills. Measured on a 7-day window: 1844 rows, 1839 distinct, with
        every duplicate within a few rows of offset 750 or 1500.

        So results are deduped as they stream, and if a pass comes up short of the
        API's own count, the window is paged again with a different page size (which
        moves the boundaries), yielding only bills not seen yet.
        """
        seen: set[str] = set()
        short = 0
        for attempt, page_size in enumerate(PASS_PAGE_SIZES, start=1):
            expected, malformed = yield from self._one_pass(since, until, seen, page_size)
            short = expected - malformed - len(seen)
            if short <= 0:
                return
            if attempt < MAX_PASSES:
                metrics.PAGING_REPASSES.labels("congress").inc()
                log.warning(
                    "congress: paging came up %d short of the api's count, re-paging", short
                )
        log.warning("congress: still %d short after %d passes", short, MAX_PASSES)

    def _one_pass(
        self, since: datetime, until: datetime, seen: set[str], page_size: int
    ) -> Generator[Bill, None, tuple[int, int]]:
        """Page through the window once. Returns (api's count, malformed records)."""
        offset = 0
        expected = 0
        malformed = 0
        while True:
            data = self.api.get_json(
                "bill",
                {
                    "fromDateTime": _fmt(since),
                    "toDateTime": _fmt(until),
                    # ascending, so bills updated mid-crawl move to the end of the list
                    # instead of shifting pages we've already read
                    "sort": "updateDate asc",
                    "limit": page_size,
                    "offset": offset,
                    "format": "json",
                },
            )
            pagination = data.get("pagination") or {}
            expected = int(pagination.get("count") or 0)
            records = data.get("bills") or []
            for raw in records:
                try:
                    bill = parse_bill(raw)
                except MalformedRecord as exc:
                    malformed += 1
                    self.malformed += 1
                    metrics.MALFORMED.labels("congress").inc()
                    log.warning("congress: skipping record: %s", exc)
                    continue
                if bill.bill_id in seen:
                    continue
                seen.add(bill.bill_id)
                yield bill

            if not records or not pagination.get("next"):
                return expected, malformed
            offset += len(records)

    def bill_context(self, congress: int, bill_type: str, number: int) -> BillContext:
        """Official policy area plus the latest CRS summary, if CRS has written one yet."""
        base = f"bill/{congress}/{bill_type}/{number}"
        detail = self.api.get_json(base, {"format": "json"}).get("bill") or {}
        summaries = self.api.get_json(f"{base}/summaries", {"format": "json"}).get("summaries")
        latest = max(summaries or [], key=lambda s: s.get("updateDate", ""), default=None)
        return BillContext(
            policy_area=(detail.get("policyArea") or {}).get("name"),
            crs_summary=html_to_text(latest["text"]) if latest and latest.get("text") else None,
            crs_summary_version=(latest or {}).get("versionCode"),
        )

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
