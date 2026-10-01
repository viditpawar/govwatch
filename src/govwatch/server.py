import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

log = logging.getLogger(__name__)


def start_server(host: str, port: int, is_healthy: Callable[[], bool]) -> ThreadingHTTPServer:
    """Serve /metrics and /healthz on a background thread."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/metrics":
                self._reply(200, generate_latest(), CONTENT_TYPE_LATEST)
            elif self.path == "/healthz":
                if is_healthy():
                    self._reply(200, b"ok\n")
                else:
                    self._reply(503, b"worker loop is stale\n")
            else:
                self._reply(404, b"not found\n")

        def _reply(self, code: int, body: bytes, ctype: str = "text/plain") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass  # prometheus scrapes every 15s, no need to log each one

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="metrics").start()
    log.info("serving /metrics and /healthz on %s:%s", host, server.server_address[1])
    return server
