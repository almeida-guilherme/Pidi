"""
The website: a JSON API under /api and, when it has been built, the React front end at every other path.

    .venv/bin/uvicorn web.app:create_app --factory --port 8000
"""
import logging
import os
import secrets
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from sqlalchemy import func
from sqlmodel import select

from pidi_template import LANGUAGES, is_translated
from web import account, asaas, auth, credits, google, keys, payments, reviews, roi_templates, runs, templates, usage
from web.config import Settings
from web.db import Run, make_engine, session
from web.jobs import JobRunner, RunFn, default_run_fn
from web.mail import Alerts, Mailer
from web.ratelimit import Limits

log = logging.getLogger("pdd.app")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # media-src blob:: the new-project page reads the chosen video's length in the browser, from a blob: URL.
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                               "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
}


# Both middlewares are plain ASGI rather than @app.middleware("http"): that kind wraps the request so the endpoint
# can't see the browser leave (request.is_disconnected), which cancelling a template's reading relies on.

class SecurityHeaders:
    """Adds SECURITY_HEADERS to every response that doesn't set them itself."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in SECURITY_HEADERS.items():
                    headers.setdefault(key, value)
            await send(message)
        await self.app(scope, receive, send_with_headers)


class ReportErrors:
    """Unexpected errors are logged and reported to the administrator (PDD_ALERT_EMAIL), and answered with a 500."""

    def __init__(self, app, alerts: Alerts):
        self.app, self.alerts = app, alerts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def tracked_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)
        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            details = traceback.format_exc()
            where = f"{scope['method']} {scope['path']}"
            log.error("%s failed:\n%s", where, details)
            self.alerts.report(f"api:{type(exc).__name__}", f"Server error: {type(exc).__name__}",
                               f"{where} failed.\n\n{details[-4000:]}")
            if not started:  # otherwise the answer is half sent; nothing more can be said
                await JSONResponse({"detail": "Something went wrong on the server. Try again in a moment."},
                                   status_code=500)(scope, receive, send)


def _default_redo_run(run_dir: str, feedback: dict, version: int, ai: dict | None = None) -> dict:
    from revise import redo
    return redo(run_dir, feedback, version, ai=ai)


def _default_assess_run(run_dir: str, ai: dict | None = None):
    from pipeline import assess_run
    return assess_run(run_dir, ai=ai)


def create_app(settings: Settings | None = None, run_fn: RunFn = default_run_fn, start_jobs: bool = True,
               analyze_template=templates.default_analyze, mailer=None, google_identity=google.identity,
               create_checkout=asaas.create_checkout, assess_run=None,
               check_api_key=keys.check_key, redo_run=None) -> FastAPI:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or Settings()
    if run_fn is default_run_fn and settings.uses_credits and not os.getenv("OPENAI_API_KEY"):
        # With credits, every project runs on it (see web/credits.py); with keys, on each person's own.
        raise RuntimeError("Set OPENAI_API_KEY (in .env): with PDD_BILLING=credits every project runs on the "
                           "server's OpenAI key.")
    engine = make_engine(settings.database_url)
    # mailer.send(to, subject, body); swapped for one that collects the emails in tests.
    mailer = mailer or Mailer(settings)
    alerts = Alerts(mailer, settings.alert_email)
    jobs = JobRunner(engine, settings.runs_dir, run_fn, settings.workers, runs_per_user=settings.runs_per_user,
                     retention_days=settings.retention_days, alerts=alerts, translations_dir=settings.translations_dir,
                     max_cost_per_credit_usd=settings.max_cost_per_credit_usd, billing=settings.billing,
                     secret_key=settings.secret_key, allow_server_key=settings.allow_server_key)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.local_login:
            auth.announce_local_login(app)
        if start_jobs:
            jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="Pidi", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings, app.state.engine, app.state.jobs = settings, engine, jobs
    app.state.local_login_token = secrets.token_urlsafe(32) if settings.local_login else None
    app.state.mailer, app.state.alerts, app.state.limits = mailer, alerts, Limits()
    # analyze_template(path) -> TemplateAnalysis; swapped for a fake in tests.
    app.state.analyze_template = analyze_template
    # google_identity(code, redirect_uri, client_id, client_secret) -> ID token claims; a fake in tests.
    app.state.google_identity = google_identity
    # create_checkout(base_url, api_key, reference, credits, price_cents, return_url) -> (id, link); a fake in tests.
    app.state.create_checkout = create_checkout
    # redo_run(run_dir, feedback, version, api_key) -> {"answers", "changed"}: a project done again from its
    # review notes (revise.redo).
    app.state.redo_run = redo_run or _default_redo_run
    # check_api_key(provider, key) raises ValueError if the provider refuses it; a fake in tests.
    app.state.check_api_key = check_api_key
    # assess_run(run_dir): the viability assessment of a run made before it existed (pipeline.assess_run).
    app.state.assess_run = assess_run or _default_assess_run

    # ReportErrors is added first, so it sits inside SecurityHeaders and its answer gets the headers too.
    app.add_middleware(ReportErrors, alerts=alerts)
    app.add_middleware(SecurityHeaders)

    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(runs.router)
    app.include_router(reviews.router)
    app.include_router(roi_templates.router)
    app.include_router(templates.router)
    app.include_router(keys.router)
    app.include_router(usage.router)
    app.include_router(credits.router)
    app.include_router(payments.router)

    @app.get("/api/health")
    def health():
        """For uptime monitors: 200 while the database answers and every worker is alive, 503 otherwise."""
        with session(engine) as db:
            counts = dict(db.exec(select(Run.status, func.count()).where(Run.status.in_(("queued", "running")))
                                  .group_by(Run.status)).all())
        workers = jobs.alive_workers()
        ok = workers == settings.workers or not start_jobs
        return JSONResponse({"ok": ok, "workers": workers, "queued": counts.get("queued", 0),
                             "running": counts.get("running", 0)}, status_code=200 if ok else 503)

    @app.get("/api/site")
    def site():
        """Public facts the pages show: who runs the site (privacy notice), retention, how to log in, the
        languages of Pidi's template, how projects are paid for (own keys or credits) and what credits cost."""
        languages = [{"code": code, "name": name, "translated": is_translated(code)}
                     for code, (name, _) in LANGUAGES.items()]
        return {"operator": settings.operator_name, "contact": settings.contact_email, "languages": languages,
                "retention_days": settings.retention_days, "privacy_version": auth.PRIVACY_VERSION,
                "signup": settings.allow_signup, "google": settings.google_configured, "local_login": settings.local_login,
                "billing": settings.billing, "server_key": keys.server_key_available(settings),
                "credits": {"per_minute": settings.credits_per_minute, "translation": credits.TRANSLATION_CREDITS,
                            "signup": max(settings.signup_credits, 0)},
                "payments": {"configured": settings.payments_configured, "sandbox": settings.asaas_sandbox,
                             "price_cents": settings.credit_price_cents, "min": settings.credit_min,
                             "max": settings.credit_max,
                             "suggested": [n for n in settings.credit_packs
                                           if settings.credit_min <= n <= settings.credit_max]}}

    _serve_frontend(app, settings.frontend_dir)
    return app


def _serve_frontend(app: FastAPI, dist: Path) -> None:
    """Serves the built React app (npm run build). In development Vite serves it instead (npm run dev)."""
    if not (dist / "index.html").exists():
        return
    dist = dist.resolve()
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found.")
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist):
            return FileResponse(file)
        # Any other path is a page of the app (e.g. /runs/abc); React Router shows it.
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
