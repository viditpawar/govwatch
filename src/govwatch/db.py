import logging
from importlib.resources import files

import psycopg

log = logging.getLogger(__name__)

# arbitrary but fixed - keeps two pods from migrating at the same time
MIGRATION_LOCK_ID = 74_200_001


def connect(url: str, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=autocommit)


def available_migrations() -> list[tuple[str, str]]:
    """(version, sql) pairs from the bundled migrations dir, in apply order."""
    found = []
    for entry in files("govwatch.migrations").iterdir():
        if entry.name.endswith(".sql"):
            found.append((entry.name.removesuffix(".sql"), entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply any pending migrations, each in its own transaction. Returns what got applied."""
    conn.autocommit = True
    conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        applied = []
        for version, sql in available_migrations():
            if version in done:
                continue
            log.info("applying migration %s", version)
            with conn.transaction():
                conn.execute(sql)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)

        if not applied:
            log.info("schema is up to date")
        return applied
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
