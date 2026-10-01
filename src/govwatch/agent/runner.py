"""Summarize changed bills: fetch context, derive facts, generate, validate, store.

Every bill ends in exactly one stored outcome, so nothing silently disappears:
  pending_review   passed the validation gate, queued for a human
  needs_attention  failed the gate twice; stored with its issues for a human to look at
  stub             no CRS summary yet, so nothing is generated (decisions.md #40)
A model or API outage stores nothing and leaves the bill to be picked up next run.
"""

import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg

from govwatch import metrics
from govwatch.agent.facts import derive_stage
from govwatch.agent.llm import Generation, LLMError, LLMUnavailable, OllamaClient
from govwatch.agent.prompt import OUTPUT_SCHEMA, PROMPT_VERSION, build_prompt, with_feedback
from govwatch.agent.validate import Issue, validate
from govwatch.sources.base import ApiError
from govwatch.sources.congress import BillContext

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
# CRS summaries usually show up days or weeks after a bill is introduced
STUB_RECHECK_HOURS = 24

FetchContext = Callable[[int, str, int], BillContext]


@dataclass(frozen=True)
class BillRow:
    bill_id: str
    congress: int
    bill_type: str
    bill_number: int
    title: str
    latest_action_text: str | None
    content_hash: str


@dataclass
class AgentResult:
    bill_id: str
    status: str
    stage: str = ""
    policy_area: str | None = None
    summary: str | None = None
    model_policy_area: str | None = None
    issues: list[Issue] = field(default_factory=list)
    attempts: int = 0
    llm_seconds: float = 0.0
    crs_summary_version: str | None = None
    # the CRS text the model was given, kept so a reviewer checks against exactly that
    source_text: str | None = None
    source_truncated: bool = False
    generation: Generation | None = None
    error: str | None = None


