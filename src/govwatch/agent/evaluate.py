"""Offline evaluation of the agent against a frozen golden set of real bills.

Each golden bill carries the CRS text and official policy area captured when the set was
built, so a run needs no congress.gov access and always sees the same inputs. It goes
through the exact production path (summarize_bill: prompt, constrained generation, gate,
retry), so a regression in any of them shows up here before it ships.
"""

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

from govwatch.agent.faithfulness import source_support
from govwatch.agent.llm import OllamaClient
from govwatch.agent.prompt import PROMPT_VERSION
from govwatch.agent.runner import BillRow, summarize_bill
from govwatch.sources.congress import BillContext

DEFAULT_GOLDEN = Path("tests/eval/golden_bills.jsonl")


@dataclass
class BillOutcome:
    bill_id: str
    status: str
    attempts: int
    policy_area: str
    model_policy_area: str | None
    llm_seconds: float
    issues: list[str]
    summary: str | None = None
    source_support: float | None = None


@dataclass
class EvalReport:
    model: str
    prompt_version: str
    outcomes: list[BillOutcome] = field(default_factory=list)

    @property
    def gate_pass_rate(self) -> float:
        return sum(o.status == "pending_review" for o in self.outcomes) / len(self.outcomes)

    @property
    def first_try_rate(self) -> float:
        ok = sum(o.status == "pending_review" and o.attempts == 1 for o in self.outcomes)
        return ok / len(self.outcomes)

    @property
    def policy_agreement(self) -> float:
        return sum(o.model_policy_area == o.policy_area for o in self.outcomes) / len(self.outcomes)

    @property
    def median_source_support(self) -> float:
        return statistics.median(o.source_support or 0.0 for o in self.outcomes)

    @property
    def median_llm_seconds(self) -> float:
        return statistics.median(o.llm_seconds for o in self.outcomes)

    def summary(self) -> dict:
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "bills": len(self.outcomes),
            "gate_pass_rate": round(self.gate_pass_rate, 3),
            "first_try_rate": round(self.first_try_rate, 3),
            "policy_agreement": round(self.policy_agreement, 3),
            "median_source_support": round(self.median_source_support, 3),
            "median_llm_seconds": round(self.median_llm_seconds, 2),
        }

    def to_json(self) -> str:
        return json.dumps(
            {"summary": self.summary(), "bills": [asdict(o) for o in self.outcomes]}, indent=2
        )


def load_golden(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_eval(llm: OllamaClient, golden: list[dict]) -> EvalReport:
    report = EvalReport(model=llm.model, prompt_version=PROMPT_VERSION)
    for g in golden:
        congress, bill_type, number = g["bill_id"].split("-")
        bill = BillRow(
            bill_id=g["bill_id"],
            congress=int(congress),
            bill_type=bill_type,
            bill_number=int(number),
            title=g["title"],
            latest_action_text=g["latest_action_text"],
            content_hash="golden",
        )
        ctx = BillContext(g["policy_area"], g["crs_summary"], g.get("crs_summary_version"))
        result = summarize_bill(bill, lambda *_, ctx=ctx: ctx, llm)
        report.outcomes.append(
            BillOutcome(
                bill_id=g["bill_id"],
                status=result.status,
                attempts=result.attempts,
                policy_area=g["policy_area"],
                model_policy_area=result.model_policy_area,
                llm_seconds=round(result.llm_seconds, 2),
                issues=[str(i) for i in result.issues],
                summary=result.summary,
                source_support=(
                    round(source_support(result.summary, g["crs_summary"], g["title"]), 3)
                    if result.summary
                    else None
                ),
            )
        )
    return report
