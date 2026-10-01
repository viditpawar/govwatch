"""Database access for the review queue. Only the newest summary row per bill is "current";
older rows are history and are never reviewable."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

REVIEWABLE = ("pending_review", "needs_attention")
DECISIONS = ("approved", "rejected")

_CURRENT = """
    SELECT DISTINCT ON (s.bill_id)
           s.id, s.bill_id, s.status, s.stage, s.summary, s.policy_area, s.model_policy_area,
           s.validation_issues, s.attempts, s.model, s.prompt_version, s.llm_seconds,
           s.prompt_tokens, s.completion_tokens, s.source_text, s.source_truncated,
           s.crs_summary_version, s.created_at, s.reviewed_at, s.reviewer, s.review_note,
           b.title, b.latest_action_text, b.latest_action_date, v.url
      FROM bill_summaries s
      JOIN bills b ON b.bill_id = s.bill_id
      JOIN v_bill_activity v ON v.bill_id = s.bill_id
"""


class AlreadyDecided(Exception):
    pass


@dataclass(frozen=True)
class Decided:
    bill_id: str
    from_status: str
    decision: str
    created_at: datetime
    reviewed_at: datetime


def queue(conn: psycopg.Connection, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    """Current summaries in the given statuses, oldest first (first in, first reviewed)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT * FROM ({_CURRENT} ORDER BY s.bill_id, s.created_at DESC, s.id DESC) c
             WHERE c.status = ANY(%s)
             ORDER BY c.created_at, c.id
            """,
            (list(statuses),),
        )
        return cur.fetchall()


def counts(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, bills FROM v_review_queue").fetchall()
    return {status: n for status, n in rows}


def current(conn: psycopg.Connection, bill_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"{_CURRENT} WHERE s.bill_id = %s ORDER BY s.bill_id, s.created_at DESC, s.id DESC",
            (bill_id,),
        )
        return cur.fetchone()


def decide(
    conn: psycopg.Connection, summary_id: int, decision: str, reviewer: str, note: str | None
) -> Decided:
    """Record a verdict on one summary row.

    The WHERE clause is the concurrency guard: the row only changes if it's still waiting
    for review and is still the newest row for its bill, so two reviewers (or a reviewer and
    a re-summarize) can't overwrite each other.
    """
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision {decision!r}")
    row = conn.execute(
        """
        WITH target AS (SELECT id, status FROM bill_summaries WHERE id = %(id)s)
        UPDATE bill_summaries s
           SET status = %(decision)s, reviewer = %(reviewer)s, review_note = %(note)s,
               reviewed_at = now()
          FROM target t
         WHERE s.id = t.id
           AND s.status IN ('pending_review', 'needs_attention')
           AND NOT EXISTS (
                 SELECT 1 FROM bill_summaries newer
                  WHERE newer.bill_id = s.bill_id
                    AND (newer.created_at, newer.id) > (s.created_at, s.id))
        RETURNING s.bill_id, t.status, s.created_at, s.reviewed_at
        """,
        {"id": summary_id, "decision": decision, "reviewer": reviewer, "note": note},
    ).fetchone()
    if row is None:
        raise AlreadyDecided(f"summary {summary_id} is no longer waiting for review")
    bill_id, from_status, created_at, reviewed_at = row
    return Decided(bill_id, from_status, decision, created_at, reviewed_at)