@contextmanager
def step(name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics.AGENT_STEP.labels(name).observe(time.perf_counter() - started)


_SELECT = """
    SELECT b.bill_id, b.congress, b.bill_type, b.bill_number, b.title,
           b.latest_action_text, b.content_hash
      FROM bills b
"""


def bills_needing_summary(conn: psycopg.Connection, limit: int) -> list[BillRow]:
    """Newest-changed first: bills never summarized, changed since their last summary,
    summarized under an older prompt (unless a human already reviewed it), or stubbed
    long enough ago that CRS may have written a summary since."""
    rows = conn.execute(
        _SELECT
        + """
          LEFT JOIN LATERAL (
                SELECT s.source_hash, s.prompt_version, s.status, s.created_at
                  FROM bill_summaries s
                 WHERE s.bill_id = b.bill_id
                 ORDER BY s.created_at DESC, s.id DESC
                 LIMIT 1
          ) s ON true
         WHERE s.status IS NULL
            OR s.source_hash <> b.content_hash
            OR (s.prompt_version <> %(pv)s AND s.status NOT IN ('approved', 'rejected'))
            OR (s.status = 'stub' AND s.created_at < now() - make_interval(hours => %(stub)s))
         ORDER BY b.last_changed_at DESC
         LIMIT %(limit)s
        """,
        {"pv": PROMPT_VERSION, "stub": STUB_RECHECK_HOURS, "limit": limit},
    ).fetchall()
    return [BillRow(*r) for r in rows]


def summarize_bill(bill: BillRow, fetch_context: FetchContext, llm: OllamaClient) -> AgentResult:
    with step("fetch_context"):
        ctx = fetch_context(bill.congress, bill.bill_type, bill.bill_number)
    result = AgentResult(
        bill.bill_id,
        status="stub",
        stage=derive_stage(bill.latest_action_text, bill.bill_type),
        policy_area=ctx.policy_area,
        crs_summary_version=ctx.crs_summary_version,
        source_text=ctx.crs_summary,
    )
    if not ctx.crs_summary:
        return result

    with step("build_prompt"):
        prompt = build_prompt(bill.title, result.stage, ctx.crs_summary)
    result.source_truncated = prompt.truncated
    source_text = f"{bill.title}\n{bill.latest_action_text or ''}\n{ctx.crs_summary}"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result.attempts = attempt
        with step("generate"):
            result.generation = llm.generate(prompt, OUTPUT_SCHEMA)
        result.llm_seconds += result.generation.seconds
        with step("validate"):
            result.issues = validate(result.generation.output, source_text, result.stage)
        for issue in result.issues:
            metrics.AGENT_VALIDATION_FAILURES.labels(issue.check).inc()
        if not result.issues:
            break
        if attempt < MAX_ATTEMPTS:
            metrics.AGENT_RETRIES.labels("validation").inc()
            log.info("%s: attempt %d rejected: %s", bill.bill_id, attempt, result.issues)
            prompt = with_feedback(prompt, [str(i) for i in result.issues])

    assert result.generation is not None
    result.summary = result.generation.output.get("summary")
    result.model_policy_area = result.generation.output.get("policy_area")
    result.status = "needs_attention" if result.issues else "pending_review"
    if ctx.policy_area:
        match = result.model_policy_area == ctx.policy_area
        metrics.AGENT_POLICY_AREA.labels("match" if match else "mismatch").inc()
    return result


def store_result(conn: psycopg.Connection, bill: BillRow, result: AgentResult) -> None:
    gen = result.generation
    with step("persist"):
        conn.execute(
            """
            INSERT INTO bill_summaries (
                bill_id, source_hash, status, stage, summary, policy_area, model_policy_area,
                crs_summary_version, source_text, source_truncated, validation_issues,
                attempts, model, prompt_version, llm_seconds, prompt_tokens, completion_tokens)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                bill.bill_id,
                bill.content_hash,
                result.status,
                result.stage,
                result.summary,
                result.policy_area,
                result.model_policy_area,
                result.crs_summary_version,
                result.source_text,
                result.source_truncated,
                json.dumps([{"check": i.check, "detail": i.detail} for i in result.issues]),
                result.attempts,
                gen.model if gen else None,
                PROMPT_VERSION,
                round(result.llm_seconds, 3) if gen else None,
                gen.prompt_tokens if gen else None,
                gen.completion_tokens if gen else None,
            ),
        )


def run_batch(
    conn: psycopg.Connection,
    fetch_context: FetchContext,
    llm: OllamaClient,
    limit: int,
    only: str | None = None,
) -> list[AgentResult]:
    if only:
        rows = conn.execute(_SELECT + " WHERE b.bill_id = %s", (only,)).fetchall()
        bills = [BillRow(*r) for r in rows]
    else:
        bills = bills_needing_summary(conn, limit)
    log.info("agent: %d bill(s) to summarize", len(bills))

    if not bills:
        return []
    # one clear error up front beats a traceback per bill when ollama isn't running
    llm.check()

    results = []
    for bill in bills:
        try:
            with step("total"):
                result = summarize_bill(bill, fetch_context, llm)
            store_result(conn, bill, result)
        except LLMUnavailable as exc:
            # ollama went away mid-batch: stop here. nothing was stored for this bill, so it
            # and the rest are picked up by the next run
            log.error("%s: stopping batch, model unavailable: %s", bill.bill_id, exc)
            metrics.AGENT_BILLS.labels("failed").inc()
            results.append(AgentResult(bill.bill_id, status="failed", error=str(exc)))
            break
        except (LLMError, ApiError) as exc:
            # expected failure modes, already explained by the message
            log.error("%s: agent failed: %s", bill.bill_id, exc)
            result = AgentResult(bill.bill_id, status="failed", error=str(exc))
        except Exception as exc:  # a real bug: keep the traceback, carry on with the batch
            log.exception("%s: agent failed", bill.bill_id)
            result = AgentResult(
                bill.bill_id, status="failed", error=f"{type(exc).__name__}: {exc}"
            )
        metrics.AGENT_BILLS.labels(result.status).inc()
        results.append(result)
    return results
