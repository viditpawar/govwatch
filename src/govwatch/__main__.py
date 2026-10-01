import argparse
import logging
from datetime import UTC, datetime, timedelta
from itertools import islice

from govwatch import db
from govwatch.config import Settings, get_settings
from govwatch.sources.congress import CongressClient
from govwatch.sources.regulations import RegulationsClient
from govwatch.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(prog="govwatch")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending database migrations")
    peek = sub.add_parser(
        "peek", help="print recently updated records from a source (no db writes)"
    )
    peek.add_argument("source", choices=["congress", "regulations"])
    peek.add_argument("--days", type=int, default=1)
    peek.add_argument("--limit", type=int, default=10)
    sub.add_parser("run", help="run the ingest worker until stopped")
    ingest = sub.add_parser("ingest", help="run a single ingest cycle and exit")
    ingest.add_argument("--source", choices=["congress", "regulations"])
    audit = sub.add_parser("audit", help="reconcile upstream record counts against the db")
    audit.add_argument("--source", choices=["congress", "regulations"])
    audit.add_argument(
        "--repair", action="store_true", help="re-ingest the window if records are missing"
    )
    agent = sub.add_parser("agent", help="summarize changed bills with the local model")
    agent.add_argument("--limit", type=int, help="max bills this run (default: settings)")
    agent.add_argument("--bill", help="summarize one bill, e.g. 119-hr-3270")
    evaluate = sub.add_parser("eval", help="run the agent over the golden set and score it")
    evaluate.add_argument("--golden", default="tests/eval/golden_bills.jsonl")
    # floors set from the first runs (decisions.md #54); exit non-zero below either
    evaluate.add_argument("--min-gate-pass", type=float, default=0.85)
    evaluate.add_argument("--min-agreement", type=float, default=0.35)
    # 0.77 when grounded, 0.20 when the model only saw titles (decisions.md #55)
    evaluate.add_argument("--min-support", type=float, default=0.6)
    evaluate.add_argument("--json", help="also write the full report to this file")
    review = sub.add_parser("review", help="serve the human review queue")
    # no auth on this app, so localhost unless you deliberately say otherwise
    review.add_argument("--host", default="127.0.0.1")
    review.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO, which drowns out everything else
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "migrate":
        with db.connect(settings.database_url) as conn:
            db.migrate(conn)
    elif args.command == "peek":
        peek_source(settings, args.source, args.days, args.limit)
    elif args.command == "run":
        Worker(settings).run_forever()
    elif args.command == "eval":
        run_evaluation(settings, args)
    elif args.command == "review":
        import uvicorn

        from govwatch.review.app import create_app

        uvicorn.run(create_app(settings.database_url), host=args.host, port=args.port)
    elif args.command == "ingest":
        worker = Worker(settings)
        try:
            results = worker.run_once(only=args.source)
        finally:
            worker.close()
        if any(r.status != "success" for r in results):
            raise SystemExit(1)
    elif args.command == "agent":
        run_agent(settings, args.limit or settings.agent_batch_size, args.bill)
    elif args.command == "audit":
        worker = Worker(settings)
        try:
            audits = worker.run_audits(only=args.source, repair=args.repair)
        finally:
            worker.close()
        for a in audits:
            if a.status == "ok":
                print(
                    f"{a.source:<12} {a.window_start:%Y-%m-%d} -> {a.window_end:%Y-%m-%d}  "
                    f"upstream {a.upstream_count:>6}  stored {a.local_count:>6}  "
                    f"missing {a.missing:>4}  ({a.ratio:.2%})"
                )
            else:
                print(f"{a.source:<12} {a.status}: {a.reason}")
        if any(a.status == "failed" or a.missing for a in audits):
            raise SystemExit(1)


