import os

import psycopg
import pytest

TEST_DB_URL = os.environ.get(
    "GOVWATCH_TEST_DATABASE_URL",
    "postgresql://govwatch:govwatch@localhost:5432/govwatch_test",
)


@pytest.fixture
def pg():
    """Fresh, empty test database. Skips if postgres isn't reachable."""
    try:
        conn = psycopg.connect(TEST_DB_URL, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError as exc:
        # CI sets this so a broken database service fails the build instead of quietly
        # skipping a third of the suite
        if os.environ.get("GOVWATCH_REQUIRE_DB"):
            pytest.fail(f"postgres required but not reachable: {exc}")
        pytest.skip("postgres not available (docker compose up -d postgres)")

    dbname = conn.info.dbname
    if not dbname.endswith("_test"):
        conn.close()
        pytest.fail(f"refusing to wipe non-test database {dbname!r}")

    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
    yield conn
    conn.close()
