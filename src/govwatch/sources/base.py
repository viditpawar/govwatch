import hashlib
import json
import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

from govwatch import __version__, metrics

log = logging.getLogger(__name__)

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 60.0


class ApiError(Exception):
    pass


class ApiClient:
    """Thin httpx wrapper shared by the api.data.gov sources.

    Handles auth, retries with backoff, and keeps track of request counts and the
    rate limit the gateway reports back. The key goes in a header, not the query
    string, so it never ends up in logged URLs.
    """

    def __init__(
        self,
        source: str,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.source = source
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url,
            headers={"X-Api-Key": api_key, "User-Agent": f"govwatch/{__version__}"},
            timeout=timeout,
        )
        self.requests_made = 0
        self.ratelimit_remaining: int | None = None

    def get_json(
        self, path: str, params: dict[str, Any], max_age: int | None = None
    ) -> dict[str, Any]:
        """GET and decode JSON. With max_age, reject responses a cache has held longer
        than that many seconds (congress.gov sits behind a CDN that caches for 30 min)."""
        for attempt in range(self.max_retries + 1):
            last_try = attempt == self.max_retries
            started = time.perf_counter()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                metrics.API_LATENCY.labels(self.source).observe(time.perf_counter() - started)
                metrics.API_REQUESTS.labels(self.source, "error").inc()
                if last_try:
                    raise ApiError(f"{self.source}: {path} failed: {exc}") from exc
                metrics.API_RETRIES.labels(self.source, type(exc).__name__).inc()
                delay = self._backoff(attempt)
                log.warning("%s: %s on %s, retrying in %.1fs", self.source, exc, path, delay)
                self._sleep(delay)
                continue

            metrics.API_LATENCY.labels(self.source).observe(time.perf_counter() - started)
            metrics.API_REQUESTS.labels(self.source, str(resp.status_code)).inc()
            self.requests_made += 1
            self._track_ratelimit(resp)

            if resp.status_code in RETRYABLE_STATUSES and not last_try:
                metrics.API_RETRIES.labels(self.source, str(resp.status_code)).inc()
                delay = self._retry_after(resp) or self._backoff(attempt)
                log.warning(
                    "%s: HTTP %s on %s, retrying in %.1fs",
                    self.source,
                    resp.status_code,
                    path,
                    delay,
                )
                self._sleep(delay)
                continue
            if resp.is_error:
                raise ApiError(f"{self.source}: {path} returned HTTP {resp.status_code}")
            age = resp.headers.get("Age", "0")
            if max_age is not None and age.isdigit() and int(age) > max_age:
                raise ApiError(f"{self.source}: {path} came from a cache and is {age}s old")
            return resp.json()

        raise AssertionError("unreachable")

    def close(self) -> None:
        self._http.close()

    def _backoff(self, attempt: int) -> float:
        # exponential with full jitter
        return random.uniform(0, min(MAX_BACKOFF_SECONDS, self.backoff_base * 2**attempt))

    def _retry_after(self, resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if value and value.isdigit():
            return min(float(value), MAX_BACKOFF_SECONDS)
        return None

    def _track_ratelimit(self, resp: httpx.Response) -> None:
        value = resp.headers.get("X-Ratelimit-Remaining")
        if value and value.isdigit():
            self.ratelimit_remaining = int(value)
            metrics.RATELIMIT_REMAINING.labels(self.source).set(self.ratelimit_remaining)
        limit = resp.headers.get("X-Ratelimit-Limit")
        if limit and limit.isdigit():
            metrics.RATELIMIT_LIMIT.labels(self.source).set(int(limit))


def content_hash(fields: dict[str, Any]) -> str:
    """Stable hash of normalized fields, used to detect real changes vs re-fetches."""
    payload = json.dumps(fields, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_timestamp(value: str) -> datetime:
    """Parse the API's timestamps. Some are full ISO datetimes, some are bare dates."""
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
