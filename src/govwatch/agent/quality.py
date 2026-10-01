"""Agent quality and drift signals, computed from stored summaries.

Read back from the database after every cycle (like the ingest lag gauges), so they're
correct after a restart and include summaries made from the CLI. Each signal is computed
for a "recent" window and a "baseline" window right before it; alerts compare the two.

The distribution drift compares two things on purpose:
  input drift   how much the *official* policy-area mix of incoming bills moved
  output drift  how much the *model's* policy-area picks moved
High output drift with low input drift means the model (or prompt) changed, not the news.
"""

import math
from collections import Counter
from collections.abc import Iterable

import psycopg

from govwatch import metrics

RECENT_DAYS = 7
BASELINE_DAYS = 30  # the 30 days before the recent window

_WINDOWS = f"""
    CASE
        WHEN created_at > now() - interval '{RECENT_DAYS} days' THEN 'recent'
        WHEN created_at > now() - interval '{RECENT_DAYS + BASELINE_DAYS} days' THEN 'baseline'
    END
"""


def jensen_shannon(p: Counter, q: Counter) -> float:
    """Jensen-Shannon divergence (base 2) between two count distributions: 0 = identical,
    1 = no overlap. Symmetric and bounded, so one threshold works regardless of volume."""
    keys = set(p) | set(q)
    p_total, q_total = sum(p.values()), sum(q.values())
    if not keys or not p_total or not q_total:
        return 0.0

    def kl(a: Iterable[float], b: Iterable[float]) -> float:
        return sum(x * math.log2(x / y) for x, y in zip(a, b, strict=True) if x > 0)

    ps = [p[k] / p_total for k in keys]
    qs = [q[k] / q_total for k in keys]
    ms = [(x + y) / 2 for x, y in zip(ps, qs, strict=True)]
    return (kl(ps, ms) + kl(qs, ms)) / 2


def refresh(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        f"""
        SELECT {_WINDOWS} AS window,
               count(*) AS generated,
               count(*) FILTER (WHERE policy_area IS NOT NULL) AS area_checked,
               count(*) FILTER (WHERE model_policy_area = policy_area) AS area_matched,
               count(*) FILTER (WHERE jsonb_array_length(validation_issues) > 0) AS gate_failed,
               avg(length(summary)) AS mean_length,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY source_support) AS support,
               count(*) FILTER (WHERE status IN ('approved', 'rejected')) AS decided,
               count(*) FILTER (WHERE status = 'rejected') AS rejected
          FROM bill_summaries
         WHERE summary IS NOT NULL
         GROUP BY 1
        """
    ).fetchall()
    seen = set()
    for (
        window,
        generated,
        checked,
        matched,
        gate_failed,
        mean_len,
        support,
        decided,
        rejected,
    ) in rows:
        if window is None:
            continue
        seen.add(window)
        metrics.AGENT_QUALITY_SAMPLES.labels(window).set(generated)
        metrics.AGENT_GATE_FAILURE_RATIO.labels(window).set(gate_failed / generated)
        metrics.AGENT_SUMMARY_LENGTH.labels(window).set(float(mean_len or 0))
        if support is not None:
            metrics.AGENT_SOURCE_SUPPORT.labels(window).set(float(support))
        if checked:
            metrics.AGENT_POLICY_AGREEMENT.labels(window).set(matched / checked)
        metrics.REVIEW_DECIDED.labels(window).set(decided)
        if decided:
            metrics.REVIEW_REJECTION_RATIO.labels(window).set(rejected / decided)
    for window in ("recent", "baseline"):
        if window not in seen:
            metrics.AGENT_QUALITY_SAMPLES.labels(window).set(0)

    dist = {(w, k): Counter() for w in ("recent", "baseline") for k in ("input", "output")}
    for window, official, picked in conn.execute(
        f"""
        SELECT {_WINDOWS}, policy_area, model_policy_area
          FROM bill_summaries
         WHERE model_policy_area IS NOT NULL
        """
    ).fetchall():
        if window is None:
            continue
        if official:
            dist[(window, "input")][official] += 1
        dist[(window, "output")][picked] += 1
    for kind in ("input", "output"):
        metrics.AGENT_DISTRIBUTION_DRIFT.labels(kind).set(
            jensen_shannon(dist[("recent", kind)], dist[("baseline", kind)])
        )
