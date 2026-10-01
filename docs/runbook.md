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

## GovwatchDataIncomplete

**What it means:** the completeness auditor asked the source how many records changed over
the last few full days, and Postgres holds fewer. Records were dropped somewhere between
the API and the database while every lag and freshness metric stayed green.

The worker re-ingests the window automatically (`GOVWATCH_AUDIT_AUTO_REPAIR=true`), so
this alert only stays up if the repair failed or is turned off.

**Check:**
```bash
docker compose exec worker govwatch audit                # re-run now, no repair
docker compose exec worker govwatch audit --repair       # re-ingest the window if short
```
- A small shortfall that clears on the next audit: probably a record updated upstream
  mid-audit. Not a real problem.
- A persistent shortfall that `--repair` fixes: something in the normal ingest path skips
  records. Compare the window edges against the cursor and overlap settings.
- A shortfall `--repair` can't fix: the API is returning a different set than its own count
  reports (it happens). Check `govwatch peek` for the window.

`GovwatchCompletenessAuditStale` means no audit has completed in 24h. Look for
`audit failed` or `audit skipped` in the worker logs.

## GovwatchRepeatedRepairs

**What it means:** auto-repair had to re-ingest a window two or more times in 24 hours.
The data is fine now, but whatever drops records is still happening.

**Check:** the `ingest_runs` rows around the repaired windows, and worker logs for
`records missing, re-ingesting`. A gap that recurs at the same boundary (midnight, page
edges) points at the cursor overlap or the regulations.gov window-sliding logic.

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

---

## Agent and review alerts

The agent runs in the worker loop (`GOVWATCH_AGENT_ENABLED=true`) and summarizes changed
bills after each ingest cycle. Its quality signals compare a recent window (7 days) with a
baseline (the 30 days before), and only fire with at least 30 summaries in each.

## GovwatchLLMDown

**What it means:** the agent's pre-flight check can't reach Ollama, or the model isn't pulled.
Ingestion carries on; summaries just stop being produced until it's back.

**Check:**
- `curl http://localhost:11434/api/version` on the host. If nothing answers, start the Ollama
  app (it doesn't always come back after a reboot).
- `ollama list` should include the configured model (`GOVWATCH_AGENT_MODEL`, default
  `qwen2.5:3b`). If it's missing: `ollama pull qwen2.5:3b`.
- From a container: `docker compose exec worker python -c "import urllib.request;
  print(urllib.request.urlopen('http://host.docker.internal:11434/api/version').read())"`.

## GovwatchLLMSlow

**What it means:** p95 model latency is over 15s. Normally it's 1-3s, when the model fits
entirely in GPU memory.

**Check:** `ollama ps`. If the model shows part of it on the CPU, something else is using
VRAM or the machine is short on RAM. Close other GPU users, or move to a smaller model.
Nothing is lost while it's slow, but the review queue falls behind.

## GovwatchAgentFailing

**What it means:** more than 5 bills failed to summarize in an hour, for reasons other than
Ollama being down (that stops the batch instead, #49).

**Check:** worker logs for `agent failed`. Usually congress.gov errors while fetching CRS
summaries (see GovwatchUpstreamErrors), or the model returning something that isn't JSON.

## GovwatchAgentAgreementDrop

**What it means:** the model now agrees with the official CRS policy area 15+ points less
often than in the baseline window. This is the shadow check from decisions.md #41. The
official area is what's stored either way, so no wrong data has gone out. But a change in
model behaviour usually affects the summaries too.

**Check:**
- Did the model, the prompt (`PROMPT_VERSION`), or Ollama itself change recently?
- Compare with GovwatchAgentOutputDrift and the "Policy area drift" panel. If the input drift
  is high, the incoming bills changed topic, and the agreement baseline may simply not apply
  to the new mix.

## GovwatchAgentGateFailuresHigh

**What it means:** over 15% of recent summaries still failed the validation gate after the
retry (normally 1-2%). They're in the review queue as `needs_attention`, so nothing unchecked
went out, but reviewers have more to do.

**Check:** the "Gate rejections by check" panel.
- Mostly one check: look at a few of those summaries in the review app. Either the model has
  started doing something new, or the check has a false positive. Both of the first two false
  positives (CDC, USDA) were found this way.
- Spread across checks: suspect the model or the prompt.

## GovwatchAgentOutputDrift

**What it means:** the distribution of the model's policy-area picks moved (Jensen-Shannon
divergence over 0.25), while the official areas of the incoming bills didn't (under 0.1). So
the model behaves differently on the same kind of input.

**Check:** a model or prompt change, or an Ollama update that changed default sampling
settings. Re-run the benchmark from decisions.md #37 against a few known bills.

## GovwatchAgentGroundingDrop

**What it means:** summaries share fewer words with their CRS source text than they used to
(the 7-day median is 0.15+ below the baseline). This is the signature of the model writing
from its own knowledge instead of from the source. When the model was shown only titles, the
median fell from 0.77 to 0.20, and one summary turned a SNAP bill into an oil spill bill.

**Check:**
- Is the CRS text actually reaching the prompt? A change in `build_prompt`, or CRS summaries
  coming back empty, both produce this pattern.
- Run `govwatch eval`. If the golden set's source support has dropped too, it's the prompt
  or the model, not the incoming bills.
- Read a few recent summaries next to their source in the review app.

## GovwatchReviewRejectionsHigh

**What it means:** reviewers rejected over 30% of recent summaries (with at least 10 decisions).

**Check:** the "Recent reviewer rejections" table. Rejections where the gate had *not* flagged
anything show what the gate is missing. Those notes are the best input for a new check or a
prompt change.

## GovwatchReviewQueueStale

**What it means:** a summary has waited more than 3 days for review. Summaries aren't used
until approved, so analysts are missing them.

**Check:** whether anyone is reviewing (http://localhost:8080), and whether the agent is
producing more than reviewers can get through. If so, raise the bar for what reaches review,
or add reviewers.

