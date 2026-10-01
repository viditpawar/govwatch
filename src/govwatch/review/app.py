"""The human review step: agent summaries wait here until a person approves or rejects them.

Deliberately small - server-rendered pages, plain forms, no JavaScript. Runs with no
authentication and binds to localhost by default; see decisions.md #46 for what a real
deployment would put in front of it.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import psycopg
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from govwatch import metrics
from govwatch.review import store

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
FILTERS = {
    "all": store.REVIEWABLE,
    "pending_review": ("pending_review",),
    "needs_attention": ("needs_attention",),
}
MAX_NOTE = 2000


def create_app(database_url: str) -> FastAPI:
    app = FastAPI(title="govwatch review", docs_url=None, redoc_url=None, openapi_url=None)
    # every decision/status pair exists at 0 from the first scrape, so rates and dashboards
    # show "none yet" instead of "no data"
    for decision in store.DECISIONS:
        for from_status in store.REVIEWABLE:
            metrics.REVIEW_DECISIONS.labels(decision, from_status)

    def db() -> Iterator[psycopg.Connection]:
        with psycopg.connect(database_url, autocommit=True) as conn:
            yield conn

    Conn = Annotated[psycopg.Connection, Depends(db)]

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request, conn: Conn, status: str = "all"):
        if status not in FILTERS:
            raise HTTPException(400, f"unknown status filter {status!r}")
        return TEMPLATES.TemplateResponse(
            request,
            "queue.html",
            {
                "items": store.queue(conn, FILTERS[status]),
                "counts": store.counts(conn),
                "status": status,
            },
        )

    @app.get("/bills/{bill_id}", response_class=HTMLResponse)
    def bill(request: Request, bill_id: str, conn: Conn):
        item = store.current(conn, bill_id)
        if item is None:
            raise HTTPException(404, f"no summary for {bill_id}")
        return TEMPLATES.TemplateResponse(
            request,
            "bill.html",
            {
                "item": item,
                "reviewable": item["status"] in store.REVIEWABLE,
                "reviewer": request.cookies.get("reviewer", ""),
                "error": request.query_params.get("error"),
            },
        )

    @app.post("/summaries/{summary_id}/decision")
    def decision(
        summary_id: int,
        bill_id: Annotated[str, Form()],
        decision: Annotated[str, Form()],
        reviewer: Annotated[str, Form()],
        conn: Conn,
        note: Annotated[str, Form()] = "",
    ):
        reviewer = reviewer.strip()[:100]
        note = note.strip()[:MAX_NOTE]
        back = f"/bills/{quote(bill_id)}"
        if decision not in store.DECISIONS:
            raise HTTPException(400, "decision must be approved or rejected")
        if not reviewer:
            return _redirect(f"{back}?error=Add+your+name+so+the+decision+is+attributable")
        # a rejection without a reason doesn't help anyone improve the prompt
        if decision == "rejected" and not note:
            return _redirect(f"{back}?error=Say+what+was+wrong+when+rejecting")

        try:
            done = store.decide(conn, summary_id, decision, reviewer, note or None)
        except store.AlreadyDecided as exc:
            raise HTTPException(409, str(exc)) from exc

        metrics.REVIEW_DECISIONS.labels(done.decision, done.from_status).inc()
        metrics.REVIEW_LATENCY.observe((done.reviewed_at - done.created_at).total_seconds())

        # straight on to the next item, so working through the queue is quick
        nxt = store.queue(conn, store.REVIEWABLE)
        resp = _redirect(f"/bills/{quote(nxt[0]['bill_id'])}" if nxt else "/")
        resp.set_cookie("reviewer", reviewer, max_age=60 * 60 * 24 * 90, samesite="strict")
        return resp

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok\n"

    @app.get("/metrics")
    def prometheus_metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)