def run_agent(settings: Settings, limit: int, bill: str | None) -> None:
    from govwatch.agent.llm import LLMUnavailable, OllamaClient
    from govwatch.agent.runner import run_batch

    congress = CongressClient(settings.congress_api_key.get_secret_value())
    llm = OllamaClient(settings.ollama_url, settings.agent_model)
    try:
        with db.connect(settings.database_url, autocommit=True) as conn:
            results = run_batch(conn, congress.bill_context, llm, limit, only=bill)
    except LLMUnavailable as exc:
        raise SystemExit(f"govwatch agent: {exc}") from None
    finally:
        congress.close()
        llm.close()

    for r in results:
        area = r.policy_area or "-"
        if r.model_policy_area and r.policy_area:
            agrees = r.model_policy_area == r.policy_area
            area += " (model agrees)" if agrees else f" (model said {r.model_policy_area})"
        print()
        print(f"{r.bill_id}  [{r.status}]  stage={r.stage}  attempts={r.attempts}  {area}")
        if r.summary:
            print(f"  {r.summary}")
        for issue in r.issues:
            print(f"  ! {issue.check}: {issue.detail}")
        if r.error:
            print(f"  ! {r.error}")
    if any(r.status == "failed" for r in results):
        raise SystemExit(1)


def run_evaluation(settings: Settings, args: argparse.Namespace) -> None:
    from pathlib import Path

    from govwatch.agent.evaluate import load_golden, run_eval
    from govwatch.agent.llm import LLMUnavailable, OllamaClient

    golden = load_golden(Path(args.golden))
    llm = OllamaClient(settings.ollama_url, settings.agent_model)
    try:
        llm.check()
        report = run_eval(llm, golden)
    except LLMUnavailable as exc:
        raise SystemExit(f"govwatch eval: {exc}") from None
    finally:
        llm.close()

    for o in report.outcomes:
        area = "agrees" if o.model_policy_area == o.policy_area else f"said {o.model_policy_area}"
        print(
            f"{o.bill_id:<16} {o.status:<16} attempts={o.attempts}  {o.llm_seconds:>5.1f}s  "
            f"policy area {area}"
        )
        for issue in o.issues:
            print(f"{'':<16} ! {issue}")
    s = report.summary()
    print(
        f"\n{s['bills']} bills | {s['model']} | prompt {s['prompt_version']}\n"
        f"gate pass {s['gate_pass_rate']:.0%} (first try {s['first_try_rate']:.0%}, "
        f"floor {args.min_gate_pass:.0%}) | policy agreement {s['policy_agreement']:.0%} "
        f"(floor {args.min_agreement:.0%})\n"
        f"source support {s['median_source_support']:.2f} (floor {args.min_support:.2f}) | "
        f"median {s['median_llm_seconds']}s per bill"
    )
    if args.json:
        Path(args.json).write_text(report.to_json(), encoding="utf-8")

    failures = []
    if report.gate_pass_rate < args.min_gate_pass:
        failures.append("gate pass rate below floor")
    if report.policy_agreement < args.min_agreement:
        failures.append("policy agreement below floor")
    if report.median_source_support < args.min_support:
        failures.append("summaries aren't grounded in the source text")
    if failures:
        raise SystemExit("govwatch eval: FAILED - " + "; ".join(failures))


def peek_source(settings: Settings, source: str, days: int, limit: int) -> None:
    until = datetime.now(UTC)
    since = until - timedelta(days=days)

    if source == "congress":
        client = CongressClient(settings.congress_api_key.get_secret_value())
        rows = (
            (b.bill_id, b.source_updated_at, b.title)
            for b in client.iter_updated_bills(since, until)
        )
    else:
        client = RegulationsClient(settings.regulations_api_key.get_secret_value())
        rows = (
            (d.document_id, d.source_updated_at, d.title or "")
            for d in client.iter_updated_documents(since, until)
        )

    with client:
        for record_id, updated, title in islice(rows, limit):
            print(f"{record_id:<32} {updated:%Y-%m-%d %H:%M}  {title[:70]}")
        print(
            f"\n{client.api.requests_made} request(s), "
            f"rate limit remaining: {client.api.ratelimit_remaining}"
        )


if __name__ == "__main__":
    main()
