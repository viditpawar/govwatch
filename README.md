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
idempotent upserts, change detection, SLO-based alerting, and a **completeness auditor**
that reconciles every source's own record counts against what's stored and re-ingests
any gap it finds.

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

Every 6 hours it also audits completeness (see below).

## Completeness auditing

Lag and freshness metrics prove the pipeline is *running*. They can't prove it's
*complete*: a paging bug or an edge case at a window boundary can silently drop records
while every dashboard stays green.

So govwatch checks against the source of truth:

1. Pick the last 3 full UTC days, but only if the ingest history shows they've been fully covered.
2. Ask each API how many records changed in that window (one cheap request per source).
3. Count the same window in Postgres, and record both numbers in `completeness_audits`.
4. If anything is missing, **re-ingest that window** (without touching the cursor) and audit again.

```text
$ govwatch audit --repair
regulations: audit 2026-09-28 -> 2026-10-01: upstream 1286, stored 1261, missing 25 (98.06%)
regulations: 25 records missing, re-ingesting 2026-09-28T00:00:00+00:00 -> 2026-10-01T00:00:00+00:00
regulations: audit 2026-09-28 -> 2026-10-01: upstream 1286, stored 1286, missing 0 (100.00%)
```

Against live data, a 4-day backfill reconciles exactly: 1,060 / 1,060 bills and
1,286 / 1,286 regulatory documents. That's direct evidence the paging, the Eastern-time
filters and the window-sliding logic don't lose records.

Repairs are counted (`govwatch_completeness_repairs_total`), and repeated repairs raise
their own alert. Auto-repair keeps the data correct, but a gap that keeps coming back is
still a bug, and it shouldn't be hidden by the fix.

The auditor has already caught two real problems, both in congress.gov's API:

1. **A stale CDN count.** On a fresh Kubernetes deploy it reported 5 bills missing, and a
   re-ingest didn't fix it. The data was fine: congress.gov's CDN was serving a
   16-minute-old cached *count* (`Age: 965`, `Cf-Cache-Status: HIT`) while the listing was
   fresh. Audit requests now bypass the cache and reject stale responses.
2. **Bills skipped at page boundaries.** On the first Terraform deploy, a 7-day backfill
   reported `1844 seen, 1838 changed`. Those two numbers should be equal on an empty
   database, so the API had returned 6 duplicates. The audit then found 3 bills missing.
   Auto-repair restored them within a second, but the root cause was in paging: bills tied
   on `updateDate` across a page boundary are returned twice and their neighbours skipped,
   the same way on every request. The client now dedupes and re-pages with a different page
   size until it matches the API's count.

Neither problem shows up on lag, freshness, or error-rate metrics. Both would have meant
silently incomplete data.

## Features

- Incremental ingestion from congress.gov and regulations.gov with per-source cursors
- Idempotent, batched upserts with content hashing to tell real changes from re-fetches
- Full raw API payload kept as `jsonb` alongside normalized columns
- Retries with exponential backoff and jitter on 429/5xx/network errors, honors `Retry-After`
- Safe to run multiple workers: per-source advisory locks, abandoned-run cleanup
- Graceful shutdown on SIGTERM (finishes the current run, then exits)
- Prometheus metrics for lag, freshness, failure rate, throughput, API latency, and rate-limit headroom
- SLO-based alerting (99% "pipeline keeping up") with multi-window burn-rate alerts, unit tested with `promtool`
- Business-hours-aware freshness alerts that don't fire on quiet weekends
- Grafana dashboard covering both pipeline health and policy activity: comment periods closing soon, latest bill actions, most active agencies
- Least-privilege reporting layer: Grafana reads curated SQL views through a role that can't touch raw tables
- Runbook entry for every alert
- Multi-stage, non-root Docker image; one-command local stack with Prometheus and Grafana
- Completeness auditor that reconciles upstream record counts against Postgres and self-heals gaps
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
| congress.gov can only sort by `updateDate` (a bare date), so hundreds of bills tie, and rows tied across a page boundary come back on both sides of it while their neighbours are skipped. It's deterministic, so re-paging the same way skips the same bills | Results are deduped as they stream and checked against the API's own count. If a pass is short, the window is paged again with a different page size (250, then 230, then 190) so the boundaries move. A 7-day window went from 1,839 / 1,844 to 1,844 / 1,844 |
| A crash mid-run | Batches commit independently and the cursor doesn't move, so the next run safely covers the same window |
| The API key showing up in logs | The key is sent as an `X-Api-Key` header, never as a query parameter |
| A record re-updated upstream leaves the source's count window before it leaves ours | The auditor only counts a shortfall as missing; extra local rows are expected drift |
| congress.gov sits behind a CDN that caches responses by URL for 30 min, **shared across all API users** (the key is a header, not part of the cache key) | Audit counts send a unique throwaway param so each call gets its own cache entry, and any count response with `Age` over 60s is rejected. Found when the auditor flagged 5 "missing" bills on a fresh ingest that were really a 16-minute-old cached count |

