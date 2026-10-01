# Decisions

A running log of the significant decisions behind govwatch: what I chose, why, and what
I considered instead. New entries go at the bottom. If a decision gets reversed, the old
entry stays and is marked superseded, so the reasoning history is kept.

| # | Decision | Area |
|---|---|---|
| 1 | [Postgres over SQLite](#1-postgres-over-sqlite) | Storage |
| 2 | [Plain SQL migrations with a small runner, not Alembic](#2-plain-sql-migrations-with-a-small-runner-not-alembic) | Storage |
| 3 | [A long-running worker, not a CronJob](#3-a-long-running-worker-not-a-cronjob) | Runtime |
| 4 | [Python 3.12, managed with uv](#4-python-312-managed-with-uv) | Runtime |
| 5 | [Retry policy for the upstream APIs](#5-retry-policy-for-the-upstream-apis) | Ingestion |
| 6 | [API key in a header, never the query string](#6-api-key-in-a-header-never-the-query-string) | Ingestion |
| 7 | [Cursors with a per-source overlap window](#7-cursors-with-a-per-source-overlap-window) | Ingestion |
| 8 | [The cursor only moves after a fully successful run](#8-the-cursor-only-moves-after-a-fully-successful-run) | Ingestion |
| 9 | [Content hash for change detection, excluding updateDate](#9-content-hash-for-change-detection-excluding-updatedate) | Ingestion |
| 10 | [regulations.gov: Eastern-time filters and window sliding](#10-regulationsgov-eastern-time-filters-and-window-sliding) | Ingestion |
| 11 | [congress.gov: dedupe and re-page with a different page size](#11-congressgov-dedupe-and-re-page-with-a-different-page-size) | Ingestion |
| 12 | [Per-source advisory locks](#12-per-source-advisory-locks) | Ingestion |
| 13 | [Lag and freshness gauges come from the database](#13-lag-and-freshness-gauges-come-from-the-database) | Observability |
| 14 | [/healthz means "loop not wedged", not "ingest working"](#14-healthz-means-loop-not-wedged-not-ingest-working) | Observability |
| 15 | [A lag SLO with multi-window burn-rate alerts](#15-a-lag-slo-with-multi-window-burn-rate-alerts) | Observability |
| 16 | [Alert thresholds](#16-alert-thresholds) | Observability |
| 17 | [Compute lag from last_over_time](#17-compute-lag-from-last_over_time) | Observability |
| 18 | [Grafana reads curated views through a read-only role](#18-grafana-reads-curated-views-through-a-read-only-role) | Observability |
| 19 | [A completeness auditor](#19-a-completeness-auditor) | Data quality |
| 20 | [Auto-repair on by default, but repairs stay visible](#20-auto-repair-on-by-default-but-repairs-stay-visible) | Data quality |
| 21 | [Bypass the congress.gov CDN for audit counts](#21-bypass-the-congressgov-cdn-for-audit-counts) | Data quality |
| 22 | [kind over k3d](#22-kind-over-k3d) | Platform |
| 23 | [CloudNativePG for Postgres in Kubernetes](#23-cloudnativepg-for-postgres-in-kubernetes) | Platform |
| 24 | [Migrations in an init container, not a Helm hook](#24-migrations-in-an-init-container-not-a-helm-hook) | Platform |
| 25 | [One replica, Recreate strategy](#25-one-replica-recreate-strategy) | Platform |
| 26 | [One source of truth for alert rules and the dashboard](#26-one-source-of-truth-for-alert-rules-and-the-dashboard) | Platform |
| 27 | [Hardened pod and a default-deny NetworkPolicy](#27-hardened-pod-and-a-default-deny-networkpolicy) | Security |
| 28 | [Secrets as mounted files, not environment variables](#28-secrets-as-mounted-files-not-environment-variables) | Security |
| 29 | [kube-prometheus-stack for in-cluster monitoring](#29-kube-prometheus-stack-for-in-cluster-monitoring) | Platform |
| 30 | [Terraform: local state, cluster-scoped providers, local image build](#30-terraform-local-state-cluster-scoped-providers-local-image-build) | IaC |
| 31 | [Actions pinned to commit SHAs, Dependabot grouped](#31-actions-pinned-to-commit-shas-dependabot-grouped) | CI/CD |
| 32 | [Checkov on rendered manifests, with documented skips](#32-checkov-on-rendered-manifests-with-documented-skips) | CI/CD |
| 33 | [Trivy gate on fixable HIGH/CRITICAL](#33-trivy-gate-on-fixable-highcritical) | CI/CD |
| 34 | [Database tests fail, not skip, in CI](#34-database-tests-fail-not-skip-in-ci) | CI/CD |
| 35 | [e2e against the live APIs](#35-e2e-against-the-live-apis) | CI/CD |
| 36 | [Python minor upgrades are deliberate, not Dependabot PRs](#36-python-minor-upgrades-are-deliberate-not-dependabot-prs) | CI/CD |

---

## 1. Postgres over SQLite

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** Store everything in PostgreSQL 17.

**Why:**
- The worker, migrations, the auditor and Grafana all hit the database concurrently, often
  from different pods. SQLite is a single file with a single writer, which doesn't fit.
- Advisory locks (#12) and `jsonb` for raw payloads come for free.
- `ON CONFLICT ... RETURNING` makes "how many rows actually changed" a single statement.
- Separate roles enable the least-privilege reporting layer (#18).
- It's what a real version of this would run on.

**Considered:** SQLite. It's simpler for a demo, but it would need rework the moment the
worker and the dashboards run in separate containers.

## 2. Plain SQL migrations with a small runner, not Alembic

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** Versioned `.sql` files in `src/govwatch/migrations`, applied by a roughly
40-line runner. Each migration runs in its own transaction, and the whole run holds a
Postgres advisory lock.

**Why:**
- There's no ORM in this codebase, so Alembic's autogenerate wouldn't buy anything.
- Plain SQL is easy to review.
- The advisory lock is what makes it safe to run from an init container (#24).
- Shipping migrations inside the package means they're always in the image.

**Considered:** Alembic, and yoyo-migrations. Both work, but they're more machinery than
four migrations need.

## 3. A long-running worker, not a CronJob

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** One long-lived process that polls every 15 minutes and serves `/metrics`.

**Why:**
- A CronJob pod lives for seconds, so Prometheus would rarely catch it in a scrape. It would
  need a Pushgateway, which brings its own staleness problems.
- A long-lived worker is scraped directly, and gauges like "last success" stay meaningful
  between runs.

**Considered:** A Kubernetes CronJob plus a Pushgateway.

## 4. Python 3.12, managed with uv

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** Pin Python 3.12 (`.python-version`, the Docker base image, CI) and use uv
with a committed lockfile.

**Why:**
- One version everywhere means the image runs exactly what the tests ran.
- uv is fast, and `uv sync --frozen` gives reproducible installs locally, in CI, and in
  the Docker build.

**Considered:** pip-tools, Poetry. See #36 for how version upgrades are handled.

## 5. Retry policy for the upstream APIs

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Retry 429, 500, 502, 503, 504 and connection errors, up to 4 retries.
- Exponential backoff with full jitter, capped at 60s.
- Honor `Retry-After`.
- Never retry other 4xx errors.
- 30s request timeout.

**Why:**
- A 403 means a bad key and a 400 a bad request; retrying wastes rate limit and hides the error.
- Full jitter avoids synchronized retries.
- 4 retries ride out a short upstream blip (a minute or two) without stalling a run for long.
- A failed run isn't lost anyway: the cursor doesn't move (#8), so the next cycle covers the
  same window.

## 6. API key in a header, never the query string

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** Send the key as `X-Api-Key`.

**Why:**
- URLs end up in logs (httpx logs them, as do proxies), and headers don't.
- There's a test asserting the key never appears in a request URL.

**Side effect:** the key isn't part of the CDN cache key either, which turned out to matter (#21).

## 7. Cursors with a per-source overlap window

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Each run pulls from `cursor - overlap` up to now. The overlap is 1 day for congress.gov
  and 15 minutes for regulations.gov.
- The cursor is set to the run's window end, not to the newest record seen.

**Why:**
- congress.gov's `updateDate` is a bare date, so anything finer than a day can miss
  updates. 15 minutes covers indexing delay on regulations.gov.
- Re-fetching the overlap is cheap thanks to the content hash (#9). It's about 2 extra
  requests per cycle.
- Using the window end means a quiet period with no new records still advances the cursor.

## 8. The cursor only moves after a fully successful run

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Batches of 250 commit independently.
- The cursor is written only after the last batch succeeds.
- Every run is recorded in `ingest_runs`.

**Why:**
- A failure partway through keeps whatever was already written, and the next run re-covers
  the whole window, idempotently.
- That's simpler and safer than tracking partial progress.

## 9. Content hash for change detection, excluding updateDate

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Hash the normalized fields, leaving out the source's own update timestamp.
- `last_changed_at` only moves when the hash changes.

**Why:**
- congress.gov bumps `updateDate` for changes we don't store.
- Downstream consumers (the Phase 2 agent) need "actually changed" to mean something, or
  they'd re-summarize the same bill every 15 minutes.
- Verified live: a second run over the same window shows `341 seen, 0 changed`.

## 10. regulations.gov: Eastern-time filters and window sliding

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Convert to US Eastern only when building the date filter; keep UTC everywhere else.
- Page at most 20 × 250 records per query, then restart the window from the last timestamp
  seen, skipping IDs from that boundary second.

**Why:**
- The filter is interpreted as Eastern time while responses are UTC. A filter from
  `00:00:00` returned records starting at `04:01Z`.
- The API caps paging depth. It rejects page numbers past 40, even though the docs say 20.
  Using 20 is safe under either limit.
- The filter only works to the second, so re-querying from the last timestamp returns that
  second's records again.

## 11. congress.gov: dedupe and re-page with a different page size

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Dedupe bills as they stream in, and compare the distinct count against the API's own
  `pagination.count`.
- If a pass is short, re-page the window with a different page size (250, then 230, then 190).

**Why:**
- congress.gov can only sort by `updateDate`, a bare date, so hundreds of bills tie.
- Rows tied across a page boundary come back on both sides of it, and their neighbours are
  skipped. This is deterministic.
- Measured on a 7-day window: 1,844 rows, 1,839 distinct. Every duplicate sat within a few
  rows of offset 750 or 1,500.
- Re-paging with the same page size skipped the same 5 bills three times in a row. Changing
  the page size moves the boundaries, and the union came to 1,844 of 1,844.

**How it was found:** the completeness auditor (#19) flagged 3 missing bills on the first
Terraform deploy.

## 12. Per-source advisory locks

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Each source's ingest runs under `pg_try_advisory_lock`.
- Whoever holds the lock marks any leftover `running` rows for that source as abandoned.

**Why:**
- Two workers (a rollout overlap, or an accidental second replica) can never ingest the same
  source at once.
- Holding the lock proves nobody else is running that source, so cleaning up a crashed
  worker's runs is safe without heartbeats or timeouts.

## 13. Lag and freshness gauges come from the database

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** After each run, the worker re-reads last success, newest record and audit
results from Postgres and sets its gauges from them.

**Why:**
- In-memory gauges start empty after a restart, so a freshly restarted pod would report
  no lag.
- Reading from the database means the metrics are true from the first scrape.

## 14. /healthz means "loop not wedged", not "ingest working"

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- The heartbeat updates once per loop cycle, whether the cycle succeeded or not.
- `/healthz` fails only if the heartbeat is older than 2× the poll interval plus 30 minutes.

**Why:**
- Liveness failures restart the pod, and restarting doesn't fix congress.gov being down.
- Upstream failures go to metrics and alerts instead.
- The 30-minute allowance covers a large first backfill.

## 15. A lag SLO with multi-window burn-rate alerts

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- SLO: each source's last successful ingest finished under 30 minutes ago, 99% of the time
  over 30 days. That's about 7.2 hours of error budget a month.
- Fast-burn alert at 14.4× (1h and 5m windows, critical); slow-burn at 6× (6h and 30m
  windows, warning).

**Why:**
- 30 minutes is two poll intervals, so one missed cycle is fine and two isn't.
- 99% suits a non-customer-facing data pipeline.
- The burn rates and window pairs are the Google SRE workbook defaults. The fast alert means
  the monthly budget would be gone in about 2 days. Requiring both the long and short window
  keeps it from firing on a blip, or long after recovery.

## 16. Alert thresholds

**Date:** 2026-09-30 · **Status:** accepted

| Alert | Threshold | Reasoning |
|---|---|---|
| `GovwatchWorkerDown` | `up == 0` for 2m | Long enough to ride out a pod restart, short enough to matter |
| `GovwatchWorkerStalled` | no heartbeat for 1h | Well past the longest normal cycle |
| `GovwatchIngestFailing` | 3+ failed runs in 1h | One failure is noise. Three in a row (about 45 min at 15-min polling) is a pattern |
| `GovwatchRegulationsDataStale` | no new document for 4h, weekdays 14:00–23:00 UTC only | Agencies publish during US business hours. Without the time gate it would fire every weekend |
| `GovwatchCongressDataStale` | no update for 4 days | Has to clear a long weekend. Long recesses will trip it, and that's worth a look anyway |
| `GovwatchUpstreamErrors` | over 20% of requests failing for 15m | Retries hide individual failures; this catches sustained degradation |
| `GovwatchRateLimitLow` | under 10% of the hourly limit left for 5m | regulations.gov allows 1,000/hour, so 10% is about 6 minutes of headroom at full speed |
| `GovwatchDataIncomplete` | completeness under 99% | Tolerates a record or two of mid-audit drift, not a real gap |
| `GovwatchRepeatedRepairs` | 2+ auto-repairs in 24h | One repair can be a fluke; two means something keeps dropping records |

All of these are unit tested with `promtool test rules`, including that the staleness alert
fires on a Thursday afternoon and stays quiet on a Saturday.

## 17. Compute lag from last_over_time

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** `govwatch:ingest_lag_seconds = time() - last_over_time(last_success[7d])`.

**Why:**
- When the worker dies, its series go stale and a plain `time() - last_success` simply
  disappears, so lag-based alerts go quiet at exactly the wrong moment.
- `last_over_time` keeps the last known value, so lag keeps climbing through the outage.
  This is covered by a rule test.

## 18. Grafana reads curated views through a read-only role

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Grafana logs in as `grafana_reader`, which only holds `govwatch_readonly`.
- That role has `SELECT` on the reporting views (`v_bill_activity`, `v_open_comment_periods`,
  and so on) and nothing else.

**Why:**
- Dashboards don't need the raw tables, and least privilege is cheap to set up from day one.
- Views also act as a stable interface: the tables can change without breaking dashboards.
- A test asserts the role gets "permission denied" on `bills`.

## 19. A completeness auditor

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Every 6 hours, ask each API how many records changed over the last 3 full UTC days, and
  compare that with Postgres.
- Store each result in `completeness_audits`.
- Only a shortfall counts as missing.
- Skip any window the ingest history doesn't fully cover.

**Why:**
- Lag, freshness and error rate prove the pipeline is running, not that it's complete.
- Silently dropped records are the failure that matters most for a data layer that agents
  and policy staff rely on.
- 3 days is far enough back that the window has settled, and recent enough to catch problems
  quickly.
- Records re-updated upstream briefly leave the API's count window before they leave ours,
  so local can exceed upstream harmlessly.

**Payoff so far:** it caught two real problems that no other metric showed (#11, #21).

## 20. Auto-repair on by default, but repairs stay visible

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- When an audit finds missing records, re-ingest that window without touching the cursor,
  then audit again.
- Count every repair, and alert on 2+ repairs in 24h.

**Why:**
- The goal is correct data, so fixing it automatically is right.
- But an auto-fix can hide a bug forever. The counter and the alert keep a recurring gap
  visible.
- The "Gaps found (7d)" panel reads from the audit table, so repairs run from the CLI show up too.

## 21. Bypass the congress.gov CDN for audit counts

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Audit count requests add a unique throwaway parameter.
- Any count response with an `Age` header over 60s is rejected.

**Why:**
- congress.gov's CDN caches by URL for 30 minutes, shared across all users, because the
  API key is a header (#6).
- Audit windows are midnight-aligned, so the count URL is identical all day.
- The auditor flagged 5 "missing" bills that were really a 16-minute-old cached count
  (`Age: 965`).
- A `Cache-Control: no-cache` request header was tested and ignored by the CDN. An unknown
  query parameter was accepted and forced a fresh response.
- The `Age` check covers it if that ever stops working.

## 22. kind over k3d

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** Use kind for both the local cluster and CI.

**Why:**
- kind was already installed and in use for other projects on this machine.
- It has a maintained Terraform provider (`tehcyx/kind`) and an official GitHub Action
  (`helm/kind-action`), so local and CI get the same cluster type.
- Its default CNI enforces NetworkPolicy, which was verified (#27).

**Considered:** k3d. It's lighter and has a built-in registry, but it would mean two cluster
types to support, and the Terraform story is weaker.

## 23. CloudNativePG for Postgres in Kubernetes

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** The chart can provision Postgres through the CloudNativePG operator.

**Why:**
- It generates the app's connection secret.
- It declares roles as code (`govwatch_readonly`, `grafana_reader`), so the app user never
  needs `CREATEROLE`.
- It's a CNCF project and a production-grade pattern.

**Considered:**
- Bitnami's Postgres chart: Bitnami moved most of its free images in 2025.
- A hand-rolled StatefulSet: more to maintain, and no role management.

## 24. Migrations in an init container, not a Helm hook

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** `govwatch migrate` runs as an init container before the worker starts.

**Why:**
- A pre-install hook runs before the chart's own resources, including the Secrets it would
  need to read.
- The migration runner's advisory lock (#2) makes concurrent pods during a rollout safe.

## 25. One replica, Recreate strategy

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** `replicas: 1`, `strategy: Recreate`.

**Why:**
- Advisory locks make a second replica safe, but it would only wait for the lock.
- Recreate avoids two versions running briefly during a rollout when there's nothing to
  gain from overlap.

## 26. One source of truth for alert rules and the dashboard

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Rules and dashboard JSON live only in `observability/`.
- Compose mounts them. The chart takes them as values (`--set-file`), and Terraform passes
  them with `file()`.

**Why:**
- Copies in the chart would drift.
- This way Compose and Kubernetes provably run the same alerts and dashboard, and the promtool
  tests cover both.

## 27. Hardened pod and a default-deny NetworkPolicy

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Run as non-root (uid 10001), with a read-only root filesystem, all capabilities dropped,
  `RuntimeDefault` seccomp, and no service account token.
- A NetworkPolicy allows egress only to DNS, the database pods and port 443, and ingress only
  on the metrics port from the monitoring namespace.

**Why:**
- The worker needs none of what's removed.
- Verified on kind: port 80 egress and scrapes from other namespaces are blocked.

## 28. Secrets as mounted files, not environment variables

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- The API keys and database URI are projected into `/var/run/secrets/govwatch` (mode
  `0440`, group-owned by the app user).
- pydantic-settings reads them from there via `GOVWATCH_SECRETS_DIR`.

**Why:**
- Environment variables leak into child processes, crash dumps and `/proc/<pid>/environ`.
- Checkov flagged it (CKV_K8S_35).
- Verified in a pod: zero secret variables in the environment.

## 29. kube-prometheus-stack for in-cluster monitoring

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Install kube-prometheus-stack, with etcd, scheduler, controller-manager and kube-proxy
  scraping turned off.
- Rule and monitor selectors accept every release.

**Why:**
- It's the standard Prometheus Operator setup, so the chart can ship `ServiceMonitor`,
  `PrometheusRule` and a dashboard ConfigMap like any real service.
- kind binds those control plane components to localhost, so scraping them only produces
  permanently-down targets and noisy alerts.

**Considered:** separate Prometheus and Grafana charts. More wiring, and no operator CRDs.

## 30. Terraform: local state, cluster-scoped providers, local image build

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Local state.
- Point the helm and kubernetes providers at the `kind_cluster` resource's own credentials.
- Give the Helm provider an isolated repo config inside `.terraform/`.
- Build the image locally and side-load it, tagged with a hash of `src/`.

**Why:**
- It's a throwaway local cluster with nothing to share, so local state is fine. A shared
  environment would use a remote backend with locking.
- Using the cluster's own credentials means an apply can never touch whatever cluster
  `kubectl` happens to point at.
- The first apply failed because of a broken global Helm repo on this machine. Isolating the
  config removes that dependency.
- The hash tag makes a code change roll the deployment without needing a registry.

**Verified:** a clean apply took about 5 minutes, a second plan showed no changes, and
destroy took about 25 seconds.

## 31. Actions pinned to commit SHAs, Dependabot grouped

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Every third-party action is pinned to a full commit SHA, with the version in a comment.
- Dependabot opens weekly grouped PRs for actions, uv, Docker and Terraform.
- The workflow token defaults to `contents: read`.

**Why:**
- A tag can be moved to malicious code; a SHA can't.
- Grouping keeps the update PRs to a handful a week.

## 32. Checkov on rendered manifests, with documented skips

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** Render the chart with production-like values and scan the manifests. Three
checks are skipped, each with its reason in an annotation on the Deployment:

- **No CPU limit (CKV_K8S_11):** CFS throttling hurts a bursty, I/O-bound worker. A CPU
  request plus a memory limit is the better trade-off.
- **Pull policy isn't `Always` (CKV_K8S_15):** images side-loaded into kind aren't in any
  registry, so `Always` would break them. It's a value.
- **Tag instead of digest (CKV_K8S_43):** `image.digest` exists and CI can use it. Local
  builds use a content-hash tag.

**Why:** Checkov's Helm mode renders default values, which deliberately fail without secrets,
so it silently scanned nothing. Rendered, the result is 90 passed, 0 failed, 3 skipped.

## 33. Trivy gate on fixable HIGH/CRITICAL

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Fail the image build on HIGH or CRITICAL vulnerabilities that have a fix available.
- Ignore unfixed ones.
- Run `apt-get upgrade` in the runtime stage.

**Why:**
- Unfixed CVEs can't be acted on and would just make the gate permanently red.
- The first scan found 7 fixable OpenSSL CVEs that hadn't reached `python:3.12-slim` yet;
  upgrading packages in the image cleared them.
- The gate also blocked the Python 3.14 Dependabot PR (#36).

## 34. Database tests fail, not skip, in CI

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Locally, Postgres-backed tests skip if no database is running.
- In CI, `GOVWATCH_REQUIRE_DB=1` turns that skip into a failure.

**Why:** Skipping is convenient on a laptop. In CI it would let a broken service container
quietly drop a third of the suite while the build stays green.

## 35. e2e against the live APIs

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- The e2e job deploys to a throwaway kind cluster and runs a real ingest against congress.gov
  and regulations.gov.
- It then runs `govwatch audit`, which fails the build if anything is missing.
- It needs an `API_DATA_GOV_KEY` secret, and skips with a notice without it.

**Why:**
- Recorded fixtures prove the parsing; only live data proves the paging and windowing
  against today's API behaviour. Both upstream quirks (#11, #21) only showed up live.
- It costs about 10 requests per run, well inside the rate limits.

## 36. Python minor upgrades are deliberate, not Dependabot PRs

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Dependabot only proposes patch updates for the `python` base image.
- Moving 3.12 to 3.13 or 3.14 is a separate change that updates `.python-version`,
  `uv.lock`, CI and the image together.

**Why:**
- Dependabot opened a 3.12 → 3.14 base image PR. Merging it would have shipped a Python
  version the test suite never ran on, because CI tests with `.python-version`.
- That PR's image also failed the Trivy gate.
