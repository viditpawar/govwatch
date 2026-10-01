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
| 37 | [qwen2.5:3b as the agent model](#37-qwen253b-as-the-agent-model) | Agent |
| 38 | [The LLM summarizes and tags; code determines facts](#38-the-llm-summarizes-and-tags-code-determines-facts) | Agent |
| 39 | [Schema-constrained decoding over a fixed CRS vocabulary](#39-schema-constrained-decoding-over-a-fixed-crs-vocabulary) | Agent |
| 40 | [Summaries are grounded in source text, never a title alone](#40-summaries-are-grounded-in-source-text-never-a-title-alone) | Agent |
| 41 | [The official policy area is the fact; the model's pick is a shadow check](#41-the-official-policy-area-is-the-fact-the-models-pick-is-a-shadow-check) | Agent |
| 42 | [One retry with feedback, then a human; never drop a bill](#42-one-retry-with-feedback-then-a-human-never-drop-a-bill) | Agent |
| 43 | [Stage rules are chamber-aware](#43-stage-rules-are-chamber-aware) | Agent |
| 44 | [What gets summarized, and when](#44-what-gets-summarized-and-when) | Agent |
| 45 | [A small server-rendered review app](#45-a-small-server-rendered-review-app) | Review |
| 46 | [No auth on the review app, so loopback only](#46-no-auth-on-the-review-app-so-loopback-only) | Review |
| 47 | [Review rules: FIFO, reasons for rejections, no overwrites](#47-review-rules-fifo-reasons-for-rejections-no-overwrites) | Review |
| 48 | [Store the exact source text the model saw](#48-store-the-exact-source-text-the-model-saw) | Review |
| 49 | [Fail fast when the model is unavailable](#49-fail-fast-when-the-model-is-unavailable) | Agent |
| 50 | [The agent runs inside the worker loop](#50-the-agent-runs-inside-the-worker-loop) | AgentOps |
| 51 | [Quality and drift from stored summaries, recent vs baseline](#51-quality-and-drift-from-stored-summaries-recent-vs-baseline) | AgentOps |
| 52 | [Separate input drift from output drift](#52-separate-input-drift-from-output-drift) | AgentOps |
| 53 | [Each process exports only its own metrics](#53-each-process-exports-only-its-own-metrics) | Observability |
| 54 | [A golden-set model eval in CI](#54-a-golden-set-model-eval-in-ci) | AgentOps |
| 55 | [Measure grounding, because nothing else caught a title-only regression](#55-measure-grounding-because-nothing-else-caught-a-title-only-regression) | AgentOps |
| 56 | [The agent in Kubernetes uses the host's Ollama](#56-the-agent-in-kubernetes-uses-the-hosts-ollama) | Platform |
| 57 | [Component labels on every selector](#57-component-labels-on-every-selector) | Platform |

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

## 37. qwen2.5:3b as the agent model

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** Run the Phase 2 agent on `qwen2.5:3b` through a local Ollama, with temperature 0.

**Why:**
- The dev machine has an RTX 3050 with 4 GB VRAM. A 3B model fits entirely on the GPU; a 7B
  model doesn't.
- I benchmarked three installed models on real bills from the pipeline, with the same prompts:

| | llama3.2:3b | qwen2.5:3b | qwen2.5:7b |
|---|---|---|---|
| Fits in 4 GB VRAM | yes | yes | no (2.3 of 5.1 GB on GPU) |
| Warm latency per bill (median) | 3.5 s | 3.2 s | 13.2 s (1 bill) |
| Official policy area matched (10 bills, schema-enforced) | 4/10 | 6/10 | not run |

- qwen2.5:3b was the fastest and the most accurate of the models that fit.
- The 7B model was more careful but about 4× slower, because of the CPU offload.
- At about 350 changed bills a day, 3.2 s each is under 20 minutes of model time a day.

**Considered:** qwen2.5:7b. It would be the pick on a GPU with 8 GB or more. Swapping it in
is a config change, and the benchmark above can be re-run to justify it.

**Caveat:** 6/10 means a small local model is a drafting aid, not an authority. That's the
reason for #38–#40 and for the human review step.

## 38. The LLM summarizes and tags; code determines facts

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- The bill's legislative stage (introduced, in committee, passed one chamber, passed both,
  enacted) is derived by code from the action data.
- The model only writes the plain-language summary and picks a policy area.

**Why:**
- On 6 real bills, with the latest-action text right in the prompt, llama3.2:3b got the
  stage right 1/6 times and qwen2.5:3b 3/6. llama labelled two Senate resolutions "enacted".
- The stage is a deterministic fact, and code gets it right every time.
- General rule for the agent: don't ask a model for anything code can work out exactly. Use
  code-derived facts as context for the model and as checks on what it claims.

## 39. Schema-constrained decoding over a fixed CRS vocabulary

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- Pass a JSON Schema to Ollama's `format`, so generation is constrained to it.
- The policy area must be one of congress.gov's 32 CRS policy areas.
- Score the model against the official `policyArea` congress.gov assigns.

**Why:**
- With a plain JSON instruction, qwen2.5:3b invented stages (`"submitted"`, `"considered"`)
  on 2 of 6 bills. With the schema enforced during decoding, both models returned 10/10 valid
  values.
- Using CRS's own vocabulary means there's ground truth to score against. congress.gov
  assigns an official area to most bills, so "agreement with CRS" becomes a real accuracy
  metric to track over time and alert on (drift), instead of guessing at quality.
- The validation gate still re-checks everything after generation, since constrained
  decoding controls the format, not whether the content is true.

## 40. Summaries are grounded in source text, never a title alone

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- The agent summarizes from source text: congress.gov's CRS summary when one exists, plus
  the title and actions.
- Bills with no substantive text yet get a title-only stub, clearly marked as such, instead
  of a generated summary.

**Why:**
- Given only the title "GLRI Act of 2026" (the Great Lakes Restoration Initiative),
  llama3.2:3b produced a confident summary about national water infrastructure and climate
  change, none of which comes from the title.
- A fluent invented summary is worse than none for policy staff, because it reads as
  authoritative.
- The validation gate will also flag summaries that mention specifics (dollar amounts,
  agencies, dates) that don't appear in the source text.

## 41. The official policy area is the fact; the model's pick is a shadow check

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- The stored policy area is always the one congress.gov assigns.
- The model still picks one (schema-constrained, #39). Its pick is recorded and compared
  with the official area, but never shown as the answer.

**Why:**
- In a sample of 20 recently changed bills, 20/20 already had an official policy area. By
  #38's rule, that makes it a fact, not something to generate.
- Keeping the model's prediction costs nothing extra (it's the same call as the summary) and
  gives a free, continuous accuracy signal against ground truth:
  `govwatch_agent_policy_area_checks_total{result="match|mismatch"}`.
- A drop in that agreement rate is the drift signal for step 16. It means the model, the
  prompt or the incoming bills have changed.
- Baseline from the first live batch: qwen2.5:3b agreed with CRS on 50/86 bills (58%).

## 42. One retry with feedback, then a human; never drop a bill

**Date:** 2026-09-30 · **Status:** accepted

**Decision:**
- If the validation gate rejects an output, re-prompt once with the rejection reasons added.
- If the second attempt also fails, store it as `needs_attention`, with its issues, for a
  person.
- If the model or the API is down, store nothing, so the bill is picked up on the next run.

**Why:**
- On the first live batch of 86 summaries: 83 passed first time, 2 were fixed by the retry,
  and 1 failed twice.
- A second retry would mostly burn GPU time repeating the same mistake.
- Keeping rejected outputs, instead of discarding them, means the review queue shows what the
  model got wrong, which is useful for improving prompts.
- Every bill ends up in exactly one stored outcome (`pending_review`, `needs_attention` or
  `stub`), so nothing disappears silently.

**What the one failure taught us:**
- The model wrote "CDC" where the CRS text spelled out "Centers for Disease Control and
  Prevention", and the acronym check flagged it as ungrounded. It was a false positive.
- The check now accepts acronyms that match the initials of a capitalized name in the source,
  and still catches invented ones (a test covers both).
- Re-running the fixed gate over all 86 stored summaries: 0 issues.

## 43. Stage rules are chamber-aware

**Date:** 2026-09-30 · **Status:** accepted (refines #38)

**Decision:** When a bill's latest action happens in the other chamber ("Received in the
Senate" on a House bill, "Message on Senate action sent to the House", "Held at the desk"),
the stage is at least `passed_one_chamber`.

**Why:**
- Checked against 60 distinct real latest-action texts, the first version of the rules
  misread 5. The worst case: a House bill sitting in a Senate committee came out as "reported"
  when the important fact is that it had already passed the House.
- All 5 are now test cases, using the real text.
- Two stages were added beyond the original five:
  - `reported`: out of committee or on a calendar
  - `adopted`: simple resolutions, which only need their own chamber
- It's still coarse, since it's derived from one line of text, not the full action history.
  If that ever isn't good enough, the next step is reading congress.gov's per-bill actions
  endpoint.

## 44. What gets summarized, and when

**Date:** 2026-09-30 · **Status:** accepted

**Decision:** A bill is (re)summarized when any of these is true:
- it has never been summarized
- its `content_hash` changed since the last summary (#9)
- its last summary used an older `PROMPT_VERSION` and no human has reviewed it yet
- its last result was a stub more than 24 hours ago

Newest-changed bills go first, in bounded batches.

**Why:**
- Tying summaries to the content hash means "re-fetched but unchanged" bills cost nothing.
- Bumping the prompt version re-summarizes everything automatically, except what a reviewer
  already approved or rejected. Their work isn't thrown away.
- Stubs are the common case: 154 of the first 240 bills had no CRS summary yet, because CRS
  writes them days or weeks after introduction. Re-checking daily picks them up when they
  appear, at a cost of 2 cheap API calls per check.

## 45. A small server-rendered review app

**Date:** 2026-10-01 · **Status:** accepted

**Decision:** FastAPI with Jinja2 templates and plain HTML forms, with no JavaScript and no
separate frontend build. It runs as its own process (`govwatch review`) next to the worker.

**Why:**
- The job is narrow: read a summary next to its source, then approve or reject it. That's
  two pages and one form.
- A single-page app would add a build toolchain and an API layer for no gain.
- Server-rendered templates escape model output by default. Model output is untrusted text,
  and there's a test that a `<script>` in a summary is rendered as text.
- It's a separate process from the worker, so the review UI can restart without touching
  ingestion, and vice versa.
- It exposes its own `/metrics`, scraped as a separate Prometheus job.

## 46. No auth on the review app, so loopback only

**Date:** 2026-10-01 · **Status:** accepted for local use

**Decision:**
- The review app has no authentication.
- `govwatch review` binds to 127.0.0.1 by default, and Compose publishes it only on
  `127.0.0.1:8080`.
- Reviewers type their name, and it's stored with each decision.

**Why:**
- This is a single-user local demo. Building a login system into it would be the wrong kind
  of work.
- A shared deployment would put it behind an identity-aware proxy (oauth2-proxy or the
  cluster's SSO), take the reviewer from the verified identity header instead of a form
  field, and add CSRF protection.
- That's where the line is, and it's written down here so nobody mistakes the local setup for
  a production one.

## 47. Review rules: FIFO, reasons for rejections, no overwrites

**Date:** 2026-10-01 · **Status:** accepted

**Decision:**
- The queue is oldest first.
- A rejection requires a note.
- A decision only applies if the summary is still waiting and is still the newest one for its
  bill, otherwise it gets a 409.
- After a decision, the app goes straight to the next item.

**Why:**
- Oldest first keeps the "oldest waiting" age (the metric step 16 alerts on) honest.
- A rejection without a reason can't be used to improve the prompt or the gate.
- The no-overwrite rule is enforced in the `UPDATE ... WHERE` itself, not by reading first and
  checking. That means two reviewers can't overwrite each other, and a reviewer can't approve
  text the agent has since replaced. Both cases have tests.

**What the first live session found:** reviewing real output turned up a second gate false
positive ("USDA" for a source that only says "Department of Agriculture"). The acronym check
now allows an implied "U.S." prefix, and the case is a test. Of the 2 `needs_attention` items
in that batch:
- 1 was that false positive.
- 1 was a real catch: the model invented "fiscal years 2020 through 2030" when the source
  only says "through FY2030".

## 48. Store the exact source text the model saw

**Date:** 2026-10-01 · **Status:** accepted

**Decision:** Each summary row stores the CRS text that went into the prompt
(`source_text`), and the review page shows that stored text, not a fresh fetch.

**Why:**
- CRS revises summaries. Re-fetching at review time could show the reviewer different text
  from what the model was given, which makes "is this summary faithful?" impossible to answer.
- It also makes every summary auditable after the fact, and lets the gate be re-run over
  stored outputs offline (that's how both false positives were measured).
- The cost is a few KB per summary.

## 49. Fail fast when the model is unavailable

**Date:** 2026-10-01 · **Status:** accepted (refines #42)

**Decision:**
- Before a batch touches any bills, check that Ollama answers and has the configured model
  pulled. If not, stop with one sentence saying how to fix it ("Start the Ollama app",
  "Run: ollama pull qwen2.5:3b").
- If Ollama goes away mid-batch, stop the batch. Nothing is stored for the remaining bills, so
  the next run picks them up.
- Expected failures (model or API errors) log one line. Tracebacks are kept for actual bugs.

**Why:**
- The first real run after a reboot, with Ollama not running, printed a full traceback for
  every bill and worked through all 30, each with retries and backoff. That's slow, and it
  buries a one-word cause ("not running") under hundreds of lines.
- A missing dependency isn't a per-bill failure. Treating it like one also inflates the
  `failed` count, which would make the step 15 failure-rate alerts fire for the wrong reason.
- #42 still holds for per-bill problems: one bad bill doesn't stop the batch.

## 50. The agent runs inside the worker loop

**Date:** 2026-10-01 · **Status:** accepted

**Decision:**
- With `GOVWATCH_AGENT_ENABLED=true`, the worker runs one agent batch (25 bills by default)
  after each ingest cycle and its audits.
- In Compose, the worker reaches Ollama on the host via `host.docker.internal`, since the
  model needs the GPU.
- `govwatch agent` still exists for one-off runs.

**Why:**
- Run only from the CLI, the agent's metrics (per-step latency, model latency, gate
  rejections) died with the process, so nothing ever scraped them. That's the same reason
  the worker isn't a CronJob (#3).
- 25 bills every 15 minutes is far more than the ~350 changed bills a day, so the queue keeps
  up, and each cycle's agent work stays bounded (25 × about 2s).
- If Ollama is down, the batch is skipped (#49) and ingestion carries on. The model being
  unavailable must never stop the data pipeline.

## 51. Quality and drift from stored summaries, recent vs baseline

**Date:** 2026-10-01 · **Status:** accepted, thresholds provisional

**Decision:**
- Quality signals are computed from `bill_summaries` after every cycle: policy-area agreement,
  gate failure rate, summary length, reviewer rejection rate, and policy-area distribution.
- Each is computed for a recent window (7 days) and a baseline (the 30 days before), and
  alerts compare the two.
- Every comparison needs at least 30 summaries in each window (10 reviewer decisions for the
  rejection alert).

**Why:**
- In-process counters reset on restart and miss CLI runs. The database has every summary ever
  made, so "agreement over the last 7 days" is an exact query instead of an approximation from
  counters.
- Comparing against a baseline instead of a fixed number means the alerts ask "is the model
  behaving differently than it did?", which is what drift actually is.
- The minimum sample sizes stop a quiet week from producing alarming percentages out of a
  handful of bills. Rule tests cover both firing and not firing.
- **The thresholds are provisional.** Agreement drop 15 points, gate failures 15%, output
  drift 0.25, rejections 30%. They're set from the first live batches (58% agreement, about
  1-2% gate failures), not from a month of history. They should be re-tuned once there is
  one; this entry gets superseded when they are.

## 52. Separate input drift from output drift

**Date:** 2026-10-01 · **Status:** accepted

**Decision:**
- Measure two distribution shifts: the official policy areas of incoming bills (input), and
  the model's policy-area picks (output).
- Alert only when output drifts while input doesn't (Jensen-Shannon divergence over 0.25 on
  output and under 0.1 on input).

**Why:**
- Congress's agenda moves: an appropriations season, a run of commemorative resolutions. When
  the bills change topic, the model's picks *should* change too.
- Alerting on output drift alone would fire every time the news changed.
- Output moving while input stays put is the signature of the model or prompt changing,
  which is the thing worth waking someone for.
- Jensen-Shannon rather than KL divergence: it's symmetric and bounded 0–1, so one threshold
  works whatever the volume, and it handles categories that appear in only one window.

## 53. Each process exports only its own metrics

**Date:** 2026-10-01 · **Status:** accepted

**Decision:**
- The review app serves its metrics from its own Prometheus registry, so it exports only
  review metrics.
- `govwatch_llm_up` carries a `model` label, so the series only exists once the agent has
  checked.
- Alerts on worker-only metrics are pinned to `job="govwatch"`.

**Why:**
- The review app (step 14) imported the shared metrics module and served the default
  registry. That published *every* govwatch metric from the review process, including the
  worker heartbeat gauge at its initial value of 0.
- Prometheus scrapes the review app as its own job, so it saw a heartbeat of 0 for
  `job="review"`, and `GovwatchWorkerStalled` would have fired permanently with a healthy
  worker. An unlabeled `govwatch_llm_up` would have reported "down" the same way.
- It was found while writing the agent alerts, before any alert fired.
- There's now a test that the review app's `/metrics` contains none of the worker's metrics,
  shown to fail with the old behavior. There's also a rule test that a heartbeat of 0 from
  `job="review"` doesn't fire.

## 54. A golden-set model eval in CI

**Date:** 2026-10-01 · **Status:** accepted

**Decision:**
- `tests/eval/golden_bills.jsonl` freezes 25 real bills across 15 policy areas and 5
  legislative stages, each with its CRS text and official policy area.
- `govwatch eval` runs the real model over them through the production path (prompt,
  constrained generation, gate, retry) and fails below these floors:
  - 85% gate pass rate
  - 35% policy-area agreement
  - 0.60 median source support
- A `model-eval` workflow runs it on GitHub's CPU runners whenever the agent code or the eval
  changes, with the model cached between runs.
- The gate itself is checked on every commit (no model needed) against labelled cases,
  including both real false positives (CDC, USDA) and the real catch (an invented FY2020).

**Why:**
- The unit tests use a fake model. Drift alerts only notice a regression after it has shipped
  and run for days. Without this, a prompt edit that makes summaries worse would merge green.
- The inputs are frozen so runs are comparable and need no congress.gov access.
- The floors come from measurement, not intuition:

| | GPU | CPU (like CI) | floor |
|---|---|---|---|
| gate pass | 100% | 100% | 85% |
| policy agreement | 44-48% | 52% | 35% |
| source support (median) | 0.75-0.77 | 0.78 | 0.60 |

- Even at temperature 0, runs aren't identical (agreement moved between 44% and 52%), so the
  floors leave room for that without letting a real regression through.
- **The first real run on GitHub** (CPU runner): gate 100%, agreement 52%, source support
  **0.71**, about 12s per bill. Support came in below my local CPU simulation (0.78), and still
  clear of the 0.60 floor. The margin was set from local runs, and this is the first
  measurement from the environment it actually guards, so it's the number to watch if the
  floor ever needs re-tuning.

**It changed what I thought the model's accuracy was:** live batches had shown 58-74%
agreement with CRS. On the golden set, deliberately balanced across policy areas, it's 44-52%.
The live stream was flattering the model with easy cases (lots of commemorative resolutions).
The balanced number is the honest one.

## 55. Measure grounding, because nothing else caught a title-only regression

**Date:** 2026-10-01 · **Status:** accepted

**Decision:**
- Track source support: the share of a summary's content words (excluding the title)
  that appear in the CRS text.
- It's stored per summary, has an eval floor (median at least 0.60), and has a production
  alert (`GovwatchAgentGroundingDrop`, recent median 0.15+ below baseline).
- It is **not** a gate check.

**Why:**
- To test the eval, I simulated the most important regression: the prompt losing the CRS
  text, so the model sees only titles.
- **The eval passed it:** gate 100%, and agreement *rose* to 52%.
  - Agreement doesn't depend on grounding: a title is enough to guess a policy area.
  - The gate's grounding checks only catch specific details (numbers, acronyms), and a fluent
    invented summary has none.
- Meanwhile the summaries were fiction. "RESTORE Act of 2025", a SNAP eligibility bill,
  became "allocating funds from oil spill cleanup efforts to coastal restoration". The model
  confused it with the 2012 law of the same name.
- Source support separated the two cleanly: median 0.77 grounded vs 0.20 title-only.
- Per summary it's too noisy to gate on. A faithful paraphrase ("allocates funds" for
  "provides amounts") scores low, and 7 of 25 grounded summaries scored under 0.5. Over a
  batch, it's reliable.
- With the floor in place, the same simulated regression now fails the eval (exit 1), and the
  real prompt passes.
- It's a deliberately simple lexical measure, run with no extra model. A stronger version
  would use an entailment model or a second LLM as a judge. That's worth it if this ever
  becomes more than a local tool.

## 56. The agent in Kubernetes uses the host's Ollama

**Date:** 2026-10-01 · **Status:** accepted for the local setup

**Decision:**
- On kind, the agent in the worker pod calls Ollama on the host machine at
  `http://host.docker.internal:11434`, instead of running Ollama as a pod.
- The worker's NetworkPolicy gets one extra egress rule for that port, only when
  `agent.enabled` is set.
- The review app runs as its own Deployment, published through a NodePort that the kind
  cluster maps to `127.0.0.1:30080` only.

**Why:**
- The model needs the GPU. kind runs inside Docker Desktop's VM, and passing an NVIDIA GPU
  through Docker Desktop into kind into a pod is fragile. An Ollama pod on the CPU would be
  about 15x slower (16s vs 1s per bill, measured in #54).
- Before building on it, I checked that kind pods can reach the host at all: from a busybox
  pod, `host.docker.internal` resolved to `192.168.65.254` and Ollama answered.
- A real cluster would run Ollama (or vLLM) on a GPU node pool as an in-cluster Service, and
  the egress rule would become a pod selector. The chart's `agent.ollamaUrl` and
  `agent.ollamaPort` already allow that without template changes.
- The review app has no authentication (#46), so it's only published on loopback, the same
  rule as Compose.

**Verified with a full `terraform apply`:**
- The in-cluster worker reached host Ollama through its NetworkPolicy (`govwatch_llm_up = 1`)
  and stored summaries.
- The review UI served on `localhost:30080`.
- Prometheus scraped both jobs, and no alerts fired.
- The review pod could reach Postgres but not the internet.
- A second plan showed no changes, and destroy was clean.

## 57. Component labels on every selector

**Date:** 2026-10-01 · **Status:** accepted (chart 0.2.0, breaking for upgrades)

**Decision:** The worker and the review app carry `app.kubernetes.io/component: worker` /
`review`, and every selector (Deployments, Services, NetworkPolicies, ServiceMonitors)
includes it.

**Why:**
- Both run in one release, and the chart's selectors only matched on app name and instance.
  With the review Deployment added, the worker's Service, NetworkPolicy and ServiceMonitor
  would have matched the review pods too: metrics scrapes sent to the wrong pod, and the wrong
  network rules applied to it.
- Verified on the cluster: each Service's endpoints now contain only its own pod.
- Deployment selectors can't change in place, so upgrading a 0.1.x release needs
  `helm uninstall` first, or the worker Deployment deleted. That's why the chart moved to
  0.2.0. For the local kind setup, `terraform apply` recreates everything anyway.