## Tech stack

- **Language:** Python 3.12, httpx, psycopg 3, pydantic-settings
- **Storage:** PostgreSQL 17, with plain-SQL migrations and a small runner guarded by an advisory lock
- **Observability:** prometheus-client, Prometheus 3 (recording rules, SLO burn-rate alerts, promtool tests), Grafana 12
- **Packaging:** uv, Docker (multi-stage, non-root), Docker Compose
- **Kubernetes:** Helm, CloudNativePG (Postgres operator), Prometheus Operator CRDs, NetworkPolicy
- **Infrastructure as code:** Terraform (kind, helm, kubernetes, random providers)
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
│   ├── audit.py              # completeness auditor + self-healing re-ingest
│   ├── worker.py             # poll loop, signal handling, heartbeat
│   ├── metrics.py            # every Prometheus metric in one place
│   ├── server.py             # /metrics and /healthz
│   ├── db.py                 # connections + migration runner
│   ├── config.py             # GOVWATCH_* settings
│   └── __main__.py           # CLI
├── tests/                    # unit + Postgres-backed tests, recorded API fixtures
├── observability/
│   ├── prometheus/rules/     # recording rules, SLOs, alerts
│   ├── prometheus/tests/     # promtool unit tests for the rules
│   └── grafana/dashboards/   # dashboard json (shared by compose and k8s)
├── charts/govwatch/          # Helm chart: worker, CloudNativePG Postgres, monitoring CRs
├── infra/                    # Terraform: kind cluster, operators, monitoring stack, app
├── deploy/compose/           # Prometheus config, Grafana provisioning, db init
├── docs/runbook.md           # one section per alert
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
| `GOVWATCH_AUDIT_INTERVAL_SECONDS` | `21600` | How often to run completeness audits |
| `GOVWATCH_AUDIT_WINDOW_DAYS` | `3` | How many full days back each audit reconciles |
| `GOVWATCH_AUDIT_AUTO_REPAIR` | `true` | Re-ingest an audit window when records are missing |
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

## Kubernetes with Terraform

One `terraform apply` stands up the whole platform on a local kind cluster. No cloud
account or registry is needed:

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # add your api key
terraform init
terraform apply
```

| Resource | What it does |
|---|---|
| `kind_cluster` | Single-node cluster (Kubernetes 1.36), with Grafana and Prometheus mapped to localhost |
| `terraform_data.image` | Builds the image from this repo and side-loads it into the node. The tag includes a content hash of `src/`, so code changes roll the deployment |
| `helm_release.cnpg` | CloudNativePG operator |
| `helm_release.kube_prometheus_stack` | Prometheus, Alertmanager, Grafana. Picks up ServiceMonitors and rules from every namespace; the Grafana sidecar loads the govwatch dashboard; Postgres data source configured as `grafana_reader` |
| `random_password` x2 | Grafana admin password, and the read-only database role's password |
| `kubernetes_secret_v1` x2 | API keys, and the `grafana_reader` credentials that CloudNativePG applies to the managed role |
| `helm_release.govwatch` | The chart, with alert rules and the dashboard read straight from `observability/` |

Then:

| | |
|---|---|
| Grafana | http://localhost:30300 (anonymous read-only; admin password: `terraform output -raw grafana_admin_password`) |
| Prometheus | http://localhost:30090 |
| kubectl | `kubectl --context kind-govwatch -n govwatch get pods` |

`terraform destroy` removes everything.

Tested end to end:
- **Apply:** a clean apply takes about 5 minutes, most of it pulling kube-prometheus-stack images.
- **Idempotent:** a second `plan` reports no changes.
- **Destroy:** takes about 25 seconds.

Some details:

- **Isolated Helm config.** The Helm provider uses its own repo config and cache inside
  `.terraform/`, so a broken global Helm repo list on the machine running it can't break
  the apply. That happened during development.
- **Providers are wired to the cluster resource's own credentials,** not the current
  kubectl context, so applying can never touch a different cluster.
- **Local state on purpose.** This is a throwaway local environment. A shared environment
  would use a remote backend with locking.
- **The provider lock file is committed** with checksums for Windows, Linux, and macOS.

## Kubernetes (Helm)

The chart in [charts/govwatch](charts/govwatch) runs the worker on any cluster.

```bash
# cloudnative-pg operator, if you want the chart to provision postgres
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm install cnpg cnpg/cloudnative-pg -n cnpg-system --create-namespace --wait

