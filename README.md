# govwatch

Continuously ingests federal bills (congress.gov) and regulatory documents (regulations.gov)
into Postgres, runs on a local Kubernetes cluster, and tracks ingestion lag, failure rate,
and data freshness with Prometheus and Grafana.

Work in progress. Full setup docs and architecture diagram coming as the pieces land.

## Local dev

```sh
uv sync
cp .env.example .env   # add your api.data.gov keys
uv run pytest
uv run ruff check .
```
