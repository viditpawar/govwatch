import logging
import signal
import threading
import time
from datetime import timedelta

from govwatch import db, metrics
from govwatch.config import Settings
from govwatch.ingest import RunResult, Source, run_ingest, upsert_bills, upsert_documents
from govwatch.server import start_server
from govwatch.sources.congress import CongressClient
from govwatch.sources.regulations import RegulationsClient

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()
        self._last_beat = time.time()

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
                self._refresh_metrics(conn)
        return results

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        log.info("worker started, polling every %ss", self.settings.poll_interval_seconds)

        metrics.init_labels()
        self._beat()
        server = start_server(self.settings.metrics_host, self.settings.metrics_port, self.healthy)
        try:
            with db.connect(self.settings.database_url, autocommit=True) as conn:
                self._refresh_metrics(conn)
        except Exception:
            log.warning("couldn't load initial metrics from db", exc_info=True)

        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                # usually the db being unreachable - log it and try again next cycle
                log.exception("ingest cycle failed")
            # the heartbeat means "the loop isn't wedged", not "ingest is working".
            # failing runs show up in metrics and alerts; restarting the pod
            # wouldn't fix a dead upstream anyway
            self._beat()
            self._stop.wait(self.settings.poll_interval_seconds)

        log.info("worker stopped")
        server.shutdown()
        self.close()

    def healthy(self) -> bool:
        # one full poll interval plus a generous allowance for a slow run (e.g. a big backfill)
        max_age = 2 * self.settings.poll_interval_seconds + 1800
        return time.time() - self._last_beat < max_age

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.congress.close()
        self.regulations.close()

    def _beat(self) -> None:
        self._last_beat = time.time()
        metrics.HEARTBEAT.set(self._last_beat)

    def _refresh_metrics(self, conn) -> None:
        try:
            metrics.refresh_from_db(conn)
        except Exception:
            log.warning("couldn't refresh metrics from db", exc_info=True)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        log.info("got signal %s, finishing current run then exiting", signum)
        self.stop()