kubectl create namespace govwatch
kubectl -n govwatch create secret generic govwatch-api-keys \
  --from-literal=congress-api-key=$KEY --from-literal=regulations-api-key=$KEY
kubectl -n govwatch create secret generic grafana-reader --type=kubernetes.io/basic-auth \
  --from-literal=username=grafana_reader --from-literal=password=$(openssl rand -hex 16)

helm install govwatch charts/govwatch -n govwatch \
  --set apiKeys.existingSecret=govwatch-api-keys \
  --set database.cnpg.enabled=true \
  --set database.cnpg.grafanaReader.passwordSecret=grafana-reader
```

With kube-prometheus-stack, also enable the ServiceMonitor, the PrometheusRule and the
dashboard ConfigMap. The rules and dashboard are passed in from `observability/`, so
Compose and Kubernetes always run the same files:

```bash
  --set monitoring.serviceMonitor.enabled=true \
  --set monitoring.prometheusRule.enabled=true \
  --set monitoring.dashboard.enabled=true \
  --set-file monitoring.prometheusRule.rules=observability/prometheus/rules/govwatch.rules.yml \
  --set-file monitoring.dashboard.json=observability/grafana/dashboards/govwatch.json
```

How the chart is set up:

- **Migrations run in an init container,** not a Helm hook. Pre-install hooks run before the
  chart's Secrets exist. Concurrent pods during a rollout are safe because the migration
  runner holds a Postgres advisory lock.
- **Postgres comes from CloudNativePG** (optional). It generates the app's connection
  secret, and declares `govwatch_readonly` and `grafana_reader` as managed roles, so the app
  user never needs `CREATEROLE`.
- **The pod is locked down:** non-root, read-only root filesystem, all capabilities dropped,
  `RuntimeDefault` seccomp, and no service account token.
- **A NetworkPolicy** allows egress only to DNS, the database pods, and 443 (the two APIs),
  and ingress only on the metrics port from the monitoring namespace. Tested on kind: port 80
  egress and scrapes from other namespaces are blocked.
- **`values.schema.json`** rejects bad config at install time, e.g. a poll interval under 60s.

## CLI

```bash
govwatch migrate                         # apply pending migrations
govwatch run                             # long-running worker (what the container runs)
govwatch ingest [--source congress]      # one cycle, non-zero exit on failure
govwatch audit [--source X] [--repair]   # reconcile upstream counts vs stored, non-zero exit on gaps
govwatch peek regulations --days 1       # print recent records from the live API, no db writes
```

## Dashboard

Grafana opens straight to the govwatch dashboard, which has two sections:

- **Pipeline health** (Prometheus): ingestion lag, newest-record age, 24h run success rate,
  remaining SLO error budget, runs per hour, records seen vs actually changed, API latency
  p50/p95, responses by status code, and rate-limit headroom.
- **Data completeness**: completeness per source, missing records, gaps found in the last
  7 days, and the audit history.
- **Policy activity** (Postgres): daily document and bill activity, the most active agencies
  over the last 7 days, comment periods closing in the next 14 days (linked to
  regulations.gov), and the latest bill actions (linked to congress.gov).

The policy panels query a small set of reporting views (`v_open_comment_periods`,
`v_bill_activity`, `v_daily_activity`, `v_agency_activity`) as `grafana_reader`. That role
only holds `govwatch_readonly`, which has `SELECT` on those views and nothing else.

## Alerting and SLOs

**SLO:** each source's last successful ingest finished less than 30 minutes ago, 99% of
the time over 30 days. That's about 7 hours of error budget a month.

| Alert | Severity | Fires when |
|---|---|---|
| `GovwatchWorkerDown` | critical | Prometheus can't scrape the worker for 2m |
| `GovwatchWorkerStalled` | critical | Worker loop hasn't completed a cycle in 1h |
| `GovwatchLagBudgetBurnFast` | critical | Error budget burning at 14.4x (1h and 5m windows) |
| `GovwatchLagBudgetBurnSlow` | warning | Error budget burning at 6x (6h and 30m windows) |
| `GovwatchIngestFailing` | warning | 3+ failed runs for a source in 1h |
| `GovwatchRegulationsDataStale` | warning | No new regulations.gov documents in 4h, **weekday business hours only** |
| `GovwatchCongressDataStale` | warning | No congress.gov updates in 4 days (clears a long weekend) |
| `GovwatchUpstreamErrors` | warning | Over 20% of API requests failing for 15m |
| `GovwatchRateLimitLow` | warning | Under 10% of the api.data.gov hourly limit left |
| `GovwatchDataIncomplete` | warning | Last audit found under 99% of the source's records stored |
| `GovwatchRepeatedRepairs` | warning | Auto-repair needed 2+ times in 24h (something keeps dropping records) |
| `GovwatchCompletenessAuditStale` | info | No completed audit in 24h |
| `GovwatchMalformedRecords` | info | A source returned records that couldn't be parsed |

Some details that matter:

- Lag is computed from `last_over_time(...)`, so it **keeps increasing while the worker is
  down** instead of disappearing along with the scrape target.
- The burn-rate alerts follow the multi-window approach from the Google SRE workbook. A
  short blip doesn't page, but a sustained outage does, within minutes.
- The rules have unit tests (`observability/prometheus/tests`) covering, among other things,
  that the regulations staleness alert fires on a Thursday afternoon and stays quiet on a
  Saturday.

Each alert links to its section in [docs/runbook.md](docs/runbook.md).

## Data model

| Table | Purpose |
|---|---|
| `bills` | One row per bill (`119-hr-1234`): normalized fields, raw payload, `content_hash`, `first_seen_at`, `last_changed_at` |
| `regulatory_documents` | One row per regulations.gov document: docket, agency, type, comment period, raw payload |
| `sync_cursors` | High watermark per source |
| `ingest_runs` | Audit trail: window, status, records seen/changed, API requests, error |
| `completeness_audits` | Upstream vs stored counts for each audit window |

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
| `govwatch_api_paging_repasses_total` | counter | Extra passes over a window because paging came up short of the API's count |
| `govwatch_stored_records` | gauge | Rows stored per source |
| `govwatch_completeness_ratio` | gauge | Share of the source's records for the audit window that are stored |
| `govwatch_completeness_missing_records` | gauge | Records the source has that we don't |
| `govwatch_completeness_repairs_total` | counter | Times a gap was found and the window re-ingested |
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

Validate and test the alert rules (no local Prometheus install needed):

```bash
docker run --rm -v "$PWD/observability:/obs:ro" --entrypoint promtool prom/prometheus:v3.5.0   check rules /obs/prometheus/rules/govwatch.rules.yml
docker run --rm -v "$PWD/observability:/obs:ro" --entrypoint promtool prom/prometheus:v3.5.0   test rules /obs/prometheus/tests/govwatch.rules.test.yml
```

## Roadmap

**Phase 1: data platform**

- [x] congress.gov and regulations.gov clients
- [x] Incremental ingest with cursors, locking, change detection
- [x] Prometheus instrumentation
- [x] Docker image and local Compose stack
- [x] Grafana dashboard, SLO burn-rate alerts, runbook
- [x] Completeness auditor with self-healing re-ingest
- [x] Helm chart (CloudNativePG Postgres, hardened pod, NetworkPolicy, monitoring CRs)
- [x] Terraform-provisioned kind cluster (cluster, operators, monitoring stack, app)
- [ ] GitHub Actions: lint, test, Trivy, Checkov, image publish, kind smoke test

**Phase 2: agent layer + AgentOps**

- [ ] Local LLM agent (Ollama) that summarizes and tags newly changed bills
- [ ] Per-step latency, retry and failure metrics for the agent
- [ ] Output validation gate and drift alerts before a human-review queue

## License

MIT. See [LICENSE](LICENSE).
