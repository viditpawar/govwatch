import argparse
import logging

from govwatch import db
from govwatch.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="govwatch")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending database migrations")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "migrate":
        with db.connect(settings.database_url) as conn:
            db.migrate(conn)


if __name__ == "__main__":
    main()
