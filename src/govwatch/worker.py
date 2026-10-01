import logging
import signal
import threading
from datetime import timedelta

from govwatch import db
from govwatch.config import Settings
from govwatch.ingest import RunResult, Source, run_ingest, upsert_bills, upsert_documents
from govwatch.sources.congress import CongressClient
from govwatch.sources.regulations import RegulationsClient

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()

        self.congress = CongressClient(settings.congress_api_key.get_secret_value())
        self.regulations = RegulationsClient(settings.regulations_api_key.get_secret_value())
        self.sources = [
            Source(
                name="congress",
                # updateDate is a bare date, so re-read the previous day every time
                overlap=timedelta(days=1),
                fetch=self.congress.iter_updated_bills,
                upsert=upsert_bills,
                api=self.congress.api,
            ),
            Source(
                name="regulations",
                overlap=timedelta(minutes=15),
                fetch=self.regulations.iter_updated_documents,
                upsert=upsert_documents,
                api=self.regulations.api,
            ),
        ]

    def run_once(self, only: str | None = None) -> list[RunResult]:
        backfill = timedelta(days=self.settings.backfill_days)
        results = []
        with db.connect(self.settings.database_url, autocommit=True) as conn:
            for source in self.sources:
                if self._stop.is_set():
                    break
                if only and source.name != only:
                    continue
                result = run_ingest(conn, source, backfill)
                if result:
                    results.append(result)
        return results

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        log.info("worker started, polling every %ss", self.settings.poll_interval_seconds)

        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                # usually the db being unreachable - log it and try again next cycle
                log.exception("ingest cycle failed")
            self._stop.wait(self.settings.poll_interval_seconds)

        log.info("worker stopped")
        self.close()

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.congress.close()
        self.regulations.close()

    def _handle_signal(self, signum: int, _frame: object) -> None:
        log.info("got signal %s, finishing current run then exiting", signum)
        self.stop()
