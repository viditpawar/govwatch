import argparse
import logging
from datetime import UTC, datetime, timedelta
from itertools import islice

from govwatch import db
from govwatch.config import get_settings
from govwatch.sources.congress import CongressClient


def main() -> None:
    parser = argparse.ArgumentParser(prog="govwatch")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending database migrations")
    peek = sub.add_parser("peek-bills", help="print recently updated bills (no db writes)")
    peek.add_argument("--days", type=int, default=1)
    peek.add_argument("--limit", type=int, default=10)
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
    elif args.command == "peek-bills":
        until = datetime.now(UTC)
        since = until - timedelta(days=args.days)
        key = settings.congress_api_key.get_secret_value()
        with CongressClient(key) as client:
            for bill in islice(client.iter_updated_bills(since, until), args.limit):
                print(f"{bill.bill_id:<16} {bill.source_updated_at:%Y-%m-%d}  {bill.title[:80]}")
            print(
                f"\n{client.api.requests_made} request(s), "
                f"rate limit remaining: {client.api.ratelimit_remaining}"
            )


if __name__ == "__main__":
    main()
