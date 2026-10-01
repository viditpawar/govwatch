# govwatch runbook

Every alert in `observability/prometheus/rules/govwatch.rules.yml` links to a section here.
Commands assume the compose stack. For Kubernetes, swap `docker compose` for
`kubectl -n govwatch`.

Useful starting points for any alert:

```bash
docker compose logs --tail 100 worker
docker compose exec postgres psql -U govwatch -c \
  "select id, source, status, records_seen, api_requests, error, finished_at
     from ingest_runs order by id desc limit 10"
```

---

## GovwatchWorkerDown

**What it means:** Prometheus can't scrape the worker's `/metrics` endpoint.

**Check:**
- `docker compose ps worker`: is it running, restarting, or exited?
- `docker compose logs worker`: a crash on startup is almost always config. Look for
  a missing API key (pydantic `ValidationError`) or a database it can't reach.
- If the container is up but unscrapeable, check that `GOVWATCH_METRICS_HOST` is `0.0.0.0`.

Ingestion stops entirely while this is firing. The lag alerts will follow.

## GovwatchWorkerStalled

**What it means:** the process is up and scrapeable, but the poll loop hasn't completed a
cycle in over an hour. `/healthz` returns 503 in this state, so Kubernetes will restart
the pod on its own.

**Check:** the last log lines. A stuck HTTP call should be impossible because every
request has a 30s timeout. The most likely cause is a database query blocked on a lock.
Look at `pg_stat_activity` for long-running queries.

## GovwatchLagBudgetBurn

**What it means:** the pipeline has fallen behind its SLO (last success within 30 minutes,
99% of the time over 30 days) fast enough to matter.
- **Fast** (critical): at this rate the whole month's budget is gone in about 2 days.
- **Slow** (warning): about 5 days.

**Check:** which source, then the `error` column in `ingest_runs` for that source.
This alert is a symptom. It usually fires alongside `GovwatchIngestFailing` or
`GovwatchUpstreamErrors`, which point at the cause.

**Budget status:** the "Lag SLO budget left" panel, or the
`govwatch:slo_lag_budget_remaining:30d` query.

## GovwatchIngestFailing

**What it means:** 3+ failed runs for one source in the last hour.

**Check:** `ingest_runs.error` for the failing source.
- `ApiError ... HTTP 403`: the key is invalid, or was revoked or rotated. Get a new
  one at https://api.data.gov/signup/.
- `ApiError ... HTTP 5xx` after retries: the upstream is down. Nothing to fix on our
  side; the cursor hasn't moved, so the next successful run will catch up automatically.
- `can't page past`: more than 5,000 regulations.gov documents share one timestamp
  (bulk re-index upstream). Needs a smaller page window or a different sort key.
- Database errors: check Postgres health and disk.

## GovwatchDataStale

**What it means:** ingestion is succeeding, but the newest stored record is old. Either
the source genuinely has no new data, or we're silently missing it.

**Check:**
- `uv run govwatch peek regulations --days 1` (or `congress`) shows what the live API has
  right now. If it lists recent records that aren't in Postgres, something is wrong in the
  cursor or window logic.
- congress.gov: long recesses (August, holidays) legitimately go quiet. Check the
  House/Senate calendars before digging further.
- regulations.gov: federal holidays are quiet; a normal weekday afternoon should not be.

## GovwatchUpstreamErrors

**What it means:** over 20% of API requests to one source failed (5xx, 429, or no response)
over the last hour, even counting the ones that eventually succeeded on retry.

**Check:** the "API responses by code" panel.
- Mostly 429: see GovwatchRateLimitLow.
- Mostly 5xx or `error`: an upstream incident. There's usually nothing to do; the
  retries and cursor design absorb it. https://api.data.gov has status information.

## GovwatchRateLimitLow

**What it means:** under 10% of the api.data.gov hourly rate limit left for a source.
regulations.gov keys get 1,000 requests/hour versus 20,000 for congress.gov, so this
is almost always regulations.gov.

**Check:**
- Is something else using the same key (a second environment, a script)? Use separate keys.
- Is a large backfill running? It'll recover by itself in under an hour.
- If it's normal load, raise `GOVWATCH_POLL_INTERVAL_SECONDS`.

## GovwatchMalformedRecords

**What it means:** a source returned records missing required fields. They were skipped
and the run still succeeded.

**Check:** worker logs for `skipping record`. One-offs are upstream noise. A steady
stream usually means the API's schema changed, and the parser needs updating.
