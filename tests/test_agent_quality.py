import json
from collections import Counter
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from govwatch import db
from govwatch.agent import quality
from govwatch.agent.llm import LLMUnavailable
from govwatch.config import Settings
from govwatch.ingest import upsert_bills
from govwatch.sources.congress import parse_bill
from govwatch.worker import Worker

FIXTURES = Path(__file__).parent / "fixtures"


def gauge(name, **labels):
    return REGISTRY.get_sample_value(name, labels)


class TestJensenShannon:
    def test_identical_distributions(self):
        assert quality.jensen_shannon(Counter(a=3, b=1), Counter(a=6, b=2)) == pytest.approx(0)

    def test_disjoint_distributions(self):
        assert quality.jensen_shannon(Counter(a=5), Counter(b=5)) == pytest.approx(1)

    def test_symmetric_and_bounded(self):
        p, q = Counter(a=8, b=1, c=1), Counter(a=2, b=5, c=3)
        d = quality.jensen_shannon(p, q)
        assert 0 < d < 1
        assert d == pytest.approx(quality.jensen_shannon(q, p))

    def test_no_data_is_no_drift(self):
        assert quality.jensen_shannon(Counter(), Counter(a=1)) == 0


@pytest.fixture
def conn(pg):
    db.migrate(pg)
    raw = json.loads((FIXTURES / "congress_bills_page1.json").read_text())["bills"]
    upsert_bills(pg, [parse_bill(b) for b in raw])
    return pg


def add(conn, days_ago, official, picked, issues=False, status="pending_review", n=1):
    for _ in range(n):
        conn.execute(
            """
            INSERT INTO bill_summaries (bill_id, source_hash, status, stage, summary,
                policy_area, model_policy_area, validation_issues, attempts, model,
                prompt_version, created_at)
            VALUES ('119-hr-6417', 'h', %s, 'in_committee', repeat('x', 200), %s, %s, %s, 1,
                    'fake', 'v1', now() - make_interval(days => %s))
            """,
            (
                status,
                official,
                picked,
                json.dumps([{"check": "length", "detail": "x"}] if issues else []),
                days_ago,
            ),
        )


def test_windows_agreement_and_gate_failures(conn):
    # baseline: model agrees 9/10, no gate failures
    add(conn, 20, "Health", "Health", n=9)
    add(conn, 20, "Health", "Taxation")
    # recent: agrees 2/4, half fail the gate, one rejected of two reviewed
    add(conn, 1, "Health", "Health", status="approved")
    add(conn, 1, "Health", "Health", status="rejected", issues=True)
    add(conn, 1, "Health", "Taxation", issues=True, n=2)
    quality.refresh(conn)

    assert gauge("govwatch_agent_quality_samples", window="recent") == 4
    assert gauge("govwatch_agent_quality_samples", window="baseline") == 10
    assert gauge("govwatch_agent_policy_area_agreement", window="recent") == 0.5
    assert gauge("govwatch_agent_policy_area_agreement", window="baseline") == 0.9
    assert gauge("govwatch_agent_gate_failure_ratio", window="recent") == 0.75
    assert gauge("govwatch_agent_gate_failure_ratio", window="baseline") == 0
    assert gauge("govwatch_review_rejection_ratio", window="recent") == 0.5


def test_output_drift_without_input_drift_points_at_the_model(conn):
    # same bills coming in (all Health) in both windows, but the model's picks moved
    add(conn, 20, "Health", "Health", n=10)
    add(conn, 1, "Health", "Taxation", n=10)
    quality.refresh(conn)

    assert gauge("govwatch_agent_policy_area_drift", kind="input") == pytest.approx(0)
    assert gauge("govwatch_agent_policy_area_drift", kind="output") == pytest.approx(1)


def test_input_drift_explains_output_drift(conn):
    # the bills themselves changed topic, and the model followed them
    add(conn, 20, "Health", "Health", n=10)
    add(conn, 1, "Taxation", "Taxation", n=10)
    quality.refresh(conn)

    assert gauge("govwatch_agent_policy_area_drift", kind="input") == pytest.approx(1)
    assert gauge("govwatch_agent_policy_area_drift", kind="output") == pytest.approx(1)


def test_old_rows_outside_both_windows_are_ignored(conn):
    add(conn, 90, "Health", "Taxation", n=5)
    quality.refresh(conn)
    assert gauge("govwatch_agent_quality_samples", window="recent") == 0
    assert gauge("govwatch_agent_quality_samples", window="baseline") == 0


def test_worker_skips_the_agent_when_ollama_is_down(conn, monkeypatch):
    for key in ("GOVWATCH_CONGRESS_API_KEY", "GOVWATCH_REGULATIONS_API_KEY"):
        monkeypatch.setenv(key, "k")
    worker = Worker(Settings(_env_file=None, agent_enabled=True))
    try:

        class Down:
            def check(self):
                raise LLMUnavailable("not running")

            def close(self):
                pass

        worker.llm = Down()
        worker._run_agent(conn)  # must not raise: ingestion carries on without the agent
    finally:
        worker.close()


def test_worker_has_no_agent_unless_enabled(monkeypatch):
    for key in ("GOVWATCH_CONGRESS_API_KEY", "GOVWATCH_REGULATIONS_API_KEY"):
        monkeypatch.setenv(key, "k")
    worker = Worker(Settings(_env_file=None))
    try:
        assert worker.llm is None
    finally:
        worker.close()
