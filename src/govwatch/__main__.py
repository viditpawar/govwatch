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
    elif args.command == "ingest":
        worker = Worker(settings)
        try:
            results = worker.run_once(only=args.source)
        finally:
            worker.close()
        if any(r.status != "success" for r in results):
            raise SystemExit(1)


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
