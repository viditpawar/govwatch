# govwatch

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Container-Docker-2496ED?logo=docker&logoColor=white)
![Prometheus](https://img.shields.io/badge/Metrics-Prometheus-E6522C?logo=prometheus&logoColor=white)
![Grafana](https://img.shields.io/badge/Dashboards-Grafana-F46800?logo=grafana&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

A government data ingestion platform. govwatch continuously pulls federal bills from
**congress.gov** and regulatory documents from **regulations.gov**, normalizes them into
Postgres, and treats the pipeline like a production service: incremental cursors,
idempotent upserts, change detection, and Prometheus metrics for ingestion lag, data
freshness, failure rate, and upstream API health.

Anything that reasons over legislation and regulations (policy research tools, AI agents
drafting briefs, alerting on new rules) is only as good as the data layer underneath it.
This project is that layer, built with the same operational rigor as any other platform
service. Runs entirely on free-tier APIs and local infrastructure.

## Architecture

```mermaid
flowchart LR
    subgraph upstream["Public APIs (api.data.gov)"]
        CG["congress.gov v3<br/>/bill"]
        RG["regulations.gov v4<br/>/documents"]
    end

    subgraph worker["govwatch worker"]
        SRC["source clients<br/>retry · backoff · paging"]
        ING["ingest loop<br/>cursors · advisory locks"]
        MET["/metrics · /healthz"]
        SRC --> ING
    end

    PG[("PostgreSQL<br/>bills · regulatory_documents<br/>sync_cursors · ingest_runs")]
    PROM["Prometheus"]
    GRAF["Grafana"]

    CG --> SRC
    RG --> SRC
    ING -- "batched upserts" --> PG
    PG -. "lag / freshness gauges" .-> MET
    MET --> PROM --> GRAF
```

Every poll interval (15 min by default), the worker does this for each source:

1. Takes a Postgres advisory lock for the source, so two workers never ingest the same source.
2. Reads the source's cursor and pulls everything updated since `cursor - overlap`.
3. Upserts in batches of 250. `last_changed_at` only moves when the record's content hash changes.
4. Advances the cursor **only if the whole run succeeded**, and records the run in `ingest_runs`.
5. Refreshes lag and freshness gauges from the database, so they're correct even right after a restart.

## Features

- Incremental ingestion from congress.gov and regulations.gov with per-source cursors
- Idempotent, batched upserts with content hashing to tell real changes from re-fetches
- Full raw API payload kept as `jsonb` alongside normalized columns
- Retries with exponential backoff and jitter on 429/5xx/network errors, honors `Retry-After`
- Safe to run multiple workers: per-source advisory locks, abandoned-run cleanup
- Graceful shutdown on SIGTERM (finishes the current run, then exits)
- Prometheus metrics for lag, freshness, failure rate, throughput, API latency, and rate-limit headroom
- Multi-stage, non-root Docker image; one-command local stack with Prometheus and Grafana
- Tests against recorded real API responses and a real Postgres

## Design notes

The upstream APIs have quirks that only show up against real data. These shaped most of
the design:

| Problem | How govwatch handles it |
|---|---|
| congress.gov `updateDate` is a bare date (`2026-09-28`), not a timestamp | Each run re-reads one day before the cursor. Re-fetched rows cost nothing thanks to the content hash. |
| congress.gov bumps `updateDate` for changes we don't store | `updateDate` is excluded from the content hash, so those bumps aren't counted as changes |
| regulations.gov date filters are in **US Eastern time**, while responses are UTC | All internal times are UTC; conversion happens only when building the filter, DST-aware |
| regulations.gov caps how deep you can page (the API rejects page numbers past 40) | Each query stops at 20 pages × 250, then the window slides forward to the last timestamp seen |
| Sliding the window re-returns records in the boundary second | IDs from that second are tracked and skipped in the next window |
| Offset paging over a list that changes while you read it | Results are sorted oldest-first, so records updated mid-crawl move to the end instead of shifting pages |
| A crash mid-run | Batches commit independently and the cursor doesn't move, so the next run safely covers the same window |
| The API key showing up in logs | The key is sent as an `X-Api-Key` header, never as a query parameter |

## Tech stack

- **Language:** Python 3.12, httpx, psycopg 3, pydantic-settings
- **Storage:** PostgreSQL 17, with plain-SQL migrations and a small runner guarded by an advisory lock
- **Observability:** prometheus-client, Prometheus 3, Grafana 12
- **Packaging:** uv, Docker (multi-stage, non-root), Docker Compose
- **Quality:** pytest, respx, ruff

## Project structure

```text
govwatch/
├── src/govwatch/
│   ├── sources/
│   │   ├── base.py           # shared HTTP client: auth, retries, rate-limit tracking
│   │   ├── congress.py       # congress.gov client + Bill model
│   │   └── regulations.py    # regulations.gov client + window sliding
│   ├── migrations/           # versioned SQL, applied by `govwatch migrate`
│   ├── ingest.py             # cursors, locking, batched upserts, run tracking
│   ├── worker.py             # poll loop, signal handling, heartbeat
│   ├── metrics.py            # every Prometheus metric in one place
│   ├── server.py             # /metrics and /healthz
│   ├── db.py                 # connections + migration runner
│   ├── config.py             # GOVWATCH_* settings
│   └── __main__.py           # CLI
├── tests/                    # unit + Postgres-backed tests, recorded API fixtures
├── deploy/compose/           # Prometheus config, Grafana provisioning, db init
├── compose.yaml
├── Dockerfile
└── pyproject.toml
```

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- A free api.data.gov key from https://api.data.gov/signup/ (one key works for both APIs)
- [uv](https://docs.astral.sh/uv/) for local development (optional if you only use Docker)

## Configuration

Copy `.env.example` to `.env` and add your key:

```env
GOVWATCH_CONGRESS_API_KEY=your_key
GOVWATCH_REGULATIONS_API_KEY=your_key

# optional
GOVWATCH_POLL_INTERVAL_SECONDS=900   # minimum 60
GOVWATCH_BACKFILL_DAYS=7             # how far back the very first run goes
```

| Variable | Default | Description |
|---|---|---|
| `GOVWATCH_CONGRESS_API_KEY` | required | api.data.gov key for congress.gov |
| `GOVWATCH_REGULATIONS_API_KEY` | required | api.data.gov key for regulations.gov |
| `GOVWATCH_DATABASE_URL` | `postgresql://govwatch:govwatch@localhost:5432/govwatch` | Postgres connection string |
| `GOVWATCH_POLL_INTERVAL_SECONDS` | `900` | Time between ingest cycles |
| `GOVWATCH_BACKFILL_DAYS` | `7` | Lookback for a source with no cursor yet |
| `GOVWATCH_METRICS_HOST` / `_PORT` | `0.0.0.0` / `9100` | Bind address for `/metrics` and `/healthz` |
| `GOVWATCH_LOG_LEVEL` | `INFO` | Python log level |

## Quick start

```bash
git clone https://github.com/viditpawar/govwatch
cd govwatch
cp .env.example .env        # then add your api key
docker compose up -d --build
```

Compose starts Postgres, runs migrations as a one-off job, then starts the worker,
Prometheus, and Grafana.

| Service | URL |
|---|---|
| Worker metrics | http://localhost:9100/metrics |
| Worker health | http://localhost:9100/healthz |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (anonymous read-only, or `admin` / `admin`) |

Check what was ingested:

```bash
docker compose exec postgres psql -U govwatch -c \
  "select source, status, records_seen, records_changed, api_requests, finished_at
     from ingest_runs order by id desc limit 10"
```

## CLI

```bash
govwatch migrate                         # apply pending migrations
govwatch run                             # long-running worker (what the container runs)
govwatch ingest [--source congress]      # one cycle, non-zero exit on failure
govwatch peek regulations --days 1       # print recent records from the live API, no db writes
```

## Data model

| Table | Purpose |
|---|---|
| `bills` | One row per bill (`119-hr-1234`): normalized fields, raw payload, `content_hash`, `first_seen_at`, `last_changed_at` |
| `regulatory_documents` | One row per regulations.gov document: docket, agency, type, comment period, raw payload |
| `sync_cursors` | High watermark per source |
| `ingest_runs` | Audit trail: window, status, records seen/changed, API requests, error |

`last_changed_at` only moves when the content actually changes. That gives downstream
consumers (summarizers, tagging agents, alerting) a cheap way to process only what's new.

## Metrics

| Metric | Type | What it answers |
|---|---|---|
| `govwatch_ingest_runs_total{source,status}` | counter | Failure rate |
| `govwatch_ingest_last_success_timestamp_seconds` | gauge | Ingestion lag: `time() - metric` |
| `govwatch_source_newest_record_timestamp_seconds` | gauge | Data freshness: age of the newest stored record |
| `govwatch_ingest_run_duration_seconds` | histogram | How long runs take |
| `govwatch_ingest_records_seen_total` / `_changed_total` | counter | Throughput vs real change volume |
| `govwatch_ingest_malformed_records_total` | counter | Upstream data quality |
| `govwatch_api_requests_total{source,code}` | counter | Upstream errors by status code |
| `govwatch_api_request_duration_seconds` | histogram | Upstream latency |
| `govwatch_api_retries_total{source,reason}` | counter | Retry pressure |
| `govwatch_api_ratelimit_remaining` | gauge | Headroom on api.data.gov rate limits |
| `govwatch_stored_records` | gauge | Rows stored per source |
| `govwatch_worker_heartbeat_timestamp_seconds` | gauge | Worker loop liveness |

`/healthz` returns 503 only if the worker loop itself is stuck. A failing upstream API is
reported through metrics and alerts rather than liveness, because restarting the pod
wouldn't fix it.

## Local development

```bash
uv sync
docker compose up -d postgres     # also creates the govwatch_test database
uv run pytest
uv run ruff check . && uv run ruff format --check src tests
```

The Postgres-backed tests use the `govwatch_test` database, which they wipe on every run,
and are skipped automatically if Postgres isn't reachable.

## Roadmap

**Phase 1: data platform**

- [x] congress.gov and regulations.gov clients
- [x] Incremental ingest with cursors, locking, change detection
- [x] Prometheus instrumentation
- [x] Docker image and local Compose stack
- [ ] Grafana dashboard and Prometheus alert rules
- [ ] Helm chart
- [ ] Terraform-provisioned kind cluster (cluster, monitoring stack, app)
- [ ] GitHub Actions: lint, test, Trivy, Checkov, image publish, kind smoke test

**Phase 2: agent layer + AgentOps**

- [ ] Local LLM agent (Ollama) that summarizes and tags newly changed bills
- [ ] Per-step latency, retry and failure metrics for the agent
- [ ] Output validation gate and drift alerts before a human-review queue

## License

MIT. See [LICENSE](LICENSE).
