"""Checks that run on every commit, with no model needed: the validation gate against
labelled real-world cases, and the golden set's own integrity. The model itself is scored
by `govwatch eval` in the model-eval workflow."""

import json
from pathlib import Path

import pytest

from govwatch.agent.evaluate import load_golden, run_eval
from govwatch.agent.facts import derive_stage
from govwatch.agent.llm import Generation
from govwatch.agent.prompt import CRS_POLICY_AREAS
from govwatch.agent.validate import CHECKS, validate

EVAL = Path(__file__).parent / "eval"
GATE_CASES = [json.loads(line) for line in (EVAL / "gate_cases.jsonl").open(encoding="utf-8")]
GOLDEN = load_golden(EVAL / "golden_bills.jsonl")


@pytest.mark.parametrize("case", GATE_CASES, ids=[c["name"] for c in GATE_CASES])
def test_gate_on_labelled_cases(case):
    """Each case is a summary with the checks a human decided it should (or shouldn't)
    trip. Loosening a check lets the 'caught' cases through; tightening one breaks the
    real false positives that were fixed."""
    issues = validate(
        {"summary": case["summary"], "policy_area": case["policy_area"]},
        case["source"],
        case["stage"],
    )
    assert sorted(i.check for i in issues) == sorted(case["expected"])


def test_gate_cases_cover_every_check_that_can_fire_on_content():
    covered = {check for c in GATE_CASES for check in c["expected"]}
    # schema and length are covered by unit tests; these four are about content
    assert covered >= set(CHECKS) - {"schema", "length"}


class TestGoldenSet:
    def test_is_big_and_varied_enough_to_mean_something(self):
        assert len(GOLDEN) >= 20
        assert len({g["policy_area"] for g in GOLDEN}) >= 10

    def test_every_bill_has_what_the_eval_needs(self):
        for g in GOLDEN:
            assert g["crs_summary"].strip(), g["bill_id"]
            assert g["policy_area"] in CRS_POLICY_AREAS, g["bill_id"]

    def test_stage_rules_still_agree_with_the_recorded_stages(self):
        # a change to derive_stage that reclassifies a golden bill should be deliberate
        for g in GOLDEN:
            assert derive_stage(g["latest_action_text"], g["bill_type"]) == g["stage"], g["bill_id"]


class EchoModel:
    """Writes the first two sentences of the source back, and picks the official area.
    Stands in for the model so the eval harness itself is tested without ollama."""

    model = "echo"

    def __init__(self):
        self.golden = {g["crs_summary"][:200]: g for g in GOLDEN}

    def generate(self, prompt, schema):
        source = prompt.user.split("Official CRS summary:\n", 1)[1]
        g = next(v for k, v in self.golden.items() if source.startswith(k[:120]))
        sentences = " ".join(g["crs_summary"].replace("\n", " ").split(". ")[1:3])[:600]
        return Generation(
            {"summary": sentences, "policy_area": g["policy_area"]}, "echo", 0.1, 1, 1
        )


def test_eval_harness_runs_the_production_path():
    report = run_eval(EchoModel(), GOLDEN)
    s = report.summary()
    assert s["bills"] == len(GOLDEN)
    assert s["policy_agreement"] == 1.0
    assert 0 <= s["gate_pass_rate"] <= 1
    assert json.loads(report.to_json())["summary"]["model"] == "echo"
