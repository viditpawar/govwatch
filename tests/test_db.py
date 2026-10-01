import psycopg
import pytest

from govwatch import db

EXPECTED_TABLES = {
    "bills",
    "regulatory_documents",
    "sync_cursors",
    "ingest_runs",
    "schema_migrations",
}


def test_migrations_are_bundled_and_ordered():
    versions = [v for v, _ in db.available_migrations()]
    assert versions[0] == "001_init"
    assert versions == sorted(versions)


def test_migrate_creates_tables(pg):
    applied = db.migrate(pg)
    assert applied == [v for v, _ in db.available_migrations()]

    rows = pg.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {r[0] for r in rows} >= EXPECTED_TABLES


def test_migrate_is_idempotent(pg):
    db.migrate(pg)
    assert db.migrate(pg) == []


def test_ingest_run_status_is_constrained(pg):
    db.migrate(pg)
    pg.execute("INSERT INTO ingest_runs (source) VALUES ('congress')")
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO ingest_runs (source, status) VALUES ('congress', 'weird')")
