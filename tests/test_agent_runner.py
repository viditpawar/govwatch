import json
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from govwatch import db
from govwatch.agent.llm import Generation, LLMError, LLMUnavailable
from govwatch.agent.prompt import PROMPT_VERSION
from govwatch.agent.runner import bills_needing_summary, run_batch
from govwatch.ingest import upsert_bills
from govwatch.sources.congress import BillContext, parse_bill

FIXTURES = Path(__file__).parent / "fixtures"
CRS = (
    "This bill expands Air Traffic Control (ATC) workforce training and provides statutory "
    "authority for the AT-CTI program."
)
CONTEXT = BillContext("Transportation and Public Works", CRS, "00")
GOOD = {
    "summary": (
        "The bill expands air traffic control workforce training. It gives the AT-CTI "
        "program a statutory basis, which affects colleges that train controllers."
    ),
    "policy_area": "Transportation and Public Works",
}
BAD = {**GOOD, "summary": GOOD["summary"] + " It costs $40 million."}


class FakeLLM:
    model = "fake"

    def __init__(self, *outputs):
        self.outputs = deque(outputs)
        self.prompts = []

    def check(self):
        pass

    def generate(self, prompt, schema):
        self.prompts.append(prompt)
        out = self.outputs.popleft()
        if isinstance(out, Exception):
            raise out
        return Generation(out, self.model, 1.5, 300, 60)


def fetch(ctx=CONTEXT):
    return lambda congress, bill_type, number: ctx


@pytest.fixture
def conn(pg):
    db.migrate(pg)
    raw = json.loads((FIXTURES / "congress_bills_page1.json").read_text())["bills"]
    upsert_bills(pg, [parse_bill(b) for b in raw])
    return pg


def summaries(conn):
    return conn.execute(
        "SELECT bill_id, status, attempts, summary IS NOT NULL, validation_issues "
        "FROM bill_summaries ORDER BY id"
    ).fetchall()


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def test_good_output_goes_to_review(conn):
    results = run_batch(conn, fetch(), FakeLLM(GOOD), limit=1)

    assert [r.status for r in results] == ["pending_review"]
    bill_id, status, attempts, has_summary, issues = summaries(conn)[0]
    assert (status, attempts, has_summary, issues) == ("pending_review", 1, True, [])
    row = conn.execute(
        "SELECT stage, policy_area, model_policy_area, model, prompt_version, prompt_tokens "
        "FROM bill_summaries"
    ).fetchone()
    assert row == (
        "in_committee",
        "Transportation and Public Works",
        "Transportation and Public Works",
        "fake",
        PROMPT_VERSION,
        300,
    )


def test_rejected_output_gets_one_retry_with_feedback(conn):
    before = sample("govwatch_agent_retries_total", reason="validation")
    llm = FakeLLM(BAD, GOOD)
    run_batch(conn, fetch(), llm, limit=1)

    assert summaries(conn)[0][1:3] == ("pending_review", 2)
    assert "previous answer was rejected" in llm.prompts[1].user
    assert "40" in llm.prompts[1].user
    assert sample("govwatch_agent_retries_total", reason="validation") == before + 1


def test_two_rejections_are_kept_for_a_human_not_dropped(conn):
    before = sample("govwatch_agent_validation_failures_total", check="ungrounded_number")
    run_batch(conn, fetch(), FakeLLM(BAD, BAD), limit=1)

    _, status, attempts, has_summary, issues = summaries(conn)[0]
    assert (status, attempts, has_summary) == ("needs_attention", 2, True)
    assert issues[0]["check"] == "ungrounded_number"
    assert (
        sample("govwatch_agent_validation_failures_total", check="ungrounded_number") == before + 2
    )


def test_no_crs_summary_means_a_stub_and_no_model_call(conn):
    llm = FakeLLM()
    run_batch(conn, fetch(BillContext("Health", None, None)), llm, limit=1)

    assert llm.prompts == []
    _, status, attempts, has_summary, _ = summaries(conn)[0]
    assert (status, attempts, has_summary) == ("stub", 0, False)


def test_model_outage_stores_nothing_so_the_bill_is_retried(conn):
    results = run_batch(conn, fetch(), FakeLLM(LLMError("ollama unreachable")), limit=1)

    assert results[0].status == "failed"
    assert summaries(conn) == []
    assert len(bills_needing_summary(conn, limit=10)) == 2


def test_shadow_policy_area_check(conn):
    before = sample("govwatch_agent_policy_area_checks_total", result="mismatch")
    run_batch(conn, fetch(), FakeLLM({**GOOD, "policy_area": "Commerce"}), limit=1)

    row = conn.execute("SELECT policy_area, model_policy_area FROM bill_summaries").fetchone()
    # the stored policy area is always the official one; the model's pick is only recorded
    assert row == ("Transportation and Public Works", "Commerce")
    assert sample("govwatch_agent_policy_area_checks_total", result="mismatch") == before + 1


class TestWorkSelection:
    def test_summarized_bills_are_not_picked_again(self, conn):
        run_batch(conn, fetch(), FakeLLM(GOOD, GOOD), limit=10)
        assert bills_needing_summary(conn, limit=10) == []

    def test_a_real_change_to_the_bill_triggers_a_new_summary(self, conn):
        run_batch(conn, fetch(), FakeLLM(GOOD, GOOD), limit=10)
        raw = json.loads((FIXTURES / "congress_bills_page1.json").read_text())["bills"][0]
        changed = replace(parse_bill(raw), latest_action_text="Passed House.")
        upsert_bills(conn, [changed])

        assert [b.bill_id for b in bills_needing_summary(conn, limit=10)] == [changed.bill_id]

    def test_new_prompt_version_resummarizes_unless_already_reviewed(self, conn):
        run_batch(conn, fetch(), FakeLLM(GOOD, GOOD), limit=10)
        conn.execute("UPDATE bill_summaries SET prompt_version = 'v0'")
        conn.execute("UPDATE bill_summaries SET status = 'approved' WHERE bill_id = '119-s-1972'")
        assert [b.bill_id for b in bills_needing_summary(conn, limit=10)] == ["119-hr-6417"]

    def test_old_stubs_are_rechecked_for_crs_text(self, conn):
        run_batch(conn, fetch(BillContext("Health", None, None)), FakeLLM(), limit=10)
        assert bills_needing_summary(conn, limit=10) == []

        conn.execute("UPDATE bill_summaries SET created_at = now() - interval '2 days'")
        assert len(bills_needing_summary(conn, limit=10)) == 2


def test_one_bill_failing_doesnt_stop_the_batch(conn):
    results = run_batch(conn, fetch(), FakeLLM(RuntimeError("boom"), GOOD), limit=10)
    assert sorted(r.status for r in results) == ["failed", "pending_review"]


def test_ollama_not_running_fails_before_touching_any_bill(conn):
    class Down(FakeLLM):
        def check(self):
            raise LLMUnavailable("can't reach Ollama. Start the Ollama app")

    llm = Down(GOOD)
    with pytest.raises(LLMUnavailable, match="Start the Ollama app"):
        run_batch(conn, fetch(), llm, limit=10)
    assert llm.prompts == []
    assert summaries(conn) == []


def test_ollama_going_away_mid_batch_stops_the_batch(conn):
    llm = FakeLLM(GOOD, LLMUnavailable("ollama unreachable"), GOOD)
    results = run_batch(conn, fetch(), llm, limit=10)

    # first bill done, second hit the outage, nothing else attempted
    assert [r.status for r in results] == ["pending_review", "failed"]
    assert len(llm.prompts) == 2
    assert len(summaries(conn)) == 1
