# syntax=docker/dockerfile:1

# --- build: resolve deps from the lockfile into a venv ---------------------------
FROM python:3.14-slim AS build

RUN pip install --no-cache-dir uv==0.12.21

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# deps first so code changes don't bust the dependency layer
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# --- runtime: just the venv, non-root -----------------------------------------------
FROM python:3.14-slim

# pick up debian security fixes that landed after the base image was published
# (trivy flagged fixable openssl CVEs otherwise)
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin govwatch

COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    GOVWATCH_METRICS_HOST=0.0.0.0

USER 10001
EXPOSE 9100

# exec form so the worker is PID 1 and gets SIGTERM directly
ENTRYPOINT ["govwatch"]
CMD ["run"]
